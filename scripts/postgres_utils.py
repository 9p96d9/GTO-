"""
postgres_utils.py - PostgreSQL データアクセス層
firebase_utils.py と同一のシグネチャで実装。
環境変数 DATABASE_URL に接続文字列を設定する。
"""

import os
import threading
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

_init_lock = threading.Lock()
_engine = None
_SessionLocal = None


def _init():
    global _engine, _SessionLocal
    if _engine is not None:
        return
    with _init_lock:
        if _engine is not None:
            return
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("環境変数 DATABASE_URL が設定されていません。")
        _engine = create_engine(url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine)


def _session() -> Session:
    _init()
    return _SessionLocal()


def _get_or_create_user(session: Session, uid: str, email: str = "") -> int:
    row = session.execute(
        text("SELECT id FROM users WHERE firebase_uid = :uid"), {"uid": uid}
    ).fetchone()
    if row:
        return row[0]
    result = session.execute(
        text("INSERT INTO users (firebase_uid, email) VALUES (:uid, :email) RETURNING id"),
        {"uid": uid, "email": email or ""},
    )
    session.commit()
    return result.fetchone()[0]


# ─── セッション（レガシー） ──────────────────────────────────────────────────

def save_session(uid: str, raw_text: str, filename: str, hand_count: int) -> str:
    # sessionsテーブルは未実装（レガシー機能）→ Firebaseに委譲するためNotImplemented
    raise NotImplementedError("sessions は Firebase のみサポート")


def get_sessions(uid: str) -> list[dict]:
    raise NotImplementedError("sessions は Firebase のみサポート")


def get_session(uid: str, session_id: str) -> dict | None:
    raise NotImplementedError("sessions は Firebase のみサポート")


def update_session_status(uid: str, session_id: str, status: str, result_pdf: str = "", job_id: str = ""):
    raise NotImplementedError("sessions は Firebase のみサポート")


def delete_session(uid: str, session_id: str):
    raise NotImplementedError("sessions は Firebase のみサポート")


# ─── ハンド ──────────────────────────────────────────────────────────────────

def get_hands(uid: str, limit: int = 500, since_iso: str = "") -> list[dict]:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        params: dict = {"user_id": user_id, "limit": limit if 0 < limit < 9999 else 9999}
        where = "WHERE user_id = :user_id"
        if since_iso:
            where += " AND saved_at >= :since"
            params["since"] = since_iso
        rows = s.execute(
            text(f"SELECT hand_id, hand_json, captured_at, saved_at FROM hands {where} ORDER BY saved_at DESC LIMIT :limit"),
            params,
        ).fetchall()
    result = []
    for r in rows:
        d = {"hand_id": r[0], **r[1], "captured_at": r[2].isoformat() if r[2] else None, "saved_at": r[3].isoformat() if r[3] else None}
        result.append(d)
    return result


