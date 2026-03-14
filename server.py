"""
server.py - ポーカーGTO FastAPI サーバー
使用法: python server.py
ブラウザで http://localhost:5000 を開く
"""

import asyncio
import os
import sys
import json
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, Form, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import uvicorn

ROOT       = Path(__file__).parent
SCRIPTS    = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from analyze import analyze_file as _analyze_file  # noqa: E402
INPUT_DIR  = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
DATA_DIR   = ROOT / "data"
DONE_DIR   = INPUT_DIR / "done"

for d in [INPUT_DIR, OUTPUT_DIR, DATA_DIR, DONE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

BASE_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

app = FastAPI()

# CORS（ブックマークレットからの別オリジンPOSTを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── ジョブ管理 ───────────────────────────────────────────────────────────────
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

# SSEイベントキュー（job_idごと）
event_queues: dict[str, asyncio.Queue] = {}

STEP_LABELS = {
    0: "処理開始...",
    1: "ハンド履歴をパース中...",
    2: "GTO分析中（Gemini API）...",
    3: "PDFを生成中...",
}

async def run_pipeline(job_id: str, txt_path: Path, api_key: str):
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q
    env  = {**BASE_ENV, "GEMINI_API_KEY": api_key}
    logs = []

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def set_step(s):
        with jobs_lock:
            jobs[job_id]["step"] = s
        print(f"[job:{job_id[:8]}] step {s}: {STEP_LABELS[s]}")

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)  # sentinel
        print(f"[job:{job_id[:8]}] ERROR: {msg[:200]}")

    # ── Step 1: パース ──────────────────────────────────────────────────────
    set_step(1)
    json_path = DATA_DIR / (txt_path.stem + ".json")

    def do_parse():
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "parse.py"), str(txt_path), str(json_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_parse)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})

    # ── Step 2: Gemini分析（analyze_fileを直接呼び出し）────────────────────
    set_step(2)

    def do_analyze():
        _analyze_file(str(json_path), progress_cb=push, api_key=api_key)

    try:
        await loop.run_in_executor(None, do_analyze)
    except SystemExit as e:
        if e.code != 0:
            fail("Gemini APIキーが無効またはAPI呼び出しエラーが発生しました")
            return

    # ── Step 3: PDF生成 ─────────────────────────────────────────────────────
    push({"type": "generate_start"})
    set_step(3)

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate.js"), str(OUTPUT_DIR), str(json_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_generate)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    pdf_files = sorted(OUTPUT_DIR.glob("GTO_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdf_files:
        fail("\n".join(logs) + "\nPDFが見つかりません")
        return

    dest = DONE_DIR / txt_path.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(txt_path), str(dest))

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["pdf"]    = pdf_files[0].name

    push({"type": "done", "pdf": pdf_files[0].name})
    loop.call_soon_threadsafe(q.put_nowait, None)  # sentinel（SSE接続を閉じる）
    print(f"[job:{job_id[:8]}] 完了: {pdf_files[0].name}")


# ─── ルート ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return UPLOAD_PAGE

@app.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    api_key: str = Form(""),
):
    # APIキー: フォーム入力 → なければ .env の値を使用
    key = api_key.strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return HTMLResponse(ERROR_PAGE.format(log="Gemini APIキーが入力されていません。"), status_code=400)

    data = await file.read()
    txt_path = INPUT_DIR / "upload.txt"
    txt_path.write_bytes(data)
    print(f"[upload] {len(data)} bytes, key={'*'*8+key[-4:] if len(key)>4 else '***'}")

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": ""}

    background_tasks.add_task(run_pipeline, job_id, txt_path, key)
    return RedirectResponse(f"/progress/{job_id}", status_code=303)

