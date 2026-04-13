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


def save_analysis(uid: str, job_id: str, classified_data: dict) -> bool:
    """
    解析結果を Firestore users/{uid}/analyses/{job_id} に保存する。
    classified_snapshot が 900KB 以下の場合は全データを保存し True を返す。
    超過する場合はメタデータのみ保存して False を返す。
    """
    import json as _json
    db = get_db()

    hands = classified_data.get("hands", [])
    blue  = sum(1 for h in hands if h.get("bluered_classification", {}).get("line") == "blue")
    red   = sum(1 for h in hands if h.get("bluered_classification", {}).get("line") == "red")
    pf    = len(hands) - blue - red

    categories: dict = {}
    for hand in hands:
        label = hand.get("bluered_classification", {}).get("category_label", "")
        if label:
            categories[label] = categories.get(label, 0) + 1

    snapshot     = _json.dumps(classified_data, ensure_ascii=False)
    has_snapshot = len(snapshot.encode("utf-8")) <= 900_000

    doc_data: dict = {
        "job_id":     job_id,
        "created_at": datetime.now(timezone.utc),
        "hand_count": len(hands),
        "blue_count": blue,
        "red_count":  red,
        "pf_count":   pf,
        "categories": categories,
    }
    if has_snapshot:
        doc_data["classified_snapshot"] = snapshot

    db.collection("users").document(uid).collection("analyses").document(job_id).set(doc_data)
    return has_snapshot


def get_analysis(uid: str, job_id: str) -> dict | None:
    """Firestore から解析結果を取得する（classified_snapshot を含む）。"""
    db = get_db()
    doc = (
        db.collection("users").document(uid)
        .collection("analyses").document(job_id)
        .get()
    )
    if not doc.exists:
        return None
    d = doc.to_dict()
    if hasattr(d.get("created_at"), "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    return d


def get_analyses(uid: str, limit: int = 20) -> list[dict]:
    """Firestore から解析一覧を取得する（最新順、snapshot は除外）。"""
    db = get_db()
    docs = (
        db.collection("users").document(uid).collection("analyses")
        .order_by("created_at", direction="DESCENDING")
        .limit(limit)
        .stream()
    )
    result = []
    for doc in docs:
        d = doc.to_dict()
        d.pop("classified_snapshot", None)
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return result


def is_firebase_enabled() -> bool:
    """FIREBASE_SERVICE_ACCOUNT_JSON が設定されているか確認（起動チェック用）"""
    return bool(os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip())


# ─── 解析カート（Phase 12） ────────────────────────────────────────────────────

def get_cart(uid: str, job_id: str) -> list:
    """アクティブカートの hand_number 配列を返す"""
    _init()
    doc = _db.collection("users").document(uid).collection("analyses").document(job_id).get()
    if not doc.exists:
        return []
    return doc.to_dict().get("active_cart", [])


def update_cart(uid: str, job_id: str, hand_numbers: list):
    """アクティブカートを更新（Firestoreへ即時反映）"""
    _init()
    _db.collection("users").document(uid).collection("analyses").document(job_id).set(
        {"active_cart": hand_numbers}, merge=True
    )


def save_cart_snapshot(uid: str, job_id: str, name: str, hand_numbers: list) -> str:
    """カートを名前付きで保存。cart_id を返す"""
    import uuid as _uuid
    _init()
    cart_id = _uuid.uuid4().hex
    label = name.strip() or datetime.now(timezone.utc).strftime("カート %Y-%m-%d %H:%M")
    _db.collection("users").document(uid).collection("carts").document(cart_id).set({
        "job_id":       job_id,
        "name":         label,
        "created_at":   datetime.now(timezone.utc),
        "hand_numbers": hand_numbers,
        "status":       "saved",
    })
    return cart_id


def list_saved_carts(uid: str, job_id: str = None) -> list:
    """保存済みカート一覧（最新20件）。job_id 指定でフィルタ可能"""
    _init()
    q = _db.collection("users").document(uid).collection("carts") \
        .order_by("created_at", direction="DESCENDING").limit(20)
    result = []
    for c in q.stream():
        d = c.to_dict()
        d["cart_id"] = c.id
        if job_id and d.get("job_id") != job_id:
            continue
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return result
