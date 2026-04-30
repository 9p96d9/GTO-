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
    gzip+base64 圧縮後に 900KB 以下の場合はスナップショットを保存し True を返す。
    超過する場合はメタデータのみ保存して False を返す。
    """
    import json as _json
    import gzip as _gzip
    import base64 as _b64
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

    raw_bytes    = _json.dumps(classified_data, ensure_ascii=False).encode("utf-8")
    compressed   = _b64.b64encode(_gzip.compress(raw_bytes, compresslevel=9)).decode("ascii")
    has_snapshot = len(compressed.encode("ascii")) <= 900_000

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
        doc_data["classified_snapshot"]  = compressed
        doc_data["snapshot_encoding"]    = "gzip_b64"

    db.collection("users").document(uid).collection("analyses").document(job_id).set(doc_data)
    return has_snapshot


def get_analysis(uid: str, job_id: str) -> dict | None:
    """Firestore から解析結果を取得する（classified_snapshot を含む・gzip_b64 は自動解凍）。"""
    import gzip as _gzip
    import base64 as _b64
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
    # gzip+base64 圧縮スナップショットを透過的に解凍（後方互換: encoding なし = 旧来の生JSON）
    if d.get("snapshot_encoding") == "gzip_b64" and d.get("classified_snapshot"):
        d["classified_snapshot"] = _gzip.decompress(
            _b64.b64decode(d["classified_snapshot"])
        ).decode("utf-8")
    return d


def get_analyses(uid: str, limit: int = 20) -> list[dict]:
    """Firestore から解析一覧を取得する（最新順、snapshot は除外）。
    classified_snapshot / gemini_results はフィールドマスクで除外し転送量を削減する。
    """
    db = get_db()
    _META_FIELDS = [
        "job_id", "created_at", "hand_count", "blue_count", "red_count", "pf_count",
        "snapshot_encoding", "active_cart",
    ]
    docs = (
        db.collection("users").document(uid).collection("analyses")
        .order_by("created_at", direction="DESCENDING")
        .limit(limit)
        .select(_META_FIELDS)
        .stream()
    )
    result = []
    for doc in docs:
        d = doc.to_dict()
        # snapshot_encoding がある → 新形式スナップショットあり
        # ない → スナップショットなし or 旧形式（旧形式は再表示不可として扱う）
        d["has_snapshot"] = "snapshot_encoding" in d
        d.pop("snapshot_encoding", None)
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return result


def delete_analysis(uid: str, job_id: str) -> None:
    """Firestore から解析ドキュメントを削除する。"""
    db = get_db()
    db.collection("users").document(uid).collection("analyses").document(job_id).delete()


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


# ─── ユーザー設定（Phase 12） ────────────────────────────────────────────────

def get_user_settings(uid: str) -> dict:
    """users/{uid}/settings/gemini を取得。存在しない場合は空 dict を返す"""
    _init()
    doc = _db.collection("users").document(uid).collection("settings").document("gemini").get()
    if not doc.exists:
        return {}
    return doc.to_dict()


def save_user_settings(uid: str, api_key: str = None, needs_api_auto_cart: bool = None):
    """users/{uid}/settings/gemini を更新（指定フィールドのみ上書き）"""
    _init()
    update: dict = {}
    if api_key is not None:
        update["encrypted_api_key"] = api_key  # Firebase が保存時に暗号化
    if needs_api_auto_cart is not None:
        update["needs_api_auto_cart"] = needs_api_auto_cart
    if update:
        _db.collection("users").document(uid).collection("settings").document("gemini").set(
            update, merge=True
        )


# ─── 管理者ダッシュボード（Phase 5） ────────────────────────────────────────

def get_admin_summary() -> dict:
    """
    全ユーザー横断のサマリーを返す。
    count() Aggregate API を使い Firestore 読み取りコストをゼロに抑える。
    """
    _init()
    from firebase_admin import auth as _auth

    # 総ユーザー数（Auth API、Firestore 読み取りなし）
    total_users = 0
    page = _auth.list_users()
    while page:
        total_users += len(page.users)
        page = page.get_next_page()

    # 全ユーザー横断のハンド数・解析数（Aggregate API = 読み取りコストなし）
    try:
        hands_count_result = _db.collection_group("hands").count().get()
        total_hands = hands_count_result[0][0].value
    except Exception:
        total_hands = None  # SDK未対応環境

    try:
        analyses_count_result = _db.collection_group("analyses").count().get()
        total_analyses = analyses_count_result[0][0].value
    except Exception:
        total_analyses = None

    # 直近7日のアクティブユーザー数（saved_at >= 7日前 のハンドを持つユーザー）
    try:
        from datetime import timedelta
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        active_docs = (
            _db.collection_group("hands")
            .where("saved_at", ">=", since_7d)
            .select(["__name__"])
            .stream()
        )
        # パスから uid を抽出して重複排除: users/{uid}/hands/{handId}
        active_uids = set()
        for doc in active_docs:
            parts = doc.reference.path.split("/")
            if len(parts) >= 2:
                active_uids.add(parts[1])
        active_users_7d = len(active_uids)
    except Exception:
        active_users_7d = None

    return {
        "total_users": total_users,
        "total_hands": total_hands,
        "total_analyses": total_analyses,
        "active_users_7d": active_users_7d,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_admin_users() -> list[dict]:
    """
    全ユーザーの一覧を返す。
    ハンド数・解析数は count() で取得（読み取りコストなし）。
    PF スコア平均は直近 10 件の analyses から計算（最大 10 読み取り/ユーザー）。
    """
    _init()
    from firebase_admin import auth as _auth

    # Firebase Auth から全ユーザー取得
    auth_users = {}
    page = _auth.list_users()
    while page:
        for u in page.users:
            auth_users[u.uid] = {
                "uid": u.uid,
                "email": u.email or "",
                "last_login": getattr(u.user_metadata, "last_sign_in_time", None) or getattr(u.user_metadata, "last_refresh_time", None),
            }
        page = page.get_next_page()

    result = []
    for uid, info in auth_users.items():
        user_ref = _db.collection("users").document(uid)

        # ハンド数（count() = 読み取りなし）
        try:
            hand_count = user_ref.collection("hands").count().get()[0][0].value
        except Exception:
            hand_count = 0

        # 解析数（count() = 読み取りなし）
        try:
            analysis_count = user_ref.collection("analyses").count().get()[0][0].value
        except Exception:
            analysis_count = 0

        # PF スコア平均（直近 10 件 × 3フィールドのみ取得）
        avg_pf = None
        try:
            recent = list(
                user_ref.collection("analyses")
                .order_by("created_at", direction="DESCENDING")
                .limit(10)
                .select(["hand_count", "pf_count"])
                .stream()
            )
            scores = []
            for doc in recent:
                d = doc.to_dict()
                hc = d.get("hand_count") or 0
                pf = d.get("pf_count") or 0
                if hc > 0:
                    scores.append(round((hc - pf) / hc * 100, 1))
            if scores:
                avg_pf = round(sum(scores) / len(scores), 1)
        except Exception:
            pass

        # APIキー設定の有無
        try:
            settings_doc = user_ref.collection("settings").document("gemini").get()
            has_api_key = settings_doc.exists and bool(
                settings_doc.to_dict().get("encrypted_api_key")
            )
        except Exception:
            has_api_key = False

        # last_login を ISO 文字列に変換（ms epoch → datetime）
        last_login_iso = None
        if info["last_login"]:
            try:
                last_login_iso = datetime.fromtimestamp(
                    info["last_login"] / 1000, tz=timezone.utc
                ).isoformat()
            except Exception:
                pass

        result.append({
            "uid": uid,
            "email": info["email"],
            "last_login": last_login_iso,
            "hand_count": hand_count,
            "analysis_count": analysis_count,
            "has_api_key": has_api_key,
            "avg_pf_score": avg_pf,
        })

    # ハンド数降順でソート
    result.sort(key=lambda x: x["hand_count"], reverse=True)
    return result


# ─── Gemini 解析結果（Phase 12） ────────────────────────────────────────────

def get_gemini_results(uid: str, job_id: str) -> dict:
    """analyses/{job_id} の gemini_results を取得。存在しない場合は空 dict"""
    _init()
    doc = _db.collection("users").document(uid).collection("analyses").document(job_id).get()
    if not doc.exists:
        return {}
    return doc.to_dict().get("gemini_results") or {}


def save_gemini_results(uid: str, job_id: str, results: dict):
    """gemini_results を analyses/{job_id} にマージ保存"""
    _init()
    _db.collection("users").document(uid).collection("analyses").document(job_id).set(
        {"gemini_results": results}, merge=True
    )


def get_admin_analytics() -> dict:
    """PostgreSQL専用機能。Firebaseモードでは空データを返す。"""
    from datetime import datetime, timezone
    return {
        "user_stats": [],
        "worsened_users": [],
        "firebase_mode": True,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
