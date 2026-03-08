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
FRONT_DIR  = ROOT / "front"
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


# ─── 静的ファイル配信 ─────────────────────────────────────────────────────────

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
}

def read_front(filename: str) -> bytes:
    return (FRONT_DIR / filename).read_bytes()

ERROR_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>エラー</title>
<style>
body {{ font-family: monospace; background: #0d0d0d; color: #eee; padding: 40px; }}
h2 {{ color: #e94560; margin-bottom: 16px; }}
pre {{ background: #111; padding: 20px; border-radius: 8px; white-space: pre-wrap; color: #f88; }}
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
            self._send_bytes(read_front("index.html"), "text/html; charset=utf-8")
        elif self.path == "/style.css":
            self._send_bytes(read_front("style.css"), "text/css; charset=utf-8")
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

    def _send_bytes(self, body: bytes, content_type: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
