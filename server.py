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
from parse import parse_file as _parse_file  # noqa: E402
from quick_analyzer import compute_quick_stats as _quick_stats  # noqa: E402
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
quick_results: dict[str, dict] = {}  # job_id → compute_quick_stats の結果
classify_results: dict[str, dict] = {}  # job_id → classify結果データ

# SSEイベントキュー（job_idごと）
event_queues: dict[str, asyncio.Queue] = {}

STEP_LABELS = {
    0: "処理開始...",
    1: "ハンド履歴をパース中...",
    2: "GTO分析中（Gemini API）...",
    3: "PDFを生成中...",
}

STEP_LABELS_NOAPI = {
    0: "処理開始...",
    1: "ハンド履歴をパース中...",
    2: "青線/赤線を分類中...",
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


async def run_noapi_pipeline(job_id: str, txt_path: Path):
    """APIなしモード: parse → classify → generate_noapilist"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q
    env  = BASE_ENV.copy()
    logs = []

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def set_step(s):
        with jobs_lock:
            jobs[job_id]["step"] = s
        print(f"[job:{job_id[:8]}] step {s}: {STEP_LABELS_NOAPI[s]}")

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)
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

    # ── Step 2: 分類 ────────────────────────────────────────────────────────
    set_step(2)
    classified_path = DATA_DIR / (txt_path.stem + "_classified.json")

    def do_classify():
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "classify.py"), str(json_path), str(classified_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_classify)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    # generate_start イベントで進捗UIがstep2→step3へ遷移
    push({"type": "generate_start"})

    # ── Step 3: PDF生成 ─────────────────────────────────────────────────────
    set_step(3)

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate_noapilist.js"), str(OUTPUT_DIR), str(classified_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_generate)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    pdf_files = sorted(OUTPUT_DIR.glob("NoAPI_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
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
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] 完了: {pdf_files[0].name}")


async def run_classify_pipeline(job_id: str, txt_path: Path, hero_name: str = ""):
    """parse → classify → Web結果画面（PDF生成なし）"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def set_step(s):
        with jobs_lock:
            jobs[job_id]["step"] = s
        print(f"[job:{job_id[:8]}] classify step {s}")

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)
        print(f"[job:{job_id[:8]}] ERROR: {msg[:200]}")

    # ── Step 1: パース ──────────────────────────────────────────────────────
    set_step(1)
    json_path = DATA_DIR / (txt_path.stem + ".json")

    r = await loop.run_in_executor(None, lambda: subprocess.run(
        [sys.executable, str(SCRIPTS / "parse.py"), str(txt_path), str(json_path)]
        + (["--hero-name", hero_name] if hero_name else []),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
        timeout=120,
    ))
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})

    # ── Step 2: 分類 ────────────────────────────────────────────────────────
    set_step(2)
    classified_path = DATA_DIR / (txt_path.stem + "_classified.json")

    r = await loop.run_in_executor(None, lambda: subprocess.run(
        [sys.executable, str(SCRIPTS / "classify.py"), str(json_path), str(classified_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
    ))
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["json_path"] = str(json_path)
        jobs[job_id]["classified_path"] = str(classified_path)

    push({"type": "classify_done"})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] 分類完了: {classified_path.name}")


async def run_pdf_pipeline(job_id: str, classified_path: str):
    """分類済みJSONからNoAPI PDFのみ生成"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)

    # step 1 を即完了扱いにして step 3 (PDF) のみ実行
    push({"type": "parse_done", "hands_total": 0})
    push({"type": "generate_start"})
    with jobs_lock:
        jobs[job_id]["step"] = 3

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate_noapilist.js"), str(OUTPUT_DIR), classified_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
        )

    r = await loop.run_in_executor(None, do_generate)
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    pdf_files = sorted(OUTPUT_DIR.glob("NoAPI_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdf_files:
        fail("PDFが見つかりません"); return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["pdf"] = pdf_files[0].name

    push({"type": "done", "pdf": pdf_files[0].name})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] PDF完了: {pdf_files[0].name}")


async def run_ai_pipeline(job_id: str, json_path: str, api_key: str):
    """分類済みJSONにGemini分析を追加してAI PDF生成"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q
    env = {**BASE_ENV, "GEMINI_API_KEY": api_key}

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)

    # パース済みなのでハンド数だけ取得してstep1を即完了
    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})
    with jobs_lock:
        jobs[job_id]["step"] = 2

    def do_analyze():
        _analyze_file(json_path, progress_cb=push, api_key=api_key)

    try:
        await loop.run_in_executor(None, do_analyze)
    except SystemExit as e:
        if e.code != 0:
            fail("Gemini APIキーが無効またはAPI呼び出しエラーが発生しました"); return

    push({"type": "generate_start"})
    with jobs_lock:
        jobs[job_id]["step"] = 3

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate.js"), str(OUTPUT_DIR), json_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_generate)
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    pdf_files = sorted(OUTPUT_DIR.glob("GTO_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdf_files:
        fail("PDFが見つかりません"); return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["pdf"] = pdf_files[0].name

    push({"type": "done", "pdf": pdf_files[0].name})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] AI PDF完了: {pdf_files[0].name}")


# ─── ルート ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return UPLOAD_PAGE

@app.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    hero_name: str = Form(""),
):
    data = await file.read()
    txt_path = INPUT_DIR / "upload.txt"
    txt_path.write_bytes(data)

    job_id = uuid.uuid4().hex
    print(f"[upload] {len(data)} bytes → classify pipeline (hero={hero_name!r})")
    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "classify", "hero_name": hero_name}
    background_tasks.add_task(run_classify_pipeline, job_id, txt_path, hero_name)
    return RedirectResponse(f"/classify_progress/{job_id}", status_code=303)

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


