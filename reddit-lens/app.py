"""app.py â€” Reddit Lens Flask application."""

import atexit
import collections
import importlib.metadata as _imd
import json
import logging
import os
import platform
import signal
import socket
import sqlite3
import threading
import time
import traceback
import webbrowser
import uuid
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request, Response

from db import init_db, insert_new_posts, get_stats, get_recent_posts, get_posts_for_export
from scraper import (
    fetch_all,
    suggest_subreddits,
    suggest_by_overlap,
    suggest_by_semantic,
    suggest_by_graph,
    suggest_by_xposts,
    suggest_all_methods,
)

_HERE = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

with open(os.path.join(_HERE, "config.json"), "r", encoding="utf-8") as _f:
    CONFIG = json.load(_f)

# SMTP env var override (Fix 1.7)
if os.environ.get("SMTP_USER"):
    CONFIG.setdefault("email_export", {})["smtp_username"] = os.environ["SMTP_USER"]
if os.environ.get("SMTP_PASS"):
    CONFIG.setdefault("email_export", {})["smtp_password"] = os.environ["SMTP_PASS"]

# Error log deque and helper (Fix 1.6)
error_log: "collections.deque[dict]" = collections.deque(maxlen=200)


def log_error(where: str, exc: BaseException) -> None:
    """Log structured error to in-memory deque and file logger."""
    error_log.appendleft({
        "ts": time.time(),
        "where": where,
        "type": type(exc).__name__,
        "msg": str(exc),
        "tb": traceback.format_exc()[-500:],
    })
    logging.getLogger(__name__).exception("[%s] %s", where, exc)


def _playwright_version() -> str:
    """Safely get Playwright version using importlib.metadata."""
    try:
        return _imd.version("playwright")
    except _imd.PackageNotFoundError:
        return "not installed"


_fh = logging.FileHandler(os.path.join(_HERE, CONFIG.get("error_log", "error.log")), encoding="utf-8")
_fh.setLevel(logging.ERROR)
_fh.setFormatter(logging.Formatter(
    "[%(asctime)s %(levelname)s %(job_id)s] %(name)s: %(message)s",
    defaults={"job_id": "-"}
))
logging.getLogger().addHandler(_fh)

# Password redaction filter
class _RedactFilter(logging.Filter):
    def __init__(self, secret):
        super().__init__()
        self.secret = secret or ""
    
    def filter(self, record):
        if self.secret and self.secret in str(record.msg):
            record.msg = str(record.msg).replace(self.secret, "<redacted>")
        return True

_smtp_pw = CONFIG.get("email_export", {}).get("smtp_password", "")
if _smtp_pw:
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactFilter(_smtp_pw))

log = logging.getLogger(__name__)

DB_PATH = os.path.join(_HERE, CONFIG.get("db_path", "reddit_lens.db"))
init_db(DB_PATH)

# Chromium probe at startup
CHROMIUM_STATUS = {"ok": False, "error": None, "path": None, "launch_ms": None}

def _probe_chromium():
    try:
        from playwright.sync_api import sync_playwright
        t0 = time.time()
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
            if not path or not os.path.exists(path):
                CHROMIUM_STATUS.update(ok=False, error=f"Executable not found at {path}", path=path)
                return
            browser = pw.chromium.launch(headless=True)
            browser.close()
            CHROMIUM_STATUS.update(ok=True, path=path, launch_ms=int((time.time()-t0)*1000))
    except Exception as e:
        CHROMIUM_STATUS.update(ok=False, error=str(e))

_probe_chromium()
if not CHROMIUM_STATUS["ok"]:
    log.error("Chromium probe failed: %s", CHROMIUM_STATUS["error"])

# Async screenshot job state
_JOBS = {}
_JOBS_LOCK = threading.RLock()
_active_job_id = None

PHASES = ("starting", "launching_browser", "capturing", "finalizing", "complete", "error", "cancelled")


def _create_job(total: int, thread: threading.Thread = None) -> str:
    """Create a new job. Must be called while holding _JOBS_LOCK."""
    global _active_job_id
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "state": "running",
        "phase": "starting",
        "done": 0,
        "total": total,
        "failed": [],
        "cancel_flag": False,
        "zip_path": None,
        "error": None,
        "started_at": datetime.now(timezone.utc),
        "thread": thread,
    }
    _active_job_id = job_id
    return job_id


