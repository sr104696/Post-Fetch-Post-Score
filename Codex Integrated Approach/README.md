# Project Setup Instructions — Codex

## Target Folder: `Codex Integrated Approach`
This folder contains a self-contained integration that extends `reddit-lens` without changing original source files.

## Folder layout

```
Codex Integrated Approach/
├── integration_file.html
├── patch.py
└── README.md
```

## Purpose
- Preserve the original Reddit Lens interface and behavior.
- Add an integrated mode with client-side post processing, scoring presets, delta tracking, manual input, and export.
- Keep modifications non-destructive and reversible.

## Setup Workflow

### 1) Establish working directory
All integration work is in this folder.

### 2) Copy into target project
Copy `integration_file.html` into the target app root:

```
reddit-lens/
├── app.py
├── ...
└── integration_file.html
```

### 3) Apply non-invasive patch

#### Option A — import patch (optional)
Add one line to the end of `reddit-lens/app.py`:

```python
import patch  # noqa
```

#### Option B — run patch directly (recommended)
Run from repository root:

```bash
python "Codex Integrated Approach/patch.py"
```

This imports the original app and adds routes at runtime.

### 4) Run
1. Start via patch command above (or your normal startup + import option).
2. Open `http://localhost:[PORT]/integrated`.
3. Use **Fetch** then **Score / Analyze / Process Posts**.

## Integration Contract
- **UI Parity:** Uses original Reddit Lens dark theme and card layout.
- **New Feature:** Adds processing button + scoring modes + manual input flow.
- **Non-destructive:** Existing routes continue to work; integrated mode is separate.
- **Zero edits:** No required source changes when using Option B.
- **Single source:** New code resides in this folder plus one copied HTML file.

## Endpoints added by patch
- `GET /integrated` — serves integrated HTML.
- `GET /data` — returns stored posts JSON.
- `GET /proxy/info?ids=t3_xxx,t3_yyy` — optional Reddit API proxy for manual IDs.

## Troubleshooting
- **Cannot connect:** Verify the running port in terminal output.
- **0 items returned:** Run Fetch first to populate DB, then process again.
- **Port conflict:** Reddit Lens auto-selects next available port.
- **Button does nothing:** Process operates on loaded items; click Fetch first.
