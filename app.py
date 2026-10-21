#!/usr/bin/env python3
"""
OpenClaw Reports UI - Local Web Interface
Lightweight Flask app for browsing reports and querying intelligence database
"""
import os
from datetime import date, timedelta
from flask import Flask, render_template, request, jsonify, abort, redirect, url_for
from modules.report_indexer import ReportIndexer
from modules.markdown_renderer import MarkdownRenderer
from modules.db_browser import DatabaseBrowser

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['TEMPLATES_AUTO_RELOAD'] = True

MAX_PER_PAGE = 500
REPORTS_PER_PAGE = 50

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
}

# Initialize modules
indexer = ReportIndexer()
renderer = MarkdownRenderer()
db_browser = DatabaseBrowser()

# Scan reports on startup
print("Scanning reports...")
indexer.scan_reports()
print(f"Found {len(indexer._index)} reports")


@app.route('/')
def index():
    """Home page: today's reports first, then the overview"""
    indexer.scan_reports()
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
    indexer.scan_reports()
    query = request.args.get('q', '').strip()
    report_type = request.args.get('type', '')
    series = request.args.get('series', '')
    show_all = request.args.get('all') == '1'
    sort = request.args.get('sort', 'date')
    if sort not in REPORT_SORTS:
        sort = 'date'
    descending = request.args.get('dir', 'desc' if sort in ('date', 'size') else 'asc') == 'desc'

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
    html_content = renderer.render(content, strip_title=True)
    title = renderer.extract_title(content)
    if title == 'Untitled Report' and report['title']:
        title = report['title']
    
    return render_template('report_view.html',
                          report=report,
                          title=title,
                          content=html_content,
                          nearby=indexer.neighbours(report))


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


@app.route('/database/<table_name>')
def view_table(table_name):
    """View table contents with pagination"""
    # Get pagination parameters
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(1, request.args.get('per_page', 50, type=int)), MAX_PER_PAGE)
    offset = (page - 1) * per_page
    
    # Get filter parameters
    filters = {}
    for key in request.args:
        if key.startswith('filter_'):
            col_name = key.replace('filter_', '')
            filters[col_name] = request.args[key]
    
    # Get table info
    table_info = db_browser.get_table_info(table_name)
    
    # Query data
    rows, total = db_browser.query_table(
        table_name,
        limit=per_page,
        offset=offset,
        order_by='id' if any(c['name'] == 'id' for c in table_info['columns']) else None,
        filters=filters if filters else None
    )
    
    # Calculate pagination
    total_pages = (total + per_page - 1) // per_page
    
    return render_template('table_view.html',
                          table_name=table_name,
                          table_info=table_info,
                          rows=rows,
                          total=total,
                          page=page,
                          per_page=per_page,
                          total_pages=total_pages,
                          filters=filters)


@app.route('/database/query', methods=['GET', 'POST'])
def query_database():
    """Execute custom SELECT queries"""
    query = request.form.get('query', '') if request.method == 'POST' else request.args.get('query', '')
    results = []
    error = None
    
    if query:
        results, error = db_browser.execute_select(query)
    
    return render_template('query.html',
                          query=query,
                          results=results,
                          error=error)


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
