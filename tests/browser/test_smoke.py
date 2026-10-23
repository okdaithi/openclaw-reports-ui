"""Browser smoke tests: the interactive parts, in headless Chromium.

Skipped unless RUN_BROWSER_TESTS=1 (CI sets it; it needs `pip install
playwright` and `playwright install chromium`). CHROMIUM_PATH points at
another Chromium build, e.g. one already on the machine.

The app runs in a thread on a sample reports folder and database written
here: China and the USA, a week apart, with signals naming each other and a
source both cite, so the map has rings and a connection.
"""
import os
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

if os.environ.get('RUN_BROWSER_TESTS') != '1':
    pytest.skip('browser tests run with RUN_BROWSER_TESTS=1', allow_module_level=True)
sync_api = pytest.importorskip('playwright.sync_api')

from werkzeug.serving import make_server  # noqa: E402

import app as app_module  # noqa: E402

TODAY = date.today()
SCHEMA = (Path(__file__).resolve().parent.parent / 'schema.sql').read_text(encoding='utf-8')
LONG_REPORT = '''# {title}

## Geopolitics

Beijing and Washington extend the truce. {filler}

## Security

Drills near Taiwan. {filler}

## Economy

Tariffs and chips. {filler}

## Sources

- https://news.example/shared
'''


def build_sample(root: Path):
    reports = root / 'reports'
    reports.mkdir()
    db = root / 'intelligence.db'
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        for code, slug, other in (('CHN', 'china', 'Washington'), ('USA', 'usa', 'Beijing')):
            for back, risk in ((7, 50), (0, 58)):
                day = (TODAY - timedelta(days=back)).isoformat()
                (reports / f'mika-{slug}-{day}.md').write_text(
                    LONG_REPORT.format(title=f'{slug.upper()} report {day}', filler='More text. ' * 80), encoding='utf-8')
                rid = conn.execute(
                    '''INSERT INTO reports (report_date, report_type, country, generation_timestamp, model_used,
                           risk_score, signal_density, json_path, markdown_path)
                       VALUES (?, 'country', ?, ?, 'test', ?, 3, 'x.json', 'x.md')''',
                    (day, code, day, risk)).lastrowid
                for n, category in enumerate(('geopolitics', 'security', 'macro')):
                    conn.execute(
                        '''INSERT INTO signals (report_id, category, title, summary, impact_score, directional_bias)
                           VALUES (?, ?, ?, ?, ?, 'negative')''',
                        (rid, category, f'{other} talks {n}', f'{other} and Taiwan', 40 + n * 10))
                conn.execute("INSERT INTO sources (report_id, url, domain) VALUES (?, 'https://news.example/shared', 'news.example')", (rid,))
                other_name = 'USA' if code == 'CHN' else 'China'
                for name, kind in ((other_name, 'country'), ('Taiwan', 'country')):
                    conn.execute("INSERT OR IGNORE INTO entities (name, type, first_seen_date) VALUES (?, ?, '2026-01-01')", (name, kind))
                    conn.execute('''INSERT INTO entity_mentions (report_id, entity_id, mention_count, importance_score)
                                    VALUES (?, (SELECT id FROM entities WHERE name = ?), 2, 50)''', (rid, name))
    return reports, db


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    reports, db = build_sample(tmp_path_factory.mktemp('site'))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app_module.indexer, 'reports_dir', reports)
        patch.setattr(app_module.db_browser, 'db_path', str(db))
        patch.setattr('modules.report_indexer.SCAN_INTERVAL', 0)
        app_module.indexer.scan_reports()
        httpd = make_server('127.0.0.1', 0, app_module.app, threaded=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f'http://127.0.0.1:{httpd.server_port}'
        httpd.shutdown()


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    page = context.new_page()
    page.errors = []
    page.on('pageerror', lambda error: page.errors.append(str(error)))
    yield page
    assert page.errors == [], page.errors
    context.close()


def test_menus_open_one_at_a_time_and_close_on_escape(page, server):
    page.goto(server + '/')
    page.click('summary:has-text("Explore")')
    assert page.is_visible('.nav-menu-list a[href="/charts"]')
    page.click('summary:has-text("Data")')
    assert page.is_visible('.nav-menu-list a[href="/pipeline"]')
    assert not page.is_visible('.nav-menu-list a[href="/charts"]')
    page.keyboard.press('Escape')
    assert not page.is_visible('.nav-menu-list a[href="/pipeline"]')


def test_the_palette_goes_to_a_country(page, server):
    page.goto(server + '/reports')
    page.keyboard.press('Control+k')
    assert page.is_visible('#palette')
    page.fill('#palette-input', 'china')
    page.wait_for_selector('#palette-list li[aria-selected="true"]:has-text("China")')
    page.keyboard.press('Enter')
    page.wait_for_url('**/country/china')


def test_the_contents_bar_jumps_to_a_section_and_closes(page, server):
    page.goto(server + f'/reports/mika-china-{TODAY.isoformat()}_md')
    page.click('#contents summary')
    page.click('#contents a[href="#economy"]')
    page.wait_for_function("location.hash === '#economy'")
    assert page.evaluate("document.getElementById('contents').open") is False


def test_the_map_draws_rings_and_a_connection_and_opens_a_country(page, server):
    page.goto(server + '/geographic')
    page.wait_for_selector('.geo-land path.covered')
    assert page.locator('.geo-land path.covered').count() == 16
    page.wait_for_selector('.geo-links .link[data-link="CHN-USA"]')
    page.click('.geo-rank button[data-code="CHN"]')
    page.wait_for_selector('.geo-detail h3:has-text("China")')
    page.wait_for_selector('#geo-signal-list li .meta')
    page.click('.geo-filter[data-filter="security"]')
    page.wait_for_selector('#geo-signal-list li .meta:has-text("Security")')
    assert 'category=security' in page.url


def test_the_charts_slider_moves_the_signal_grid(page, server):
    page.goto(server + '/charts')
    page.wait_for_selector('#chart-time:not([hidden])')
    before = page.text_content('#grid-range')
    page.evaluate("var s = document.getElementById('chart-day'); s.value = 0; s.dispatchEvent(new Event('input'))")
    assert page.text_content('#grid-range') != before
    assert 'end=' in page.url


@pytest.mark.parametrize('path', ['/', '/geographic', '/charts', f'/reports/mika-usa-{TODAY.isoformat()}_md'])
def test_phone_pages_do_not_scroll_sideways(browser, server, path):
    context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    page = context.new_page()
    page.goto(server + path)
    page.wait_for_load_state('networkidle')
    assert not page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
    context.close()


def test_on_a_phone_the_search_button_opens_the_palette(browser, server):
    context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    page = context.new_page()
    page.goto(server + '/')
    assert not page.is_visible('#header-search')
    page.click('#search-open')
    page.fill('#palette-input', 'usa')
    page.wait_for_selector('#palette-list li:has-text("USA")')
    context.close()


def test_the_network_draws_and_a_click_opens_a_node(page, server):
    page.goto(server + '/network')
    page.wait_for_selector('#net-svg[data-settled="1"]')
    page.click('.net-node[data-id="USA"] circle')
    page.wait_for_selector('#net-panel h3:has-text("USA")')
    assert 'focus=USA' in page.url


def test_compare_draws_both_lines(page, server):
    page.goto(server + '/compare?a=CHN&b=USA')
    assert page.locator('.pair-chart .line').count() == 2
