"""
server.py - ポーカーGTO ローカルWebサーバー
使用法: docker compose up
ブラウザで http://localhost:5000 を開く
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import cgi

ROOT       = Path(__file__).parent
SCRIPTS    = ROOT / "scripts"
INPUT_DIR  = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
DATA_DIR   = ROOT / "data"
DONE_DIR   = INPUT_DIR / "done"

for d in [INPUT_DIR, OUTPUT_DIR, DATA_DIR, DONE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

# ─── パイプライン ─────────────────────────────────────────────────────────────

def run_pipeline(txt_path: Path) -> tuple[bool, str, Path | None]:
    """txt → JSON → 評価 → HTML の一連処理。(成功, ログ, htmlパス) を返す"""
    logs = []

    # 1. parse
    json_path = DATA_DIR / (txt_path.stem + ".json")
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "parse.py"), str(txt_path), str(json_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV,
    )
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        return False, "\n".join(logs), None

    # 2. analyze
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "analyze.py"), str(json_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV,
    )
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        return False, "\n".join(logs), None

    # 3. generate
    r = subprocess.run(
        ["node", str(SCRIPTS / "generate.js"), str(OUTPUT_DIR), str(json_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV,
    )
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        return False, "\n".join(logs), None

    # 最新の HTML を探す
    html_files = sorted(OUTPUT_DIR.glob("GTO_Report_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not html_files:
        return False, "\n".join(logs) + "\nHTMLが見つかりません", None

    return True, "\n".join(logs), html_files[0]


# ─── HTMLテンプレート ─────────────────────────────────────────────────────────

UPLOAD_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
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
}}
.card {{
  background: #16213e;
  border-radius: 16px;
  padding: 48px 56px;
  width: 480px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  text-align: center;
}}
h1 {{ font-size: 22px; margin-bottom: 6px; color: #e94560; }}
.sub {{ font-size: 13px; color: #888; margin-bottom: 32px; }}
.dropzone {{
  border: 2px dashed #e94560;
  border-radius: 12px;
  padding: 36px 20px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 24px;
  position: relative;
}}
.dropzone:hover, .dropzone.dragover {{ background: rgba(233,69,96,0.08); }}
.dropzone input[type=file] {{
  position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
}}
.dropzone-icon {{ font-size: 40px; margin-bottom: 8px; }}
.dropzone-label {{ font-size: 14px; color: #aaa; }}
.dropzone-label span {{ color: #e94560; font-weight: bold; }}
.file-name {{ font-size: 13px; color: #4caf93; margin-top: 8px; min-height: 20px; }}
button {{
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
}}
button:hover {{ background: #c73652; }}
button:disabled {{ background: #555; cursor: not-allowed; }}
.loading {{ display: none; margin-top: 20px; font-size: 14px; color: #888; }}
.spinner {{
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid #555; border-top-color: #e94560;
  border-radius: 50%; animation: spin 0.8s linear infinite;
  margin-right: 8px; vertical-align: middle;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
<div class="card">
  <h1>🂡 ポーカーGTO</h1>
  <p class="sub">ハンド履歴をアップロードしてレポート生成</p>
  <form id="form" method="post" enctype="multipart/form-data" action="/upload">
    <div class="dropzone" id="drop">
      <input type="file" name="file" id="file" accept=".txt" required>
      <div class="dropzone-icon">📄</div>
      <div class="dropzone-label"><span>ファイルを選択</span>またはドロップ</div>
      <div class="file-name" id="fname"></div>
    </div>
    <button type="submit" id="btn">レポート生成</button>
    <div class="loading" id="loading"><span class="spinner"></span>処理中...</div>
  </form>
</div>
<script>
const file = document.getElementById('file');
const fname = document.getElementById('fname');
const drop = document.getElementById('drop');
const btn = document.getElementById('btn');
const loading = document.getElementById('loading');
const form = document.getElementById('form');

file.addEventListener('change', () => {{
  fname.textContent = file.files[0]?.name || '';
}});
drop.addEventListener('dragover', e => {{ e.preventDefault(); drop.classList.add('dragover'); }});
drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
drop.addEventListener('drop', e => {{
  e.preventDefault(); drop.classList.remove('dragover');
  if (e.dataTransfer.files[0]) {{
    file.files = e.dataTransfer.files;
    fname.textContent = e.dataTransfer.files[0].name;
  }}
}});
form.addEventListener('submit', () => {{
  btn.disabled = true;
  loading.style.display = 'block';
}});
</script>
</body>
</html>"""

ERROR_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>エラー</title>
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #eee; padding: 40px; }}
h2 {{ color: #e94560; margin-bottom: 16px; }}
pre {{ background: #0f0f1a; padding: 20px; border-radius: 8px; white-space: pre-wrap; color: #f88; }}
a {{ color: #e94560; }}
</style></head><body>
<h2>❌ エラーが発生しました</h2>
<pre>{log}</pre>
<p><a href="/">← 戻る</a></p>
</body></html>"""


# ─── HTTPハンドラー ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # アクセスログを簡略化
        print(f"[{self.address_string()}] {fmt % args}")

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send_html(UPLOAD_PAGE)
        elif self.path.startswith("/report/"):
            fname = self.path[len("/report/"):]
            fpath = OUTPUT_DIR / fname
            if fpath.exists() and fpath.suffix == ".html":
                self._send_file(fpath, "text/html; charset=utf-8")
            else:
                self._send_404()
        else:
            self._send_404()

    def do_POST(self):
        if self.path != "/upload":
            self._send_404()
            return

        ctype, pdict = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype != "multipart/form-data":
            self._send_html(ERROR_PAGE.format(log="multipart/form-data が必要です"), 400)
            return

        pdict["boundary"] = bytes(pdict["boundary"], "utf-8")
        pdict["CONTENT-LENGTH"] = int(self.headers.get("Content-Length", 0))
        fields = cgi.parse_multipart(self.rfile, pdict)

        file_data = fields.get("file")
        if not file_data:
            self._send_html(ERROR_PAGE.format(log="ファイルが見つかりません"), 400)
            return

        # ファイル名取得
        raw_headers = self.headers.get("Content-Disposition", "")
        filename = "upload.txt"
        for part in self.headers._headers:
            pass
        # multipartのファイル名はcgiモジュールから取れないのでデフォルト名で保存
        txt_path = INPUT_DIR / "upload.txt"
        data = file_data[0] if isinstance(file_data[0], bytes) else file_data[0].encode("utf-8")
        txt_path.write_bytes(data)

        print(f"[upload] {txt_path} ({len(data)} bytes)")

        # パイプライン実行
        ok, log, html_path = run_pipeline(txt_path)

        if not ok:
            self._send_html(ERROR_PAGE.format(log=self._esc(log)), 500)
            return

        # ファイルをdoneへ
        dest = DONE_DIR / txt_path.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(txt_path), str(dest))

        # レポートにリダイレクト
        report_name = html_path.name
        self._redirect(f"/report/{report_name}")

    def _send_html(self, html: str, code: int = 200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url: str):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _send_404(self):
        self._send_html("<h1>404</h1>", 404)

    def _esc(self, s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── メイン ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"ポーカーGTO サーバー起動: http://localhost:{port}")
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
