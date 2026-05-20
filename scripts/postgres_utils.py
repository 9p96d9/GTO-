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
        d = {"hand_id": r[0], "hand_json": r[1], "captured_at": r[2].isoformat() if r[2] else None, "saved_at": r[3].isoformat() if r[3] else None}
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
    import json as _json, re as _re
    table_id = str(hand_json.get("tableId", "unknown"))
    safe_table = _re.sub(r'[^a-zA-Z0-9_\-]', '_', table_id)[:64]
    safe_ts = captured_at.replace(":", "").replace(".", "").replace("-", "")
    hand_id = f"{safe_table}_{safe_ts}"
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        s.execute(
            text("""
                INSERT INTO hands (user_id, hand_id, hand_json, captured_at, saved_at)
                VALUES (:user_id, :hand_id, CAST(:hand_json AS jsonb), :captured_at, :saved_at)
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
    import json as _json
    hands = classified_data.get("hands", [])
    blue  = sum(1 for h in hands if h.get("bluered_classification", {}).get("line") == "blue")
    red   = sum(1 for h in hands if h.get("bluered_classification", {}).get("line") == "red")
    pf    = len(hands) - blue - red
    categories: dict = {}
    for hand in hands:
        label = hand.get("bluered_classification", {}).get("category_label", "")
        if label:
            categories[label] = categories.get(label, 0) + 1

    # hand_ids: DB の hands.hand_id リスト（restore時に使用）
    hand_ids = [h.get("_db_hand_id") for h in hands if h.get("_db_hand_id")]

    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        row = s.execute(
            text("""
                INSERT INTO analyses
                  (user_id, job_id, created_at, hand_count, blue_count, red_count, pf_count,
                   categories, hand_ids)
                VALUES
                  (:user_id, :job_id, :created_at, :hand_count, :blue_count, :red_count, :pf_count,
                   CAST(:categories AS jsonb), CAST(:hand_ids AS jsonb))
                ON CONFLICT (job_id) DO UPDATE SET
                  hand_count = EXCLUDED.hand_count,
                  blue_count = EXCLUDED.blue_count,
                  red_count  = EXCLUDED.red_count,
                  pf_count   = EXCLUDED.pf_count,
                  categories = EXCLUDED.categories,
                  hand_ids   = EXCLUDED.hand_ids
                RETURNING id
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
                "hand_ids":   _json.dumps(hand_ids),
            },
        )
        analysis_id = row.fetchone()[0]
        s.commit()

    ah_rows = []
        for hand in hands:
            bc    = hand.get("bluered_classification", {})
            line  = bc.get("line", "")
            label = bc.get("category_label", "")
            hnum  = hand.get("hand_number")
            if not (line and label and hnum):
                continue

            # B4: street_reached（到達した最深ストリート）
            raw_streets = hand.get("streets", {})
            street_reached = "preflop"
            for st in ("flop", "turn", "river"):
                if raw_streets.get(st):
                    street_reached = st

            # B4: pot_size_bb（最終ポット = 全勝者の獲得合計）
            winners = hand.get("result", {}).get("winners", [])
            pot_size_bb = sum(float(w.get("amount_bb", 0)) for w in winners) if winners else None

            ah_rows.append({
                "aid":            analysis_id,
                "hnum":           hnum,
                "line":           line,
                "label":          label,
                "pos":            hand.get("hero_position", "") or None,
                "cat":            hand.get("datetime") or None,
                "hand_id":        hand.get("_db_hand_id") or None,
                "pot_size_bb":    pot_size_bb,
                "street_reached": street_reached,
            })
        s.commit()

    # analysis_hands は別トランザクション（失敗しても analyses は保存済み）
    if ah_rows:
        try:
            with _session() as s:
                s.execute(text("DELETE FROM analysis_hands WHERE analysis_id = :aid"), {"aid": analysis_id})
                s.execute(
                    text("""
                        INSERT INTO analysis_hands
                          (analysis_id, hand_number, line, category_label, position, captured_at,
                           hand_id, pot_size_bb, street_reached)
                        VALUES (:aid, :hnum, :line, :label, :pos, :cat,
                                :hand_id, :pot_size_bb, :street_reached)
                    """),
                    ah_rows,
                )
                s.commit()
        except Exception as _e:
            print(f"[WARN] analysis_hands保存失敗: {_e}", file=sys.stderr)
    return bool(hand_ids)


