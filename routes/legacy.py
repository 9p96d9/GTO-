"""
routes/legacy.py - 旧フロールート（将来削除予定・隔離区画）
/scrape_upload /progress /error /report /pdf /download
/analyze/quick /dashboard
"""

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from state import jobs, jobs_lock, event_queues, INPUT_DIR, OUTPUT_DIR
from pipelines import run_pipeline
from html.pages import ERROR_PAGE, progress_page, report_page, dashboard_page, _esc
from state import quick_results

router = APIRouter()


@router.post("/scrape_upload")
async def scrape_upload(background_tasks: BackgroundTasks, request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text is empty"}, status_code=400)
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return JSONResponse({"error": "GEMINI_API_KEY not set on server"}, status_code=500)
    txt_path = INPUT_DIR / "upload.txt"
    txt_path.write_text(text, encoding="utf-8")
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"step": 0, "status": "running", "pdf": "", "log": ""}
    background_tasks.add_task(run_pipeline, job_id, txt_path, key)
    return JSONResponse({"job_id": job_id})


@router.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            return HTMLResponse("<h1>404</h1>", status_code=404)
        mode = jobs[job_id].get("mode", "api")
    return progress_page(job_id, mode)


@router.get("/error/{job_id}", response_class=HTMLResponse)
async def error_page(job_id: str):
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


@router.post("/analyze/quick")
async def analyze_quick(request: Request):
    from fastapi import UploadFile
    from state import ROOT
    form = await request.form()
    file = form.get("file")
    data = await file.read()
    txt_path = ROOT / "input" / "quick_upload.txt"
    txt_path.write_bytes(data)

    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from parse import parse_file as _parse_file
    from quick_analyzer import compute_quick_stats as _quick_stats

    loop = asyncio.get_running_loop()

    def do_work():
        parsed = _parse_file(str(txt_path))
        return _quick_stats(parsed)

    try:
        result = await loop.run_in_executor(None, do_work)
    except Exception as e:
        return HTMLResponse(ERROR_PAGE.format(log=_esc(str(e))), status_code=500)

    if "error" in result:
        return HTMLResponse(ERROR_PAGE.format(log=_esc(result["error"])), status_code=400)

    job_id = uuid.uuid4().hex
    quick_results[job_id] = result
    return RedirectResponse(f"/dashboard/{job_id}", status_code=303)


@router.get("/dashboard/{job_id}", response_class=HTMLResponse)
async def dashboard(job_id: str):
    result = quick_results.get(job_id)
    if not result:
        return HTMLResponse("<h1>404: セッションが見つかりません。再度アップロードしてください。</h1>", status_code=404)
    return dashboard_page(result)
