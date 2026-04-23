"""
routes/admin.py - 管理者ダッシュボード（Phase 5）
GET /admin         → 管理者画面
GET /api/admin/summary  → KPI サマリー
GET /api/admin/users    → ユーザー一覧
"""

import os
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

def _render(name: str) -> str:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)
    return env.get_template(name).render()

ADMIN_UID = os.environ.get("ADMIN_UID", "")


def _get_uid(request: Request) -> str | None:
    """Authorization ヘッダーから uid を取得。失敗時は None。"""
    from scripts.firebase_utils import verify_id_token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    try:
        return verify_id_token(auth_header.removeprefix("Bearer ").strip())["uid"]
    except Exception:
        return None


def _check_admin(uid: str | None) -> bool:
    return bool(ADMIN_UID) and uid == ADMIN_UID


# ── ページルート ──────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """管理者ダッシュボード画面。認証はフロントエンド側で Firebase JWT を取得してから
    API を叩く設計のため、ページ自体は HTMLを返すだけ。"""
    from scripts.firebase_utils import is_firebase_enabled
    if not is_firebase_enabled():
        return HTMLResponse("<h2>Firebase未設定</h2>", status_code=503)
    if not ADMIN_UID:
        return HTMLResponse("<h2>ADMIN_UID 環境変数が設定されていません</h2>", status_code=503)
    return HTMLResponse(_render("admin.html"))


# ── API エンドポイント ────────────────────────────────────────────────────────

@router.get("/api/admin/summary")
async def api_admin_summary(request: Request):
    from scripts.firebase_utils import is_firebase_enabled, get_admin_summary
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    uid = _get_uid(request)
    if not _check_admin(uid):
        return JSONResponse({"error": "管理者権限が必要です"}, status_code=403)
    try:
        return JSONResponse(get_admin_summary())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/admin/users")
async def api_admin_users(request: Request):
    from scripts.firebase_utils import is_firebase_enabled, get_admin_users
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    uid = _get_uid(request)
    if not _check_admin(uid):
        return JSONResponse({"error": "管理者権限が必要です"}, status_code=403)
    try:
        return JSONResponse({"users": get_admin_users()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
