"""
firebase_utils.py - Firebase Admin SDK ユーティリティ
環境変数 FIREBASE_SERVICE_ACCOUNT_JSON にサービスアカウントJSONの中身を設定する。
"""

import os
import json
import threading
from datetime import datetime, timezone

_init_lock = threading.Lock()
_initialized = False
_db = None  # firestore client


def _init():
    global _initialized, _db
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        import firebase_admin
        from firebase_admin import credentials, firestore

        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
        if not sa_json:
            raise RuntimeError(
                "環境変数 FIREBASE_SERVICE_ACCOUNT_JSON が設定されていません。"
                "Railwayの Environment Variables に Firebase サービスアカウントJSONを設定してください。"
            )

        sa_dict = json.loads(sa_json)
        cred = credentials.Certificate(sa_dict)

        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)

        _db = firestore.client()
        _initialized = True


def get_db():
    """Firestore クライアントを返す（初回のみ初期化）"""
    _init()
    return _db


def verify_id_token(id_token: str) -> dict:
    """
    Firebase Auth の idToken を検証し、デコード済みトークン dict を返す。
    失敗時は例外を送出。
    """
    _init()
    from firebase_admin import auth
    return auth.verify_id_token(id_token)


def save_session(uid: str, raw_text: str, filename: str, hand_count: int) -> str:
    """
    Firestore users/{uid}/sessions/{sessionId} にセッションを保存する。
    生成した sessionId を返す。
    """
    db = get_db()
    sessions_ref = db.collection("users").document(uid).collection("sessions")

    doc_ref = sessions_ref.document()  # 自動ID生成
    doc_ref.set({
        "raw_text":    raw_text,
        "filename":    filename,
        "hand_count":  hand_count,
        "uploaded_at": datetime.now(timezone.utc),
        "status":      "pending",
        "result_pdf":  "",
    })
    return doc_ref.id


def get_sessions(uid: str) -> list[dict]:
    """
    Firestore users/{uid}/sessions を uploaded_at 降順で返す。
    各 dict に id フィールドを追加する。
    """
    db = get_db()
    sessions_ref = (
        db.collection("users").document(uid).collection("sessions")
        .order_by("uploaded_at", direction="DESCENDING")
    )
    docs = sessions_ref.stream()
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        # Firestore Timestamp → ISO文字列に変換
        if hasattr(d.get("uploaded_at"), "isoformat"):
            d["uploaded_at"] = d["uploaded_at"].isoformat()
        result.append(d)
    return result


def get_session(uid: str, session_id: str) -> dict | None:
    """
    Firestore users/{uid}/sessions/{session_id} を取得して返す。
    存在しない場合は None。
    """
    db = get_db()
    doc = (
        db.collection("users").document(uid)
        .collection("sessions").document(session_id)
        .get()
    )
    if not doc.exists:
        return None
    d = doc.to_dict()
    d["id"] = doc.id
    if hasattr(d.get("uploaded_at"), "isoformat"):
        d["uploaded_at"] = d["uploaded_at"].isoformat()
    return d


def update_session_status(uid: str, session_id: str, status: str, result_pdf: str = "", job_id: str = ""):
    """セッションのステータス・PDFファイル名・job_id を更新する"""
    db = get_db()
    doc_ref = (
        db.collection("users").document(uid)
        .collection("sessions").document(session_id)
    )
    update = {"status": status}
    if result_pdf:
        update["result_pdf"] = result_pdf
    if job_id:
        update["job_id"] = job_id
    doc_ref.update(update)


def delete_session(uid: str, session_id: str):
    """Firestore からセッションを削除する"""
    db = get_db()
    db.collection("users").document(uid).collection("sessions").document(session_id).delete()


def get_hands(uid: str, limit: int = 500, since_iso: str = "") -> list[dict]:
    """
    Firestore users/{uid}/hands を saved_at 降順で取得する。
    since_iso: この ISO 文字列以降のハンドのみ取得（例: "2026-04-05T00:00:00"）
    各 dict に hand_id フィールドを追加して返す。
    """
    db = get_db()
    ref = db.collection("users").document(uid).collection("hands")

    if since_iso:
        since_dt = datetime.fromisoformat(since_iso)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
        ref = ref.where("saved_at", ">=", since_dt)

    max_fetch = limit if 0 < limit < 9999 else 9999
    hands_ref = ref.order_by("saved_at", direction="DESCENDING").limit(max_fetch)

    result = []
    for doc in hands_ref.stream():
        d = doc.to_dict()
        d["hand_id"] = doc.id
        if hasattr(d.get("saved_at"), "isoformat"):
            d["saved_at"] = d["saved_at"].isoformat()
        result.append(d)
    return result


def get_hands_stats(uid: str) -> dict:
    """
    Firestore users/{uid}/hands の件数・最古/最新の captured_at を返す。
    COUNT 集計クエリで件数を取得（全件読み取りなし）。
    """
    db = get_db()
    col = db.collection("users").document(uid).collection("hands")

    # COUNT 集計（ドキュメント読み取りコストなし）
    try:
        count_result = col.count().get()
        count = count_result[0][0].value
    except Exception:
        # 旧バージョンの SDK では count() 未対応のためフォールバック
        count = sum(1 for _ in col.order_by("saved_at").limit(9999).stream())

    if count == 0:
        return {"count": 0, "newest": None, "oldest": None}

    # 最新1件・最古1件（各1ドキュメント読み取りのみ）
    newest_docs = list(col.order_by("captured_at", direction="DESCENDING").limit(1).stream())
    oldest_docs = list(col.order_by("captured_at", direction="ASCENDING").limit(1).stream())

    newest = newest_docs[0].to_dict().get("captured_at", "") if newest_docs else None
    oldest = oldest_docs[0].to_dict().get("captured_at", "") if oldest_docs else newest
    return {"count": count, "newest": newest, "oldest": oldest}


def save_hand(uid: str, hand_json: dict, captured_at: str) -> str:
    """
    Firestore users/{uid}/hands/{handId} にリアルタイムハンドを保存する。
    handId = tableId_captured_at（重複防止）
    """
    db = get_db()
    table_id = hand_json.get("tableId", "unknown")
    safe_ts = captured_at.replace(":", "").replace(".", "").replace("-", "")
    hand_id = f"{table_id}_{safe_ts}"

    doc_ref = db.collection("users").document(uid).collection("hands").document(hand_id)
    doc_ref.set({
        "hand_json":   hand_json,
        "captured_at": captured_at,
        "saved_at":    datetime.now(timezone.utc),
    })
    return hand_id


def is_firebase_enabled() -> bool:
    """FIREBASE_SERVICE_ACCOUNT_JSON が設定されているか確認（起動チェック用）"""
    return bool(os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip())