def _update_job(job_id: str, **kwargs):
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kwargs)


def _get_job(job_id: str):
    with _JOBS_LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return None
        # Return copy minus the Thread object (not JSON-serializable)
        return {k: v for k, v in j.items() if k != "thread"}


def _reap_stale_slot():
    """Release _active_job_id if its thread is dead."""
    global _active_job_id
    with _JOBS_LOCK:
        if _active_job_id is None:
            return
        j = _JOBS.get(_active_job_id)
        if not j:
            _active_job_id = None
            return
        t = j.get("thread")
        if t is not None and not t.is_alive() and j["state"] == "running":
            j["state"] = "error"
            j["error"] = j.get("error") or "Worker thread died without updating state"
            _active_job_id = None


def _cleanup_old_jobs():
    global _active_job_id
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    with _JOBS_LOCK:
        to_delete = [jid for jid, j in _JOBS.items()
                     if j.get("started_at", datetime.now(timezone.utc)) < cutoff]
        for jid in to_delete:
            j = _JOBS.pop(jid)
            if j.get("zip_path") and os.path.exists(j["zip_path"]):
                try:
                    os.unlink(j["zip_path"])
                except Exception:
                    pass
            if _active_job_id == jid:
                _active_job_id = None


def _find_free_port(start: int, tries: int) -> int:
    for port in range(start, start + tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start + tries


PORT = _find_free_port(CONFIG.get("server_port_start", 5001), CONFIG.get("server_port_tries", 20))
try:
    with open(os.path.join(_HERE, ".port"), "w", encoding="utf-8") as _pf:
        _pf.write(str(PORT))
except Exception:
    pass

app = Flask(__name__)


def _to_old_reddit(url: str) -> str:
    if not url:
        return ""
    if url.startswith("/"):
        return "https://old.reddit.com" + url
    return url.replace("https://www.reddit.com", "https://old.reddit.com") \
              .replace("https://reddit.com", "https://old.reddit.com")


def _screenshot_worker(job_id: str, posts: list, config: dict, full_page: bool):
    global _active_job_id
    adapter = logging.LoggerAdapter(log, {"job_id": job_id})
    zip_file = None
    zf = None
    try:
        from playwright.sync_api import sync_playwright
        _update_job(job_id, phase="launching_browser")

        zip_file = tempfile.NamedTemporaryFile(mode="w+b", suffix=".zip", delete=False)
        zip_path = zip_file.name
        zf = zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED)
        
        failed = []
        failed_lock = threading.Lock()
        zip_lock = threading.Lock()
        done_lock = threading.Lock()
        done_count = 0
        cancel_triggered = False

        def capture_task(chunk):
            nonlocal done_count, cancel_triggered
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
                    locale="en-US",
                    java_script_enabled=True,
                )
                page = ctx.new_page()
                
                for i, p in chunk:
                    if cancel_triggered:
                        break
                    
                    j = _get_job(job_id)
                    if not j or j["cancel_flag"]:
                        cancel_triggered = True
                        break
                    
                    if datetime.now(timezone.utc) - j["started_at"] > timedelta(minutes=20):
                        with failed_lock:
                            failed.append("WALL_CLOCK_TIMEOUT: remaining posts skipped")
                        cancel_triggered = True
                        break

                    url = _to_old_reddit(p.get("permalink", ""))
                    if not url:
                        with failed_lock:
                            failed.append(f"{p.get('title','(no title)')}: no permalink")
                        with done_lock:
                            done_count += 1
                            _update_job(job_id, done=done_count)
                        continue

                    try:
                        resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        if resp and resp.status in (503, 429):
                            time.sleep(2)
                            resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        
                        if resp and resp.status >= 400:
                            with failed_lock:
                                failed.append(f"{p.get('title','(no title)')}: HTTP {resp.status}")
                        else:
                            # Settle wait: check for content or cancel, max 3s
                            for _ in range(30):
                                j = _get_job(job_id)
                                if not j or j.get("cancel_flag"):
                                    cancel_triggered = True
                                    break
                                # Look for common Reddit content markers
                                if page.query_selector(".sitetable, #siteTable, .Post, .commentarea"):
                                    break
                                page.wait_for_timeout(100)
                            
                            if cancel_triggered: break
                            page.wait_for_timeout(400) # Final font/image settle

                            if full_page:
                                png = page.screenshot(full_page=True, type="png")
                            else:
                                png = page.screenshot(
                                    clip={"x": 0, "y": 0, "width": 1280, "height": 3000},
                                    type="png",
                                )
                            
                            safe = "".join(c if c.isalnum() or c in " -_" else "_"
                                           for c in p.get("title", "post"))[:60]
                            with zip_lock:
                                zf.writestr(f"{i+1:03d}_{safe}.png", png)
                    except Exception as e:
                        adapter.warning("screenshot failed for %s: %s", url, e)
                        with failed_lock:
                            failed.append(f"{p.get('title','(no title)')}: {e}")
                        try:
                            with suppress(Exception):
                                page.close()
                            page = ctx.new_page()
                        except Exception:
                            pass

                    with done_lock:
                        done_count += 1
                        _update_job(job_id, done=done_count)
                
                with suppress(Exception):
                    browser.close()

        _update_job(job_id, phase="capturing")
        
        # Concurrency: use up to 4 parallel workers
        num_workers = min(len(posts), 4)
        indexed_posts = list(enumerate(posts))
        chunks = [indexed_posts[i::num_workers] for i in range(num_workers)]
        
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(capture_task, chunk) for chunk in chunks]
            for f in futures:
                f.result()  # Propagate worker exceptions

        # Finalize ZIP even if cancelled to allow partial download
        if zf:
            zf.close()
            zf = None
        if zip_file:
            zip_file.close()
            zip_file = None

        # Cancelled beats everything - check before marking complete
        j = _get_job(job_id)
        if j and (j.get("cancel_flag") or cancel_triggered):
            _update_job(job_id, state="cancelled", phase="cancelled",
                        zip_path=zip_path, finished_at=time.time())
            adapter.info("job cancelled: %d posts processed", done_count)
        else:
            if failed:
                with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf_retry:
                    zf_retry.writestr("FAILED.txt", "\n".join(failed))

            _update_job(job_id, state="complete", phase="complete",
                        zip_path=zip_path, failed=failed)
            adapter.info("job complete: %d posts, %d failed", len(posts), len(failed))

    except Exception as exc:
        adapter.error("worker error: %s", exc, exc_info=True)
        _update_job(job_id, state="error", error=str(exc))
    finally:
        if zf:
            with suppress(Exception): zf.close()
        if zip_file:
            with suppress(Exception): zip_file.close()

        with _JOBS_LOCK:
            if _active_job_id == job_id:
                _active_job_id = None