@app.post("/scrape_upload")
async def scrape_upload(background_tasks: BackgroundTasks, request: Request):
    """ブックマークレットからのJSON直接POSTを受け付けるエンドポイント"""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text is empty"}, status_code=400)

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return JSONResponse({"error": "GEMINI_API_KEY not set on server"}, status_code=500)

    txt_path = INPUT_DIR / "upload.txt"
    txt_path.write_text(text, encoding="utf-8")
    print(f"[scrape_upload] {len(text)} chars")

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": ""}

    background_tasks.add_task(run_pipeline, job_id, txt_path, key)
    return JSONResponse({"job_id": job_id})

@app.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            return HTMLResponse("<h1>404</h1>", status_code=404)
    return progress_page(job_id)

@app.get("/status/{job_id}")
async def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "error", "log": "job not found"})
    return JSONResponse(job)

@app.get("/stream/{job_id}")
async def stream(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)

    # ジョブが既に完了/エラーの場合はその場でイベントを送って終了
    async def immediate_done():
        if job["status"] == "done":
            yield {"data": json.dumps({"type": "done", "pdf": job["pdf"]}, ensure_ascii=False)}
        elif job["status"] == "error":
            yield {"data": json.dumps({"type": "error", "message": job.get("log", "不明なエラー")[:300]}, ensure_ascii=False)}

    if job["status"] in ("done", "error"):
        return EventSourceResponse(immediate_done())

    q = event_queues.get(job_id)
    if not q:
        return JSONResponse({"error": "stream not ready"}, status_code=503)

    async def event_generator():
        try:
            while True:
                data = await asyncio.wait_for(q.get(), timeout=60.0)
                if data is None:  # sentinel: ストリーム終了
                    break
                yield {"data": json.dumps(data, ensure_ascii=False)}
        except asyncio.TimeoutError:
            yield {"data": json.dumps({"type": "error", "message": "タイムアウト"}, ensure_ascii=False)}
        finally:
            event_queues.pop(job_id, None)

    return EventSourceResponse(event_generator())

@app.get("/error/{job_id}", response_class=HTMLResponse)
async def error_page(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id, {})
    return HTMLResponse(ERROR_PAGE.format(log=_esc(job.get("log", "不明なエラー"))), status_code=500)

@app.get("/report/{name}", response_class=HTMLResponse)
async def report(name: str):
    fpath = OUTPUT_DIR / name
    if not fpath.exists() or fpath.suffix != ".pdf":
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return report_page(name)

@app.get("/pdf/{name}")
async def serve_pdf(name: str):
    fpath = OUTPUT_DIR / name
    if not fpath.exists() or fpath.suffix != ".pdf":
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return FileResponse(fpath, media_type="application/pdf", headers={"Content-Disposition": "inline"})

@app.get("/download/{name}")
async def download_pdf(name: str):
    fpath = OUTPUT_DIR / name
    if not fpath.exists() or fpath.suffix != ".pdf":
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return FileResponse(fpath, media_type="application/pdf", filename=name)


# ─── ユーティリティ ───────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── HTMLテンプレート ─────────────────────────────────────────────────────────

# .env にキーがあればデフォルト値として埋め込む（BYOK: 空欄でも手入力可）
_DEFAULT_KEY = os.environ.get("GEMINI_API_KEY", "")
_KEY_PLACEHOLDER = "AIza... (Gemini APIキーを入力)"

UPLOAD_PAGE = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ポーカーGTO レポート生成</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e;
  color: #eee;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}}
