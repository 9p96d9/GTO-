"""
routes/cart.py - 解析カートAPI（Phase 12）
GET/POST /api/cart/{job_id} /api/cart/{job_id}/hands /api/cart/{job_id}/analyze
"""

import json
import os
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from state import jobs, jobs_lock
from routes.deps import get_uid_from_request

router = APIRouter()


@router.get("/api/cart/{job_id}")
async def api_get_cart(job_id: str, request: Request):
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    from scripts.firebase_utils import is_firebase_enabled, get_cart, get_gemini_results
    if not is_firebase_enabled():
        return JSONResponse({"hand_numbers": [], "gemini_results": {}})
    return JSONResponse({
        "hand_numbers": get_cart(uid, job_id),
        "gemini_results": get_gemini_results(uid, job_id),
    })


@router.post("/api/cart/{job_id}/hands")
async def api_update_cart(job_id: str, request: Request):
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    body = await request.json()
    hand_numbers = [int(n) for n in body.get("hand_numbers", [])]
    from scripts.firebase_utils import is_firebase_enabled, update_cart
    if is_firebase_enabled():
        update_cart(uid, job_id, hand_numbers)
    return JSONResponse({"ok": True, "hand_numbers": hand_numbers})


@router.post("/api/cart/{job_id}/save")
async def api_save_cart(job_id: str, request: Request):
    """名前付き保存（廃止予定・後方互換のため残存）"""
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    body = await request.json()
    name         = body.get("name", "")
    hand_numbers = [int(n) for n in body.get("hand_numbers", [])]
    from scripts.firebase_utils import is_firebase_enabled, save_cart_snapshot
    if not is_firebase_enabled():
        return JSONResponse({"ok": False, "error": "Firebase未設定"})
    cart_id = save_cart_snapshot(uid, job_id, name, hand_numbers)
    return JSONResponse({"ok": True, "cart_id": cart_id})


@router.get("/api/carts")
async def api_list_carts(request: Request, job_id: str = None):
    """保存済みカート一覧（廃止予定・後方互換のため残存）"""
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    from scripts.firebase_utils import is_firebase_enabled, list_saved_carts
    if not is_firebase_enabled():
        return JSONResponse({"carts": []})
    return JSONResponse({"carts": list_saved_carts(uid, job_id)})


@router.post("/api/cart/{job_id}/analyze")
async def api_analyze_cart(job_id: str, request: Request):
    """カート内ハンドを Gemini で解析し SSE で結果を逐次返却"""
    try:
        uid = get_uid_from_request(request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    from scripts.firebase_utils import (
        is_firebase_enabled, get_cart, get_user_settings,
        get_analysis, save_gemini_results,
    )
    if not is_firebase_enabled():
        return JSONResponse({"error": "Firebase未設定"}, status_code=503)

    settings = get_user_settings(uid)
    api_key  = (settings.get("encrypted_api_key") or "").strip()
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(
            {"error": "Gemini APIキーが設定されていません。カートの設定から登録してください。"},
            status_code=400,
        )

    hand_numbers = get_cart(uid, job_id)
    if not hand_numbers:
        return JSONResponse({"error": "カートが空です"}, status_code=400)

    classified_data = None
    with jobs_lock:
        job = jobs.get(job_id)
    if job and job.get("classified_path"):
        try:
            with open(job["classified_path"], encoding="utf-8") as f:
                classified_data = json.load(f)
        except Exception:
            pass

    if not classified_data:
        analysis = get_analysis(uid, job_id)
        if analysis and analysis.get("classified_snapshot"):
            try:
                classified_data = json.loads(analysis["classified_snapshot"])
            except Exception:
                pass

    if not classified_data:
        return JSONResponse(
            {"error": "解析データが見つかりません。ページをリロードしてください。"},
            status_code=404,
        )

    hand_map = {
        h.get("hand_number"): h
        for h in classified_data.get("hands", [])
        if h.get("hand_number") is not None
    }
    cart_hands = [(n, hand_map[n]) for n in hand_numbers if n in hand_map]
    if not cart_hands:
        return JSONResponse({"error": "カートのハンドが見つかりません"}, status_code=400)

    import asyncio as _asyncio
    from scripts.analyze2 import evaluate_batch, BATCH_SIZE, detect_provider, make_client, PROVIDERS, MODE

    provider = detect_provider(api_key)
    model    = PROVIDERS[provider]["model"]
    total    = len(cart_hands)
    batches  = [cart_hands[i:i + BATCH_SIZE] for i in range(0, len(cart_hands), BATCH_SIZE)]

    async def generate():
        client = make_client(provider, api_key)
        all_results: dict = {}
        done_count = 0
        loop = _asyncio.get_event_loop()

        for batch in batches:
            try:
                result_map = await loop.run_in_executor(
                    None, evaluate_batch, client, model, batch, MODE
                )
            except Exception as e:
                key_hint = f"（キー末尾: ...{api_key[-4:]}）" if api_key else ""
                yield {"data": json.dumps({"type": "error", "message": str(e)[:300] + key_hint}, ensure_ascii=False)}
                return

            batch_results = []
            for hnum, hand in batch:
                text     = result_map.get(hnum, "評価エラー")
                category = hand.get("bluered_classification", {}).get("category", "")
                all_results[str(hnum)] = {"text": text, "category": category}
                batch_results.append({"hand_number": hnum, "text": text, "category": category})
                done_count += 1

            yield {"data": json.dumps({
                "type": "batch",
                "results": batch_results,
                "done": done_count,
                "total": total,
            }, ensure_ascii=False)}

        try:
            await loop.run_in_executor(None, save_gemini_results, uid, job_id, all_results)
        except Exception as e:
            print(f"[WARN] gemini_results 保存失敗: {e}", file=sys.stderr)

        yield {"data": json.dumps({"type": "done", "total": total}, ensure_ascii=False)}

    return EventSourceResponse(generate())
