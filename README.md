# OpenClaw Reports UI

A lightweight, dependency-light Flask web interface for browsing a directory of
Markdown intelligence reports and querying the SQLite database that indexes them.

Originally built as part of the OpenClaw intelligence infrastructure; published here
so other people and agents can reuse and improve it.

## What it does

- **Today** — the home page leads with today's Daily Brief and Executive
  Summary, says when either is not out yet or is out of date, and lists the
  other reports dated today.
- **Report browser** — recursively scans a directory of `.md` reports, classifies them
  by type from their path/filename, and lists them newest-first.
- **Full-text search** — matches report filename, folder path, and file content
  (with a context snippet on content hits).
- **Rendered report view** — Markdown → HTML with syntax highlighting, tables,
  fenced code, TOC, and `nl2br`/`sane_lists` handling.
- **Database explorer** — read-only SQLite browser: table/view list, schema,
  row counts, paginated browsing with per-column filters.
- **SQL query console** — run single `SELECT` statements against the database,
  with keyword/statement validation.
- **Dark mode** — light/dark theme toggle persisted in `localStorage`.
- **No external assets** — all CSS/JS is inline in the templates; no CDN calls,
  no internet access required at runtime.

## Architecture

```
┌─────────────────────┐
│   Flask Web App     │
├─────────────────────┤
│ • Report Indexer    │
│ • Markdown Renderer │
│ • Database Browser  │
└─────────────────────┘
         │
         ├──> $OPENCLAW_REPORTS_DIR  (Markdown files)
         └──> $OPENCLAW_DB_PATH      (SQLite database, read-only)
```

- **Backend** — Flask
- **Report Indexer** (`modules/report_indexer.py`) — scans `.md` files, extracts
  metadata, classifies report type, caches content, powers search.
- **Markdown Renderer** (`modules/markdown_renderer.py`) — Markdown → HTML.
- **Database Browser** (`modules/db_browser.py`) — read-only SQLite access with
  identifier allowlisting and SELECT-only validation.
- **Templates** — Jinja2 HTML with embedded CSS (see `templates/base.html`).

## Requirements

- Python 3.9+ (developed on 3.12)
- A directory of Markdown reports to browse
- Optionally, a SQLite database to explore

Dependencies are pinned in `requirements.txt`:

```
Flask==3.0.0
Markdown==3.5.2
Pygments==2.17.2
```

## Quick start

```bash
git clone <this-repo-url>
cd openclaw-reports-ui

# Configure paths (see .env.example)
export OPENCLAW_REPORTS_DIR="$HOME/reports"
export OPENCLAW_DB_PATH="$HOME/reports/intelligence.db"

./start.sh          # creates venv, installs deps, starts server
# or
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:8000>.

`run.sh` relaunches using an existing venv; `stop.sh` kills whatever is on the
configured port.

## Configuration

All configuration is via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENCLAW_REPORTS_DIR` | `/home/dg/openclaw-reports` | Markdown reports root |
| `OPENCLAW_DB_PATH` | `/home/dg/openclaw-reports/intelligence.db` | SQLite database (read-only) |
| `HOST` | `127.0.0.1` | Flask bind address |
| `PORT` | `8000` | Flask port |
| `FLASK_DEBUG` | `0` | `1` enables the Flask debugger |
| `SECRET_KEY` | `dev-secret-change-me` | Flask session secret — **change for any non-local deploy** |

> **Note on defaults:** the built-in defaults point at the original author's
> layout. Set `OPENCLAW_REPORTS_DIR` and `OPENCLAW_DB_PATH` (or instantiate
> `ReportIndexer(reports_dir=...)` / `DatabaseBrowser(db_path=...)`) for your own
> environment.

## Usage

### Browsing reports

Go to **Reports**, search by keyword, optionally filter by series (a country,
Daily Brief, RSS Digest...) or report type, then click through to the rendered
view. The list is newest first, grouped by day, 50 to a page; click a column
heading to sort by it.

A report's series and date come from its file name (`mika-china-2026-10-21.md`
is the China series on 21 Oct 2026), so a report keeps its place after it moves
into `archive/`. Undated files (design notes, logs) and test runs
(`daily-brief-hermes-test-…`) are hidden unless you tick "Include design notes
and test runs". A report page links to the previous and next report of its
series, and an old link to a report that has since been archived redirects to
its new place.

### Exploring the database

Go to **Database** for the table/view list with row counts, then click a table to
browse it with pagination and per-column filters.

### Running queries

Go to **Query** and enter a single `SELECT`. Example:

```sql
SELECT report_date, country, risk_score
FROM reports
ORDER BY report_date DESC
LIMIT 20;
```

Only `SELECT` is permitted. The console rejects multi-statement input and blocks
`DELETE`, `UPDATE`, `INSERT`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, and `PRAGMA`
(matched on word boundaries, so a column like `updated_at` is not misflagged).

