#!/usr/bin/env python3
"""
OpenClaw Reports UI - Local Web Interface
Lightweight Flask app for browsing reports and querying intelligence database
"""
import csv
import io
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from flask import Flask, Response, render_template, request, jsonify, abort, redirect, url_for
from modules import charts
from modules import countries
from modules import pipeline
from modules.changes import compare, points
from modules.report_indexer import ReportIndexer, search_terms, series_label
from modules.markdown_renderer import MarkdownRenderer
from modules.db_browser import DatabaseBrowser

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['TEMPLATES_AUTO_RELOAD'] = True

MAX_PER_PAGE = 500
REPORTS_PER_PAGE = 50
MAX_QUERY_ROWS = 1000      # rows shown on the Query page
MAX_EXPORT_ROWS = 100_000  # rows in a query's CSV download
CONTENTS_MIN_HEADINGS = 3  # fewer headings than this: no contents bar
RISK_RANGES = {'30': 30, '90': 90, 'all': None}
DIGEST_POINTS = 4          # new points shown per report in the digest
WATCH_DAYS = 7             # how far back /api/search looks by default

# Ready-made queries for the Query page: the four views, then a few joins
EXAMPLE_QUERIES = [
    ('Country risk trend', 'SELECT * FROM v_country_risk_trend LIMIT 100'),
    ('Signals in the last 7 days, by category', 'SELECT * FROM v_signal_heatmap_7d'),
    ('Top entities, last 30 days', 'SELECT * FROM v_top_entities_30d'),
    ('Entities seen together', 'SELECT * FROM v_entity_cooccurrence ORDER BY cooccurrence_count DESC LIMIT 50'),
    ('Highest-impact signals', 'SELECT s.impact_score, s.category, s.title, r.country, r.report_date\n'
     'FROM signals s JOIN reports r ON r.id = s.report_id\nORDER BY s.impact_score DESC LIMIT 25'),
    ('Most-cited source domains', 'SELECT domain, COUNT(*) AS citations FROM sources\n'
     'GROUP BY domain ORDER BY citations DESC LIMIT 25'),
    ('Reports per country', "SELECT country, COUNT(*) AS reports, MAX(report_date) AS latest\n"
     "FROM reports WHERE report_type = 'country' GROUP BY country ORDER BY reports DESC"),
]

# The series shown first on the home page, in this order
BRIEF_SERIES = 'daily-brief'
SUMMARY_SERIES = 'mika-executive-summary'

# Sort keys for the reports list; date sorts by the date in the file name
REPORT_SORTS = {
    'date': lambda r: (r['report_date'] or r['modified'].date(), r['modified']),
    'title': lambda r: (r['title'] or r['filename']).lower(),
    'series': lambda r: ((r['series_label'] or '~').lower(), r['report_date'] or date.min),
    'type': lambda r: (r['type'], r['modified']),
    'size': lambda r: r['size'],
    'relevance': lambda r: (r.get('score', 0), r['modified']),
}

# Initialize modules
indexer = ReportIndexer()
renderer = MarkdownRenderer()
db_browser = DatabaseBrowser()
# The agent's database loader, whose version the Pipeline page shows
INSERT_SCRIPT = Path(os.environ.get(
    'OPENCLAW_INSERT_SCRIPT',
    Path(db_browser.db_path).parent / 'scripts' / 'intelligence_db_insert.sh'))

@app.context_processor
def shared_names():
    """Country slugs for links from any page (code -> slug)"""
    return {'country_slugs': {code: slug for code, (_, slug) in countries.COUNTRIES.items()}}


# Scan reports on startup
print("Scanning reports...")
indexer.scan_reports()
print(f"Found {len(indexer._index)} reports")


@app.route('/')
def index():
    """Home page: today's reports first, then the overview"""
    indexer.refresh()
    report_stats = indexer.get_stats()
    db_stats = db_browser.get_db_stats()
    today = date.today()

    brief = indexer.latest(BRIEF_SERIES)
    summary = indexer.latest(SUMMARY_SERIES)
    lead = {BRIEF_SERIES, SUMMARY_SERIES}
    todays = [r for r in indexer.on_date(today) if r['series'] not in lead]
    todays.sort(key=lambda r: r['series_label'] or '')
    week_start = today - timedelta(days=6)
    this_week = sum(
        1 for r in indexer._index
        if r['kind'] == 'report' and r['report_date'] and r['report_date'] >= week_start
    )

    # Latest reports, leaving out design notes and test runs
    latest_reports = [r for r in indexer._index if r['kind'] == 'report'][:10]

    return render_template('index.html',
                          today=today,
                          brief=brief,
                          brief_state=freshness(brief, today),
                          summary=summary,
                          summary_state=freshness(summary, today),
                          todays=todays,
                          this_week=this_week,
                          series_count=len({r['series'] for r in indexer._index if r['kind'] == 'report'}),
                          report_stats=report_stats,
                          db_stats=db_stats,
                          latest_reports=latest_reports)


