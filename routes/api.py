"""
routes/api.py - /api/* エンドポイント
/api/hands/* /api/analyses/* /api/sessions/* /api/user/settings
/api/upload-from-extension
"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from state import jobs, jobs_lock, DATA_DIR, ROOT, INPUT_DIR, limiter
from pipelines import run_classify_pipeline, run_classify_pipeline_from_json
from routes.deps import get_uid_from_request

router = APIRouter()


@router.post("/api/upload-from-extension")
async def upload_from_extension(request: Request):
    from scripts.db import is_firebase_enabled, save_session
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSONパース失敗"}, status_code=400)

    raw_text   = body.get("raw_text", "").strip()
    filename   = body.get("filename", "upload.txt")
    hand_count = int(body.get("hand_count", 0))
    if not raw_text:
        return JSONResponse({"error": "raw_text が空です"}, status_code=400)
    try:
        session_id = save_session(uid, raw_text, filename, hand_count)
    except Exception as e:
        return JSONResponse({"error": f"Firestore保存失敗: {e}"}, status_code=500)
    return JSONResponse({"session_id": session_id, "status": "saved"})


@router.get("/api/sessions")
async def api_sessions(request: Request):
    from scripts.db import is_firebase_enabled, get_sessions
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        sessions = get_sessions(uid)
    except Exception as e:
        return JSONResponse({"error": f"Firestore取得失敗: {e}"}, status_code=500)
    return JSONResponse({"sessions": sessions})


@router.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str, request: Request):
    from scripts.db import is_firebase_enabled, delete_session
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        delete_session(uid, session_id)
    except Exception as e:
        return JSONResponse({"error": f"削除失敗: {e}"}, status_code=500)
    return JSONResponse({"status": "deleted"})


@router.post("/api/sessions/{session_id}/analyze")
async def api_analyze_session(session_id: str, request: Request, background_tasks: BackgroundTasks):
    from scripts.db import is_firebase_enabled, get_session, update_session_status
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        session = get_session(uid, session_id)
    except Exception as e:
        return JSONResponse({"error": f"Firestore取得失敗: {e}"}, status_code=500)
    if not session:
        return JSONResponse({"error": "セッションが見つかりません"}, status_code=404)

    raw_text = session.get("raw_text", "")
    if not raw_text:
        return JSONResponse({"error": "raw_text が空です"}, status_code=400)

    txt_path = INPUT_DIR / f"fb_{session_id}.txt"
    txt_path.write_text(raw_text, encoding="utf-8")
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "step": 0, "status": "running", "pdf": "", "log": "",
            "mode": "classify", "firebase_uid": uid, "firebase_session_id": session_id,
        }
    try:
        update_session_status(uid, session_id, "analyzing")
    except Exception:
        pass
    background_tasks.add_task(run_classify_pipeline, job_id, txt_path)
    return JSONResponse({"job_id": job_id, "progress_url": f"/classify_progress/{job_id}"})


@router.post("/api/sessions/analyze-multi")
async def api_analyze_multi(request: Request, background_tasks: BackgroundTasks):
    from scripts.db import is_firebase_enabled, get_session
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSONパース失敗"}, status_code=400)

    session_ids = body.get("session_ids", [])
    if not session_ids:
        return JSONResponse({"error": "session_idsが空です"}, status_code=400)

    combined = []
    for sid in session_ids:
        try:
            session = get_session(uid, sid)
        except Exception:
            continue
        if session and session.get("raw_text"):
            combined.append(session["raw_text"].strip())
    if not combined:
        return JSONResponse({"error": "有効なセッションが見つかりません"}, status_code=404)

    raw_text = "\n\n".join(combined)
    job_id   = uuid.uuid4().hex
    txt_path = INPUT_DIR / f"multi_{job_id}.txt"
    txt_path.write_text(raw_text, encoding="utf-8")
    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "classify"}
    background_tasks.add_task(run_classify_pipeline, job_id, txt_path)
    return JSONResponse({"job_id": job_id, "progress_url": f"/classify_progress/{job_id}"})


@router.post("/api/sessions/download-text")
async def api_download_text(request: Request):
    import io
    from fastapi.responses import StreamingResponse
    from datetime import date
    from scripts.db import is_firebase_enabled, get_session
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSONパース失敗"}, status_code=400)

    session_ids = body.get("session_ids", [])
    if not session_ids:
        return JSONResponse({"error": "session_idsが空です"}, status_code=400)

    combined = []
    for sid in session_ids:
        try:
            session = get_session(uid, sid)
        except Exception:
            continue
        if session and session.get("raw_text"):
            combined.append(session["raw_text"].strip())
    if not combined:
        return JSONResponse({"error": "有効なセッションが見つかりません"}, status_code=404)

    filename = f"t4_hands_combined_{date.today().strftime('%Y%m%d')}.txt"
    content  = "\n\n".join(combined).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/hands/stats")
async def api_hands_stats(request: Request):
    from scripts.db import is_firebase_enabled, get_hands_stats
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        stats = get_hands_stats(uid)
    except Exception as e:
        return JSONResponse({"error": f"Firestore取得失敗: {e}"}, status_code=500)
    return JSONResponse(stats)


@router.post("/api/hands/analyze")
@limiter.limit("10/minute")
async def api_hands_analyze(request: Request, background_tasks: BackgroundTasks):
    from scripts.db import is_firebase_enabled, get_hands
    from scripts.hand_converter import convert_hands_batch
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}

    limit       = int(body.get("limit", 500))
    since_hours = int(body.get("since_hours", 0))
    since_iso   = body.get("since_iso", "")
    if not since_iso and since_hours > 0:
        from datetime import datetime, timezone, timedelta
        since_dt  = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        since_iso = since_dt.isoformat()

    try:
        hands_data = get_hands(uid, limit=limit, since_iso=since_iso)
    except Exception as e:
        return JSONResponse({"error": f"Firestore取得失敗: {e}"}, status_code=500)

    fetched_count = len(hands_data)
    print(f"[analyze] uid={uid[:8]}... fetched={fetched_count} limit={limit} since_iso={since_iso!r}")
    if not hands_data:
        return JSONResponse({"error": "保存済みハンドがありません"}, status_code=404)

    try:
        parsed_data = convert_hands_batch(hands_data)
    except Exception as e:
        return JSONResponse({"error": f"変換失敗: {e}"}, status_code=500)

    converted_count = len(parsed_data.get("hands", []))
    print(f"[analyze] converted={converted_count} (dropped={fetched_count - converted_count})")

    job_id = uuid.uuid4().hex
    json_path = DATA_DIR / f"{job_id}.json"
    json_path.write_text(json.dumps(parsed_data, ensure_ascii=False), encoding="utf-8")

    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "classify", "hero_name": "", "firebase_uid": uid}
    background_tasks.add_task(run_classify_pipeline_from_json, job_id, json_path)
    return JSONResponse({"job_id": job_id, "progress_url": f"/classify_progress/{job_id}"})


@router.post("/api/hands/realtime")
@limiter.limit("120/minute")
async def api_hands_realtime(request: Request):
    from scripts.db import is_firebase_enabled, save_hand
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSONパース失敗"}, status_code=400)

    hand_json   = body.get("hand_json")
    captured_at = body.get("captured_at", "")
    if not hand_json:
        return JSONResponse({"error": "hand_json が空です"}, status_code=400)
    try:
        hand_id = save_hand(uid, hand_json, captured_at)
    except Exception as e:
        return JSONResponse({"error": f"Firestore保存失敗: {e}"}, status_code=500)
    return JSONResponse({"ok": True, "hand_id": hand_id})


@router.get("/api/debug/hand-sample")
async def api_debug_hand_sample(request: Request):
    """ポストフロップあり最新ハンドのactionHistory＋変換後streets（BET額確認用・認証必須）"""
    from scripts.db import is_firebase_enabled, get_db
    from scripts.hand_converter import convert_hand_json
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    db = get_db()
    docs = list(db.collection("users").document(uid).collection("hands")
                .order_by("saved_at", direction="DESCENDING").limit(30).stream())
    if not docs:
        return JSONResponse({"error": "ハンドなし"})
    # ポストフロップあり（flop が存在する）ハンドを優先して返す
    target = None
    for doc in docs:
        d = doc.to_dict()
        hj = d.get("hand_json", {})
        ah = hj.get("actionHistory", [])
        if any("FLOP" in line for line in ah):
            target = d
            break
    if not target:
        target = docs[0].to_dict()
    hj = target.get("hand_json", {})
    # 変換後 streets も確認
    try:
        converted = convert_hand_json(hj, target.get("captured_at", ""), hand_index=1)
        streets_debug = {
            st: [
                {"pos": a.get("position"), "act": a.get("action"), "amt": a.get("amount_bb")}
                for a in (v if isinstance(v, list) else v.get("actions", []))
            ]
            for st, v in converted.get("streets", {}).items() if v
        }
    except Exception as e:
        streets_debug = {"error": str(e)}
    return JSONResponse({
        "actionHistory": hj.get("actionHistory", []),
        "streets_converted": streets_debug,
    })


@router.get("/api/analyses")
async def api_analyses_list(request: Request):
    from scripts.db import is_firebase_enabled, get_analyses
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        analyses = get_analyses(uid, limit=20)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"analyses": analyses})


@router.delete("/api/analyses/{job_id}")
async def api_analyses_delete(job_id: str, request: Request):
    from scripts.db import is_firebase_enabled, delete_analysis
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        delete_analysis(uid, job_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


@router.post("/api/analyses/{job_id}/restore")
async def api_analyses_restore(job_id: str, request: Request):
    from scripts.db import is_firebase_enabled, get_analysis
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)
    try:
        uid = get_uid_from_request(request)
    except Exception as e:
        return JSONResponse({"error": f"認証失敗: {e}"}, status_code=401)
    try:
        analysis = get_analysis(uid, job_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not analysis:
        return JSONResponse({"error": "解析結果が見つかりません"}, status_code=404)

    snapshot = analysis.get("classified_snapshot")
    if not snapshot:
        return JSONResponse({"error": "スナップショットが保存されていません（データが大きすぎます）"}, status_code=404)

    classified_path = DATA_DIR / f"{job_id}_classified.json"
    json_path       = DATA_DIR / f"{job_id}.json"
    classified_path.write_text(snapshot, encoding="utf-8")

    with jobs_lock:
        jobs[job_id] = {
            "status": "done",
            "classified_path": str(classified_path),
            "json_path": str(json_path),
            "pdf": "", "log": "", "mode": "classify", "hero_name": "",
        }
    return JSONResponse({"status": "ok"})


@router.get("/api/user/settings")
async def api_get_user_settings(request: Request):
    from scripts.db import is_firebase_enabled, get_user_settings
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    if not is_firebase_enabled():
        return JSONResponse({"has_key": False, "needs_api_auto_cart": True})
    settings = get_user_settings(uid)
    api_key  = settings.get("encrypted_api_key", "")
    has_key  = bool(api_key)
    key_masked = ("****" + api_key[-4:]) if has_key and len(api_key) >= 4 else ("*" * len(api_key) if has_key else "")
    return JSONResponse({
        "has_key": has_key,
        "key_masked": key_masked,
        "needs_api_auto_cart": settings.get("needs_api_auto_cart", True),
    })


@router.put("/api/user/settings")
async def api_put_user_settings(request: Request):
    from scripts.db import is_firebase_enabled, save_user_settings
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    if not is_firebase_enabled():
        return JSONResponse({"ok": False, "error": "Firebase未設定"})
    body = await request.json()
    api_key             = body.get("api_key")
    needs_api_auto_cart = body.get("needs_api_auto_cart")
    save_user_settings(uid, api_key=api_key, needs_api_auto_cart=needs_api_auto_cart)
    return JSONResponse({"ok": True})
