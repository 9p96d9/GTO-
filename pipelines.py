"""
pipelines.py - バックグラウンドパイプライン関数群
run_pipeline / run_noapi_pipeline / run_classify_pipeline_from_json
run_classify_pipeline / run_pdf_pipeline
"""

import asyncio
import json
import shutil
import subprocess
import sys

from pathlib import Path

from state import (
    jobs, jobs_lock, event_queues,
    BASE_ENV, SCRIPTS, DATA_DIR, DONE_DIR, OUTPUT_DIR,
    STEP_LABELS, STEP_LABELS_NOAPI,
)


async def run_pipeline(job_id: str, txt_path: Path, api_key: str):
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q
    env  = {**BASE_ENV, "GEMINI_API_KEY": api_key}
    logs = []

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def set_step(s):
        with jobs_lock:
            jobs[job_id]["step"] = s
        print(f"[job:{job_id[:8]}] step {s}: {STEP_LABELS[s]}")

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)
        print(f"[job:{job_id[:8]}] ERROR: {msg[:200]}")

    # ── Step 1: パース ──────────────────────────────────────────────────────
    set_step(1)
    json_path = DATA_DIR / (txt_path.stem + ".json")

    def do_parse():
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "parse.py"), str(txt_path), str(json_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_parse)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})

    # ── Step 2: Gemini分析 ──────────────────────────────────────────────────
    set_step(2)
    from analyze import analyze_file as _analyze_file  # noqa: E402

    def do_analyze():
        _analyze_file(str(json_path), progress_cb=push, api_key=api_key)

    try:
        await loop.run_in_executor(None, do_analyze)
    except SystemExit as e:
        if e.code != 0:
            fail("Gemini APIキーが無効またはAPI呼び出しエラーが発生しました")
            return

    # ── Step 3: PDF生成 ─────────────────────────────────────────────────────
    push({"type": "generate_start"})
    set_step(3)

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate.js"), str(OUTPUT_DIR), str(json_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_generate)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    pdf_files = sorted(OUTPUT_DIR.glob("GTO_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdf_files:
        fail("\n".join(logs) + "\nPDFが見つかりません")
        return

    dest = DONE_DIR / txt_path.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(txt_path), str(dest))

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["pdf"]    = pdf_files[0].name

    push({"type": "done", "pdf": pdf_files[0].name})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] 完了: {pdf_files[0].name}")


