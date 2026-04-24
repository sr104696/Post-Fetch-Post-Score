"""Non-invasive patch routes for Reddit Lens integrated UI.

Usage:
1) Preferred (zero edits): python "Codex Integrated Approach/patch.py"
2) Optional import patch from app.py: import patch  # noqa
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
TARGET_PROJECT = REPO_ROOT / "reddit-lens"

if str(TARGET_PROJECT) not in sys.path:
    sys.path.insert(0, str(TARGET_PROJECT))

from app import PORT, app, CONFIG  # type: ignore  # noqa: E402
from db import get_recent_posts  # type: ignore  # noqa: E402


@app.get("/integrated")
def integrated_ui():
    """Serve integrated HTML from project root, with fallback to this folder."""
    project_copy = TARGET_PROJECT / "integration_file.html"
    source_copy = THIS_DIR / "integration_file.html"

    html_path = project_copy if project_copy.exists() else source_copy
    if not html_path.exists():
        return "integration_file.html not found", 404
    return html_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/data")
def data_endpoint():
    """Return stored posts as JSON for integration_file.html."""
    from flask import request

    limit = max(1, min(int(request.args.get("limit", 250)), 2000))
    items = get_recent_posts(str(TARGET_PROJECT / CONFIG.get("db_path", "reddit_lens.db")), limit=limit)
    return {
        "ok": True,
        "total": len(items),
        "items": items,
    }


@app.get("/proxy/info")
def proxy_info():
    """Optional proxy for manual post IDs using Reddit /api/info.json."""
    from flask import request

    ids = request.args.get("ids", "").strip()
    if not ids:
        return {"ok": False, "error": "Missing ids query string."}, 400

    query = urlencode({"id": ids})
    target = f"https://www.reddit.com/api/info.json?{query}"
    req = Request(target, headers={"User-Agent": "reddit-lens-integrated/1.0"})
    with urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload


if __name__ == "__main__":
    print(f"Starting reddit-lens with patch routes on http://127.0.0.1:{PORT}/integrated")
    app.run(host="127.0.0.1", port=PORT, debug=False)