@app.get("/classify_progress/{job_id}", response_class=HTMLResponse)
async def classify_progress(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            return HTMLResponse("<h1>404</h1>", status_code=404)
    return HTMLResponse(classify_progress_page(job_id))


@app.get("/classify_result/{job_id}", response_class=HTMLResponse)
async def classify_result_view(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return HTMLResponse("<h1>404: 結果が見つかりません</h1>", status_code=404)

    classified_path = job.get("classified_path", "")
    json_path = job.get("json_path", "")

    try:
        with open(classified_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return HTMLResponse("<h1>データが見つかりません</h1>", status_code=404)

    hands = data.get("hands", [])
    blue_count = red_count = pf_count = 0
    categories: dict = {}

    for hand in hands:
        clf = hand.get("bluered_classification", {})
        line = clf.get("line", "")
        label = clf.get("category_label", "")
        if line == "blue":
            blue_count += 1
        elif line == "red":
            red_count += 1
        else:
            pf_count += 1
        if label:
            categories[label] = categories.get(label, 0) + 1

    total_hands = blue_count + red_count + pf_count

    # オールインEV差（Heroのみ集計）
    hero_ev_total = 0.0
    hero_name_found = ""
    hero_ev_count = 0
    for hand in hands:
        allin_ev = hand.get("result", {}).get("allin_ev", {})
        if not allin_ev:
            continue
        for p in hand.get("players", []):
            if not p.get("is_hero"):
                continue
            name = p.get("name", "")
            if name in allin_ev:
                ev = float(allin_ev[name])
                actual = float(p.get("result_bb", 0.0))
                hero_ev_total = round(hero_ev_total + (ev - actual), 2)
                hero_name_found = name
                hero_ev_count += 1
    allin_ev_diffs = {hero_name_found: hero_ev_total} if hero_name_found and abs(hero_ev_total) > 0.05 else {}

    # AI推定時間
    batches = max(1, (total_hands + 9) // 10)
    ai_secs = batches * 5
    if ai_secs < 60:
        ai_time_str = f"約{ai_secs}秒"
    else:
        m = ai_secs // 60
        s = ai_secs % 60
        ai_time_str = f"約{m}分{s:02d}秒" if s else f"約{m}分"

    return HTMLResponse(classify_result_page(
        job_id, total_hands, blue_count, red_count, pf_count,
        categories, allin_ev_diffs, ai_time_str, classified_path, json_path, hands,
    ))


@app.post("/generate_pdf/{job_id}")
async def generate_pdf(job_id: str, background_tasks: BackgroundTasks):
    with jobs_lock:
        src_job = jobs.get(job_id)
    if not src_job:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    classified_path = src_job.get("classified_path", "")
    if not classified_path or not Path(classified_path).exists():
        return HTMLResponse(ERROR_PAGE.format(log="分類データが見つかりません"), status_code=400)

    new_job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[new_job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "noapi"}
    background_tasks.add_task(run_pdf_pipeline, new_job_id, classified_path)
    return RedirectResponse(f"/progress/{new_job_id}", status_code=303)


@app.post("/start_ai/{job_id}")
async def start_ai(
    job_id: str,
    background_tasks: BackgroundTasks,
    api_key: str = Form(""),
):
    with jobs_lock:
        src_job = jobs.get(job_id)
    if not src_job:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    json_path = src_job.get("json_path", "")
    if not json_path or not Path(json_path).exists():
        return HTMLResponse(ERROR_PAGE.format(log="パース済みデータが見つかりません"), status_code=400)

    key = api_key.strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return HTMLResponse(ERROR_PAGE.format(log="Gemini APIキーが入力されていません。"), status_code=400)

    new_job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[new_job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "api"}
    background_tasks.add_task(run_ai_pipeline, new_job_id, json_path, key)
    return RedirectResponse(f"/progress/{new_job_id}", status_code=303)


@app.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            return HTMLResponse("<h1>404</h1>", status_code=404)
        mode = jobs[job_id].get("mode", "api")
    return progress_page(job_id, mode)

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


@app.get("/download-extension")
async def download_extension():
    """Chrome拡張機能をZIPにまとめてダウンロード"""
    import io
    import zipfile
    from fastapi.responses import StreamingResponse

    ext_dir = ROOT / "extension"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(ext_dir.rglob("*")):
            if fpath.is_file() and fpath.name != "README.md":
                zf.write(fpath, fpath.relative_to(ext_dir).as_posix())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=pokergto-extension.zip"},
    )


@app.post("/analyze/quick")
async def analyze_quick(file: UploadFile):
    """クイック解析: Gemini API不要、即座にダッシュボードを返す"""
    data = await file.read()
    txt_path = INPUT_DIR / "quick_upload.txt"
    txt_path.write_bytes(data)

    loop = asyncio.get_running_loop()

    def do_work():
        parsed = _parse_file(str(txt_path))
        return _quick_stats(parsed)

    try:
        result = await loop.run_in_executor(None, do_work)
    except Exception as e:
        return HTMLResponse(ERROR_PAGE.format(log=_esc(str(e))), status_code=500)

    if "error" in result:
        return HTMLResponse(ERROR_PAGE.format(log=_esc(result["error"])), status_code=400)

    job_id = uuid.uuid4().hex
    quick_results[job_id] = result
    return RedirectResponse(f"/dashboard/{job_id}", status_code=303)


@app.get("/dashboard/{job_id}", response_class=HTMLResponse)
async def dashboard(job_id: str):
    result = quick_results.get(job_id)
    if not result:
        return HTMLResponse("<h1>404: セッションが見つかりません。再度アップロードしてください。</h1>", status_code=404)
    return dashboard_page(result)


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
<title>ポーカーGTO</title>
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
  .card {{ padding: 28px 16px; }}
  .modal-body {{ padding: 16px 16px 24px; }}
  .modal-header {{ padding: 20px 16px 0; }}
  .step-indicator {{ padding: 16px 16px 0; }}
  .mode-btns {{ flex-direction: column; gap: 8px; }}
  .btn-mode {{ padding: 12px 10px; }}
  .dropzone {{ min-height: 120px; padding: 24px 16px; }}
  .btn-primary {{ padding: 16px; min-height: 44px; font-size: 15px; }}
}}
</style>
</head>
<body>
<div class="card">
  <h1>&#x1F0A1; ポーカーGTO</h1>
  <p class="sub">TenFourのハンド履歴をアップロードして分析</p>
  <form id="form" method="post" enctype="multipart/form-data" action="/upload">
    <div class="dropzone" id="drop">
      <input type="file" name="file" id="file" accept=".txt" required>
      <div class="dropzone-icon">&#x1F4C4;</div>
      <div class="dropzone-label"><span>ファイルを選択</span>またはドロップ</div>
      <div class="file-name" id="fname"></div>
    </div>
    <button type="submit" id="btn" class="btn-primary" disabled>ファイルを選択してください</button>
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

function onFileSelected() {{
  const name = file.files[0]?.name || '';
  fname.textContent = name;
  if (name) {{
    btn.disabled = false;
    btn.textContent = '&#x1F4CA; 解析を開始';
  }} else {{
    btn.disabled = true;
    btn.textContent = 'ファイルを選択してください';
  }}
}}

file.addEventListener('change', onFileSelected);
drop.addEventListener('dragover', e => {{ e.preventDefault(); drop.classList.add('dragover'); }});
drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
drop.addEventListener('drop', e => {{
  e.preventDefault(); drop.classList.remove('dragover');
  if (e.dataTransfer.files[0]) {{
    const dt = new DataTransfer();
    dt.items.add(e.dataTransfer.files[0]);
    file.files = dt.files;
    onFileSelected();
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


def progress_page(job_id: str, mode: str = "api") -> str:
    label2 = "青線/赤線を分類" if mode == "noapi" else "GTO分析（Gemini API）"
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>処理中... - ポーカーGTO</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e; color: #eee;
  min-height: 100vh; display: flex;
  align-items: center; justify-content: center;
  padding: 20px;
}}
.card {{
  background: #16213e; border-radius: 16px;
  padding: 48px 56px; width: 100%; max-width: 500px;
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
@media (max-width: 540px) {{
  .card {{ padding: 28px 16px; }}
}}
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
        <span class="step-label" id="label2">{label2}</span>
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


def classify_progress_page(job_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>解析中... - ポーカーGTO</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e; color: #eee;
  min-height: 100vh; display: flex;
  align-items: center; justify-content: center;
  padding: 20px;
}}
.card {{
  background: #16213e; border-radius: 16px;
  padding: 48px 56px; width: 100%; max-width: 500px;
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
.spinner {{
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid #444; border-top-color: #e94560;
  border-radius: 50%; animation: spin 0.8s linear infinite;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
@media (max-width: 540px) {{ .card {{ padding: 28px 16px; }} }}
</style>
</head>
<body>
<div class="card">
  <h1>&#x1F0A1; 解析中...</h1>
  <p class="status-msg" id="msg">処理を開始しています...</p>
  <div class="progress-bar-wrap"><div class="progress-bar" id="pbar"></div></div>
  <p class="elapsed" id="elapsed"></p>
  <div class="steps">
    <div class="step active" id="step1">
      <span class="step-icon" id="icon1"><span class="spinner"></span></span>
      <div class="step-body"><span id="label1">ハンド履歴をパース中...</span></div>
    </div>
    <div class="step" id="step2">
      <span class="step-icon" id="icon2">2</span>
      <div class="step-body"><span id="label2">青線/赤線を分類</span></div>
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

function setPbar(pct) {{ document.getElementById('pbar').style.width = pct + '%'; }}
function markDone(n) {{
  document.getElementById('step' + n).className = 'step done';
  document.getElementById('icon' + n).textContent = '✓';
}}
function markActive(n) {{
  document.getElementById('step' + n).className = 'step active';
  document.getElementById('icon' + n).innerHTML = '<span class="spinner"></span>';
}}

const es = new EventSource('/stream/' + JOB_ID);
const fallbackTimer = setTimeout(() => {{
  if (!sseActive) {{ es.close(); pollFallback(); }}
}}, 10000);

es.onmessage = function(e) {{
  sseActive = true;
  clearTimeout(fallbackTimer);
  const d = JSON.parse(e.data);

  if (d.type === 'parse_done') {{
    markDone(1);
    document.getElementById('label1').textContent =
      `ハンド履歴を解析しました（${{d.hands_total}}ハンド検出）`;
    markActive(2);
    setPbar(50);
    document.getElementById('msg').textContent = '青線/赤線を分類中...';

  }} else if (d.type === 'classify_done' || d.type === 'done') {{
    markDone(1); markDone(2);
    setPbar(100);
    document.getElementById('msg').textContent = '完了！結果を表示します...';
    es.close();
    setTimeout(() => {{ window.location.href = '/classify_result/' + JOB_ID; }}, 500);

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

async function pollFallback() {{
  if (sseActive) return;
  try {{
    const res  = await fetch('/status/' + JOB_ID);
    const data = await res.json();
    if (data.status === 'done') {{
      window.location.href = '/classify_result/' + JOB_ID;
      return;
    }}
    if (data.status === 'error') {{
      window.location.href = '/error/' + JOB_ID;
      return;
    }}
    const s = data.step || 0;
    setPbar(s >= 2 ? 50 : 10);
    if (s >= 1) markDone(1);
    if (s >= 2) markActive(2);
  }} catch(_) {{}}
  if (!sseActive) setTimeout(pollFallback, 2000);
}}
</script>
</body>
</html>"""


def classify_result_page(
    job_id: str,
    total_hands: int,
    blue_count: int,
    red_count: int,
    pf_count: int,
    categories: dict,
    allin_ev_diffs: dict,
    ai_time_str: str,
    classified_path: str,
    json_path: str,
    hands: list = None,
) -> str:
    import re as _re
    _DEFAULT_KEY = os.environ.get("GEMINI_API_KEY", "")

    # カテゴリ行HTML
    cat_rows = ""
    cat_colors = {
        "バリュー/ブラフ成功": "#4caf93",
        "ブラフキャッチ": "#7ec8e3",
        "アグレッション勝利": "#4caf93",
        "ブラフ失敗": "#e94560",
        "コール負け": "#e94560",
        "バッドフォールド": "#e94560",
        "ナイスフォールド": "#aaa",
        "フォールド(要確認)": "#888",
        "プリフロップのみ": "#555",
    }
    for label, count in sorted(categories.items(), key=lambda x: -x[1]):
        color = cat_colors.get(label, "#aaa")
        cat_rows += f'<div class="cat-row"><span class="cat-label" style="color:{color}">{_esc(label)}</span><span class="cat-count">{count}</span></div>\n'

    # オールインEV差HTML（Heroのみ表示）
    ev_html = ""
    if allin_ev_diffs:
        player, diff = next(iter(allin_ev_diffs.items()))
        # オールイン発生ハンド数をhands引数から算出
        ev_count = sum(
            1 for h in (hands or [])
            for p in h.get("players", [])
            if p.get("is_hero") and h.get("result", {}).get("allin_ev", {})
        )
        sign = "+" if diff >= 0 else ""
        if diff > 0:
            ev_color = "#e94560"
            ev_verdict = "運が悪かった（EV より実収支が悪い）"
            ev_detail = f"Heroはオールインで期待値通りなら {sign}{diff:.2f}bb 多く取れていた"
        else:
            ev_color = "#4caf93"
            ev_verdict = "運が良かった（EV より実収支が良い）"
            ev_detail = f"Heroはオールインで期待値より {abs(diff):.2f}bb 多く得た"
        ev_html = f"""
  <div class="section" style="padding:10px 16px">
    <div style="display:flex;align-items:center;gap:14px">
      <span style="font-size:11px;color:#888">&#x1F3B2; All-in EV差</span>
      <span style="font-size:22px;font-weight:bold;color:{ev_color}">{sign}{diff:.2f}bb</span>
      <span style="font-size:12px;color:{ev_color};font-weight:bold">{_esc(ev_verdict)}</span>
      <span style="font-size:11px;color:#666">{_esc(ev_detail)}（{ev_count}手）</span>
    </div>
  </div>"""

    # ─── 青線/赤線 ハンド一覧 ──────────────────────────────────────────────
    # ダーク背景向けスートカラー（♠黒→グレー、♣濃緑→明緑）
    _SUIT_COLORS = {'♠': '#c0c0c0', '♥': '#ff6b6b', '♦': '#5ba8ff', '♣': '#55cc55'}
    def _card_html(s):
        if not s: return ""
        def _r(m):
            c = _SUIT_COLORS.get(m.group(2), '#000')
            return f'{_esc(m.group(1))}<span style="color:{c}">{m.group(2)}</span>'
        return _re.sub(r'([23456789TJQKA]{1,2})([\u2660\u2665\u2666\u2663])', _r, str(s))

    def _fmt_bb(val):
        try:
            n = float(val)
            if n > 0: return f"+{n:.2f}"
            if n < 0: return f"{n:.2f}"
            return "0"
        except Exception: return "—"

    def _opp_cards(hand):
        others = [p for p in hand.get("players", []) if not p.get("is_hero")]
        if not others: return ""
        winners = [w.get("name") for w in hand.get("result", {}).get("winners", [])]
        opp = next((p for p in others if p.get("name") in winners), others[0])
        return "".join(opp.get("hole_cards", []))

    def _board_at(hand, last_st):
        order = ["flop", "turn", "river"]
        if last_st == "preflop": return ""
        idx = order.index(last_st) if last_st in order else len(order) - 1
        cards = []
        for s in order[:idx + 1]:
            board = hand.get("streets", {}).get(s, {}).get("board", [])
            cards.extend(c for c in board if c and c != "-")
        return " ".join(cards)

    _ST_JP = {"preflop": "PF", "flop": "F", "turn": "T", "river": "R"}
    _BLUE_ORDER = ["value_or_bluff_success", "bluff_catch", "bluff_failed", "call_lost"]
    _RED_ORDER  = ["hero_aggression_won", "bad_fold", "nice_fold", "fold_unknown"]

    # ダーク背景向けカテゴリバッジ色（暗めのbg + 明るいfg）
    _CAT_COLORS = {
        "value_or_bluff_success": ("#1a3d2a", "#6dd49a"),
        "bluff_catch":            ("#1a2d40", "#7ec8e3"),
        "bluff_failed":           ("#3d1a22", "#f47b8a"),
        "call_lost":              ("#3d1a22", "#f47b8a"),
        "hero_aggression_won":    ("#1a3d2a", "#6dd49a"),
        "bad_fold":               ("#3d1a22", "#f47b8a"),
        "nice_fold":              ("#1e3020", "#a0cfa0"),
        "fold_unknown":           ("#2e2a14", "#c8a840"),
    }

    def _fmt_actions(actions):
        """アクションリストを '→' 区切りの HTML 文字列に変換（ポジション名を使用）"""
        parts = []
        for a in actions:
            pos = a.get("position") or a.get("name", "?")
            act = a.get("action", "")
            amt = a.get("amount_bb")
            amt_s = f"&nbsp;{amt}bb" if amt else ""
            if act == "Fold":
                parts.append(f'<span style="color:#555">{pos}&nbsp;F</span>')
            elif act == "Check":
                parts.append(f'<span style="color:#888">{pos}&nbsp;X</span>')
            elif act == "Call":
                parts.append(f'<span style="color:#5ba8ff">{pos}&nbsp;Call{amt_s}</span>')
            elif act in ("Bet", "Raise"):
                parts.append(f'<span style="color:#c8a030;font-weight:bold">{pos}&nbsp;{act}{amt_s}</span>')
            elif act:
                parts.append(f'<span style="color:#aaa">{pos}&nbsp;{act}</span>')
        sep = ' <span style="color:#333">›</span> '
        return sep.join(parts)

    def _build_hand_card(h):
        """1ハンドの詳細カードHTMLを返す"""
        clf = h.get("bluered_classification", {})
        hero_cards = "".join(h.get("hero_cards", []))
        hero_pos   = h.get("hero_position", "?")
        is_3bet    = h.get("is_3bet_pot", False)
        pl         = float(h.get("hero_result_bb", 0))
        pl_c       = "#4caf93" if pl > 0 else "#e94560" if pl < 0 else "#888"
        needs_api  = clf.get("needs_api", False)

        badge_3bet = '<span style="background:#2a1a40;color:#b08aff;font-size:9px;padding:1px 5px;border-radius:3px;font-weight:bold">3BET</span> ' if is_3bet else ""
        api_mark   = '<span style="color:#c8a840;font-size:9px">★</span> ' if needs_api else ""
        card_bg    = "#1c180a" if needs_api else "#0f1828"

        # 相手プレイヤーのカード（ポジション付き）
        opp_parts = []
        for p in h.get("players", []):
            if not p.get("is_hero"):
                cards = "".join(p.get("hole_cards", []))
                pos   = p.get("position", "?")
                if cards:
                    opp_parts.append(f'<span style="color:#888;font-size:10px">{pos}</span>&nbsp;{_card_html(cards)}')
                else:
                    opp_parts.append(f'<span style="color:#555;font-size:10px">{pos}</span>')
        opp_html = "  ".join(opp_parts) if opp_parts else '<span style="color:#444">—</span>'

        hero_c_html = _card_html(hero_cards) if hero_cards else '<span style="color:#555">—</span>'

        # ストリート別アクション
        streets = h.get("streets", {})
        st_lines = []

        pf = streets.get("preflop", [])
        if pf:
            acts = _fmt_actions(pf)
            if acts:
                st_lines.append(f'<span style="color:#555;font-size:10px">PF</span>&nbsp;{acts}')

        for st_key, st_lbl in [("flop","F"), ("turn","T"), ("river","R")]:
            s = streets.get(st_key)
            if not s or not isinstance(s, dict): continue
            board_cards = [c for c in s.get("board", []) if c and c != "-"]
            pot         = s.get("pot_bb", 0)
            actions     = s.get("actions", [])
            board_part  = f'<span style="font-size:11px">{_card_html(" ".join(board_cards))}</span>' if board_cards else ""
            pot_part    = f'<span style="color:#444;font-size:10px">({pot}bb)</span>'
            acts        = _fmt_actions(actions)
            line = f'<span style="color:#555;font-size:10px">{st_lbl}</span>&nbsp;{board_part}&nbsp;{pot_part}'
            if acts:
                line += f'&nbsp;&nbsp;{acts}'
            st_lines.append(line)

        streets_html = "<br>".join(st_lines) if st_lines else ""

        return (
            f'<div style="background:{card_bg};border-radius:5px;padding:8px 10px;'
            f'margin-bottom:5px;border-left:2px solid #1e2535">'
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;flex-wrap:wrap">'
            f'<span style="color:#666;font-size:10px">{api_mark}H{h.get("hand_number","")}</span>'
            f'{badge_3bet}'
            f'<span style="font-size:11px;font-weight:bold;color:#ddd">{hero_pos}</span>'
            f'<span style="color:#666;font-size:10px">(Hero)</span>'
            f'<span style="font-size:12px">{hero_c_html}</span>'
            f'<span style="color:#444;font-size:10px">vs</span>'
            f'<span style="font-size:11px">{opp_html}</span>'
            f'<span style="margin-left:auto;color:{pl_c};font-weight:bold;font-size:12px">{_fmt_bb(pl)}bb</span>'
            f'</div>'
            f'<div style="font-size:11px;color:#aaa;line-height:1.9">{streets_html}</div>'
            f'</div>'
        )

    def _build_hand_section(filtered_hands, cat_order):
        html = ""
        for cat in cat_order:
            cat_hands = [h for h in filtered_hands
                         if h.get("bluered_classification", {}).get("category") == cat]
            # 3BETポット優先、次にラストストリート順、次にハンド番号順
            cat_hands.sort(key=lambda h: (
                0 if h.get("is_3bet_pot") else 1,
                ["preflop","flop","turn","river"].index(
                    h.get("bluered_classification", {}).get("last_street", "preflop")
                ),
                h.get("hand_number", 0)
            ))
            if not cat_hands: continue
            cat_label = cat_hands[0].get("bluered_classification", {}).get("category_label", cat)
            cat_pl    = sum(float(h.get("hero_result_bb", 0)) for h in cat_hands)
            pl_color  = "#4caf93" if cat_pl > 0 else "#e94560" if cat_pl < 0 else "#888"
            bg, fg    = _CAT_COLORS.get(cat, ("#222", "#aaa"))
            needs_api_cnt = sum(1 for h in cat_hands if h.get("bluered_classification", {}).get("needs_api"))
            api_badge = f' <span style="color:#c8a030;font-size:10px">★要AI {needs_api_cnt}手</span>' if needs_api_cnt else ""

            html += (
                f'<div style="background:{bg};border-radius:4px;padding:5px 10px;'
                f'margin:10px 0 5px;display:flex;align-items:center;gap:10px">'
                f'<span style="color:{fg};font-size:11px;font-weight:bold">{_esc(cat_label)}</span>'
                f'<span style="color:#666;font-size:10px">{len(cat_hands)}手</span>'
                f'<span style="margin-left:auto;color:{pl_color};font-size:11px;font-weight:bold">{_fmt_bb(cat_pl)}bb</span>'
                f'{api_badge}</div>\n'
            )
            for h in cat_hands:
                html += _build_hand_card(h)
        return html

    hands_html = ""
    if hands:
        blue_hands = [h for h in hands if h.get("bluered_classification", {}).get("line") == "blue"]
        red_hands  = [h for h in hands if h.get("bluered_classification", {}).get("line") == "red"]
        blue_pl    = sum(float(h.get("hero_result_bb", 0)) for h in blue_hands)
        red_pl     = sum(float(h.get("hero_result_bb", 0)) for h in red_hands)
        blue_pl_c  = "#4caf93" if blue_pl > 0 else "#e94560" if blue_pl < 0 else "#888"
        red_pl_c   = "#4caf93" if red_pl  > 0 else "#e94560" if red_pl  < 0 else "#888"
        blue_section = _build_hand_section(blue_hands, _BLUE_ORDER)
        red_section  = _build_hand_section(red_hands,  _RED_ORDER)
        hands_html = f"""
  <div class="section" id="hand-list-section">
    <div class="section-title">&#x1F4CB; 青線 / 赤線 ハンド一覧
      <button onclick="var b=document.getElementById('hand-list-body');b.style.display=b.style.display==='none'?'block':'none'" style="float:right;padding:2px 10px;background:transparent;color:#e94560;border:1px solid #e94560;border-radius:4px;cursor:pointer;font-size:11px">折りたたむ</button>
    </div>
    <div id="hand-list-body">
      <div style="font-size:12px;color:#7ec8e3;font-weight:bold;margin-bottom:4px;padding-top:4px">
        &#x1F535; 青線（ショーダウン）&nbsp; {len(blue_hands)}手 &nbsp;
        <span style="color:{blue_pl_c}">{_fmt_bb(blue_pl)}bb</span>
      </div>
      {blue_section or '<div style="color:#555;font-size:12px;padding:8px">該当なし</div>'}
      <div style="border-top:1px solid #1e2535;margin:16px 0 8px"></div>
      <div style="font-size:12px;color:#e94560;font-weight:bold;margin-bottom:4px">
        &#x1F534; 赤線（ノーショーダウン）&nbsp; {len(red_hands)}手 &nbsp;
        <span style="color:{red_pl_c}">{_fmt_bb(red_pl)}bb</span>
      </div>
      {red_section or '<div style="color:#555;font-size:12px;padding:8px">該当なし</div>'}
    </div>
  </div>"""

    # APIキーのデフォルト値
    key_val = _DEFAULT_KEY
    key_placeholder = "AIza... (Gemini APIキーを入力)"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>解析結果 - ポーカーGTO</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e; color: #eee;
  min-height: 100vh; padding: 20px;
}}
.topbar {{
  background: #16213e; padding: 12px 24px;
  display: flex; align-items: center; gap: 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,.5);
  position: sticky; top: 0; z-index: 100;
  margin: -20px -20px 24px;
}}
.topbar h1 {{ font-size: 16px; color: #e94560; flex: 1; }}
.btn-back {{
  padding: 8px 16px; background: transparent; color: #aaa;
  border: 1px solid #444; border-radius: 6px; font-size: 13px;
  text-decoration: none; transition: border-color .2s, color .2s;
}}
.btn-back:hover {{ border-color: #e94560; color: #e94560; }}
.container {{ max-width: 1100px; margin: 0 auto; }}
/* サマリーストリップ */
.summary {{ display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }}
.stat-card {{ background: #16213e; border-radius: 8px; padding: 8px 16px;
  display: flex; align-items: center; gap: 10px; }}
.stat-label {{ font-size: 11px; color: #888; white-space: nowrap; }}
.stat-value {{ font-size: 20px; font-weight: bold; }}
/* セクション */
.section {{ background: #16213e; border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }}
.section-title {{ font-size: 13px; font-weight: bold; color: #e94560; margin-bottom: 8px; }}
/* カテゴリ */
.cat-row {{ display: flex; align-items: center; justify-content: space-between;
  padding: 4px 0; border-bottom: 1px solid #1e2535; }}
.cat-row:last-child {{ border-bottom: none; }}
.cat-label {{ font-size: 12px; }}
.cat-count {{ font-size: 13px; font-weight: bold; color: #eee; min-width: 28px; text-align: right; }}
/* アクションエリア */
.actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
.action-card {{ background: #16213e; border-radius: 12px; padding: 24px; text-align: center; }}
.action-icon {{ font-size: 32px; margin-bottom: 10px; }}
.action-title {{ font-size: 15px; font-weight: bold; margin-bottom: 6px; }}
.action-desc {{ font-size: 12px; color: #888; margin-bottom: 16px; line-height: 1.6; }}
.action-time {{ font-size: 12px; color: #e94560; margin-bottom: 16px; font-weight: bold; }}
.btn-primary {{
  width: 100%; padding: 12px; background: #e94560;
  color: #fff; border: none; border-radius: 8px;
  font-size: 14px; font-weight: bold; cursor: pointer;
  transition: background .2s;
}}
.btn-primary:hover {{ background: #c73652; }}
.btn-primary:disabled {{ background: #555; cursor: not-allowed; }}
.btn-secondary {{
  width: 100%; padding: 12px; background: rgba(233,69,96,.1);
  color: #e94560; border: 1px solid #e94560; border-radius: 8px;
  font-size: 14px; font-weight: bold; cursor: pointer;
  transition: background .2s;
}}
.btn-secondary:hover {{ background: rgba(233,69,96,.2); }}
/* AIパネル */
#ai-panel {{ display: none; margin-top: 14px; text-align: left; }}
#ai-panel.show {{ display: block; }}
.field-group {{ margin-bottom: 12px; }}
.field-group label {{ font-size: 11px; color: #888; display: block; margin-bottom: 5px; }}
.field-group input[type=password] {{
  width: 100%; padding: 9px 11px;
  background: #0f0f1a; border: 1px solid #333;
  border-radius: 6px; color: #eee; font-size: 13px;
  outline: none; transition: border-color .2s;
}}
.field-group input:focus {{ border-color: #e94560; }}
.key-hint {{ font-size: 11px; color: #555; margin-top: 4px; }}
.key-hint a {{ color: #e94560; text-decoration: none; }}
/* ハンド一覧テーブル */
.cat-hdr td {{ background: #1e2a3a; font-size: 11px; padding: 3px 6px; border-bottom: 1px solid #334; }}
#hand-list-section table td {{ padding: 3px 5px; border-bottom: 1px solid #1e2535; }}
#hand-list-section table tr:hover td {{ background: rgba(255,255,255,0.04); }}
@media (max-width: 800px) {{
  #hand-list-section > div > div {{ grid-template-columns: 1fr !important; }}
}}
@media (max-width: 540px) {{
  .summary {{ grid-template-columns: 1fr 1fr; }}
  .actions {{ grid-template-columns: 1fr; }}
  .stat-value {{ font-size: 22px; }}
}}
</style>
</head>
<body>
<div class="topbar">
  <h1>&#x1F0A1; ポーカーGTO — 解析結果</h1>
  <a class="btn-back" href="/">&#x2190; 戻る</a>
</div>

<div class="container">

  <!-- サマリー（1行ストリップ） -->
  <div class="summary">
    <div class="stat-card">
      <div class="stat-label">総ハンド数</div>
      <div class="stat-value" style="color:#7ec8e3">{total_hands}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">🔵 青線</div>
      <div class="stat-value" style="color:#7ec8e3">{blue_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">🔴 赤線</div>
      <div class="stat-value" style="color:#e94560">{red_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">PFのみ</div>
      <div class="stat-value" style="color:#555">{pf_count}</div>
    </div>
  </div>

  <!-- カテゴリ内訳 -->
  <div class="section">
    <div class="section-title">&#x1F4CA; ハンド分類内訳</div>
    {cat_rows}
  </div>

  {ev_html}

  {hands_html}

  <!-- アクション選択 -->
  <div class="actions">

    <!-- PDF生成 -->
    <div class="action-card">
      <div class="action-icon">&#x1F4C4;</div>
      <div class="action-title">PDFレポート生成</div>
      <div class="action-desc">APIなし・無料<br>分類結果をPDFに出力</div>
      <form method="post" action="/generate_pdf/{job_id}" target="_blank">
        <button type="submit" class="btn-primary">&#x1F4CA; PDFを生成</button>
      </form>
    </div>

    <!-- AI分析 -->
    <div class="action-card">
      <div class="action-icon">&#x1F916;</div>
      <div class="action-title">AI分析 (Gemini)</div>
      <div class="action-desc">Gemini APIを使用<br>GTO評価付きPDFを生成</div>
      <div class="action-time">推定時間: {ai_time_str}</div>
      <button type="button" class="btn-secondary" onclick="toggleAI()">&#x1F916; AI分析を開始</button>
      <div id="ai-panel">
        <form method="post" action="/start_ai/{job_id}" id="ai-form" target="_blank">
          <div class="field-group">
            <label>Gemini API キー</label>
            <input type="password" name="api_key" id="ai-key"
                   placeholder="{key_placeholder}"
                   value="{key_val}"
                   autocomplete="off">
            <p class="key-hint">
              取得: <a href="https://aistudio.google.com/app/apikey" target="_blank">Google AI Studio</a>
            </p>
          </div>
          <button type="submit" id="ai-submit" class="btn-primary">分析を開始</button>
        </form>
      </div>
    </div>

  </div>
</div>

<script>
function toggleAI() {{
  const panel = document.getElementById('ai-panel');
  panel.classList.toggle('show');
}}
document.getElementById('ai-form').addEventListener('submit', function() {{
  document.getElementById('ai-submit').disabled = true;
  document.getElementById('ai-submit').textContent = '送信中...';
}});
</script>
</body>
</html>"""


def report_page(pdf_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
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
  gap: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  flex-shrink: 0;
  flex-wrap: wrap;
}}
.toolbar h1 {{ font-size: 15px; color: #e94560; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.btn-download {{
  padding: 10px 20px; min-height: 44px;
  background: #e94560;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: bold;
  text-decoration: none;
  display: inline-flex; align-items: center;
  transition: background 0.2s;
}}
.btn-download:hover {{ background: #c73652; }}
.btn-back {{
  padding: 10px 16px; min-height: 44px;
  background: transparent;
  color: #aaa;
  border: 1px solid #444;
  border-radius: 6px;
  font-size: 14px;
  text-decoration: none;
  display: inline-flex; align-items: center;
  transition: border-color 0.2s, color 0.2s;
}}
.btn-back:hover {{ border-color: #e94560; color: #e94560; }}
iframe {{ flex: 1; width: 100%; border: none; }}
.mobile-hint {{
  display: none;
  flex: 1;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 16px;
  padding: 40px 20px;
  text-align: center;
}}
.mobile-hint p {{ color: #888; font-size: 14px; margin-bottom: 8px; }}
@media (max-width: 600px) {{
  iframe {{ display: none; }}
  .mobile-hint {{ display: flex; }}
  .toolbar h1 {{ font-size: 13px; }}
}}
</style>
</head>
<body>
<div class="toolbar">
  <h1>&#x1F0A1; GTO レポート — {pdf_name}</h1>
  <a class="btn-back" href="/">&#x2190; 戻る</a>
  <a class="btn-download" href="/download/{pdf_name}" download="{pdf_name}">&#x2B07; ダウンロード</a>
</div>
<iframe src="/pdf/{pdf_name}" type="application/pdf"></iframe>
<div class="mobile-hint">
  <p>スマートフォンではPDFビューアを直接表示できません。<br>下のボタンからダウンロードしてご確認ください。</p>
  <a class="btn-download" href="/download/{pdf_name}" download="{pdf_name}">&#x2B07; PDFをダウンロード</a>
</div>
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
<pre>{log}</pre>
<p><a href="/">&#x2190; 戻る</a></p>
</body></html>"""


# ─── クイック解析ダッシュボード ───────────────────────────────────────────────

def dashboard_page(result: dict) -> str:
    import json as _json
    data_json = _json.dumps(result, ensure_ascii=False)
    hero = result.get("hero_name", "Hero")
    summary = result.get("summary", {})
    total_hands = summary.get("total_hands", 0)
    total_bb    = summary.get("total_bb", 0)
    bb_per_100  = summary.get("bb_per_100", 0)
    bb_color    = "#4caf93" if total_bb >= 0 else "#e94560"
    bb100_color = "#4caf93" if bb_per_100 >= 0 else "#e94560"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>クイック解析 — ポーカーGTO</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Meiryo',sans-serif;background:#1a1a2e;color:#eee;min-height:100vh}}
.topbar{{background:#16213e;padding:12px 24px;display:flex;align-items:center;gap:16px;
  box-shadow:0 2px 8px rgba(0,0,0,.5);position:sticky;top:0;z-index:100}}
.topbar h1{{font-size:16px;color:#e94560;flex:1}}
.btn-pdf{{padding:8px 20px;background:#e94560;color:#fff;border:none;border-radius:6px;
  font-size:13px;font-weight:bold;cursor:pointer;transition:background .2s}}
.btn-pdf:hover{{background:#c73652}}
.btn-back{{padding:8px 16px;background:transparent;color:#aaa;border:1px solid #444;
  border-radius:6px;font-size:13px;text-decoration:none;transition:border-color .2s,color .2s}}
.btn-back:hover{{border-color:#e94560;color:#e94560}}
.container{{max-width:1200px;margin:0 auto;padding:24px 20px}}
/* サマリー */
.summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}}
.stat-card{{background:#16213e;border-radius:12px;padding:20px 24px;text-align:center}}
.stat-label{{font-size:12px;color:#888;margin-bottom:8px}}
.stat-value{{font-size:32px;font-weight:bold}}
.stat-sub{{font-size:11px;color:#555;margin-top:4px}}
/* グリッド */
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}}
.grid-1{{margin-bottom:20px}}
.card{{background:#16213e;border-radius:12px;padding:20px 24px}}
.card-title{{font-size:14px;font-weight:bold;color:#e94560;margin-bottom:16px;
  display:flex;align-items:center;gap:8px}}
.chart-wrap{{position:relative;height:260px}}
/* EV計算機 */
.ev-grid{{display:flex;flex-direction:column;gap:16px}}
.ev-row{{display:flex;flex-direction:column;gap:6px}}
.ev-row label{{font-size:12px;color:#aaa}}
.ev-slider-row{{display:flex;align-items:center;gap:12px}}
.ev-slider-row input[type=range]{{flex:1;accent-color:#e94560}}
.ev-slider-val{{font-size:14px;font-weight:bold;color:#eee;width:52px;text-align:right}}
.ev-results{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:8px}}
.ev-stat{{background:#0f0f1a;border-radius:8px;padding:12px;text-align:center}}
.ev-stat span{{display:block;font-size:11px;color:#888;margin-bottom:6px}}
.ev-stat strong{{font-size:18px;font-weight:bold}}
/* ヒートマップ */
.heatmap-wrap{{overflow-x:auto}}
.heatmap{{display:grid;grid-template-columns:repeat(13,1fr);gap:2px;min-width:520px}}
.hm-cell{{
  aspect-ratio:1;border-radius:3px;display:flex;align-items:center;
  justify-content:center;font-size:9px;font-weight:bold;cursor:pointer;
  transition:transform .15s;position:relative
}}
.hm-cell:hover{{transform:scale(1.15);z-index:10}}
.hm-label{{display:grid;grid-template-columns:repeat(13,1fr);gap:2px;
  min-width:520px;margin-bottom:4px}}
.hm-lbl{{text-align:center;font-size:10px;color:#555}}
/* ツールチップ */
.tooltip{{
  display:none;position:fixed;background:#16213e;border:1px solid #333;
  border-radius:8px;padding:10px 14px;font-size:12px;z-index:999;
  pointer-events:none;line-height:1.7;min-width:150px
}}
.tooltip.show{{display:block}}
/* レスポンシブ */
@media(max-width:700px){{
  .summary{{grid-template-columns:1fr 1fr}}
  .grid-2{{grid-template-columns:1fr}}
  .ev-results{{grid-template-columns:1fr}}
  .stat-value{{font-size:24px}}
}}
@media(max-width:400px){{.summary{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="topbar">
  <h1>&#x1F0A1; クイック解析 — {_esc(hero)}</h1>
  <a class="btn-back" href="/">&#x2190; 戻る</a>
  <button class="btn-pdf" onclick="exportPDF()">&#x1F4C4; PDFとして保存</button>
</div>

<div class="container" id="dashboard">

  <!-- サマリー -->
  <div class="summary">
    <div class="stat-card">
      <div class="stat-label">総ハンド数</div>
      <div class="stat-value" style="color:#7ec8e3">{total_hands}</div>
      <div class="stat-sub">hands</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">総収支</div>
      <div class="stat-value" style="color:{bb_color}">{'+' if total_bb >= 0 else ''}{total_bb}</div>
      <div class="stat-sub">bb</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">bb/100</div>
      <div class="stat-value" style="color:{bb100_color}">{'+' if bb_per_100 >= 0 else ''}{bb_per_100}</div>
      <div class="stat-sub">bb per 100 hands</div>
    </div>
  </div>

  <!-- 1. タイムライン -->
  <div class="grid-1 card">
    <div class="card-title">&#x1F4C8; セッション損益推移</div>
    <div class="chart-wrap"><canvas id="chartTimeline"></canvas></div>
  </div>

  <!-- 2. ストリート / 3. ベットサイジング -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title">&#x1F3AF; ストリート別決着率</div>
      <div class="chart-wrap"><canvas id="chartStreets"></canvas></div>
    </div>
    <div class="card">
      <div class="card-title">&#x1F4B0; ベットサイジング分析</div>
      <div class="chart-wrap"><canvas id="chartBetSizing"></canvas></div>
    </div>
  </div>

  <!-- 4. 勝利パターン / 5. EV計算機 -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title">&#x1F3C6; 勝利パターン分析</div>
      <div class="chart-wrap"><canvas id="chartWinTypes"></canvas></div>
    </div>
    <div class="card">
      <div class="card-title">&#x1F9EE; EV計算機 <span style="font-size:11px;color:#555;font-weight:normal">(APIなし・即時計算)</span></div>
      <div class="ev-grid">
        <div class="ev-row">
          <label>ポットサイズ (bb)</label>
          <div class="ev-slider-row">
            <input type="range" id="evPot" min="1" max="200" value="10">
            <span class="ev-slider-val" id="evPotVal">10</span>
          </div>
        </div>
        <div class="ev-row">
          <label>コール額 (bb)</label>
          <div class="ev-slider-row">
            <input type="range" id="evCall" min="1" max="100" value="5">
            <span class="ev-slider-val" id="evCallVal">5</span>
          </div>
        </div>
        <div class="ev-row">
          <label>相手のブラフ頻度 (%)</label>
          <div class="ev-slider-row">
            <input type="range" id="evBluff" min="0" max="100" value="30">
            <span class="ev-slider-val" id="evBluffVal">30%</span>
          </div>
        </div>
        <div class="ev-results">
          <div class="ev-stat"><span>ポットオッズ</span><strong id="evOdds">—</strong></div>
          <div class="ev-stat"><span>ブレークイーブン勝率</span><strong id="evBE">—</strong></div>
          <div class="ev-stat"><span>EV (コール)</span><strong id="evResult">—</strong></div>
        </div>
      </div>
    </div>
  </div>

  <!-- 6. コンボヒートマップ -->
  <div class="grid-1 card">
    <div class="card-title">&#x1F0CF; 169コンボ ヒートマップ
      <span style="font-size:11px;color:#555;font-weight:normal">
        上三角=スーテッド / 下三角=オフスート / 対角=ペア　|　5サンプル未満はグレー
      </span>
    </div>
    <div class="heatmap-wrap">
      <div class="hm-label" id="hmLabels"></div>
      <div class="heatmap"  id="heatmap"></div>
    </div>
  </div>

</div><!-- /container -->

<div class="tooltip" id="tooltip"></div>

<script>
const DATA = {data_json};
const RANKS = ['A','K','Q','J','T','9','8','7','6','5','4','3','2'];

// ─── Chart.js 共通設定 ─────────────────────────────────────────────────
Chart.defaults.color = '#888';
Chart.defaults.font.family = 'Meiryo, sans-serif';

// ─── 1. タイムライン ───────────────────────────────────────────────────
(function(){{
  const tl = DATA.timeline || [];
  const labels = tl.map(d => d.hand);
  const values = tl.map(d => d.cumulative);
  new Chart(document.getElementById('chartTimeline'), {{
    type: 'line',
    data: {{
      labels,
      datasets: [{{
        label: '累積収支 (bb)',
        data: values,
        borderColor: '#4caf93',
        backgroundColor: 'rgba(76,175,147,.12)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ mode: 'index', intersect: false }} }},
      scales: {{
        x: {{ grid: {{ color: '#222' }}, ticks: {{ maxTicksLimit: 10 }} }},
        y: {{ grid: {{ color: '#222' }},
          ticks: {{ callback: v => (v >= 0 ? '+' : '') + v + 'bb' }} }}
      }}
    }}
  }});
}})();

// ─── 2. ストリート別決着率 ────────────────────────────────────────────
(function(){{
  const sc = DATA.streets?.counts  || {{}};
  const sd = DATA.streets?.showdown || {{}};
  const labels = ['Preflop','Flop','Turn','River'];
  const keys   = ['preflop','flop','turn','river'];
  const noSD = keys.map(k => (sc[k] || 0) - (sd[k] || 0));
  const withSD = keys.map(k => sd[k] || 0);
  new Chart(document.getElementById('chartStreets'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{ label: 'ショーダウンなし', data: noSD,   backgroundColor: 'rgba(233,69,96,.7)'   }},
        {{ label: 'ショーダウンあり', data: withSD, backgroundColor: 'rgba(126,200,227,.7)' }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }},
      scales: {{
        x: {{ stacked: true, grid: {{ color: '#222' }} }},
        y: {{ stacked: true, grid: {{ color: '#222' }},
          ticks: {{ stepSize: 1 }} }}
      }}
    }}
  }});
}})();

// ─── 3. ベットサイジング ──────────────────────────────────────────────
(function(){{
  const bs = DATA.bet_sizing || [];
  const labels = bs.map(d => d.range);
  const wr  = bs.map(d => d.winrate != null ? Math.round(d.winrate * 100) : null);
  const cnt = bs.map(d => d.count);
  new Chart(document.getElementById('chartBetSizing'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{
          label: '勝率 (%)',
          data: wr,
          backgroundColor: wr.map(v => v == null ? '#333' : v >= 50 ? 'rgba(76,175,147,.8)' : 'rgba(233,69,96,.8)'),
          yAxisID: 'y',
        }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            afterBody: (items) => {{
              const i = items[0].dataIndex;
              const d = bs[i];
              const lines = [`サンプル: ${{d.count}}件`];
              if (d.avg_bb != null) lines.push(`平均収支: ${{d.avg_bb >= 0 ? '+' : ''}}${{d.avg_bb}}bb`);
              else lines.push('サンプル不足 (5件未満)');
              return lines;
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ grid: {{ color: '#222' }} }},
        y: {{ grid: {{ color: '#222' }}, min: 0, max: 100,
          ticks: {{ callback: v => v + '%' }} }}
      }}
    }}
  }});
}})();

// ─── 4. 勝利パターン ──────────────────────────────────────────────────
(function(){{
  const wt = DATA.win_types || {{}};
  const labels = ['バリュー', 'ブラフ', 'ブラフキャッチ', 'その他'];
  const keys   = ['value', 'bluff', 'bluff_catch', 'other'];
  const values = keys.map(k => wt[k] || 0);
  new Chart(document.getElementById('chartWinTypes'), {{
    type: 'doughnut',
    data: {{
      labels,
      datasets: [{{
        data: values,
        backgroundColor: ['rgba(76,175,147,.85)','rgba(233,69,96,.85)','rgba(126,200,227,.85)','rgba(100,100,120,.7)'],
        borderWidth: 0,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      cutout: '58%',
      plugins: {{
        legend: {{ position: 'right', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => ` ${{ctx.label}}: ${{ctx.raw}}件`
          }}
        }}
      }}
    }}
  }});
}})();

// ─── 5. EV計算機 ─────────────────────────────────────────────────────
function calcEV(){{
  const pot   = parseFloat(document.getElementById('evPot').value);
  const call  = parseFloat(document.getElementById('evCall').value);
  const bluff = parseFloat(document.getElementById('evBluff').value) / 100;
  document.getElementById('evPotVal').textContent   = pot;
  document.getElementById('evCallVal').textContent  = call;
  document.getElementById('evBluffVal').textContent = Math.round(bluff * 100) + '%';
  const potOdds = call / (pot + call);
  const ev = bluff * pot - (1 - bluff) * call;
  document.getElementById('evOdds').textContent   = (potOdds * 100).toFixed(1) + '%';
  document.getElementById('evBE').textContent     = (potOdds * 100).toFixed(1) + '%';
  const evEl = document.getElementById('evResult');
  evEl.textContent = (ev >= 0 ? '+' : '') + ev.toFixed(2) + 'bb';
  evEl.style.color = ev >= 0 ? '#4caf93' : '#e94560';
}}
['evPot','evCall','evBluff'].forEach(id =>
  document.getElementById(id).addEventListener('input', calcEV));
calcEV();

// ─── 6. ヒートマップ ──────────────────────────────────────────────────
(function(){{
  const combos = DATA.combos || {{}};
  const tooltip = document.getElementById('tooltip');

  function getKey(r, c){{
    if (r === c) return RANKS[r] + RANKS[r];
    if (r < c)   return RANKS[r] + RANKS[c] + 's';
    return RANKS[c] + RANKS[r] + 'o';
  }}

  function cellColor(bb, count){{
    if (!count || count < 5) return '#1e1e30';
    const cap = 20;
    if (bb > 0){{
      const t = Math.min(bb / cap, 1);
      return `rgba(76,175,147,${{(0.25 + t * 0.75).toFixed(2)}})`;
    }} else {{
      const t = Math.min(-bb / cap, 1);
      return `rgba(233,69,96,${{(0.25 + t * 0.75).toFixed(2)}})`;
    }}
  }}

  // ラベル行
  const lblWrap = document.getElementById('hmLabels');
  RANKS.forEach(r => {{
    const d = document.createElement('div');
    d.className = 'hm-lbl'; d.textContent = r;
    lblWrap.appendChild(d);
  }});

  // セルグリッド
  const wrap = document.getElementById('heatmap');
  for (let row = 0; row < 13; row++){{
    for (let col = 0; col < 13; col++){{
      const key = getKey(row, col);
      const d   = combos[key] || {{}};
      const count = d.count || 0;
      const bb    = d.bb || 0;
      const wr    = d.winrate || 0;

      const cell = document.createElement('div');
      cell.className = 'hm-cell';
      cell.style.background = cellColor(bb, count);
      cell.textContent = count >= 5 ? key : (count > 0 ? key : '');
      cell.style.color = count >= 5 ? '#fff' : '#444';
      cell.style.fontSize = key.length <= 2 ? '9px' : '8px';

      cell.addEventListener('mousemove', e => {{
        if (count === 0) {{ tooltip.classList.remove('show'); return; }}
        tooltip.innerHTML =
          `<b>${{key}}</b><br>` +
          `試行: ${{count}}回<br>` +
          (count >= 5
            ? `勝率: ${{(wr * 100).toFixed(1)}}%<br>収益: ${{bb >= 0 ? '+' : ''}}${{bb.toFixed(2)}}bb`
            : `<span style="color:#888">サンプル不足</span>`);
        tooltip.style.left = (e.clientX + 14) + 'px';
        tooltip.style.top  = (e.clientY - 10) + 'px';
        tooltip.classList.add('show');
      }});
      cell.addEventListener('mouseleave', () => tooltip.classList.remove('show'));

      wrap.appendChild(cell);
    }}
  }}
}})();

// ─── PDF出力 ──────────────────────────────────────────────────────────
async function exportPDF(){{
  const btn = document.querySelector('.btn-pdf');
  btn.textContent = '生成中...'; btn.disabled = true;
  try{{
    const canvas = await html2canvas(document.getElementById('dashboard'), {{
      backgroundColor: '#1a1a2e', scale: 1.5, useCORS: true,
    }});
    const {{ jsPDF }} = window.jspdf;
    const pdf = new jsPDF({{ orientation: 'p', unit: 'mm', format: 'a4' }});
    const W = pdf.internal.pageSize.getWidth();
    const H = pdf.internal.pageSize.getHeight();
    const imgW = W;
    const imgH = canvas.height * imgW / canvas.width;
    const img = canvas.toDataURL('image/png');
    let y = 0;
    while (y < imgH){{
      if (y > 0) pdf.addPage();
      pdf.addImage(img, 'PNG', 0, -y, imgW, imgH);
      y += H;
    }}
    pdf.save('poker_quick_report.pdf');
  }} catch(e){{
    alert('PDF生成に失敗しました: ' + e.message);
  }} finally{{
    btn.textContent = '&#x1F4C4; PDFとして保存'; btn.disabled = false;
  }}
}}
</script>
</body>
</html>"""


# ─── Firebase連携エンドポイント ───────────────────────────────────────────────
# 環境変数 FIREBASE_SERVICE_ACCOUNT_JSON が未設定の場合はこれらのエンドポイントは
# 503を返す（既存機能への影響なし）

def _get_uid_from_request(request: Request) -> str:
    """
    Authorization: Bearer {idToken} ヘッダーからuidを取得。
    失敗時は ValueError を送出。
    """
    from scripts.firebase_utils import verify_id_token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Authorization ヘッダーがありません")
    id_token = auth_header.removeprefix("Bearer ").strip()
    decoded = verify_id_token(id_token)
    return decoded["uid"]


@app.post("/api/upload-from-extension")
async def upload_from_extension(request: Request):
    """
    Chrome拡張機能からのハンドログ受信エンドポイント。
    Header: Authorization: Bearer {Firebase idToken}
    Body JSON: { raw_text: str, filename: str, hand_count: int }
    → Firestore users/{uid}/sessions/{id} に保存
    """
    from scripts.firebase_utils import is_firebase_enabled, save_session
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)

    try:
        uid = _get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSONパース失敗"}, status_code=400)

    raw_text   = body.get("raw_text", "").strip()
    filename   = body.get("filename", "upload.txt")
    hand_count = int(body.get("hand_count", 0))

    if not raw_text:
        return JSONResponse({"error": "raw_text が空です"}, status_code=400)

    try:
        session_id = save_session(uid, raw_text, filename, hand_count)
    except Exception as e:
        return JSONResponse({"error": f"Firestore保存失敗: {e}"}, status_code=500)

    return JSONResponse({"session_id": session_id, "status": "saved"})


@app.get("/api/sessions")
async def api_sessions(request: Request):
    """
    ログイン中ユーザーのセッション一覧を返す。
    Header: Authorization: Bearer {Firebase idToken}
    """
    from scripts.firebase_utils import is_firebase_enabled, get_sessions
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)

    try:
        uid = _get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)

    try:
        sessions = get_sessions(uid)
    except Exception as e:
        return JSONResponse({"error": f"Firestore取得失敗: {e}"}, status_code=500)

    return JSONResponse({"sessions": sessions})


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str, request: Request):
    """
    Firestoreからセッションを削除する。
    Header: Authorization: Bearer {Firebase idToken}
    """
    from scripts.firebase_utils import is_firebase_enabled, delete_session
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)

    try:
        uid = _get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)

    try:
        delete_session(uid, session_id)
    except Exception as e:
        return JSONResponse({"error": f"削除失敗: {e}"}, status_code=500)

    return JSONResponse({"status": "deleted"})


@app.post("/api/sessions/{session_id}/analyze")
async def api_analyze_session(session_id: str, request: Request, background_tasks: BackgroundTasks):
    """
    Firestoreのセッションデータを取得して classifyパイプラインを実行する。
    Header: Authorization: Bearer {Firebase idToken}
    → /classify_progress/{job_id} の URLを返す
    """
    from scripts.firebase_utils import is_firebase_enabled, get_session, update_session_status
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)

    try:
        uid = _get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)

    try:
        session = get_session(uid, session_id)
    except Exception as e:
        return JSONResponse({"error": f"Firestore取得失敗: {e}"}, status_code=500)

    if not session:
        return JSONResponse({"error": "セッションが見つかりません"}, status_code=404)

    raw_text = session.get("raw_text", "")
    if not raw_text:
        return JSONResponse({"error": "raw_text が空です"}, status_code=400)

    # txtファイルとして書き出して既存パイプラインに渡す
    txt_path = INPUT_DIR / f"fb_{session_id}.txt"
    txt_path.write_text(raw_text, encoding="utf-8")

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "step": 0, "status": "running", "pdf": "", "log": "",
            "mode": "classify",
            "firebase_uid": uid,
            "firebase_session_id": session_id,
        }

    try:
        update_session_status(uid, session_id, "analyzing")
    except Exception:
        pass  # ステータス更新失敗は致命的ではない

    background_tasks.add_task(run_classify_pipeline, job_id, txt_path)
    return JSONResponse({"job_id": job_id, "progress_url": f"/classify_progress/{job_id}"})


# ─── PokerGTO ログイン / セッション一覧 画面 ─────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Firebase Auth (Google) ログイン画面"""
    from scripts.firebase_utils import is_firebase_enabled
    if not is_firebase_enabled():
        return HTMLResponse("<h1>Firebase未設定</h1><p>環境変数 FIREBASE_SERVICE_ACCOUNT_JSON を設定してください。</p>", status_code=503)
    return HTMLResponse(_LOGIN_PAGE_HTML)


@app.get("/sessions", response_class=HTMLResponse)
async def sessions_page():
    """セッション一覧画面（フロントエンドがAPIを叩いて表示）"""
    from scripts.firebase_utils import is_firebase_enabled
    if not is_firebase_enabled():
        return HTMLResponse("<h1>Firebase未設定</h1>", status_code=503)
    return HTMLResponse(_SESSIONS_PAGE_HTML)


# ─── ログイン / セッション一覧 HTMLテンプレート ───────────────────────────────

# Firebase設定はフロントエンドが /api/firebase-config から取得する
# （クライアントSDKはpublicキーなので環境変数に置いてOK）
_FIREBASE_API_KEY       = os.environ.get("FIREBASE_API_KEY", "")
_FIREBASE_AUTH_DOMAIN   = os.environ.get("FIREBASE_AUTH_DOMAIN", "")
_FIREBASE_PROJECT_ID    = os.environ.get("FIREBASE_PROJECT_ID", "")


@app.get("/api/firebase-config")
async def firebase_config():
    """フロントエンドのFirebase JS SDKに渡すpublic設定を返す"""
    return JSONResponse({
        "apiKey":     _FIREBASE_API_KEY,
        "authDomain": _FIREBASE_AUTH_DOMAIN,
        "projectId":  _FIREBASE_PROJECT_ID,
    })


_LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PokerGTO ログイン</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e;
  color: #eee;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.card {
  background: #16213e;
  border-radius: 16px;
  padding: 48px;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  text-align: center;
}
h1 { font-size: 22px; color: #e94560; margin-bottom: 8px; }
.sub { font-size: 13px; color: #888; margin-bottom: 32px; }
.btn-google {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  width: 100%;
  padding: 14px;
  background: #fff;
  color: #333;
  border: none;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-google:hover { background: #f0f0f0; }
.btn-google img { width: 20px; height: 20px; }
.status { margin-top: 16px; font-size: 13px; color: #888; min-height: 20px; }
.error { color: #e94560; }
</style>
</head>
<body>
<div class="card">
  <h1>🃏 PokerGTO</h1>
  <p class="sub">Googleアカウントでログインしてください</p>
  <button class="btn-google" id="btn-login">
    <img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg" alt="Google">
    Googleでログイン
  </button>
  <p class="status" id="status"></p>
</div>

<script type="module">
  import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
  import { getAuth, GoogleAuthProvider, signInWithPopup, onAuthStateChanged }
    from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

  const cfg = await fetch("/api/firebase-config").then(r => r.json());
  const app  = initializeApp(cfg);
  const auth = getAuth(app);

  // すでにログイン済みならセッション一覧へ
  onAuthStateChanged(auth, user => {
    if (user) window.location.href = "/sessions";
  });

  document.getElementById("btn-login").addEventListener("click", async () => {
    const st = document.getElementById("status");
    st.textContent = "ログイン中...";
    st.classList.remove("error");
    try {
      const provider = new GoogleAuthProvider();
      await signInWithPopup(auth, provider);
      // onAuthStateChanged が /sessions にリダイレクトする
    } catch (e) {
      st.textContent = "ログイン失敗: " + e.message;
      st.classList.add("error");
    }
  });
</script>
</body>
</html>"""


_SESSIONS_PAGE_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PokerGTO セッション一覧</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Meiryo', sans-serif;
  background: #1a1a2e;
  color: #eee;
  min-height: 100vh;
  padding: 20px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: 900px;
  margin: 0 auto 24px;
}
h1 { font-size: 20px; color: #e94560; }
.user-info { font-size: 13px; color: #888; display: flex; align-items: center; gap: 12px; }
.btn-logout {
  padding: 6px 14px;
  background: transparent;
  border: 1px solid #555;
  border-radius: 6px;
  color: #aaa;
  cursor: pointer;
  font-size: 12px;
}
.btn-logout:hover { border-color: #e94560; color: #e94560; }
.btn-dl-ext {
  padding: 6px 14px;
  background: transparent;
  border: 1px solid #4a7a4a;
  border-radius: 6px;
  color: #5cb85c;
  cursor: pointer;
  font-size: 12px;
  text-decoration: none;
}
.btn-dl-ext:hover { background: #1a3a1a; }
.container { max-width: 900px; margin: 0 auto; }
.loading { text-align: center; color: #888; padding: 60px; }
.empty { text-align: center; color: #666; padding: 60px; }
.empty p { margin-bottom: 8px; }

/* 一括操作バー */
.bulk-bar {
  display: none;
  align-items: center;
  gap: 10px;
  background: #0f3460;
  border-radius: 10px;
  padding: 12px 18px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.bulk-bar.visible { display: flex; }
.bulk-count { font-size: 13px; color: #aad4ff; flex: 1; }
.btn-bulk-analyze {
  padding: 8px 18px;
  background: #e94560;
  border: none;
  border-radius: 8px;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-bulk-analyze:hover { background: #c73652; }
.btn-bulk-download {
  padding: 8px 18px;
  background: #1a4a30;
  border: none;
  border-radius: 8px;
  color: #5cb85c;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-bulk-download:hover { background: #255a38; }
.btn-bulk-clear {
  padding: 6px 12px;
  background: transparent;
  border: 1px solid #445;
  border-radius: 6px;
  color: #888;
  font-size: 12px;
  cursor: pointer;
}
.btn-bulk-clear:hover { border-color: #aaa; color: #aaa; }

.session-card {
  background: #16213e;
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  transition: background 0.15s;
}
.session-card.selected { background: #1a2e50; outline: 1px solid #0f3460; }
.cb-wrap {
  display: flex;
  align-items: center;
  padding-right: 4px;
}
.cb-wrap input[type=checkbox] {
  width: 18px;
  height: 18px;
  cursor: pointer;
  accent-color: #e94560;
}
.session-info { flex: 1; }
.session-date { font-size: 13px; color: #888; margin-bottom: 4px; }
.session-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.session-meta { font-size: 12px; color: #666; }
.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.badge-pending   { background: #333; color: #888; }
.badge-analyzing { background: #1a3a5c; color: #5bc0de; }
.badge-done      { background: #1a3a1a; color: #5cb85c; }
.badge-error     { background: #3a1a1a; color: #e94560; }
.actions { display: flex; gap: 8px; align-items: center; }
.btn-analyze {
  padding: 8px 18px;
  background: #e94560;
  border: none;
  border-radius: 8px;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-analyze:hover { background: #c73652; }
.btn-analyze:disabled { background: #555; cursor: not-allowed; }
.btn-result {
  padding: 8px 18px;
  background: #0f3460;
  border: none;
  border-radius: 8px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-result:hover { background: #1a4a80; }
.btn-delete {
  padding: 6px 12px;
  background: transparent;
  border: 1px solid #444;
  border-radius: 6px;
  color: #888;
  font-size: 12px;
  cursor: pointer;
}
.btn-delete:hover { border-color: #e94560; color: #e94560; }
.alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }
.alert-error { background: #3a1a1a; color: #e94560; border: 1px solid #5a2a2a; }
</style>
</head>
<body>
<div class="header">
  <h1>🃏 PokerGTO</h1>
  <div class="user-info">
    <a href="/download-extension" class="btn-dl-ext" title="Chrome拡張機能をダウンロード">⬇ 拡張機能ZIP</a>
    <span id="user-email"></span>
    <button class="btn-logout" id="btn-logout">ログアウト</button>
  </div>
</div>
<div class="container">
  <div id="alert-area"></div>

  <!-- 一括操作バー -->
  <div class="bulk-bar" id="bulk-bar">
    <span class="bulk-count" id="bulk-count">0件選択中</span>
    <button class="btn-bulk-analyze" id="btn-bulk-analyze">まとめて解析</button>
    <button class="btn-bulk-download" id="btn-bulk-download">テキストを保存</button>
    <button class="btn-bulk-clear" id="btn-bulk-clear">選択解除</button>
  </div>

  <div id="content" class="loading">読み込み中...</div>
</div>

<script type="module">
  import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
  import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
    from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

  const cfg  = await fetch("/api/firebase-config").then(r => r.json());
  const app  = initializeApp(cfg);
  const auth = getAuth(app);

  let currentUser = null;
  let selectedIds = new Set();

  function showAlert(msg) {
    document.getElementById("alert-area").innerHTML =
      `<div class="alert alert-error">${msg}</div>`;
  }

  async function getIdToken() {
    if (!currentUser) throw new Error("未ログイン");
    return currentUser.getIdToken();
  }

  async function loadSessions() {
    document.getElementById("content").innerHTML = '<div class="loading">読み込み中...</div>';
    try {
      const token = await getIdToken();
      const res = await fetch("/api/sessions", {
        headers: { "Authorization": "Bearer " + token }
      });
      const data = await res.json();
      if (!res.ok) { showAlert(data.error || "取得失敗"); return; }
      renderSessions(data.sessions || []);
    } catch (e) {
      showAlert("セッション取得失敗: " + e.message);
    }
  }

  function statusBadge(status) {
    const map = {
      pending:   ["pending", "未解析"],
      analyzing: ["analyzing", "解析中..."],
      done:      ["done", "完了"],
      error:     ["error", "エラー"],
    };
    const [cls, label] = map[status] || ["pending", status];
    return `<span class="badge badge-${cls}">${label}</span>`;
  }

  function updateBulkBar() {
    const bar = document.getElementById("bulk-bar");
    const count = document.getElementById("bulk-count");
    if (selectedIds.size > 0) {
      bar.classList.add("visible");
      count.textContent = `${selectedIds.size}件選択中`;
    } else {
      bar.classList.remove("visible");
    }
  }

  function toggleSelect(sessionId) {
    const card = document.getElementById(`card-${sessionId}`);
    const cb = document.getElementById(`cb-${sessionId}`);
    if (selectedIds.has(sessionId)) {
      selectedIds.delete(sessionId);
      card.classList.remove("selected");
      cb.checked = false;
    } else {
      selectedIds.add(sessionId);
      card.classList.add("selected");
      cb.checked = true;
    }
    updateBulkBar();
  }

  function renderSessions(sessions) {
    const el = document.getElementById("content");
    if (!sessions.length) {
      el.innerHTML = `<div class="empty">
        <p>セッションがありません</p>
        <p style="font-size:12px">Chrome拡張機能を使ってT4からハンドログを送信してください</p>
      </div>`;
      return;
    }

    el.innerHTML = sessions.map(s => {
      const date = s.uploaded_at ? s.uploaded_at.replace("T", " ").slice(0, 16) : "-";
      const canAnalyze = s.status === "pending" || s.status === "error";
      const isDone = s.status === "done";

      const analyzeBtn = canAnalyze
        ? `<button class="btn-analyze" onclick="analyzeSession('${s.id}', this)">解析する</button>`
        : `<button class="btn-analyze" disabled>${s.status === "analyzing" ? "解析中..." : "解析済"}</button>`;

      const resultBtn = isDone && s.result_pdf
        ? `<button class="btn-result" onclick="window.open('/report/${s.result_pdf}','_blank')">結果を見る</button>`
        : "";

      return `<div class="session-card" id="card-${s.id}" onclick="toggleSelect('${s.id}')">
        <div class="cb-wrap" onclick="event.stopPropagation()">
          <input type="checkbox" id="cb-${s.id}" onclick="toggleSelect('${s.id}')">
        </div>
        <div class="session-info">
          <div class="session-date">${date}</div>
          <div class="session-title">${s.filename || "upload.txt"}</div>
          <div class="session-meta">${s.hand_count || 0} ハンド　${statusBadge(s.status)}</div>
        </div>
        <div class="actions" onclick="event.stopPropagation()">
          ${resultBtn}
          ${analyzeBtn}
          <button class="btn-delete" onclick="deleteSession('${s.id}', this)">削除</button>
        </div>
      </div>`;
    }).join("");
  }

  window.analyzeSession = async (sessionId, btn) => {
    btn.disabled = true;
    btn.textContent = "送信中...";
    try {
      const token = await getIdToken();
      const res = await fetch(`/api/sessions/${sessionId}/analyze`, {
        method: "POST",
        headers: { "Authorization": "Bearer " + token }
      });
      const data = await res.json();
      if (!res.ok) { showAlert(data.error || "解析開始失敗"); btn.disabled = false; return; }
      window.location.href = data.progress_url;
    } catch (e) {
      showAlert("エラー: " + e.message);
      btn.disabled = false;
    }
  };

  window.deleteSession = async (sessionId, btn) => {
    if (!confirm("このセッションを削除しますか？")) return;
    btn.disabled = true;
    try {
      const token = await getIdToken();
      const res = await fetch(`/api/sessions/${sessionId}`, {
        method: "DELETE",
        headers: { "Authorization": "Bearer " + token }
      });
      if (!res.ok) { showAlert("削除失敗"); btn.disabled = false; return; }
      selectedIds.delete(sessionId);
      updateBulkBar();
      document.getElementById(`card-${sessionId}`)?.remove();
    } catch (e) {
      showAlert("削除エラー: " + e.message);
      btn.disabled = false;
    }
  };

  // 一括操作ボタン（バックエンド未実装のため現状はアラート）
  document.getElementById("btn-bulk-analyze").addEventListener("click", async () => {
    const ids = [...selectedIds];
    if (!ids.length) return;
    showAlert("複数セッション結合解析は近日実装予定です（選択中: " + ids.length + "件）");
  });

  document.getElementById("btn-bulk-download").addEventListener("click", async () => {
    const ids = [...selectedIds];
    if (!ids.length) return;
    showAlert("テキストダウンロードは近日実装予定です（選択中: " + ids.length + "件）");
  });

  document.getElementById("btn-bulk-clear").addEventListener("click", () => {
    selectedIds.clear();
    document.querySelectorAll(".session-card").forEach(c => c.classList.remove("selected"));
    document.querySelectorAll("input[type=checkbox]").forEach(cb => cb.checked = false);
    updateBulkBar();
  });

  document.getElementById("btn-logout").addEventListener("click", async () => {
    await signOut(auth);
    window.location.href = "/login";
  });

  onAuthStateChanged(auth, user => {
    if (!user) {
      window.location.href = "/login";
      return;
    }
    currentUser = user;
    document.getElementById("user-email").textContent = user.email || user.displayName || "";
    loadSessions();
  });
</script>
</body>
</html>"""


# ─── メイン ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"ポーカーGTO サーバー起動: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