def get_analysis(uid: str, job_id: str) -> dict | None:
    import gzip as _gzip, base64 as _b64
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        row = s.execute(
            text("""
                SELECT a.job_id, a.created_at, a.hand_count, a.blue_count, a.red_count,
                       a.pf_count, a.categories, a.active_cart, a.hand_ids,
                       a.classified_snapshot, a.snapshot_encoding
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
        "active_cart": row[7],
        "hand_ids":   row[8] or [],
        "classified_snapshot": row[9],
        "snapshot_encoding":   row[10],
    }
    # 旧方式フォールバック用にデコード
    if d.get("snapshot_encoding") == "gzip_b64" and d.get("classified_snapshot"):
        d["classified_snapshot"] = _gzip.decompress(
            _b64.b64decode(d["classified_snapshot"])
        ).decode("utf-8")
    return d


def get_hands_by_ids(uid: str, hand_ids: list) -> list[dict]:
    """指定した hand_id リストのハンドを取得する（B3 restore用）"""
    if not hand_ids:
        return []
    import json as _json
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        rows = s.execute(
            text("""
                SELECT hand_id, hand_json, captured_at, saved_at
                FROM hands
                WHERE user_id = :user_id AND hand_id = ANY(CAST(:ids AS VARCHAR[]))
                ORDER BY saved_at ASC
            """),
            {"user_id": user_id, "ids": "{" + ",".join(hand_ids) + "}"},
        ).fetchall()
    result = []
    for r in rows:
        result.append({
            "hand_id":     r[0],
            "hand_json":   r[1],
            "captured_at": r[2].isoformat() if r[2] else None,
            "saved_at":    r[3].isoformat() if r[3] else None,
        })
    return result


def get_analysis_hands_for_3d(uid: str, job_id: str) -> list[dict]:
    """3D view用: analysis_hands JOIN hands を返す（B2 DBフォールバック）"""
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        ana_row = s.execute(
            text("SELECT id FROM analyses WHERE user_id = :uid AND job_id = :jid AND deleted_at IS NULL"),
            {"uid": user_id, "jid": job_id},
        ).fetchone()
        if not ana_row:
            return []
        analysis_id = ana_row[0]
        rows = s.execute(
            text("""
                SELECT ah.hand_number, ah.line, ah.category_label, ah.position,
                       ah.street_reached, ah.captured_at,
                       h.hand_json, h.hand_id
                FROM analysis_hands ah
                LEFT JOIN hands h ON h.hand_id = ah.hand_id AND h.user_id = :uid
                WHERE ah.analysis_id = :aid
                ORDER BY ah.hand_number ASC
            """),
            {"aid": analysis_id, "uid": user_id},
        ).fetchall()
    return [
        {
            "hand_number":    r[0],
            "line":           r[1],
            "category_label": r[2],
            "position":       r[3],
            "street_reached": r[4],
            "captured_at":    r[5].isoformat() if r[5] else None,
            "hand_json":      r[6],
            "hand_id":        r[7],
        }
        for r in rows
    ]


def get_analyses(uid: str, limit: int = 20) -> list[dict]:
    with _session() as s:
        user_id = _get_or_create_user(s, uid)
        rows = s.execute(
            text("""
                SELECT job_id, created_at, hand_count, blue_count, red_count, pf_count,
                       active_cart, hand_ids, snapshot_encoding
                FROM analyses
                WHERE user_id = :user_id AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        ).fetchall()
    result = []
    for r in rows:
        # has_snapshot: 新方式(hand_ids) or 旧方式(snapshot_encoding) どちらかがあれば復元可
        result.append({
            "job_id":       r[0],
            "created_at":   r[1].isoformat() if r[1] else None,
            "hand_count":   r[2],
            "blue_count":   r[3],
            "red_count":    r[4],
            "pf_count":     r[5],
            "active_cart":  r[6],
            "has_snapshot": bool(r[7]) or (r[8] is not None),
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
            text("UPDATE analyses SET active_cart = CAST(:cart AS jsonb) WHERE user_id = :user_id AND job_id = :job_id"),
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
                VALUES (:user_id, :cart_id, :job_id, :name, CAST(:hand_numbers AS jsonb), :created_at)
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
            text("SELECT encrypted_api_key FROM user_settings WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).fetchone()
    if not row:
        return {}
    return {"encrypted_api_key": row[0]}


def save_user_settings(uid: str, api_key: str = None):
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

def get_admin_analytics() -> dict:
    """全ユーザー横断KPI（PostgreSQLにしかできない集計）"""
    with _session() as s:
        # ① 全ユーザーblue/red率ランキング（RANK()ウィンドウ関数）
        user_rows = s.execute(text("""
            SELECT
                u.email,
                SUM(a.hand_count)  AS total_hands,
                SUM(a.blue_count)  AS blue,
                SUM(a.red_count)   AS red,
                SUM(a.pf_count)    AS pf,
                ROUND(SUM(a.blue_count)::numeric / NULLIF(SUM(a.hand_count),0) * 100, 1) AS blue_rate,
                ROUND(SUM(a.red_count)::numeric  / NULLIF(SUM(a.hand_count),0) * 100, 1) AS red_rate,
                RANK() OVER (
                    ORDER BY SUM(a.red_count)::float / NULLIF(SUM(a.hand_count),0) DESC
                ) AS red_rank
            FROM users u
            JOIN analyses a ON a.user_id = u.id AND a.deleted_at IS NULL
            WHERE u.deleted_at IS NULL
            GROUP BY u.id, u.email
            ORDER BY red_rate DESC NULLS LAST
        """)).fetchall()

        # ② 先週比赤線率悪化ユーザー（CTE＋期間比較）
        worsened_rows = s.execute(text("""
            WITH this_week AS (
                SELECT user_id,
                       SUM(red_count)::float / NULLIF(SUM(hand_count),0) AS red_rate
                FROM analyses
                WHERE created_at >= DATE_TRUNC('week', NOW())
                  AND deleted_at IS NULL
                GROUP BY user_id
            ),
            last_week AS (
                SELECT user_id,
                       SUM(red_count)::float / NULLIF(SUM(hand_count),0) AS red_rate
                FROM analyses
                WHERE created_at >= DATE_TRUNC('week', NOW()) - INTERVAL '7 days'
                  AND created_at <  DATE_TRUNC('week', NOW())
                  AND deleted_at IS NULL
                GROUP BY user_id
            )
            SELECT
                u.email,
                ROUND((tw.red_rate * 100)::numeric, 1) AS this_week_pct,
                ROUND((lw.red_rate * 100)::numeric, 1) AS last_week_pct,
                ROUND(((tw.red_rate - lw.red_rate) * 100)::numeric, 1) AS diff_pct
            FROM this_week tw
            JOIN last_week lw ON lw.user_id = tw.user_id
            JOIN users u ON u.id = tw.user_id
            WHERE tw.red_rate - lw.red_rate > 0.05
            ORDER BY diff_pct DESC
        """)).fetchall()

    return {
        "user_stats": [
            {
                "email":       r[0] or "—",
                "total_hands": int(r[1] or 0),
                "blue_rate":   float(r[5] or 0),
                "red_rate":    float(r[6] or 0),
                "pf_rate":     round(float(r[4] or 0) / max(int(r[1] or 1), 1) * 100, 1),
                "red_rank":    int(r[7]),
            }
            for r in user_rows
        ],
        "worsened_users": [
            {
                "email":         r[0] or "—",
                "this_week_pct": float(r[1] or 0),
                "last_week_pct": float(r[2] or 0),
                "diff_pct":      float(r[3] or 0),
            }
            for r in worsened_rows
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


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

    # Firebase Auth からメールを補完（DB の email 列が空の場合）
    firebase_emails: dict[str, str] = {}
    try:
        import firebase_admin
        from firebase_admin import auth as _fa
        firebase_admin.get_app()
        page = _fa.list_users()
        while page:
            for u in page.users:
                if u.email:
                    firebase_emails[u.uid] = u.email
            page = page.get_next_page()
    except Exception:
        pass

    result = []
    for r in rows:
        uid = r[0]
        email = r[1] or firebase_emails.get(uid, "")
        result.append({
            "uid":            uid,
            "email":          email,
            "last_login":     None,
            "hand_count":     r[3],
            "analysis_count": r[4],
            "has_api_key":    False,
            "avg_pf_score":   None,
        })
    return result


def delete_admin_user(uid: str) -> dict:
    """
    指定 firebase_uid のユーザーを完全削除する（ハードDELETE + Firebase Auth削除）。
    戻り値: {"hands": N, "analyses": N, "ok": True}
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    with _session() as s:
        row = s.execute(
            text("SELECT id FROM users WHERE firebase_uid = :uid AND deleted_at IS NULL"),
            {"uid": uid}
        ).fetchone()
        if not row:
            raise ValueError(f"ユーザーが見つかりません: {uid}")
        user_id = row[0]

        hand_res     = s.execute(text("DELETE FROM hands         WHERE user_id = :uid"), {"uid": user_id})
        analysis_res = s.execute(text("DELETE FROM analyses      WHERE user_id = :uid"), {"uid": user_id})
        s.execute(text("DELETE FROM user_settings WHERE user_id = :uid"), {"uid": user_id})
        s.execute(text("UPDATE users SET deleted_at = :now WHERE id = :uid"), {"now": now, "uid": user_id})
        s.commit()

        hand_count     = hand_res.rowcount
        analysis_count = analysis_res.rowcount

    # Firebase Auth からも削除
    try:
        import firebase_admin
        from firebase_admin import auth as _fa
        firebase_admin.get_app()
        _fa.delete_user(uid)
    except Exception:
        pass

    return {"hands": hand_count, "analyses": analysis_count, "ok": True}


def is_firebase_enabled() -> bool:
    return False


def get_db():
    raise NotImplementedError("get_db() は PostgreSQL モードでは使用できません")
