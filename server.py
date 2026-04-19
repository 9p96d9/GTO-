"""
server.py - FastAPI アプリ初期化・ミドルウェア・起動
ルーティングは routes/ へ、パイプラインは pipelines.py へ分離済み
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import state  # パス定数・グローバル変数の初期化（sys.path も設定される）

import uvicorn

app = FastAPI()

# CORS（ブックマークレットからの別オリジンPOSTを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS", "DELETE", "PUT"],
    allow_headers=["*"],
)

STATIC_DIR = state.ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── ルーター登録 ────────────────────────────────────────────────────────────
from routes.pages import router as pages_router
from routes.api   import router as api_router
from routes.cart  import router as cart_router

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(cart_router)

# ─── メイン ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"ポーカーGTO サーバー起動: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
