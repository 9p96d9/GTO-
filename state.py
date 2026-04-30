"""
state.py - グローバル変数・パス定数
server.py / pipelines.py / routes/* から共通でインポートして使う
"""

import asyncio
import os
import sys
import threading
from pathlib import Path

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

ROOT       = Path(__file__).parent
SCRIPTS    = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

INPUT_DIR  = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
DATA_DIR   = ROOT / "data"
DONE_DIR   = INPUT_DIR / "done"

for _d in [INPUT_DIR, OUTPUT_DIR, DATA_DIR, DONE_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

BASE_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

# ─── レート制限 ──────────────────────────────────────────────────────────────
# ALB経由のため X-Forwarded-For から実IPを取得
def _get_real_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return get_remote_address(request)

limiter = Limiter(key_func=_get_real_ip)

# ─── ジョブ管理 ───────────────────────────────────────────────────────────────
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
quick_results: dict[str, dict] = {}   # job_id → compute_quick_stats の結果
classify_results: dict[str, dict] = {}  # job_id → classify 結果データ

# SSEイベントキュー（job_id ごと）
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