@app.route("/")
def index():
    return render_template("index.html", config=CONFIG)


@app.route("/fetch", methods=["POST"])
def fetch():
    log.info("/fetch called")
    try:
        body = request.get_json(silent=True) or {}
        subreddits = [s.lower() for s in body.get("subreddits", [])]

        cfg = CONFIG
        if subreddits:
            cfg = dict(CONFIG)
            cfg["reddit_sources"] = [
                s for s in CONFIG.get("reddit_sources", [])
                if s["subreddit"].lower() in subreddits
            ]

        raw = fetch_all(cfg)
        
        # Persist last_fetched timestamps
        with open(os.path.join(_HERE, "config.json"), "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)

        new, skipped = insert_new_posts(DB_PATH, raw)
        new.sort(key=lambda p: (p.get("controversy_score", 0), p.get("velocity", 0)), reverse=True)
        new = new[:CONFIG.get("max_display", 200)]
        log.info("/fetch: %d raw, %d new, %d skipped", len(raw), len(new), skipped)
        return jsonify(status="ok", fetched=len(raw), new=len(new), skipped=skipped, posts=new)
    except Exception as exc:
        log_error("/fetch", exc)
        return jsonify(status="error", message=str(exc)), 500


@app.route("/sources", methods=["GET"])
def sources_get():
    return jsonify(CONFIG.get("reddit_sources", []))


@app.route("/suggest_similar")
def suggest_similar():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        results = suggest_subreddits(query)
        return jsonify(results)
    except Exception as exc:
        log_error("/suggest_similar", exc)
        return jsonify([]), 500


@app.route("/suggest_overlap")
def suggest_overlap():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(suggest_by_overlap(query))
    except Exception as exc:
        log_error("/suggest_overlap", exc)
        return jsonify([]), 500


@app.route("/suggest_semantic")
def suggest_semantic():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(suggest_by_semantic(query))
    except Exception as exc:
        log_error("/suggest_semantic", exc)
        return jsonify([]), 500


@app.route("/suggest_graph")
def suggest_graph():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(suggest_by_graph(query))
    except Exception as exc:
        log_error("/suggest_graph", exc)
        return jsonify([]), 500


@app.route("/suggest_xposts")
def suggest_xposts():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(suggest_by_xposts(query))
    except Exception as exc:
        log_error("/suggest_xposts", exc)
        return jsonify([]), 500


@app.route("/suggest_all")
def suggest_all():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    try:
        return jsonify(suggest_all_methods(query))
    except Exception as exc:
        log_error("/suggest_all", exc)
        return jsonify([]), 500


@app.route("/sources", methods=["POST"])
def sources_post():
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    
    if action == "batch_add":
        subs = body.get("subreddits", [])
        if not subs:
            return jsonify(status="error", message="subreddits list required"), 400
        
        sources = CONFIG.setdefault("reddit_sources", [])
        added = 0
        for sub in subs:
            sub = sub.strip()
            if not sub: continue
            if not any(s["subreddit"].lower() == sub.lower() for s in sources):
                sources.append({
                    "subreddit": sub,
                    "sort": body.get("sort", "hot"),
                    "min_score": int(body.get("min_score", 10)),
                    "nsfw": bool(body.get("nsfw", False)),
                    "flair_filter": [],
                    "keyword_blocklist": [],
                    "keyword_allowlist": [],
                })
                added += 1
        
        with open(os.path.join(_HERE, "config.json"), "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)
        return jsonify(status="ok", added=added, sources=CONFIG["reddit_sources"])

    sub = (body.get("subreddit") or "").strip()
    if not sub:
        return jsonify(status="error", message="subreddit required"), 400

    sources = CONFIG.setdefault("reddit_sources", [])

    if action == "remove":
        CONFIG["reddit_sources"] = [s for s in sources if s["subreddit"].lower() != sub.lower()]
    elif action == "add":
        if any(s["subreddit"].lower() == sub.lower() for s in sources):
            return jsonify(status="error", message=f"r/{sub} already in list"), 409
        CONFIG["reddit_sources"].append({
            "subreddit": sub,
            "sort": body.get("sort", "hot"),
            "min_score": int(body.get("min_score", 10)),
            "nsfw": bool(body.get("nsfw", False)),
            "flair_filter": [],
            "keyword_blocklist": [],
            "keyword_allowlist": [],
        })
    else:
        return jsonify(status="error", message="action must be add or remove"), 400

    with open(os.path.join(_HERE, "config.json"), "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, indent=2)

    return jsonify(status="ok", sources=CONFIG["reddit_sources"])


@app.route("/stats")
def stats():
    try:
        return jsonify(get_stats(DB_PATH))
    except Exception as exc:
        log_error("/stats", exc)
        return jsonify(total=0, by_subreddit=[])


def _apply_export_filters(posts, body_json):
    """Apply limit/subreddits/percent filters from request body."""
    limit      = int(body_json.get("limit", CONFIG.get("max_display", 200)))
    subreddits = [s.lower() for s in body_json.get("subreddits", [])]
    percent    = float(body_json.get("percent", 100))

    if subreddits:
        posts = [p for p in posts if p["subreddit"].lower() in subreddits]
    if percent < 100:
        posts = posts[:max(1, int(len(posts) * percent / 100))]
    else:
        posts = posts[:limit]
    return posts


def _smtp_send(email_config, msg):
    import smtplib
    import socket
    import ssl
    try:
        with smtplib.SMTP(email_config["smtp_server"], email_config["smtp_port"], timeout=30) as server:
            use_tls = email_config.get("smtp_use_tls", True)
            if use_tls:
                server.starttls()
            if email_config.get("smtp_username"):
                server.login(email_config["smtp_username"], email_config.get("smtp_password", ""))
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(f"SMTP authentication failed â€” check app password. ({e.smtp_code})")
    except smtplib.SMTPRecipientsRefused as e:
        raise RuntimeError(f"SMTP recipient refused: {list(e.recipients)[:1]}")
    except smtplib.SMTPServerDisconnected:
        raise RuntimeError("SMTP server dropped the connection â€” retry in a moment")
    except (socket.timeout, TimeoutError):
        raise RuntimeError("SMTP timeout â€” check network or firewall")
    except ssl.SSLError as e:
        raise RuntimeError(f"SMTP TLS error: {e}")
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP error: {e}")


@app.route("/export", methods=["POST"])
def export():
    email_config = CONFIG.get("email_export", {})
    if not email_config.get("enabled", False):
        return jsonify(status="error", message="Email export not enabled"), 400

    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        body_json = request.get_json(silent=True) or {}
        posts = _apply_export_filters(get_posts_for_export(DB_PATH, limit=2000), body_json)

        body = "\n".join(
            f"{p['title']}\nr/{p['subreddit']} Â· â–²{p['score']} Â· ðŸ’¬{p['num_comments']}\n{p['url']}\n---"
            for p in posts
        )
        msg = MIMEMultipart()
        msg["From"]    = email_config["from_email"]
        msg["To"]      = email_config["to_email"]
        msg["Subject"] = f"Reddit Lens Export â€” {len(posts)} posts"
        msg.attach(MIMEText(body, "plain"))

        _smtp_send(email_config, msg)
        log.info("Email export sent: %d posts", len(posts))
        return jsonify(status="ok", message="Export sent", posts=len(posts))
    except Exception as exc:
        log_error("/export", exc)
        return jsonify(status="error", message=str(exc)), 500


def _build_screenshot_html(posts):
    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    cards = []
    for p in posts:
        badges = [f"r/{esc(p['subreddit'])}", f"â–² {p.get('score', 0):,}"]
        if p.get("velocity", 0) > 1:
            badges.append(f"ðŸ’¬ {p['velocity']:.1f}/hr")
        if p.get("controversy_score", 0) > 0:
            badges.append(f"âš¡ {round(p['controversy_score']):,}")
        if p.get("flair"):
            badges.append(esc(p["flair"]))
        if p.get("over_18"):
            badges.append("NSFW")

        def _cls(b):
            if "NSFW" in b or "âš¡" in b:
                return " controversy"
            if "ðŸ’¬" in b:
                return " velocity"
            return ""

        badges_html = "".join(f'<span class="badge{_cls(b)}">{b}</span>' for b in badges)
        cards.append(f'<div class="card"><div class="card-title">{esc(p["title"])}</div><div class="card-meta">{badges_html}</div></div>')

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:12px;width:390px}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px;margin-bottom:10px}}
.card-title{{color:#58a6ff;font-size:15px;font-weight:600;line-height:1.4;margin-bottom:8px;word-wrap:break-word}}
.card-meta{{display:flex;flex-wrap:wrap;gap:5px}}
.badge{{background:#21262d;border-radius:4px;padding:2px 7px;font-size:12px;color:#8b949e;white-space:nowrap}}
.badge.controversy{{background:#3d1f1f;color:#f85149}}
.badge.velocity{{background:#1f2d1f;color:#3fb950}}
</style></head><body>{"".join(cards)}</body></html>"""


@app.route("/export/images", methods=["POST"])
def export_images():
    if not CHROMIUM_STATUS["ok"]:
        return jsonify(
            status="error", code="chromium_missing",
            message=f"Chromium not available: {CHROMIUM_STATUS['error']}"
        ), 200

    email_config = CONFIG.get("email_export", {})
    if not email_config.get("enabled", False):
        return jsonify(status="error", message="Email export not enabled"), 400

    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.image import MIMEImage
        from playwright.sync_api import sync_playwright

        body_json = request.get_json(silent=True) or {}
        posts = _apply_export_filters(get_posts_for_export(DB_PATH, limit=2000), body_json)

        html = _build_screenshot_html(posts)

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3).new_page()
            page.set_content(html)
            page.wait_for_timeout(200)
            png_bytes = page.screenshot(full_page=True, type="png")
            browser.close()

        msg = MIMEMultipart()
        msg["From"]    = email_config["from_email"]
        msg["To"]      = email_config["to_email"]
        msg["Subject"] = f"Reddit Lens â€” {len(posts)} posts (screenshot)"
        msg.attach(MIMEText(f"{len(posts)} posts attached as screenshot.", "plain"))
        img_part = MIMEImage(png_bytes, _subtype="png")
        img_part.add_header("Content-Disposition", "attachment", filename="reddit_lens.png")
        msg.attach(img_part)

        _smtp_send(email_config, msg)
        log.info("Image export sent: %d posts", len(posts))
        return jsonify(status="ok", message="Image sent", posts=len(posts))

    except ImportError:
        return jsonify(status="error", message="Playwright not installed. Run: pip install playwright && playwright install chromium"), 500
    except Exception as exc:
        log_error("/export/images", exc)
        return jsonify(status="error", message=str(exc)), 500


@app.route("/export/report", methods=["POST"])
def export_report():
    # TODO: migrate to _screenshot_worker for async job tracking
    if not CHROMIUM_STATUS["ok"]:
        return jsonify(
            status="error", code="chromium_missing",
            message=f"Chromium not available: {CHROMIUM_STATUS['error']}"
        ), 200

    try:
        import io
        import zipfile
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

        body_json = request.get_json(silent=True) or {}
        posts = _apply_export_filters(get_posts_for_export(DB_PATH, limit=2000), body_json)

        # get_posts_for_export only returns title/url/subreddit/score/num_comments/created_utc
        # we need permalink â€” fetch it separately
        with __import__("sqlite3").connect(DB_PATH) as _conn:
            _conn.row_factory = __import__("sqlite3").Row
            _rows = _conn.execute(
                "SELECT url, permalink FROM seen_posts"
            ).fetchall()
        _permalink_map = {r["url"]: r["permalink"] for r in _rows}
        for p in posts:
            p["permalink"] = _permalink_map.get(p["url"], "")

        posts = [p for p in posts if p.get("permalink")]

        zip_buf = io.BytesIO()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            )
            page = ctx.new_page()
            page.set_default_timeout(15000)
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, p in enumerate(posts):
                    permalink = p["permalink"]
                    # Use old.reddit.com for clean, consistent layout
                    if permalink.startswith("/"):
                        url = "https://old.reddit.com" + permalink
                    else:
                        url = permalink.replace("www.reddit.com", "old.reddit.com")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        # Settle wait
                        try:
                            page.wait_for_selector(".sitetable, #siteTable, .Post", timeout=4000)
                        except Exception:
                            pass
                        page.wait_for_timeout(300)
                        png = page.screenshot(full_page=False, type="png")
                        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in p.get("title", "post"))[:60]
                        zf.writestr(f"{i+1:03d}_{safe_title}.png", png)
                    except PlaywrightTimeoutError as e:
                        log.warning("Screenshot timeout for %s: %s", url, e)
                    except Exception as e:
                        log.warning("Screenshot failed for %s: %s", url, e)
            with suppress(Exception):
                browser.close()

        zip_buf.seek(0)
        from flask import send_file
        return send_file(
            zip_buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name="reddit_lens_report.zip",
        )
    except ImportError:
        return jsonify(status="error", message="Playwright not installed. Run: pip install playwright && playwright install chromium"), 500
    except Exception as exc:
        log_error("/export/report", exc)
        return jsonify(status="error", message=str(exc)), 500


@app.route("/export/screenshots/start", methods=["POST"])
def export_screenshots_start():
    if not CHROMIUM_STATUS["ok"]:
        return jsonify(
            status="error",
            code="chromium_missing",
            message="Chromium not installed. Run: .venv\\Scripts\\playwright.exe install chromium",
            detail=CHROMIUM_STATUS["error"],
        ), 200

    try:
        body_json = request.get_json(silent=True) or {}
        full_page = bool(body_json.get("full_page", False))
        posts = _apply_export_filters(get_posts_for_export(DB_PATH, limit=2000), body_json)
        posts = posts[:CONFIG.get("max_screenshots", 50)]
        
        # Fetch permalinks
        from db import _open_db
        with _open_db(DB_PATH) as _conn:
            _conn.row_factory = __import__("sqlite3").Row
            _rows = _conn.execute("SELECT url, permalink FROM seen_posts").fetchall()
        _permalink_map = {r["url"]: r["permalink"] for r in _rows}
        for p in posts:
            p["permalink"] = _permalink_map.get(p["url"], "")
        
        posts = [p for p in posts if p.get("permalink")]
        
        if not posts:
            return jsonify(status="error", code="no_posts", message="No posts with permalinks found"), 200
        
        _cleanup_old_jobs()
        
        with _JOBS_LOCK:
            _reap_stale_slot()  # May clear _active_job_id if dead
            if _active_job_id is not None:
                return jsonify(
                    status="error",
                    code="job_running",
                    message="A screenshot job is already running"
                ), 200
            t = threading.Thread(target=lambda: None)  # Placeholder
            job_id = _create_job(len(posts), thread=t)
        
        # Replace placeholder with real thread; start it OUTSIDE the lock
        real = threading.Thread(
            target=_screenshot_worker, args=(job_id, posts, CONFIG, full_page), daemon=True
        )
        with _JOBS_LOCK:
            _JOBS[job_id]["thread"] = real
        real.start()
        return jsonify(status="ok", job_id=job_id, total=len(posts), full_page=full_page), 200
    
    except Exception as exc:
        log_error("/export/screenshots/start", exc)
        return jsonify(status="error", code="server_error", message=str(exc)), 200


@app.route("/export/screenshots/status/<job_id>")
def export_screenshots_status(job_id):
    _reap_stale_slot()
    job = _get_job(job_id)
    if not job:
        return jsonify(status="error", message="Job not found"), 404
    return jsonify(
        state=job["state"],
        phase=job.get("phase"),
        done=job["done"],
        total=job["total"],
        failed=len(job["failed"]),
        zip_ready=(job["state"] in ("complete", "cancelled") and job.get("zip_path") is not None),
        error=job.get("error")
    )


@app.route("/export/screenshots/download/<job_id>")
def export_screenshots_download(job_id):
    job = _get_job(job_id)
    if not job:
        return jsonify(status="error", message="Job not found"), 404
    if job["state"] not in ("complete", "cancelled"):
        return jsonify(status="error", message="Job not finished"), 400
    if not job.get("zip_path") or not os.path.exists(job["zip_path"]):
        return jsonify(status="error", message="ZIP file not found"), 404
    
    from flask import send_file
    return send_file(
        job["zip_path"],
        mimetype="application/zip",
        as_attachment=True,
        download_name="reddit_lens_screenshots.zip"
    )


@app.route("/export/screenshots/cancel/<job_id>", methods=["POST"])
def export_screenshots_cancel(job_id):
    _update_job(job_id, cancel_flag=True)
    return jsonify(status="ok")


@app.route("/export/screenshots/email/<job_id>", methods=["POST"])
def export_screenshots_email(job_id):
    email_config = CONFIG.get("email_export", {})
    if not email_config.get("enabled", False):
        return jsonify(status="error", message="Email export not enabled"), 400
    
    job = _get_job(job_id)
    if not job:
        return jsonify(status="error", message="Job not found"), 404
    if job["state"] != "complete":
        return jsonify(status="error", message="Job not complete"), 400
    
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        
        with open(job["zip_path"], "rb") as f:
            zip_data = f.read()
        
        msg = MIMEMultipart()
        msg["From"] = email_config["from_email"]
        msg["To"] = email_config["to_email"]
        msg["Subject"] = f"Reddit Lens Screenshots â€” {job['done']} posts"
        msg.attach(MIMEText(f"{job['done']} post screenshots attached.", "plain"))
        
        zip_part = MIMEApplication(zip_data, _subtype="zip")
        zip_part.add_header("Content-Disposition", "attachment", filename="reddit_lens_screenshots.zip")
        msg.attach(zip_part)
        
        _smtp_send(email_config, msg)
        log.info("Screenshot ZIP emailed: %d posts", job["done"])
        return jsonify(status="ok")
    
    except Exception as exc:
        log_error("/export/screenshots/email", exc)
        return jsonify(status="error", message=str(exc)), 500


def _redact_config(cfg: dict) -> dict:
    """Return deep copy of config with sensitive values redacted."""
    import copy, re
    redacted = copy.deepcopy(cfg)
    sensitive_pattern = re.compile(r'password|token|secret|key|cookie', re.IGNORECASE)
    
    def redact_values(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if sensitive_pattern.search(k):
                    d[k] = "<redacted>"
                elif isinstance(v, (dict, list)):
                    redact_values(v)
        elif isinstance(d, list):
            for item in d:
                redact_values(item)
    
    redact_values(redacted)
    return redacted


def _build_debug_data() -> dict:
    """Shared debug data builder. Used by /debug/report and /api/bug-report."""
    import platform
    from db import get_sqlite_retry_count
    
    # Get jobs snapshot
    with _JOBS_LOCK:
        jobs = []
        for jid, j in list(_JOBS.items())[-10:]:
            dur = (datetime.now(timezone.utc) - j["started_at"]).total_seconds()
            jobs.append({
                "job_id": jid,
                "state": j["state"],
                "phase": j.get("phase"),
                "done": j["done"],
                "total": j["total"],
                "failed": len(j["failed"]),
                "started_at": j["started_at"].isoformat(),
                "duration_s": round(dur, 1),
            })
    
    # Get error log tail from in-memory deque
    log_tail = [str(e) for e in list(error_log)[:20]]
    
    return dict(
        env=f"Python {platform.python_version()} / Flask {__import__('flask').__version__} / "
            f"Playwright {_playwright_version()} / {platform.platform()}",
        chromium=CHROMIUM_STATUS,
        sqlite_retries=get_sqlite_retry_count(),
        jobs=jobs,
        error_log_tail=log_tail,
        config_redacted=_redact_config(CONFIG),
    )


def _build_bug_report_md(data: dict) -> str:
    """Render the debug/report JSON dict as a markdown bug report string."""
    jobs = data.get("jobs", [])
    jobs_table = "\n".join(
        f"| {j['job_id'][:8]} | {j['state']} | {j['phase']} | {j['done']}/{j['total']} "
        f"| {j['failed']} | {j['started_at']} | {j['duration_s']}s |"
        for j in jobs
    ) or "_(none)_"
    log_tail = "\n".join(data.get("error_log_tail", []))
    ch = data.get("chromium", {})
    ts = datetime.now(timezone.utc).isoformat()
    fence = "```"
    suggestion = "none" if ch.get("ok") else r"Run: .venv\Scripts\playwright.exe install chromium"
    cfg_json = json.dumps(data.get("config_redacted", {}), indent=2)
    return (
        f"# Reddit Lens Bug Report - {ts}\n\n"
        f"## Environment\n{data.get('env', '(unknown)')}\n\n"
        f"## Chromium probe\n"
        f"- ok: {ch.get('ok')}\n"
        f"- path: {ch.get('path') or '(none)'}\n"
        f"- launch_ms: {ch.get('launch_ms') or '(n/a)'}\n"
        f"- error: {ch.get('error') or '(none)'}\n"
        f"- suggestion: {suggestion}\n\n"
        f"## SQLite\n- retries_since_start: {data.get('sqlite_retries', 0)}\n\n"
        f"## Recent jobs\n"
        f"| id | state | phase | done/total | failed | started | duration |\n"
        f"|----|-------|-------|-----------:|-------:|---------|---------:|\n"
        f"{jobs_table}\n\n"
        f"## Server error log tail\n{fence}\n{log_tail}\n{fence}\n\n"
        f"## Configuration (redacted)\n{fence}json\n{cfg_json}\n{fence}\n"
    )


@app.route("/api/bug-report")
def api_bug_report():
    """Download the bug report as a .md file. Used by the bookmarklet and PowerShell helper."""
    try:
        data = _build_debug_data()
        md = _build_bug_report_md(data)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"reddit_lens_bugreport_{ts}.md"
        return Response(
            md,
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        log_error("/api/bug-report", exc)
        return jsonify(ok=False, error=str(exc)), 500


@app.route("/debug/report")
def debug_report():
    """Return debug data as JSON."""
    try:
        return jsonify(_build_debug_data())
    except Exception as exc:
        log_error("/debug/report", exc)
        return jsonify(ok=False, error=str(exc)), 500


# Graceful shutdown
def _shutdown_active_jobs():
    log.info("Shutdown: cancelling active jobs")
    with _JOBS_LOCK:
        for jid, j in _JOBS.items():
            if j["state"] == "running":
                j["cancel_flag"] = True
    # Wait up to 3s for workers to acknowledge
    deadline = time.time() + 3
    while time.time() < deadline:
        with _JOBS_LOCK:
            still = [j for j in _JOBS.values() if j["state"] == "running"]
            if not still:
                break
        time.sleep(0.1)
    # Checkpoint WAL
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:
        log.warning("WAL checkpoint failed: %s", e)


atexit.register(_shutdown_active_jobs)
for sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(sig, lambda *_: (_shutdown_active_jobs(), os._exit(0)))
    except Exception:
        pass


if __name__ == "__main__":
    host = CONFIG.get("server_host", "127.0.0.1")
    url  = f"http://{host}:{PORT}"

    def _open_browser():
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"\n  Reddit Lens â†’ {url}\n  Close this window to stop.\n")
    app.run(host=host, port=PORT, debug=False, use_reloader=False, threaded=True)