.card {{
  background: #16213e;
  border-radius: 16px;
  padding: 40px 48px;
  width: 100%;
  max-width: 500px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  text-align: center;
}}
h1 {{ font-size: 22px; margin-bottom: 6px; color: #e94560; }}
.sub {{ font-size: 13px; color: #888; margin-bottom: 28px; }}
.dropzone {{
  border: 2px dashed #e94560;
  border-radius: 12px;
  padding: 32px 20px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 16px;
  position: relative;
}}
.dropzone:hover, .dropzone.dragover {{ background: rgba(233,69,96,0.08); }}
.dropzone input[type=file] {{
  position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
}}
.dropzone-icon {{ font-size: 36px; margin-bottom: 8px; }}
.dropzone-label {{ font-size: 14px; color: #aaa; }}
.dropzone-label span {{ color: #e94560; font-weight: bold; }}
.file-name {{ font-size: 13px; color: #4caf93; margin-top: 6px; min-height: 18px; }}
.field-group {{ margin-bottom: 16px; text-align: left; }}
.field-group label {{ font-size: 12px; color: #888; display: block; margin-bottom: 6px; }}
.field-group input[type=password] {{
  width: 100%;
  padding: 10px 12px;
  background: #0f0f1a;
  border: 1px solid #333;
  border-radius: 6px;
  color: #eee;
  font-size: 13px;
  outline: none;
  transition: border-color 0.2s;
}}
.field-group input:focus {{ border-color: #e94560; }}
.key-hint {{ font-size: 11px; color: #555; margin-top: 4px; }}
.key-hint a {{ color: #e94560; text-decoration: none; }}
.btn-primary {{
  width: 100%;
  padding: 14px;
  background: #e94560;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: bold;
  cursor: pointer;
  transition: background 0.2s;
  margin-top: 8px;
}}
.btn-primary:hover {{ background: #c73652; }}
.btn-primary:disabled {{ background: #555; cursor: not-allowed; }}
.btn-guide {{
  width: 100%;
  padding: 11px;
  background: transparent;
  color: #aaa;
  border: 1px solid #333;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 10px;
}}
.btn-guide:hover {{ border-color: #e94560; color: #e94560; }}
/* ─── モーダル ─── */
.modal-overlay {{
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.7);
  z-index: 100;
  align-items: center;
  justify-content: center;
  padding: 20px;
}}
.modal-overlay.open {{ display: flex; }}
.modal {{
  background: #16213e;
  border-radius: 16px;
  width: 100%;
  max-width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 16px 48px rgba(0,0,0,0.6);
}}
.modal-header {{
  padding: 24px 28px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}
.modal-header h2 {{ font-size: 17px; color: #e94560; }}
.modal-close {{
  background: none; border: none; color: #666;
  font-size: 22px; cursor: pointer; padding: 0 4px;
  transition: color 0.2s;
}}
.modal-close:hover {{ color: #eee; }}
.step-indicator {{
  display: flex;
  align-items: center;
  padding: 20px 28px 0;
  gap: 0;
}}
.step-dot {{
  width: 28px; height: 28px;
  border-radius: 50%;
  background: #0f0f1a;
  border: 2px solid #333;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: bold; color: #555;
  flex-shrink: 0;
  transition: all 0.3s;
}}
.step-dot.active {{ border-color: #e94560; color: #e94560; background: rgba(233,69,96,0.1); }}
.step-dot.done  {{ border-color: #4caf93; color: #fff; background: #4caf93; }}
.step-line {{ flex: 1; height: 2px; background: #333; transition: background 0.3s; }}
.step-line.done {{ background: #4caf93; }}
.modal-body {{ padding: 20px 28px 28px; }}
.step-panel {{ display: none; }}
.step-panel.active {{ display: block; }}
.step-title {{ font-size: 15px; font-weight: bold; margin-bottom: 16px; color: #eee; }}
.guide-steps {{ list-style: none; display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }}
.guide-steps li {{
  display: flex; gap: 12px; align-items: flex-start;
  background: #0f0f1a; border-radius: 8px; padding: 12px 14px;
  font-size: 13px; line-height: 1.5; color: #ccc;
}}
.guide-steps li .num {{
  background: #e94560; color: #fff;
  width: 20px; height: 20px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: bold; flex-shrink: 0; margin-top: 1px;
}}
.skip-row {{
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 20px; font-size: 13px; color: #888; cursor: pointer;
}}
.skip-row input {{ accent-color: #e94560; width: 16px; height: 16px; cursor: pointer; }}
.dl-btn {{
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 18px;
  background: rgba(233,69,96,0.12);
  border: 1px solid #e94560;
  border-radius: 8px;
  color: #e94560;
  text-decoration: none;
  font-size: 14px; font-weight: bold;
  margin-bottom: 20px;
  transition: background 0.2s;
}}
.dl-btn:hover {{ background: rgba(233,69,96,0.22); }}
.info-box {{
  background: #0f0f1a; border-radius: 8px; padding: 14px 16px;
  font-size: 13px; color: #aaa; line-height: 1.7; margin-bottom: 20px;
}}
.info-box code {{
  background: #1e2a3a; padding: 1px 6px; border-radius: 4px;
  font-size: 12px; color: #7ec8e3;
}}
.modal-footer {{
  display: flex; gap: 10px; justify-content: flex-end;
}}
.btn-back-modal {{
  padding: 10px 20px;
  background: transparent; color: #aaa;
  border: 1px solid #444; border-radius: 6px;
  font-size: 14px; cursor: pointer; transition: all 0.2s;
}}
.btn-back-modal:hover {{ border-color: #e94560; color: #e94560; }}
.btn-next-modal {{
  padding: 10px 24px;
  background: #e94560; color: #fff;
  border: none; border-radius: 6px;
  font-size: 14px; font-weight: bold; cursor: pointer;
  transition: background 0.2s;
}}
.btn-next-modal:hover {{ background: #c73652; }}
@media (max-width: 540px) {{
  .card {{ padding: 28px 20px; }}
  .modal-body {{ padding: 16px 16px 24px; }}
  .modal-header {{ padding: 20px 16px 0; }}
  .step-indicator {{ padding: 16px 16px 0; }}
}}
</style>
</head>
<body>
<div class="card">
  <h1>&#x1F0A1; ポーカーGTO</h1>
  <p class="sub">TenFourのハンド履歴を分析してPDFレポートを生成</p>
  <form id="form" method="post" enctype="multipart/form-data" action="/upload">
    <div class="dropzone" id="drop">
      <input type="file" name="file" id="file" accept=".txt" required>
      <div class="dropzone-icon">&#x1F4C4;</div>
      <div class="dropzone-label"><span>ファイルを選択</span>またはドロップ</div>
      <div class="file-name" id="fname"></div>
    </div>
    <div class="field-group">
      <label>Gemini API キー</label>
      <input type="password" name="api_key" id="api_key"
             placeholder="{_KEY_PLACEHOLDER}"
             value="{_DEFAULT_KEY}"
             autocomplete="off">
      <p class="key-hint">
        キーは送信時のみ使用・サーバーに保存されません。
        取得: <a href="https://aistudio.google.com/app/apikey" target="_blank">Google AI Studio</a>
      </p>
    </div>
    <button type="submit" id="btn" class="btn-primary">レポート生成</button>
  </form>
  <button class="btn-guide" id="open-guide">&#x1F4D6; はじめての方 — ハンド履歴の取得方法</button>
</div>

<!-- ウィザードモーダル -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-header">
      <h2>ハンド履歴の取得方法</h2>
      <button class="modal-close" id="modal-close">&#x2715;</button>
    </div>

    <!-- ステップインジケーター -->
    <div class="step-indicator">
      <div class="step-dot active" id="dot1">1</div>
      <div class="step-line" id="line1"></div>
      <div class="step-dot" id="dot2">2</div>
      <div class="step-line" id="line2"></div>
      <div class="step-dot" id="dot3">3</div>
    </div>

    <div class="modal-body">

      <!-- STEP 1: 拡張機能インストール -->
      <div class="step-panel active" id="panel1">
        <p class="step-title">STEP 1 — Chrome拡張機能をインストール</p>
        <label class="skip-row">
          <input type="checkbox" id="already-installed">
          すでにインストール済み（スキップ）
        </label>
        <div id="install-guide">
          <a class="dl-btn" href="/static/tenfour-scraper.zip" download>
            &#x2B07; tenfour-scraper.zip をダウンロード
          </a>
          <ul class="guide-steps">
            <li><span class="num">1</span>ダウンロードしたZIPを解凍して <code>tenfour-scraper</code> フォルダを取り出す</li>
            <li><span class="num">2</span>Chromeのアドレスバーに <code>chrome://extensions</code> と入力して開く</li>
            <li><span class="num">3</span>右上の「デベロッパーモード」をONにする</li>
            <li><span class="num">4</span>「パッケージ化されていない拡張機能を読み込む」をクリックして <code>tenfour-scraper</code> フォルダを選択</li>
            <li><span class="num">5</span>アドレスバー右の &#x1F9E9; アイコン → TenFour Scraper の &#x1F4CC; ピンをクリックしてツールバーに表示</li>
          </ul>
        </div>
        <div class="modal-footer">
          <button class="btn-next-modal" id="next1">次へ &#x2192;</button>
        </div>
      </div>

      <!-- STEP 2: ハンド履歴の取得 -->
      <div class="step-panel" id="panel2">
        <p class="step-title">STEP 2 — TenFourからハンド履歴を取得</p>
        <ul class="guide-steps">
          <li><span class="num">1</span>tenfour-poker.com を開き、ブックマークタブを表示する</li>
          <li><span class="num">2</span>ツールバーの 🃏 TenFour Scraper アイコンをクリック</li>
          <li><span class="num">3</span>「&#x25B6; ハンド履歴を取得」ボタンを押す</li>
          <li><span class="num">4</span>進捗バーが完了したら <code>hand_history_YYYY-MM-DD.txt</code> が自動ダウンロードされる</li>
        </ul>
        <div class="info-box">
          取得対象はブックマーク済みのハンドのみです（最大100件）。<br>
          分析したいハンドを事前にTenFourでブックマークしておいてください。
        </div>
        <div class="modal-footer">
          <button class="btn-back-modal" id="back2">&#x2190; 戻る</button>
          <button class="btn-next-modal" id="next2">次へ &#x2192;</button>
        </div>
      </div>

      <!-- STEP 3: アップロード -->
      <div class="step-panel" id="panel3">
        <p class="step-title">STEP 3 — ファイルをアップロードして分析</p>
        <div class="info-box">
          ダウンロードした <code>hand_history_*.txt</code> をモーダルを閉じて<br>
          メイン画面にドロップするか、「ファイルを選択」からアップロードしてください。
        </div>
        <div class="modal-footer">
          <button class="btn-back-modal" id="back3">&#x2190; 戻る</button>
          <button class="btn-next-modal" id="close-final">&#x2705; 準備完了 — 閉じる</button>
        </div>
      </div>

    </div>
  </div>
</div>

<script>
const file = document.getElementById('file');
const fname = document.getElementById('fname');
const drop = document.getElementById('drop');
const btn = document.getElementById('btn');
const form = document.getElementById('form');

file.addEventListener('change', () => {{ fname.textContent = file.files[0]?.name || ''; }});
drop.addEventListener('dragover', e => {{ e.preventDefault(); drop.classList.add('dragover'); }});
drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
drop.addEventListener('drop', e => {{
  e.preventDefault(); drop.classList.remove('dragover');
  if (e.dataTransfer.files[0]) {{
    file.files = e.dataTransfer.files;
    fname.textContent = e.dataTransfer.files[0].name;
  }}
}});
form.addEventListener('submit', () => {{ btn.disabled = true; }});

// ─── ウィザード ───
const modal = document.getElementById('modal');
let currentStep = 1;

function openModal() {{
  modal.classList.add('open');
  goStep(1);
}}
function closeModal() {{
  modal.classList.remove('open');
}}
function goStep(n) {{
  currentStep = n;
  [1,2,3].forEach(i => {{
    document.getElementById('panel' + i).classList.toggle('active', i === n);
    const dot = document.getElementById('dot' + i);
    dot.classList.remove('active', 'done');
    if (i < n) dot.classList.add('done'), dot.textContent = '✓';
    else if (i === n) dot.classList.add('active'), dot.textContent = i;
    else dot.textContent = i;
    if (i < 3) {{
      document.getElementById('line' + i).classList.toggle('done', i < n);
    }}
  }});
}}

document.getElementById('open-guide').addEventListener('click', openModal);
document.getElementById('modal-close').addEventListener('click', closeModal);
modal.addEventListener('click', e => {{ if (e.target === modal) closeModal(); }});

document.getElementById('already-installed').addEventListener('change', e => {{
  document.getElementById('install-guide').style.display = e.target.checked ? 'none' : '';
}});

document.getElementById('next1').addEventListener('click', () => goStep(2));
document.getElementById('back2').addEventListener('click', () => goStep(1));
document.getElementById('next2').addEventListener('click', () => goStep(3));
document.getElementById('back3').addEventListener('click', () => goStep(2));
document.getElementById('close-final').addEventListener('click', closeModal);
</script>
</body>
</html>"""


def progress_page(job_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>処理中... - ポーカーGTO</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e; color: #eee;
  min-height: 100vh; display: flex;
  align-items: center; justify-content: center;
}}
.card {{
  background: #16213e; border-radius: 16px;
  padding: 48px 56px; width: 500px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5); text-align: center;
}}
h1 {{ font-size: 20px; margin-bottom: 8px; color: #e94560; }}
.status-msg {{ font-size: 14px; color: #aaa; margin-bottom: 24px; min-height: 20px; }}
.progress-bar-wrap {{
  background: #0f0f1a; border-radius: 99px;
  height: 6px; overflow: hidden; margin-bottom: 6px;
}}
.progress-bar {{
  height: 100%; background: #e94560; border-radius: 99px;
  transition: width 0.5s ease; width: 5%;
}}
.elapsed {{ font-size: 12px; color: #555; margin-bottom: 24px; }}
.steps {{ display: flex; flex-direction: column; gap: 10px; text-align: left; }}
.step {{
  display: flex; align-items: flex-start; gap: 12px;
  padding: 12px 16px; border-radius: 8px;
  background: #0f0f1a; font-size: 14px; color: #555;
  transition: all 0.3s; border-left: 3px solid transparent;
}}
.step.active {{ background: rgba(233,69,96,0.1); color: #eee; border-left-color: #e94560; }}
.step.done   {{ background: rgba(76,175,147,0.08); color: #4caf93; border-left-color: #4caf93; }}
.step-icon {{ width: 22px; text-align: center; flex-shrink: 0; font-size: 16px; margin-top: 1px; }}
.step-body {{ flex: 1; }}
.step-label {{ display: block; }}
.spinner {{
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid #444; border-top-color: #e94560;
  border-radius: 50%; animation: spin 0.8s linear infinite;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
/* バッチ進捗 */
.batch-detail {{ display: none; margin-top: 8px; }}
.batch-bar-wrap {{
  background: rgba(0,0,0,0.35); border-radius: 99px;
  height: 4px; overflow: hidden; margin-bottom: 6px;
}}
.batch-bar {{
  height: 100%; background: #e94560; border-radius: 99px;
  transition: width 0.4s ease; width: 0%;
}}
.batch-text {{ font-size: 11px; color: #a0a0b8; font-family: monospace; }}
.hand-text  {{ font-size: 10px; color: #666; margin-top: 3px; font-style: italic; }}
</style>
</head>
<body>
<div class="card">
  <h1>&#x1F0A1; レポート生成中...</h1>
  <p class="status-msg" id="msg">処理を開始しています...</p>
  <div class="progress-bar-wrap"><div class="progress-bar" id="pbar"></div></div>
  <p class="elapsed" id="elapsed"></p>
  <div class="steps">
    <div class="step" id="step1">
      <span class="step-icon" id="icon1">1</span>
      <div class="step-body"><span class="step-label" id="label1">ハンド履歴をパース</span></div>
    </div>
    <div class="step" id="step2">
      <span class="step-icon" id="icon2">2</span>
      <div class="step-body">
        <span class="step-label" id="label2">GTO分析（Gemini API）</span>
        <div class="batch-detail" id="batch-detail">
          <div class="batch-bar-wrap"><div class="batch-bar" id="batch-bar"></div></div>
          <div class="batch-text" id="batch-text"></div>
          <div class="hand-text"  id="hand-text"></div>
        </div>
      </div>
    </div>
    <div class="step" id="step3">
      <span class="step-icon" id="icon3">3</span>
      <div class="step-body"><span class="step-label" id="label3">PDFを生成</span></div>
    </div>
  </div>
</div>
<script>
const JOB_ID = '{job_id}';
let startTime = Date.now();
let sseActive = false;

setInterval(() => {{
  const s = Math.floor((Date.now() - startTime) / 1000);
  const m = Math.floor(s / 60), sec = s % 60;
  document.getElementById('elapsed').textContent =
    m > 0 ? `経過時間: ${{m}}分${{sec}}秒` : `経過時間: ${{sec}}秒`;
}}, 1000);

function blocks(pct, len=12) {{
  const f = Math.round(pct / 100 * len);
  return '\u2588'.repeat(f) + '\u2591'.repeat(len - f) + ' ' + pct + '%';
}}
function setPbar(pct) {{ document.getElementById('pbar').style.width = pct + '%'; }}

function markDone(n, txt) {{
  const el = document.getElementById('step' + n);
  el.className = 'step done';
  document.getElementById('icon' + n).textContent = '\u2713';
  if (txt) document.getElementById('label' + n).textContent = txt;
}}
function markActive(n, txt) {{
  const el = document.getElementById('step' + n);
  el.className = 'step active';
  document.getElementById('icon' + n).innerHTML = '<span class="spinner"></span>';
  if (txt) document.getElementById('label' + n).textContent = txt;
}}
function markIdle(n) {{
  document.getElementById('step' + n).className = 'step';
  document.getElementById('icon' + n).textContent = n;
}}

// ─── SSE ──────────────────────────────────────────────────────────────────
const es = new EventSource('/stream/' + JOB_ID);

// 10秒以内にSSEイベントが届かなければポーリングへフォールバック
const fallbackTimer = setTimeout(() => {{
  if (!sseActive) {{ es.close(); pollFallback(); }}
}}, 10000);

es.onmessage = function(e) {{
  sseActive = true;
  clearTimeout(fallbackTimer);
  const d = JSON.parse(e.data);

  if (d.type === 'parse_done') {{
    markDone(1, `ハンド履歴を解析しました（${{d.hands_total}}ハンド検出）`);
    markActive(2);
    setPbar(20);
    document.getElementById('msg').textContent = 'GTO分析中（Gemini API）...';

  }} else if (d.type === 'batch_progress') {{
    const pct = Math.round(d.hands_done / d.hands_total * 100);
    setPbar(20 + Math.round(pct * 0.5));
    document.getElementById('batch-detail').style.display = 'block';
    document.getElementById('batch-bar').style.width = pct + '%';
    document.getElementById('batch-text').textContent =
      `バッチ ${{d.batch_current}}/${{d.batch_total}} 完了  ${{blocks(pct)}}  (${{d.hands_done}}/${{d.hands_total}}ハンド)`;
    if (d.current_hand_info)
      document.getElementById('hand-text').textContent = '\u2192 ' + d.current_hand_info;

  }} else if (d.type === 'generate_start') {{
    markDone(2, 'GTO分析が完了しました');
    markActive(3, 'PDFを生成中...');
    document.getElementById('batch-detail').style.display = 'none';
    setPbar(72);
    document.getElementById('msg').textContent = 'PDFを生成中...';

  }} else if (d.type === 'done') {{
    markDone(1); markDone(2); markDone(3);
    setPbar(100);
    document.getElementById('msg').textContent = '完了！レポートを表示します...';
    es.close();
    setTimeout(() => {{ window.location.href = '/report/' + d.pdf; }}, 800);

  }} else if (d.type === 'error') {{
    document.getElementById('msg').textContent = 'エラーが発生しました';
    es.close();
    setTimeout(() => {{ window.location.href = '/error/' + JOB_ID; }}, 1000);
  }}
}};

es.onerror = function() {{
  if (sseActive) return;
  es.close();
  clearTimeout(fallbackTimer);
  pollFallback();
}};

// ─── ポーリングフォールバック ──────────────────────────────────────────────
const POLL_PROGRESS = [0, 10, 30, 85, 100];
const POLL_MSGS     = ['', 'ハンド履歴をパース中...', 'GTO分析中（Gemini API）...', 'PDFを生成中...', '完了！'];

async function pollFallback() {{
  if (sseActive) return;
  try {{
    const res  = await fetch('/status/' + JOB_ID);
    const data = await res.json();
    if (data.status === 'done') {{
      markDone(1); markDone(2); markDone(3);
      setPbar(100);
      document.getElementById('msg').textContent = '完了！レポートを表示します...';
      setTimeout(() => {{ window.location.href = '/report/' + data.pdf; }}, 800);
      return;
    }}
    if (data.status === 'error') {{
      window.location.href = '/error/' + JOB_ID;
      return;
    }}
    const s = data.step || 0;
    setPbar(POLL_PROGRESS[s]);
    if (POLL_MSGS[s]) document.getElementById('msg').textContent = POLL_MSGS[s];
    for (let i = 1; i <= 3; i++) {{
      if (i < s) markDone(i);
      else if (i === s) markActive(i);
      else markIdle(i);
    }}
  }} catch(_) {{}}
  if (!sseActive) setTimeout(pollFallback, 3000);
}}
</script>
</body>
</html>"""


def report_page(pdf_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>GTO レポート</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e;
  color: #eee;
  height: 100vh;
  display: flex;
  flex-direction: column;
}}
.toolbar {{
  background: #16213e;
  padding: 12px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  flex-shrink: 0;
}}
.toolbar h1 {{ font-size: 16px; color: #e94560; flex: 1; }}
.btn-download {{
  padding: 8px 20px;
  background: #e94560;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: bold;
  text-decoration: none;
  display: inline-block;
  transition: background 0.2s;
}}
.btn-download:hover {{ background: #c73652; }}
.btn-back {{
  padding: 8px 16px;
  background: transparent;
  color: #aaa;
  border: 1px solid #444;
  border-radius: 6px;
  font-size: 14px;
  text-decoration: none;
  display: inline-block;
  transition: border-color 0.2s, color 0.2s;
}}
.btn-back:hover {{ border-color: #e94560; color: #e94560; }}
iframe {{ flex: 1; width: 100%; border: none; }}
</style>
</head>
<body>
<div class="toolbar">
  <h1>&#x1F0A1; GTO レポート — {pdf_name}</h1>
  <a class="btn-back" href="/">&#x2190; 戻る</a>
  <a class="btn-download" href="/download/{pdf_name}" download="{pdf_name}">&#x2B07; ダウンロード</a>
</div>
<iframe src="/pdf/{pdf_name}" type="application/pdf"></iframe>
</body>
</html>"""


ERROR_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>エラー</title>
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #eee; padding: 40px; }}
h2 {{ color: #e94560; margin-bottom: 16px; }}
pre {{ background: #0f0f1a; padding: 20px; border-radius: 8px; white-space: pre-wrap; color: #f88; font-size: 12px; }}
a {{ color: #e94560; }}
</style></head><body>
<h2>&#x274C; エラーが発生しました</h2>
<pre>{{log}}</pre>
<p><a href="/">&#x2190; 戻る</a></p>
</body></html>"""


# ─── メイン ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"ポーカーGTO サーバー起動: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
