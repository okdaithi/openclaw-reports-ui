"""Table filters, sorting and CSV export, the Query page, and the charts."""
import csv
import io
from datetime import date, timedelta

import app as app_module
from conftest import add_report, add_signal
from modules import charts

TODAY = date.today()


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def csv_rows(response):
    return list(csv.reader(io.StringIO(response.get_data(as_text=True))))


# --- tables ------------------------------------------------------------------------


def test_a_table_can_be_filtered_and_sorted_and_the_links_keep_both(client, db):
    for n, country in enumerate(['CHN', 'USA', 'CHN', 'DEU'] * 20):
        add_report(db, country, day(n), risk=n)
    page = client.get('/database/reports?filter_country=CHN&sort=risk_score&dir=asc&per_page=25')
    text = page.get_data(as_text=True)
    assert '40 matching rows' in text and 'page 1 of 2' in text
    assert 'name="filter_country" value="CHN"' in text
    # Paging keeps the filter, the order and the page size
    assert 'filter_country=CHN' in text and 'sort=risk_score' in text and 'dir=asc' in text
    assert 'page=2' in text
    # Column headings sort, and the current one flips direction
    assert 'aria-sort="ascending"' in text


def test_unknown_filters_and_sort_columns_are_ignored(client, db):
    add_report(db, 'CHN', day(0), risk=10)
    text = client.get('/database/reports?filter_nope=1&sort=nope').get_data(as_text=True)
    assert '1 rows' in text and 'sorted by <code>id</code>' in text


def test_a_tables_csv_has_every_matching_row_in_order(client, db):
    for n in range(60):
        add_report(db, 'CHN' if n % 2 else 'USA', day(n), risk=n)
    response = client.get('/database/reports/csv?filter_country=CHN&sort=risk_score&dir=asc')
    assert response.headers['Content-Type'].startswith('text/csv')
    assert 'filename="reports.csv"' in response.headers['Content-Disposition']
    rows = csv_rows(response)
    header, body = rows[0], rows[1:]
    assert 'risk_score' in header and len(body) == 30
    scores = [int(row[header.index('risk_score')]) for row in body]
    assert scores == sorted(scores)


def test_an_unknown_table_is_not_found(client, db):
    assert client.get('/database/nope').status_code == 404
    assert client.get('/database/nope/csv').status_code == 404


# --- the Query page ------------------------------------------------------------------


def test_every_example_query_runs(client, db):
    for label, sql in app_module.EXAMPLE_QUERIES:
        rows, error = app_module.db_browser.execute_select(sql)
        assert error is None, (label, error)


def test_the_query_page_offers_examples_and_the_schema(client, db):
    text = client.get('/database/query').get_data(as_text=True)
    assert 'Country risk trend' in text and 'v_signal_heatmap_7d' in text
    assert 'data-text="risk_score"' in text  # a column in the schema sidebar


def test_a_long_result_is_cut_short_and_the_csv_has_it_all(client, db, monkeypatch):
    monkeypatch.setattr(app_module, 'MAX_QUERY_ROWS', 3)
    for n in range(5):
        add_report(db, 'CHN', day(n), risk=n)
    text = client.get('/database/query?query=SELECT+id+FROM+reports').get_data(as_text=True)
    assert 'Results: 3+ rows' in text and 'Only the first 3 rows are shown' in text
    rows = csv_rows(client.get('/database/query/csv?query=SELECT+id+FROM+reports'))
    assert len(rows) == 6


def test_a_query_csv_refuses_what_the_page_refuses(client, db):
    assert client.get('/database/query/csv?query=DELETE+FROM+reports').status_code == 400


def test_only_the_current_page_is_marked_in_the_menu(client, db):
    text = client.get('/database/query').get_data(as_text=True)
    assert text.count('aria-current="page"') == 1
    assert '<a href="/database/query" class="active"' in text


# --- charts --------------------------------------------------------------------------


def test_risk_lines_leave_out_unscored_days_and_bad_dates(db):
    add_report(db, 'CHN', day(3), risk=40)
    add_report(db, 'CHN', day(2), risk=0)
    add_report(db, 'CHN', day(1), risk=55)
    add_report(db, 'US', day(1), risk=30)
    add_report(db, 'USA', day(0), risk=35)
    add_report(db, 'RUS', 'null', risk=20)
    trends = charts.risk_trends(app_module.db_browser._connect, days=None)
    by_code = {chart['code']: chart for chart in trends['charts']}
    assert set(by_code) == {'CHN', 'USA'}  # 'US' joins 'USA'; RUS had no usable date
    china = by_code['CHN']
    assert [score for _, _, _, score in china['points']] == [40, 55]
    assert china['unscored'] == 1 and china['change'] == 15
    assert by_code['USA']['latest'][1] == 35
    assert trends['skipped'] == 1
    assert trends['first'] == TODAY - timedelta(days=3)  # the axis starts at the first score


def test_the_signal_grid_counts_signals_per_category_and_day(db):
    report = add_report(db, 'CHN', day(1), risk=10)
    add_signal(db, report, 'security', impact=80)
    add_signal(db, report, 'security', impact=40, bias='positive')
    old = add_report(db, 'USA', day(30), risk=10)
    add_signal(db, old, 'macro')
    grid = charts.signal_grid(app_module.db_browser._connect)
    assert grid['total'] == 2
    rows = dict(grid['grid'])
    cell = rows['security'][-2]  # yesterday
    assert (cell['count'], cell['avg_impact'], cell['negative'], cell['positive']) == (2, 60, 1, 1)
    assert rows['macro'] == [None] * 7
    assert grid['latest'] == TODAY - timedelta(days=1)


def test_the_charts_page_offers_the_last_week_with_signals(client, db):
    report = add_report(db, 'CHN', day(40), risk=10)
    add_signal(db, report, 'macro')
    text = client.get('/charts').get_data(as_text=True)
    assert 'No signals in these 7 days' in text
    assert 'have no risk score' not in text  # nothing unscored in this data
    assert f'end={day(40)}' in text
    then = client.get(f'/charts?end={day(40)}').get_data(as_text=True)
    assert 'No signals in these 7 days' not in then and 'Back to the last 7 days' in then


def test_blank_and_zero_risk_scores_are_both_counted_as_not_scored(client, db):
    add_report(db, 'CHN', day(2), risk=40)
    add_report(db, 'CHN', day(1), risk=0)
    add_report(db, 'CHN', day(0), risk=None)
    text = client.get('/charts').get_data(as_text=True)
    assert '2 of 3 reports in this range have no risk score' in text


def test_the_charts_page_reports_a_database_it_cannot_read(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.db_browser, 'db_path', str(tmp_path / 'missing.db'))
    text = client.get('/charts').get_data(as_text=True)
    assert 'The database could not be read' in text


# --- health --------------------------------------------------------------------------


def test_healthz_is_ok_with_reports_and_a_readable_database(client, db, reports_dir):
    (reports_dir / f'mika-china-{day(2)}.md').write_text('# China\n', encoding='utf-8')
    response = client.get('/healthz')
    assert response.status_code == 200
    body = response.get_json()
    assert (body['status'], body['db'], body['reports'], body['latest_report_days']) == ('ok', 'ok', 1, 2)


def test_healthz_fails_without_a_database(client, reports_dir, tmp_path, monkeypatch):
    (reports_dir / f'mika-china-{day(0)}.md').write_text('# China\n', encoding='utf-8')
    monkeypatch.setattr(app_module.db_browser, 'db_path', str(tmp_path / 'missing.db'))
    response = client.get('/healthz')
    assert response.status_code == 503 and response.get_json()['db'].startswith('error')