async def run_noapi_pipeline(job_id: str, txt_path: Path):
    """APIなしモード: parse → classify → generate_noapilist"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q
    env  = BASE_ENV.copy()
    logs = []

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def set_step(s):
        with jobs_lock:
            jobs[job_id]["step"] = s
        print(f"[job:{job_id[:8]}] step {s}: {STEP_LABELS_NOAPI[s]}")

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)
        print(f"[job:{job_id[:8]}] ERROR: {msg[:200]}")

    # ── Step 1: パース ──────────────────────────────────────────────────────
    set_step(1)
    json_path = DATA_DIR / (txt_path.stem + ".json")

    def do_parse():
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "parse.py"), str(txt_path), str(json_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_parse)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})

    # ── Step 2: 分類 ────────────────────────────────────────────────────────
    set_step(2)
    classified_path = DATA_DIR / (txt_path.stem + "_classified.json")

    def do_classify():
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "classify.py"), str(json_path), str(classified_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_classify)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    push({"type": "generate_start"})

    # ── Step 3: PDF生成 ─────────────────────────────────────────────────────
    set_step(3)

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate_noapilist.js"), str(OUTPUT_DIR), str(classified_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    r = await loop.run_in_executor(None, do_generate)
    logs.append(r.stdout.strip())
    if r.stderr.strip():
        logs.append(r.stderr.strip())
    if r.returncode != 0:
        fail("\n".join(logs))
        return

    pdf_files = sorted(OUTPUT_DIR.glob("NoAPI_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdf_files:
        fail("\n".join(logs) + "\nPDFが見つかりません")
        return

    dest = DONE_DIR / txt_path.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(txt_path), str(dest))

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["pdf"]    = pdf_files[0].name

    push({"type": "done", "pdf": pdf_files[0].name})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] 完了: {pdf_files[0].name}")


async def run_classify_pipeline_from_json(job_id: str, json_path: Path):
    """parse済みJSONから直接 classify → Web結果画面（parse.py をスキップ）"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)

    with jobs_lock:
        jobs[job_id]["step"] = 1
    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})

    with jobs_lock:
        jobs[job_id]["step"] = 2
    classified_path = DATA_DIR / (json_path.stem + "_classified.json")

    r = await loop.run_in_executor(None, lambda: subprocess.run(
        [sys.executable, str(SCRIPTS / "classify.py"), str(json_path), str(classified_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
    ))
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["json_path"] = str(json_path)
        jobs[job_id]["classified_path"] = str(classified_path)

    # Phase 8: 解析結果を Firestore に永続化
    with jobs_lock:
        fb_uid = jobs[job_id].get("firebase_uid", "")
    if fb_uid:
        try:
            from scripts.firebase_utils import is_firebase_enabled, save_analysis
            if is_firebase_enabled():
                with open(classified_path, encoding="utf-8") as _f:
                    _classified = json.load(_f)
                has_snap = save_analysis(fb_uid, job_id, _classified)
                print(f"[job:{job_id[:8]}] Firestore保存完了 snapshot={'あり' if has_snap else 'なし(サイズ超過)'}")
        except Exception as _e:
            print(f"[job:{job_id[:8]}] Firestore保存失敗: {_e}")

    push({"type": "classify_done"})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] hands分類完了: {classified_path.name}")


async def run_classify_pipeline(job_id: str, txt_path: Path, hero_name: str = ""):
    """parse → classify → Web結果画面（PDF生成なし）"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def set_step(s):
        with jobs_lock:
            jobs[job_id]["step"] = s
        print(f"[job:{job_id[:8]}] classify step {s}")

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)
        print(f"[job:{job_id[:8]}] ERROR: {msg[:200]}")

    set_step(1)
    json_path = DATA_DIR / (txt_path.stem + ".json")

    r = await loop.run_in_executor(None, lambda: subprocess.run(
        [sys.executable, str(SCRIPTS / "parse.py"), str(txt_path), str(json_path)]
        + (["--hero-name", hero_name] if hero_name else []),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
        timeout=120,
    ))
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    try:
        with open(json_path, encoding="utf-8") as f:
            hands_total = len(json.load(f).get("hands", []))
    except Exception:
        hands_total = 0
    push({"type": "parse_done", "hands_total": hands_total})

    set_step(2)
    classified_path = DATA_DIR / (txt_path.stem + "_classified.json")

    r = await loop.run_in_executor(None, lambda: subprocess.run(
        [sys.executable, str(SCRIPTS / "classify.py"), str(json_path), str(classified_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
    ))
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["json_path"] = str(json_path)
        jobs[job_id]["classified_path"] = str(classified_path)
        fb_uid = jobs[job_id].get("firebase_uid", "")
        fb_sid = jobs[job_id].get("firebase_session_id", "")

    if fb_uid and fb_sid:
        try:
            from scripts.firebase_utils import update_session_status
            update_session_status(fb_uid, fb_sid, "done", job_id=job_id)
        except Exception as e:
            print(f"[job:{job_id[:8]}] Firestore status update failed: {e}")

    push({"type": "classify_done"})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] 分類完了: {classified_path.name}")


async def run_pdf_pipeline(job_id: str, classified_path: str):
    """分類済みJSONからNoAPI PDFのみ生成"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    event_queues[job_id] = q

    def push(data: dict):
        loop.call_soon_threadsafe(q.put_nowait, data)

    def fail(msg):
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["log"] = msg
        push({"type": "error", "message": msg[:300]})
        loop.call_soon_threadsafe(q.put_nowait, None)

    push({"type": "parse_done", "hands_total": 0})
    push({"type": "generate_start"})
    with jobs_lock:
        jobs[job_id]["step"] = 3

    def do_generate():
        return subprocess.run(
            ["node", str(SCRIPTS / "generate_noapilist.js"), str(OUTPUT_DIR), classified_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=BASE_ENV,
        )

    r = await loop.run_in_executor(None, do_generate)
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()); return

    pdf_files = sorted(OUTPUT_DIR.glob("NoAPI_Report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdf_files:
        fail("PDFが見つかりません"); return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["pdf"] = pdf_files[0].name

    push({"type": "done", "pdf": pdf_files[0].name})
    loop.call_soon_threadsafe(q.put_nowait, None)
    print(f"[job:{job_id[:8]}] PDF完了: {pdf_files[0].name}")
