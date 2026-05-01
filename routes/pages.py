"""
routes/pages.py - 現役Webページルート
/ /legacy /classify_progress /classify_result /generate_pdf
/progress /error /report /pdf /download
/stream /status /login /sessions /api/firebase-config /download-extension /api/extension.zip
"""

import asyncio
import io
import json
import os
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from state import jobs, jobs_lock, event_queues, DATA_DIR, ROOT, OUTPUT_DIR
from pipelines import run_classify_pipeline, run_pdf_pipeline
from html_pages.pages import (
    LANDING_PAGE, UPLOAD_PAGE, ERROR_PAGE,
    classify_progress_page, classify_result_page,
    three_d_view_page,
    progress_page, report_page,
    _RESTORE_PAGE_HTML, _LOGIN_PAGE_HTML, _SESSIONS_PAGE_HTML,
    _esc,
)

router = APIRouter()

_FIREBASE_API_KEY     = os.environ.get("FIREBASE_API_KEY", "")
_FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")
_FIREBASE_PROJECT_ID  = os.environ.get("FIREBASE_PROJECT_ID", "")


@router.get("/", response_class=HTMLResponse)
async def index():
    return LANDING_PAGE


@router.get("/legacy", response_class=HTMLResponse)
async def legacy():
    return UPLOAD_PAGE


@router.post("/upload")
async def upload(background_tasks: BackgroundTasks, request: Request):
    from fastapi import UploadFile, Form
    form = await request.form()
    file = form.get("file")
    hero_name = form.get("hero_name", "")
    data = await file.read()
    txt_path = ROOT / "input" / "upload.txt"
    txt_path.write_bytes(data)

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "classify", "hero_name": hero_name}
    background_tasks.add_task(run_classify_pipeline, job_id, txt_path, hero_name)
    return RedirectResponse(f"/classify_progress/{job_id}", status_code=303)