def get_hands_stats(uid: str) -> dict:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        row = s.execute(
            text("SELECT COUNT(*), MIN(captured_at), MAX(captured_at) FROM hands WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchone()
    count = row[0] or 0
    if count == 0:
        return {"count": 0, "newest": None, "oldest": None}
    return {
        "count": count,
        "newest": row[2].isoformat() if row[2] else None,
        "oldest": row[1].isoformat() if row[1] else None,
    }


def save_hand(uid: str, hand_json: dict, captured_at: str) -> str:
    import json as _json
    table_id = hand_json.get("tableId", "unknown")
    safe_ts = captured_at.replace(":", "").replace(".", "").replace("-", "")
    hand_id = f"{table_id}_{safe_ts}"
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        s.execute(
            text("""
                INSERT INTO hands (user_id, hand_id, hand_json, captured_at, saved_at)
                VALUES (:user_id, :hand_id, :hand_json::jsonb, :captured_at, :saved_at)
                ON CONFLICT (hand_id) DO NOTHING
            """),
            {
                "user_id": user_id,
                "hand_id": hand_id,
                "hand_json": _json.dumps(hand_json),
                "captured_at": captured_at,
                "saved_at": datetime.now(timezone.utc),
            },
        )
        s.commit()
    return hand_id


# ─── 解析 ────────────────────────────────────────────────────────────────────

def save_analysis(uid: str, job_id: str, classified_data: dict) -> bool:
    import json as _json, gzip as _gzip, base64 as _b64
    hands = classified_data.get("hands", [])
    blue  = sum(1 for h in hands if h.get("bluered_classification", {}).get("line") == "blue")
    red   = sum(1 for h in hands if h.get("bluered_classification", {}).get("line") == "red")
    pf    = len(hands) - blue - red
    categories: dict = {}
    for hand in hands:
        label = hand.get("bluered_classification", {}).get("category_label", "")
        if label:
            categories[label] = categories.get(label, 0) + 1

    raw_bytes  = _json.dumps(classified_data, ensure_ascii=False).encode("utf-8")
    compressed = _b64.b64encode(_gzip.compress(raw_bytes, compresslevel=9)).decode("ascii")
    has_snapshot = len(compressed.encode("ascii")) <= 900_000

    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        s.execute(
            text("""
                INSERT INTO analyses
                  (user_id, job_id, created_at, hand_count, blue_count, red_count, pf_count,
                   categories, classified_snapshot, snapshot_encoding)
                VALUES
                  (:user_id, :job_id, :created_at, :hand_count, :blue_count, :red_count, :pf_count,
                   :categories::jsonb, :snapshot, :encoding)
                ON CONFLICT (job_id) DO UPDATE SET
                  hand_count = EXCLUDED.hand_count,
                  blue_count = EXCLUDED.blue_count,
                  red_count  = EXCLUDED.red_count,
                  pf_count   = EXCLUDED.pf_count,
                  categories = EXCLUDED.categories,
                  classified_snapshot = EXCLUDED.classified_snapshot,
                  snapshot_encoding   = EXCLUDED.snapshot_encoding
            """),
            {
                "user_id":    user_id,
                "job_id":     job_id,
                "created_at": datetime.now(timezone.utc),
                "hand_count": len(hands),
                "blue_count": blue,
                "red_count":  red,
                "pf_count":   pf,
                "categories": _json.dumps(categories),
                "snapshot":   compressed if has_snapshot else None,
                "encoding":   "gzip_b64" if has_snapshot else None,
            },
        )
        s.commit()
    return has_snapshot


def get_analysis(uid: str, job_id: str) -> dict | None:
    import gzip as _gzip, base64 as _b64
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        row = s.execute(
            text("""
                SELECT a.job_id, a.created_at, a.hand_count, a.blue_count, a.red_count,
                       a.pf_count, a.categories, a.classified_snapshot, a.snapshot_encoding,
                       a.active_cart
                FROM analyses a
                WHERE a.user_id = :user_id AND a.job_id = :job_id AND a.deleted_at IS NULL
            """),
            {"user_id": user_id, "job_id": job_id},
        ).fetchone()
    if not row:
        return None
    d = {
        "job_id":     row[0],
        "created_at": row[1].isoformat() if row[1] else None,
        "hand_count": row[2],
        "blue_count": row[3],
        "red_count":  row[4],
        "pf_count":   row[5],
        "categories": row[6],
        "classified_snapshot": row[7],
        "snapshot_encoding":   row[8],
        "active_cart": row[9],
    }
    if d.get("snapshot_encoding") == "gzip_b64" and d.get("classified_snapshot"):
        d["classified_snapshot"] = _gzip.decompress(
            _b64.b64decode(d["classified_snapshot"])
        ).decode("utf-8")
    return d


def get_analyses(uid: str, limit: int = 20) -> list[dict]:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        rows = s.execute(
            text("""
                SELECT job_id, created_at, hand_count, blue_count, red_count, pf_count,
                       active_cart, snapshot_encoding
                FROM analyses
                WHERE user_id = :user_id AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        ).fetchall()
    result = []
    for r in rows:
        result.append({
            "job_id":       r[0],
            "created_at":   r[1].isoformat() if r[1] else None,
            "hand_count":   r[2],
            "blue_count":   r[3],
            "red_count":    r[4],
            "pf_count":     r[5],
            "active_cart":  r[6],
            "has_snapshot": r[7] is not None,
        })
    return result


def delete_analysis(uid: str, job_id: str) -> None:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        s.execute(
            text("UPDATE analyses SET deleted_at = :now WHERE user_id = :user_id AND job_id = :job_id"),
            {"now": datetime.now(timezone.utc), "user_id": user_id, "job_id": job_id},
        )
        s.commit()


# ─── カート ──────────────────────────────────────────────────────────────────

def get_cart(uid: str, job_id: str) -> list:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        row = s.execute(
            text("SELECT active_cart FROM analyses WHERE user_id = :user_id AND job_id = :job_id"),
            {"user_id": user_id, "job_id": job_id},
        ).fetchone()
    return row[0] or [] if row else []


def update_cart(uid: str, job_id: str, hand_numbers: list):
    import json as _json
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        s.execute(
            text("UPDATE analyses SET active_cart = :cart::jsonb WHERE user_id = :user_id AND job_id = :job_id"),
            {"cart": _json.dumps(hand_numbers), "user_id": user_id, "job_id": job_id},
        )
        s.commit()


def save_cart_snapshot(uid: str, job_id: str, name: str, hand_numbers: list) -> str:
    import uuid as _uuid, json as _json
    cart_id = _uuid.uuid4().hex
    label = name.strip() or datetime.now(timezone.utc).strftime("カート %Y-%m-%d %H:%M")
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        s.execute(
            text("""
                INSERT INTO carts (user_id, cart_id, job_id, name, hand_numbers, created_at)
                VALUES (:user_id, :cart_id, :job_id, :name, :hand_numbers::jsonb, :created_at)
            """),
            {
                "user_id":      user_id,
                "cart_id":      cart_id,
                "job_id":       job_id,
                "name":         label,
                "hand_numbers": _json.dumps(hand_numbers),
                "created_at":   datetime.now(timezone.utc),
            },
        )
        s.commit()
    return cart_id


def list_saved_carts(uid: str, job_id: str = None) -> list:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        params: dict = {"user_id": user_id}
        where = "WHERE user_id = :user_id"
        if job_id:
            where += " AND job_id = :job_id"
            params["job_id"] = job_id
        rows = s.execute(
            text(f"SELECT cart_id, job_id, name, hand_numbers, created_at FROM carts {where} ORDER BY created_at DESC LIMIT 20"),
            params,
        ).fetchall()
    return [
        {
            "cart_id":      r[0],
            "job_id":       r[1],
            "name":         r[2],
            "hand_numbers": r[3],
            "created_at":   r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


# ─── ユーザー設定 ────────────────────────────────────────────────────────────

def get_user_settings(uid: str) -> dict:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        row = s.execute(
            text("SELECT encrypted_api_key, needs_api_auto_cart FROM user_settings WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).fetchone()
    if not row:
        return {}
    return {"encrypted_api_key": row[0], "needs_api_auto_cart": row[1]}


def save_user_settings(uid: str, api_key: str = None, needs_api_auto_cart: bool = None):
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        exists = s.execute(
            text("SELECT 1 FROM user_settings WHERE user_id = :user_id"), {"user_id": user_id}
        ).fetchone()
        if not exists:
            s.execute(
                text("INSERT INTO user_settings (user_id) VALUES (:user_id)"), {"user_id": user_id}
            )
        if api_key is not None:
            s.execute(
                text("UPDATE user_settings SET encrypted_api_key = :v, updated_at = now() WHERE user_id = :user_id"),
                {"v": api_key, "user_id": user_id},
            )
        if needs_api_auto_cart is not None:
            s.execute(
                text("UPDATE user_settings SET needs_api_auto_cart = :v, updated_at = now() WHERE user_id = :user_id"),
                {"v": needs_api_auto_cart, "user_id": user_id},
            )
        s.commit()


# ─── AI解析結果 ──────────────────────────────────────────────────────────────

def get_gemini_results(uid: str, job_id: str) -> dict:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        rows = s.execute(
            text("""
                SELECT ar.hand_number, ar.ai_text
                FROM ai_results ar
                JOIN analyses a ON a.id = ar.analysis_id
                WHERE a.user_id = :user_id AND a.job_id = :job_id
            """),
            {"user_id": user_id, "job_id": job_id},
        ).fetchall()
    return {str(r[0]): {"ai_text": r[1]} for r in rows}


def save_gemini_results(uid: str, job_id: str, results: dict):
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        analysis_row = s.execute(
            text("SELECT id FROM analyses WHERE user_id = :user_id AND job_id = :job_id"),
            {"user_id": user_id, "job_id": job_id},
        ).fetchone()
        if not analysis_row:
            return
        analysis_id = analysis_row[0]
        for hand_number_str, data in results.items():
            ai_text = data.get("ai_text", "") if isinstance(data, dict) else str(data)
            s.execute(
                text("""
                    INSERT INTO ai_results (analysis_id, hand_number, ai_text)
                    VALUES (:analysis_id, :hand_number, :ai_text)
                    ON CONFLICT DO NOTHING
                """),
                {"analysis_id": analysis_id, "hand_number": int(hand_number_str), "ai_text": ai_text},
            )
        s.commit()


# ─── 管理者 ──────────────────────────────────────────────────────────────────

def get_admin_summary() -> dict:
    with _session() as s:
        total_users    = s.execute(text("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL")).scalar()
        total_hands    = s.execute(text("SELECT COUNT(*) FROM hands")).scalar()
        total_analyses = s.execute(text("SELECT COUNT(*) FROM analyses WHERE deleted_at IS NULL")).scalar()
        from datetime import timedelta
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        active_users_7d = s.execute(
            text("SELECT COUNT(DISTINCT user_id) FROM hands WHERE saved_at >= :since"),
            {"since": since_7d},
        ).scalar()
    return {
        "total_users":    total_users,
        "total_hands":    total_hands,
        "total_analyses": total_analyses,
        "active_users_7d": active_users_7d,
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
    }


def get_admin_users() -> list[dict]:
    with _session() as s:
        rows = s.execute(
            text("""
                SELECT u.firebase_uid, u.email, u.created_at,
                       COUNT(DISTINCT h.id) AS hand_count,
                       COUNT(DISTINCT a.id) AS analysis_count
                FROM users u
                LEFT JOIN hands h ON h.user_id = u.id
                LEFT JOIN analyses a ON a.user_id = u.id AND a.deleted_at IS NULL
                WHERE u.deleted_at IS NULL
                GROUP BY u.id
                ORDER BY hand_count DESC
            """)
        ).fetchall()
    return [
        {
            "uid":            r[0],
            "email":          r[1],
            "last_login":     None,
            "hand_count":     r[3],
            "analysis_count": r[4],
            "has_api_key":    False,
            "avg_pf_score":   None,
        }
        for r in rows
    ]


def is_firebase_enabled() -> bool:
    return False


def get_db():
    raise NotImplementedError("get_db() は PostgreSQL モードでは使用できません")
