"""Country pages, the daily digest, watched-search JSON and read markers."""
from datetime import date, timedelta

import app as app_module
from conftest import add_report, add_signal, write_report
from modules import countries

TODAY = date.today()


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


YESTERDAY = """# China

## Security
- PLA drills near Taiwan continue across the Taiwan Strait with carrier groups.
"""

TODAY_REPORT = """# China

## Security
- Carrier groups keep PLA drills going near Taiwan and across the Strait.

## Economy
- Export controls proposed on forty technologies including chips and solar.
"""


def two_china_reports(reports_dir):
    (reports_dir / f'mika-china-{day(1)}.md').write_text(YESTERDAY, encoding='utf-8')
    (reports_dir / f'mika-china-{day(0)}.md').write_text(TODAY_REPORT, encoding='utf-8')


# --- countries -------------------------------------------------------------------------


def test_every_country_has_a_slug_and_a_series():
    assert countries.by_slug('south-korea') == {
        'code': 'KOR', 'name': 'South Korea', 'slug': 'south-korea', 'series': 'mika-south-korea'}
    assert countries.by_slug('atlantis') is None
    assert len(countries.all_countries()) == 16


def test_the_countries_page_shows_each_latest_report_and_risk(client, db, reports_dir):
    two_china_reports(reports_dir)
    add_report(db, 'CHN', day(1), risk=50)
    add_report(db, 'CHN', day(0), risk=58)
    text = client.get('/countries').get_data(as_text=True)
    assert '<a href="/country/china">China</a>' in text
    assert 'Risk <strong>58</strong>' in text and '+8' in text
    assert f'data-report-key="mika-china-{day(0)}.md"' in text
    assert text.count('class="card country-card"') == 16
    assert 'No reports yet.' in text  # the countries without files


def test_a_country_page_brings_its_report_risk_and_signals_together(client, db, reports_dir):
    two_china_reports(reports_dir)
    add_report(db, 'CHN', day(1), risk=50)
    report = add_report(db, 'CHN', day(0), risk=58)
    add_signal(db, report, 'security', impact=80, title='Drills near Taiwan')
    add_signal(db, report, 'macro', impact=None, title='Export controls proposed')
    other = add_report(db, 'USA', day(0), risk=40)
    add_signal(db, other, 'macro', impact=90, title='Not about China')
    text = client.get('/country/china').get_data(as_text=True)
    assert f'New since {(TODAY - timedelta(days=1)).strftime("%a %d %b")}' in text
    assert 'Export controls proposed on forty technologies' in text  # the new point
    assert 'Risk, last 90 days' in text and '<polyline' in text
    # Signals: this country only, highest impact first, unscored last
    assert text.index('Drills near Taiwan') < text.index('Export controls proposed</td>')
    assert 'Not about China' not in text
    assert client.get('/country/atlantis').status_code == 404


def test_a_country_without_reports_or_data_still_renders(client, db, reports_dir):
    text = client.get('/country/uae').get_data(as_text=True)
    assert 'No reports yet.' in text and 'No signals in the last 14 days.' in text


def test_charts_link_to_their_country(client, db):
    add_report(db, 'CHN', day(0), risk=58)
    text = client.get('/charts').get_data(as_text=True)
    assert '<a class="chart-link" href="/country/china">' in text


# --- the digest ------------------------------------------------------------------------


def test_the_digest_puts_a_days_reports_on_one_page(client, reports_dir):
    write_report(reports_dir, f'daily-brief-{day(0)}.md', 'Brief', body='- The day in one long sentence for the brief.')
    (reports_dir / f'mika-executive-summary-{day(0)}.md').write_text(
        '## SUMMARY OF THE DAY\n\n- Summary point long enough to count here.\n', encoding='utf-8')
    two_china_reports(reports_dir)
    write_report(reports_dir, f'daily-brief-hermes-test-{day(0)}.md', 'Test brief')
    text = client.get('/digest').get_data(as_text=True)
    assert 'The day in one long sentence for the brief.' in text
    assert '<li>Summary point long enough to count here.</li>' in text  # no title as section
    assert '<a href="/country/china">China</a>' in text
    assert '1 new point since' in text and 'Export controls proposed' in text
    assert 'Carrier groups keep PLA drills' not in text  # continuing, not new
    assert 'Test brief' not in text
    assert f'date={day(1)}' in text  # the day before has reports


def test_a_day_without_reports_points_to_the_latest_one(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(3)}.md', 'China', body='- An older point that is long enough.')
    text = client.get('/digest').get_data(as_text=True)
    assert 'No reports dated' in text and f'date={day(3)}' in text
    older = client.get(f'/digest?date={day(3)}').get_data(as_text=True)
    assert 'First report of the series' in older and 'An older point that is long enough.' in older


# --- watched searches -----------------------------------------------------------------------


def test_the_search_api_lists_recent_matching_reports(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China today', body='Rare earths.')
    write_report(reports_dir, f'mika-china-{day(30)}.md', 'China last month', body='Rare earths.')
    write_report(reports_dir, f'daily-brief-hermes-test-{day(0)}.md', 'Test', body='Rare earths.')
    body = client.get('/api/search?q=rare+earths').get_json()
    assert body['count'] == 1 and body['days'] == 7
    [hit] = body['results']
    assert hit['title'] == 'China today' and hit['key'] == f'mika-china-{day(0)}.md'
    assert hit['date'] == day(0) and hit['series'] == 'China'
    assert hit['url'] == f'/reports/mika-china-{day(0)}_md?q=rare+earths'
    assert client.get('/api/search?q=rare+earths&days=60').get_json()['count'] == 2
    assert client.get('/api/search?q=').get_json()['count'] == 0


def test_the_home_page_and_search_results_offer_watching(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='Taiwan.')
    assert 'id="watch-panel"' in client.get('/').get_data(as_text=True)
    text = client.get('/reports?q=taiwan').get_data(as_text=True)
    assert 'id="watch-search" data-term="taiwan"' in text


# --- read markers ----------------------------------------------------------------------


def test_report_links_carry_what_read_markers_need(client, reports_dir):
    write_report(reports_dir, f'archive/mika-china-{day(0)}.md', 'China')
    app_module.indexer.scan_reports()
    listing = client.get('/reports').get_data(as_text=True)
    assert f'data-report-key="mika-china-{day(0)}.md" data-report-date="{day(0)}"' in listing
    page = client.get(f'/reports/archive_mika-china-{day(0)}_md').get_data(as_text=True)
    assert f'window.markRead("mika-china-{day(0)}.md");' in page


def test_the_menu_has_the_new_pages(client, reports_dir):
    text = client.get('/digest').get_data(as_text=True)
    assert '<a href="/digest" class="active" aria-current="page">Digest</a>' in text
    assert '<a href="/countries">Countries</a>' in text
