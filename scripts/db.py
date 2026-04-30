"""
db.py - データアクセス層ルーター
USE_POSTGRES=true のとき postgres_utils を、それ以外は firebase_utils を使う。
verify_id_token は常に Firebase Auth を使用する。
"""

import os

_use_postgres = os.environ.get("USE_POSTGRES", "false").lower() == "true"

if _use_postgres:
    from scripts.postgres_utils import (
        save_session, get_sessions, get_session, update_session_status, delete_session,
        get_hands, get_hands_stats, save_hand,
        save_analysis, get_analysis, get_analyses, delete_analysis,
        get_cart, update_cart, save_cart_snapshot, list_saved_carts,
        get_user_settings, save_user_settings,
        get_gemini_results, save_gemini_results,
        get_admin_summary, get_admin_users, get_admin_analytics,
        is_firebase_enabled, get_db,
    )
else:
    from scripts.firebase_utils import (
        save_session, get_sessions, get_session, update_session_status, delete_session,
        get_hands, get_hands_stats, save_hand,
        save_analysis, get_analysis, get_analyses, delete_analysis,
        get_cart, update_cart, save_cart_snapshot, list_saved_carts,
        get_user_settings, save_user_settings,
        get_gemini_results, save_gemini_results,
        get_admin_summary, get_admin_users, get_admin_analytics,
        is_firebase_enabled, get_db,
    )

# verify_id_token は常に Firebase Auth
from scripts.firebase_utils import verify_id_token

__all__ = [
    "verify_id_token",
    "save_session", "get_sessions", "get_session", "update_session_status", "delete_session",
    "get_hands", "get_hands_stats", "save_hand",
    "save_analysis", "get_analysis", "get_analyses", "delete_analysis",
    "get_cart", "update_cart", "save_cart_snapshot", "list_saved_carts",
    "get_user_settings", "save_user_settings",
    "get_gemini_results", "save_gemini_results",
    "get_admin_summary", "get_admin_users", "get_admin_analytics",
    "is_firebase_enabled", "get_db",
]