def freshness(report, today):
    """'today', 'yesterday' (not out yet today) or 'stale' (older, or none at all)"""
    if report is None:
        return 'stale'
    if report['report_date'] >= today:
        return 'today'
    if report['report_date'] == today - timedelta(days=1):
        return 'yesterday'
    return 'stale'


@app.route('/reports')
def reports():
    """List reports, newest first, with search, filters, sorting and pages"""
    indexer.refresh()
    query = request.args.get('q', '').strip()
    report_type = request.args.get('type', '')
    series = request.args.get('series', '')
    show_all = request.args.get('all') == '1'
    # A search lists the most matches first unless another order is chosen
    sort = request.args.get('sort') or ('relevance' if query else 'date')
    if sort not in REPORT_SORTS or (sort == 'relevance' and not query):
        sort = 'date'
    descending = request.args.get('dir') or ('desc' if sort in ('date', 'size', 'relevance') else 'asc')
    descending = descending == 'desc'

    # Get reports
    if query:
        results = indexer.search(query)
    else:
        results = indexer._index

    # Design notes and test runs only appear on request
    hidden = sum(1 for r in results if r['kind'] != 'report')
    if not show_all:
        results = [r for r in results if r['kind'] == 'report']

    # Filter by type and series
    if report_type:
        results = [r for r in results if r['type'] == report_type]
    if series:
        results = [r for r in results if r['series'] == series]

    results = sorted(results, key=REPORT_SORTS[sort], reverse=descending)

    total = len(results)
    total_pages = max(1, (total + REPORTS_PER_PAGE - 1) // REPORTS_PER_PAGE)
    page = min(max(1, request.args.get('page', 1, type=int)), total_pages)
    shown = results[(page - 1) * REPORTS_PER_PAGE:page * REPORTS_PER_PAGE]

    # Get unique types and series for the filter dropdowns
    pool = indexer._index if show_all else [r for r in indexer._index if r['kind'] == 'report']
    all_types = sorted(set(r['type'] for r in pool))
    all_series = sorted(
        {(r['series'], r['series_label']) for r in pool if r['series']},
        key=lambda s: s[1].lower(),
    )

    params = {'q': query, 'type': report_type, 'series': series,
              'all': '1' if show_all else '', 'sort': sort,
              'dir': 'desc' if descending else 'asc'}

    def link(**changes):
        """This list's URL with some parameters changed, leaving out empty ones"""
        merged = {**params, **changes}
        return url_for('reports', **{k: v for k, v in merged.items() if v not in ('', None)})

    return render_template('reports.html',
                          reports=shown,
                          total=total,
                          hidden=0 if show_all else hidden,
                          page=page,
                          total_pages=total_pages,
                          link=link,
                          query=query,
                          report_type=report_type,
                          series=series,
                          series_label=dict(all_series).get(series, series),
                          show_all=show_all,
                          sort=sort,
                          descending=descending,
                          all_types=all_types,
                          all_series=all_series)


@app.route('/reports/<safe_id>')
def view_report(safe_id):
    """View individual report"""
    if not indexer.get_report(safe_id):
        indexer.scan_reports()
    report = indexer.get_report(safe_id)
    
    if not report:
        # Reports move into archive/ after a few days; follow old links there
        moved = indexer.find_moved(safe_id)
        if moved:
            return redirect(url_for('view_report', safe_id=moved['safe_id']))
        abort(404)
    
    # Read and render content
    content = indexer.read_content(report['path'])
    html_content, contents = renderer.render_with_contents(content, strip_title=True)
    title = renderer.extract_title(content)
    if title == 'Untitled Report' and report['title']:
        title = report['title']
    
    return render_template('report_view.html',
                          report=report,
                          title=title,
                          content=html_content,
                          contents=contents if len(contents) >= CONTENTS_MIN_HEADINGS else [],
                          highlight=request.args.get('q', '').strip(),
                          highlight_terms=search_terms(request.args.get('q', '')),
                          nearby=indexer.neighbours(report))


@app.route('/reports/<safe_id>/changes')
def report_changes(safe_id):
    """What is new in a report since the previous report of its series"""
    indexer.refresh()
    report = indexer.get_report(safe_id)
    if not report:
        moved = indexer.find_moved(safe_id)
        if moved:
            return redirect(url_for('report_changes', safe_id=moved['safe_id']))
        abort(404)
    nearby = indexer.neighbours(report)
    previous = nearby['previous']
    if previous is None:
        return redirect(url_for('view_report', safe_id=safe_id))
    changes = compare(indexer.read_content(previous['path']), indexer.read_content(report['path']))
    return render_template('report_changes.html',
                          report=report,
                          previous=previous,
                          nearby=nearby,
                          changes=changes)


@app.route('/healthz')
def healthz():
    """For monitoring: 503 when reports aren't indexed or the database doesn't
    read. Otherwise 200, with status 'degraded' when a report expected today
    is late or loaded empty (see /pipeline); ?strict=1 makes that a 503 too.
    `latest_report_days` says how old the newest dated report is."""
    reports = indexer.refresh()
    dated = [r['report_date'] for r in reports if r['kind'] == 'report' and r['report_date']]
    latest = max(dated) if dated else None
    body = {
        'reports': len(reports),
        'latest_report': latest.isoformat() if latest else None,
        'latest_report_days': (date.today() - latest).days if latest else None,
    }
    try:
        conn = db_browser._connect()
        try:
            body['db_reports'] = conn.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        finally:
            conn.close()
        body['db'] = 'ok'
        body['pipeline'] = pipeline.health(pipeline.overview(reports, db_browser._connect))
    except sqlite3.Error as exc:
        body['db'] = f'error: {exc}'
    healthy = bool(reports) and body['db'] == 'ok'
    if not healthy:
        body['status'] = 'fail'
    elif not body['pipeline']['ok']:
        body['status'] = 'degraded'
    else:
        body['status'] = 'ok'
    failing = body['status'] == 'fail' or (body['status'] == 'degraded' and request.args.get('strict'))
    return jsonify(body), 503 if failing else 200


@app.route('/api/reports/refresh')
def refresh_reports():
    """API endpoint to refresh report index"""
    indexer.scan_reports()
    return jsonify({
        'status': 'success',
        'count': len(indexer._index)
    })


@app.route('/database')
def database():
    """Database explorer home"""
    tables = db_browser.get_tables()
    db_stats = db_browser.get_db_stats()
    
    # Separate tables and views
    table_info = []
    for table in tables:
        info = db_browser.get_table_info(table)
        table_info.append(info)
    
    return render_template('database.html',
                          tables=table_info,
                          db_stats=db_stats)


def table_request(table_info):
    """Filters and sort order for a table, from the query string"""
    columns = [c['name'] for c in table_info['columns']]
    filters = {}
    for key in request.args:
        if key.startswith('filter_'):
            col_name = key[len('filter_'):]
            value = request.args[key].strip()
            if col_name in columns and value:
                filters[col_name] = value
    sort = request.args.get('sort', '')
    if sort not in columns:
        sort = 'id' if 'id' in columns else ''
    descending = request.args.get('dir', 'desc') != 'asc'
    return filters, sort, descending


@app.route('/database/<table_name>')
def view_table(table_name):
    """View table contents with pagination, column filters and sorting"""
    # Get pagination parameters
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(1, request.args.get('per_page', 50, type=int)), MAX_PER_PAGE)
    offset = (page - 1) * per_page

    # Get table info
    table_info = db_browser.get_table_info(table_name)
    if table_info.get('error') == 'Unknown table':
        abort(404)
    filters, sort, descending = table_request(table_info)

    # Query data
    rows, total = db_browser.query_table(
        table_name,
        limit=per_page,
        offset=offset,
        order_by=sort or None,
        filters=filters or None,
        descending=descending,
    )

    # Calculate pagination
    total_pages = max(1, (total + per_page - 1) // per_page)

    params = {f'filter_{col}': value for col, value in filters.items()}
    params.update(per_page=per_page, sort=sort, dir='desc' if descending else 'asc')

    def link(**changes):
        """This table's URL with some parameters changed, leaving out empty ones"""
        merged = {**params, **changes}
        return url_for('view_table', table_name=table_name,
                       **{k: v for k, v in merged.items() if v not in ('', None)})

    return render_template('table_view.html',
                          table_name=table_name,
                          table_info=table_info,
                          rows=rows,
                          total=total,
                          page=page,
                          per_page=per_page,
                          total_pages=total_pages,
                          filters=filters,
                          sort=sort,
                          descending=descending,
                          link=link,
                          csv_link=url_for('export_table', table_name=table_name,
                                           **{k: v for k, v in params.items() if k != 'per_page' and v}))


@app.route('/database/<table_name>/csv')
def export_table(table_name):
    """The whole table (with the page's filters and order) as a CSV file"""
    table_info = db_browser.get_table_info(table_name)
    if table_info.get('error') == 'Unknown table':
        abort(404)
    filters, sort, descending = table_request(table_info)
    rows, _ = db_browser.query_table(table_name, limit=None, order_by=sort or None,
                                     filters=filters or None, descending=descending)
    columns = [c['name'] for c in table_info['columns']]
    return csv_response(columns, rows, f'{table_name}.csv')


def csv_response(columns, rows, filename):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(['' if row.get(col) is None else row.get(col) for col in columns])
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def schema_overview():
    """Tables and views with their columns, for the Query page's sidebar"""
    found = []
    for name in db_browser.get_tables():
        info = db_browser.get_table_info(name)
        found.append({'name': name, 'is_view': info.get('is_view', False),
                      'columns': [(c['name'], c['type']) for c in info['columns']]})
    return found


@app.route('/database/query', methods=['GET', 'POST'])
def query_database():
    """Execute custom SELECT queries"""
    query = request.form.get('query', '') if request.method == 'POST' else request.args.get('query', '')
    results = []
    error = None
    truncated = False

    if query:
        results, error = db_browser.execute_select(query, max_rows=MAX_QUERY_ROWS)
        truncated = len(results) > MAX_QUERY_ROWS
        results = results[:MAX_QUERY_ROWS]

    return render_template('query.html',
                          query=query,
                          results=results,
                          error=error,
                          truncated=truncated,
                          max_rows=MAX_QUERY_ROWS,
                          examples=EXAMPLE_QUERIES,
                          schema=schema_overview())


@app.route('/database/query/csv')
def export_query():
    """A query's results as a CSV file"""
    query = request.args.get('query', '')
    results, error = db_browser.execute_select(query, max_rows=MAX_EXPORT_ROWS)
    if error or not query:
        abort(400, error or 'No query')
    columns = list(results[0].keys()) if results else []
    return csv_response(columns, results[:MAX_EXPORT_ROWS], 'query.csv')


@app.route('/charts')
def charts_page():
    """Country risk over time and the last week of signals"""
    risk_range = request.args.get('range', '90')
    if risk_range not in RISK_RANGES:
        risk_range = '90'
    end = charts.as_date(request.args.get('end', '')) or date.today()
    try:
        risk = charts.risk_trends(db_browser._connect, RISK_RANGES[risk_range])
        signals = charts.signal_grid(db_browser._connect, end=end)
        heatmap = charts.heatmap_view(db_browser._connect)
        error = None
    except sqlite3.Error as exc:
        risk = signals = heatmap = None
        error = str(exc)
    return render_template('charts.html',
                          risk=risk,
                          risk_range=risk_range,
                          signals=signals,
                          heatmap=heatmap,
                          error=error)


def lead_points(report, count):
    """The first `count` points of a report, as (section, text); a section
    that is only the report's own title (e.g. an H2 title) is left out"""
    title = (report['title'] or '').casefold()
    return [('' if section.casefold() == title else section, text)
            for section, text in points(indexer.read_content(report['path']))[:count]]


def new_points(report, count):
    """Up to `count` points new since the previous report of the series, the
    number of new points, and that previous report (None for a first report)"""
    previous = indexer.neighbours(report)['previous']
    if previous is None:
        return lead_points(report, count), None, None
    changes = compare(indexer.read_content(previous['path']), indexer.read_content(report['path']))
    found = [(section, text) for section, texts in changes['added'] for text in texts]
    return found[:count], changes['counts']['added'], previous


@app.route('/digest')
def digest():
    """One day on one page: the brief, the summary and what is new per country"""
    reports = indexer.refresh()
    day = charts.as_date(request.args.get('date', '')) or date.today()
    todays = [r for r in reports if r['report_date'] == day and r['kind'] == 'report']
    by_series = {r['series']: r for r in todays}
    brief = by_series.pop(BRIEF_SERIES, None)
    summary = by_series.pop(SUMMARY_SERIES, None)
    country_series = {c['series']: c for c in countries.all_countries()}
    sections = []
    for series, report in sorted(by_series.items(), key=lambda item: item[1]['series_label']):
        found, added, previous = new_points(report, DIGEST_POINTS)
        sections.append({'report': report, 'points': found, 'added': added, 'previous': previous,
                         'country': country_series.get(series)})
    dated = sorted({r['report_date'] for r in reports if r['kind'] == 'report' and r['report_date']})
    earlier = [d for d in dated if d < day]
    later = [d for d in dated if d > day]
    return render_template('digest.html',
                          day=day,
                          today=date.today(),
                          brief=brief,
                          brief_points=lead_points(brief, 6) if brief else [],
                          summary=summary,
                          summary_points=lead_points(summary, 6) if summary else [],
                          sections=sections,
                          previous_day=earlier[-1] if earlier else None,
                          next_day=later[0] if later else None,
                          latest_day=dated[-1] if dated else None)


@app.route('/countries')
def countries_page():
    """Every country with its latest report and risk score"""
    indexer.refresh()
    try:
        trends = {c['code']: c for c in charts.risk_trends(db_browser._connect, 90)['charts']}
        error = None
    except sqlite3.Error as exc:
        trends, error = {}, str(exc)
    cards = []
    for country in countries.all_countries():
        latest = indexer.latest(country['series'])
        cards.append({**country, 'latest': latest,
                      'reports': len(indexer.series_reports(country['series'])),
                      'risk': trends.get(country['code'])})
    return render_template('countries.html', cards=cards, error=error,
                          today=date.today(), risk_size=(charts.WIDTH, charts.HEIGHT))


@app.route('/country/<slug>')
def country_page(slug):
    """One country: latest report, what is new, risk over time, signals, reports"""
    country = countries.by_slug(slug)
    if country is None:
        abort(404)
    indexer.refresh()
    series = indexer.series_reports(country['series'])
    latest = series[-1] if series else None
    found, added, previous = new_points(latest, 8) if latest else ([], None, None)
    try:
        risk = charts.risk_trends(db_browser._connect, 90)
        country_risk = next((c for c in risk['charts'] if c['code'] == country['code']), None)
        signals = charts.recent_signals(db_browser._connect, country['code'])
        error = None
    except sqlite3.Error as exc:
        risk, country_risk, signals, error = None, None, [], str(exc)
    return render_template('country.html',
                          country=country,
                          latest=latest,
                          points=found,
                          added=added,
                          previous=previous,
                          risk=risk,
                          country_risk=country_risk,
                          signals=signals,
                          recent=list(reversed(series[-10:])),
                          total=len(series),
                          error=error,
                          today=date.today())


@app.route('/pipeline')
def pipeline_page():
    """Today's reports against the usual schedule, and how they loaded"""
    reports = indexer.refresh()
    day = charts.as_date(request.args.get('date', ''))
    try:
        summary = pipeline.overview(reports, db_browser._connect, day=day)
        error = None
    except sqlite3.Error as exc:
        summary = pipeline.overview(reports, None, day=day)
        error = str(exc)
    return render_template('pipeline.html',
                          summary=summary,
                          today=date.today(),
                          label=series_label,
                          series_slugs={f'mika-{slug}': slug for _, slug in countries.COUNTRIES.values()},
                          loader=pipeline.loader_version(INSERT_SCRIPT),
                          min_seen=pipeline.MIN_SEEN,
                          weeks=pipeline.LOOKBACK_WEEKS,
                          grace_hours=int(pipeline.GRACE.total_seconds() // 3600),
                          error=error)


@app.route('/api/search')
def api_search():
    """Recent reports matching a search, as JSON (used by watched searches)"""
    indexer.refresh()
    query = request.args.get('q', '').strip()
    days = min(max(1, request.args.get('days', WATCH_DAYS, type=int)), 365)
    since = date.today() - timedelta(days=days - 1)
    found = [r for r in indexer.search(query)
             if query and r['kind'] == 'report' and r['report_date'] and r['report_date'] >= since]
    found.sort(key=lambda r: (r['report_date'], r['score']), reverse=True)
    return jsonify({
        'query': query,
        'days': days,
        'count': len(found),
        'results': [{
            'title': r['title'] or r['filename'],
            'key': r['filename'],
            'url': url_for('view_report', safe_id=r['safe_id'], q=query),
            'date': r['report_date'].isoformat(),
            'series': r['series_label'],
            'hits': r['hits'],
        } for r in found[:20]],
    })


@app.errorhandler(404)
def not_found(e):
    """404 error handler"""
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    """500 error handler"""
    return render_template('500.html'), 500


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 8000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'

    print("\n" + "="*60)
    print("OpenClaw Reports UI")
    print("="*60)
    print(f"Reports directory: {indexer.reports_dir}")
    print(f"Database: {db_browser.db_path}")
    print(f"Indexed reports: {len(indexer._index)}")
    print(f"\nStarting server on http://{host}:{port}")
    print("Set HOST=0.0.0.0 to expose beyond localhost (e.g. over Tailscale)")
    print("Press Ctrl+C to stop")
    print("="*60 + "\n")

    app.run(host=host, port=port, debug=debug)
