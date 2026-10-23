"""Site-wide: the menus, the go-to palette, static files and the query time limit"""
import re
import sqlite3

import app as app_module
from conftest import write_report


def test_menus_group_the_pages_and_mark_where_you_are(client, reports_dir, db):
    page = client.get('/charts').get_data(as_text=True)
    assert '<summary class="active">Explore<span class="nav-here">: Charts</span></summary>' in page
    assert '<a href="/charts" class="active" aria-current="page">Charts</a>' in page
    assert re.search(r'<summary>Data</summary>', page)
    for url in ('/pipeline', '/database', '/database/query', '/countries', '/geographic'):
        assert f'<a href="{url}"' in page
    assert '<a class="skip-link" href="#main">Skip to content</a>' in page and '<main id="main"' in page


def test_static_files_carry_a_version_and_may_be_cached(client, reports_dir):
    page = client.get('/').get_data(as_text=True)
    css = re.search(r'href="(/static/css/app\.css\?v=\d+)"', page).group(1)
    response = client.get(css)
    assert response.status_code == 200 and 'max-age=604800' in response.headers['Cache-Control']
    assert re.search(r'src="/static/js/palette\.js\?v=\d+"', page)


def test_the_palette_lists_pages_countries_entities_and_reports(client, reports_dir, db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO entities (name, type, first_seen_date, total_mentions) VALUES ('Strait of Hormuz', 'infrastructure', '2026-10-01', 7)")
        conn.execute("INSERT INTO entities (name, type, first_seen_date, total_mentions) VALUES ('Hormuz Unused', 'event', '2026-10-01', 0)")
    write_report(reports_dir, 'mika-usa-2026-10-20.md', 'Hormuz shipping update', body='Tankers wait at Hormuz.')
    app_module.indexer.scan_reports()

    items = client.get('/api/palette?q=hormuz').get_json()['items']
    kinds = [(i['kind'], i['label']) for i in items]
    assert ('Entity', 'Strait of Hormuz') in kinds and ('Entity', 'Hormuz Unused') not in kinds
    assert ('Report', 'Hormuz shipping update') in kinds
    assert kinds[-1] == ('Search', 'Search all reports for "hormuz"')

    assert [i['label'] for i in client.get('/api/palette?q=KOR').get_json()['items'] if i['kind'] == 'Country'] == ['South Korea']
    assert [i['url'] for i in client.get('/api/palette?q=query').get_json()['items'] if i['kind'] == 'Page'] == ['/database/query']
    everything = client.get('/api/palette').get_json()['items']
    assert sum(i['kind'] == 'Page' for i in everything) == len(app_module.PALETTE_PAGES)
    assert sum(i['kind'] == 'Country' for i in everything) == 16


ENDLESS = 'WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n'


def test_a_query_that_runs_too_long_is_stopped(db):
    rows, error = app_module.db_browser.execute_select(ENDLESS, time_limit=0.3)
    assert rows == [] and 'stopped after 0.3 seconds' in error


def test_the_query_page_says_when_it_stopped_a_query(client, db, monkeypatch):
    monkeypatch.setattr(app_module, 'QUERY_SECONDS', 0.3)
    page = client.post('/database/query', data={'query': ENDLESS}).get_data(as_text=True)
    assert 'The query was stopped after 0.3 seconds' in page


def test_a_with_query_is_allowed_but_still_read_only(db):
    rows, error = app_module.db_browser.execute_select('WITH a AS (SELECT 1 AS v) SELECT v FROM a')
    assert (rows, error) == ([{'v': 1}], None)
    _, error = app_module.db_browser.execute_select('WITH a AS (SELECT 1) DELETE FROM reports')
    assert 'forbidden keyword: DELETE' in error