@router.get("/classify_progress/{job_id}", response_class=HTMLResponse)
async def classify_progress(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            return HTMLResponse("<h1>404</h1>", status_code=404)
    return HTMLResponse(classify_progress_page(job_id))


@router.get("/classify_result/{job_id}", response_class=HTMLResponse)
async def classify_result_view(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.get("status") != "done":
        candidate = DATA_DIR / f"{job_id}_classified.json"
        if candidate.exists():
            with jobs_lock:
                jobs[job_id] = {
                    "status": "done",
                    "classified_path": str(candidate),
                    "json_path": str(DATA_DIR / f"{job_id}.json"),
                    "pdf": "", "log": "", "mode": "classify", "hero_name": "",
                }
            job = jobs[job_id]
        else:
            return HTMLResponse(_RESTORE_PAGE_HTML.replace("{job_id}", job_id))

    classified_path = job.get("classified_path", "")
    json_path = job.get("json_path", "")

    try:
        with open(classified_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return HTMLResponse("<h1>データが見つかりません</h1>", status_code=404)

    hands = data.get("hands", [])
    for hand in hands:
        clf = hand.get("bluered_classification", {})
        if clf.get("category") == "value_or_bluff_success":
            clf["category"] = "value_success"
            clf["category_label"] = "バリュー成功"

    blue_count = red_count = pf_count = 0
    categories: dict = {}
    for hand in hands:
        clf = hand.get("bluered_classification", {})
        line = clf.get("line", "")
        label = clf.get("category_label", "")
        if line == "blue":
            blue_count += 1
        elif line == "red":
            red_count += 1
        else:
            pf_count += 1
        if label:
            categories[label] = categories.get(label, 0) + 1

    total_hands = blue_count + red_count + pf_count

    hero_ev_total = 0.0
    hero_name_found = ""
    for hand in hands:
        allin_ev = hand.get("result", {}).get("allin_ev", {})
        if not allin_ev:
            continue
        for p in hand.get("players", []):
            if not p.get("is_hero"):
                continue
            name = p.get("name", "")
            if name in allin_ev:
                ev = float(allin_ev[name])
                actual = float(p.get("result_bb", 0.0))
                hero_ev_total = round(hero_ev_total + (ev - actual), 2)
                hero_name_found = name
    allin_ev_diffs = {hero_name_found: hero_ev_total} if hero_name_found and abs(hero_ev_total) > 0.05 else {}

    return HTMLResponse(classify_result_page(
        job_id, total_hands, blue_count, red_count, pf_count,
        categories, allin_ev_diffs, classified_path, json_path, hands,
    ))


@router.get("/3d_view/{job_id}", response_class=HTMLResponse)
async def three_d_view(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.get("status") != "done":
        candidate = DATA_DIR / f"{job_id}_classified.json"
        if candidate.exists():
            classified_path = str(candidate)
        else:
            return HTMLResponse(_RESTORE_PAGE_HTML.replace("{job_id}", job_id))
    else:
        classified_path = job.get("classified_path", "")

    try:
        with open(classified_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return HTMLResponse("<h1>データが見つかりません</h1>", status_code=404)

    hands = data.get("hands", [])
    return HTMLResponse(three_d_view_page(job_id, hands))


@router.post("/generate_pdf/{job_id}")
async def generate_pdf(job_id: str, background_tasks: BackgroundTasks):
    with jobs_lock:
        src_job = jobs.get(job_id)
    if not src_job:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    classified_path = src_job.get("classified_path", "")
    if not classified_path or not Path(classified_path).exists():
        return HTMLResponse(ERROR_PAGE.format(log="分類データが見つかりません"), status_code=400)

    new_job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[new_job_id] = {"step": 0, "status": "running", "pdf": "", "log": "", "mode": "noapi"}
    background_tasks.add_task(run_pdf_pipeline, new_job_id, classified_path)
    return RedirectResponse(f"/progress/{new_job_id}", status_code=303)


@router.get("/stream/{job_id}")
async def stream(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)

    async def immediate_done():
        if job["status"] == "done":
            yield {"data": json.dumps({"type": "done", "pdf": job["pdf"]}, ensure_ascii=False)}
        elif job["status"] == "error":
            yield {"data": json.dumps({"type": "error", "message": job.get("log", "不明なエラー")[:300]}, ensure_ascii=False)}

    if job["status"] in ("done", "error"):
        return EventSourceResponse(immediate_done())

    q = event_queues.get(job_id)
    if not q:
        return JSONResponse({"error": "stream not ready"}, status_code=503)

    async def event_generator():
        try:
            while True:
                data = await asyncio.wait_for(q.get(), timeout=60.0)
                if data is None:
                    break
                yield {"data": json.dumps(data, ensure_ascii=False)}
        except asyncio.TimeoutError:
            yield {"data": json.dumps({"type": "error", "message": "タイムアウト"}, ensure_ascii=False)}
        finally:
            event_queues.pop(job_id, None)

    return EventSourceResponse(event_generator())


@router.get("/status/{job_id}")
async def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "error", "log": "job not found"})
    return JSONResponse(job)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_LOGIN_PAGE_HTML)


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page():
    return HTMLResponse(_SESSIONS_PAGE_HTML)


@router.get("/api/firebase-config")
async def firebase_config():
    return JSONResponse({
        "apiKey":     _FIREBASE_API_KEY,
        "authDomain": _FIREBASE_AUTH_DOMAIN,
        "projectId":  _FIREBASE_PROJECT_ID,
    })


@router.get("/download-extension")
@router.get("/api/extension.zip")
async def download_extension():
    ext_dir = ROOT / "extension"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(ext_dir.rglob("*")):
            if fpath.is_file() and fpath.name != "README.md":
                zf.write(fpath, fpath.relative_to(ext_dir).as_posix())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=pokergto-extension-v2.2.0.zip"},
    )


# ─── PDF生成フロー（旧 legacy.py より移動） ─────────────────────────────────
@router.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            return HTMLResponse("<h1>404</h1>", status_code=404)
        mode = jobs[job_id].get("mode", "api")
    return progress_page(job_id, mode)


@router.get("/error/{job_id}", response_class=HTMLResponse)
async def error_view(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id, {})
    return HTMLResponse(ERROR_PAGE.format(log=_esc(job.get("log", "不明なエラー"))), status_code=500)


@router.get("/report/{name}", response_class=HTMLResponse)
async def report(name: str):
    fpath = OUTPUT_DIR / name
    if not fpath.exists() or fpath.suffix != ".pdf":
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return report_page(name)


@router.get("/pdf/{name}")
async def serve_pdf(name: str):
    fpath = OUTPUT_DIR / name
    if not fpath.exists() or fpath.suffix != ".pdf":
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return FileResponse(fpath, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@router.get("/download/{name}")
async def download_pdf(name: str):
    fpath = OUTPUT_DIR / name
    if not fpath.exists() or fpath.suffix != ".pdf":
        return HTMLResponse("<h1>404</h1>", status_code=404)
    return FileResponse(fpath, media_type="application/pdf", filename=name)