## Security model

- **Read-only database** — connections use `file:...?mode=ro` URI mode.
- **Identifier allowlisting** — table and column names are validated against the
  live schema (`sqlite_master` / `PRAGMA table_info`) and quoted before
  interpolation; they are never taken from raw user input.
- **Parameterized filters** — filter values are bound, never interpolated.
- **SELECT-only console** — statement and keyword validation as above.
- **Loopback by default** — `HOST` defaults to `127.0.0.1`, so the app is not
  reachable from other machines unless you explicitly opt in.

There is **no authentication layer** in this app. Every route is anonymous, and
the SQL console is readable by anyone who can reach the port (it is read-only and
SELECT-only, but it can read your entire database). Choose the bind address with
that in mind:

| `HOST` | Reachable from | Use when |
| --- | --- | --- |
| `127.0.0.1` *(default)* | This machine only | Local browsing, or a reverse proxy / overlay network on the same host |
| `0.0.0.0` | Anything that can route to the port — your LAN, etc. | You accept unauthenticated access from the local network |

**Recommended pattern:** keep `HOST=127.0.0.1` and put the app behind an
overlay network or authenticated reverse proxy. The original deployment binds
loopback and exposes it tailnet-only via Tailscale Serve:

```bash
# Serve the loopback-bound app to your tailnet, never to the public internet
tailscale serve --bg --https=8443 http://127.0.0.1:8000
```

This gives you remote access without binding the port to every interface. Note
that `tailscale serve` is tailnet-only by default — only `tailscale funnel`
exposes a service publicly, which you should not use for this app as-is.

If you do bind `0.0.0.0`, scope it with a host firewall rule for your subnet and
be aware that the port is unauthenticated. Binding `0.0.0.0` is a deliberate
tradeoff, not a neutral default.

## Project layout

```
.
├── app.py                      # Flask routes and app entrypoint
├── requirements.txt            # Pinned dependencies
├── start.sh / run.sh / stop.sh # Convenience launchers
├── .env.example                # Environment variable template
├── modules/
│   ├── report_indexer.py       # Report scan, classify, search
│   ├── markdown_renderer.py    # Markdown → HTML
│   └── db_browser.py           # Read-only SQLite browser
└── templates/
    ├── base.html               # Layout, CSS variables, dark mode
    ├── index.html              # Overview / stats
    ├── reports.html            # Report listing + search
    ├── report_view.html        # Rendered report
    ├── database.html           # Table/view list
    ├── table_view.html         # Paginated table browser
    ├── query.html              # SQL console
    └── 404.html / 500.html     # Error pages
```

## Expected database schema

The UI is schema-agnostic — it enumerates whatever tables and views exist. The
original intelligence database it was built against contains:

- **Tables:** `reports`, `entities`, `entity_mentions`, `signals`, `sources`,
  `system_health`
- **Views:** `v_country_risk_trend`, `v_signal_heatmap_7d`, `v_top_entities_30d`,
  `v_entity_cooccurrence`

## Deployment (systemd user unit)

The unit sets every path and bind option explicitly. This matters: if `HOST` is
omitted, the app falls back to its `127.0.0.1` default and silently loses any LAN
access you thought you had — the setting is not inherited from whatever shell you
last tested in.

```ini
[Unit]
Description=OpenClaw Reports UI - Web Interface
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/openclaw-reports-ui
Environment=OPENCLAW_REPORTS_DIR=%h/reports
Environment=OPENCLAW_DB_PATH=%h/reports/intelligence.db
# Loopback + Tailscale Serve (recommended): leave as-is. See "Security model"
# above before changing this to 0.0.0.0, which exposes an unauthenticated app
# to your LAN.
Environment=HOST=127.0.0.1
Environment=PORT=8000
ExecStart=%h/openclaw-reports-ui/venv/bin/python app.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now openclaw-reports-ui.service
```

## Known limitations / good first issues

- **Full re-scan per request.** `scan_reports()` walks the whole tree on every
  page load; a cached index with mtime invalidation would be cheaper.
- **Content search is O(n) on file reads.** An SQLite FTS5 index would be a real
  improvement; the in-memory cache is unbounded.
- **Index lives in memory** and is rebuilt on each request rather than persisted.
- **No CI or linter config** yet. Tests: `pip install pytest` in the venv,
  then `python -m pytest tests`.
- **No authentication** — every route is anonymous. This is a deliberate
  consequence of the original loopback-or-tailnet deployment; if you bind to a
  shared network, everything is readable by anyone who can reach the port.
- **Content cache never evicts**, which matters for large report trees.
- **`SECRET_KEY` fallback** is a development placeholder; there are no
  session-dependent features yet, so it is currently low-impact.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and pull requests welcome. Please keep the app dependency-light (Flask +
Markdown + Pygments) and keep any new database access read-only.
