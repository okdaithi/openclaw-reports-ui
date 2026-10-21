"""The Today panel, the reports list and moving between reports of a series."""
import re
from datetime import date, timedelta

import pytest

import app as app_module
from conftest import write_report
from modules.report_indexer import parse_name, series_label

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


# --- file names -----------------------------------------------------------------


@pytest.mark.parametrize('filename, series, kind', [
    ('mika-china-2026-10-21.md', 'mika-china', 'report'),
    ('mika-south-korea-2026-10-21.md', 'mika-south-korea', 'report'),
    ('daily-brief-2026-10-21.md', 'daily-brief', 'report'),
    ('macro_strategy_2026-10-17.md', 'macro_strategy', 'report'),
    ('daily-brief-hermes-test-2026-10-21.md', 'daily-brief-hermes-test', 'test'),
    ('weekly_report_status_2026-10-05_manual.md', 'weekly_report_status', 'system'),
    ('config-overview-2026-10-02.md', 'config-overview', 'system'),
    ('mika-china-2026-10-21-draft.md', 'mika-china', 'test'),
    ('PHASE3_DEPLOYMENT_LOG.md', None, 'system'),
    ('README.md', None, 'system'),
])
def test_series_date_and_kind_come_from_the_file_name(filename, series, kind):
    named = parse_name(filename)
    assert (named['series'], named['kind']) == (series, kind)
    if series:
        assert named['report_date'].year == 2026


def test_a_title_can_be_a_level_two_heading(reports_dir):
    (reports_dir / f'mika-executive-summary-{day(0)}.md').write_text(
        '## MIKA EXECUTIVE SUMMARY\n\n### Critical\n', encoding='utf-8'
    )
    (reports_dir / f'mika-uk-{day(0)}.md').write_text('No heading at all\n', encoding='utf-8')
    found = {r['series']: r['title'] for r in app_module.indexer.scan_reports()}
    assert found == {'mika-executive-summary': 'MIKA EXECUTIVE SUMMARY', 'mika-uk': ''}


def test_series_labels_read_like_names():
    assert series_label('mika-south-korea') == 'South Korea'
    assert series_label('mika-usa') == 'USA'
    assert series_label('mika-executive-summary') == 'Executive Summary'
    assert series_label('macro_strategy') == 'Macro Strategy'


# --- Today -------------------------------------------------------------------------


def test_home_leads_with_todays_brief_and_summary(client, reports_dir):
    write_report(reports_dir, f'daily-brief-{day(0)}.md', 'Brief for today')
    write_report(reports_dir, f'mika-executive-summary-{day(0)}.md', 'Summary for today')
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China today')
    write_report(reports_dir, f'mika-usa-{day(1)}.md', 'USA yesterday')
    write_report(reports_dir, f'daily-brief-hermes-test-{day(0)}.md', 'Test brief')
    page = client.get('/').get_data(as_text=True)
    assert 'Brief for today' in page and 'Summary for today' in page
    assert page.count('freshness freshness-today') == 2
    today_panel = page.split('Other reports dated today')[1].split('stats-grid')[0]
    assert 'China' in today_panel
    assert 'USA' not in today_panel  # dated yesterday
    assert 'Test brief' not in page  # test runs are left out


def test_home_says_when_todays_brief_is_not_out_yet(client, reports_dir):
    write_report(reports_dir, f'daily-brief-{day(1)}.md', 'Yesterday')
    write_report(reports_dir, f'mika-executive-summary-{day(3)}.md', 'Old summary')
    page = client.get('/').get_data(as_text=True)
    assert 'Not out yet today' in page
    assert 'Out of date: latest is' in page
    assert 'freshness freshness-stale' in page


def test_home_copes_with_no_reports(client):
    page = client.get('/').get_data(as_text=True)
    assert 'None found' in page and 'None yet.' in page


# --- the reports list -----------------------------------------------------------------


def titles(page: str) -> list[str]:
    return re.findall(r'<a href="/reports/[^"]+"[^>]*>([^<]+)</a>', page)


def test_design_notes_and_test_runs_are_hidden_until_asked_for(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China report')
    write_report(reports_dir, 'PHASE3_DEPLOYMENT_LOG.md', 'Deployment log')
    write_report(reports_dir, f'daily-brief-hermes-test-{day(0)}.md', 'Test brief')
    page = client.get('/reports').get_data(as_text=True)
    assert titles(page) == ['China report']
    assert '2 design notes and test runs hidden' in page
    shown = titles(client.get('/reports?all=1').get_data(as_text=True))
    assert sorted(shown) == ['China report', 'Deployment log', 'Test brief']


def test_archived_reports_are_listed_by_the_date_in_their_name(client, reports_dir):
    old = write_report(reports_dir, f'archive/intelligence-reports/mika-china-{day(5)}.md', 'Old China')
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'New China')
    old.touch()  # archiving gives the old file a newer mtime
    page = client.get('/reports').get_data(as_text=True)
    assert titles(page) == ['New China', 'Old China']
    groups = re.findall(r'<tr class="date-group">\s*<th[^>]*>([^<]+)</th>', page)
    assert len(groups) == 2


def test_series_filter_and_sorting(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(2)}.md', 'Beta')
    write_report(reports_dir, f'mika-china-{day(1)}.md', 'Alpha')
    write_report(reports_dir, f'mika-usa-{day(0)}.md', 'Gamma')
    page = client.get('/reports?series=mika-china').get_data(as_text=True)
    assert titles(page) == ['Alpha', 'Beta']
    assert 'Series:</strong> China' in page
    by_title = client.get('/reports?series=mika-china&sort=title').get_data(as_text=True)
    assert titles(by_title) == ['Alpha', 'Beta']
    reverse = client.get('/reports?sort=title&dir=desc').get_data(as_text=True)
    assert titles(reverse) == ['Gamma', 'Beta', 'Alpha']
    unknown = client.get('/reports?sort=nonsense').get_data(as_text=True)
    assert titles(unknown) == ['Gamma', 'Alpha', 'Beta']


def test_the_list_is_split_into_pages_that_keep_the_filters(client, reports_dir):
    count = app_module.REPORTS_PER_PAGE + 5
    for n in range(count):
        write_report(reports_dir, f'mika-china-{day(n)}.md', f'China {n}')
    first = client.get('/reports?series=mika-china').get_data(as_text=True)
    assert len(titles(first)) == app_module.REPORTS_PER_PAGE
    assert 'page=2' in first and 'series=mika-china' in first
    second = client.get('/reports?series=mika-china&page=2').get_data(as_text=True)
    assert len(titles(second)) == 5
    past_the_end = client.get('/reports?series=mika-china&page=99').get_data(as_text=True)
    assert titles(past_the_end) == titles(second)


def test_search_still_finds_text_inside_reports(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='Rare earth export quotas.')
    write_report(reports_dir, f'mika-usa-{day(0)}.md', 'USA', body='Tariffs.')
    page = client.get('/reports?q=rare+earth').get_data(as_text=True)
    assert titles(page) == ['China']
    assert '<mark>Rare</mark> <mark>earth</mark>' in page


# --- a series, report by report --------------------------------------------------------


def test_a_report_links_to_the_days_before_and_after_in_its_series(client, reports_dir):
    write_report(reports_dir, f'archive/mika-china-{day(2)}.md', 'Two days ago')
    write_report(reports_dir, f'mika-china-{day(1)}.md', 'Yesterday')
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'Today')
    write_report(reports_dir, f'mika-usa-{day(0)}.md', 'Another series')
    app_module.indexer.scan_reports()
    middle = client.get(f'/reports/mika-china-{day(1)}_md').get_data(as_text=True)
    assert 'href="/reports/archive_mika-china-' in middle and 'rel="prev"' in middle
    assert f'href="/reports/mika-china-{day(0)}_md" rel="next"' in middle
    assert 'All 3 China reports' in middle
    latest = client.get(f'/reports/mika-china-{day(0)}_md').get_data(as_text=True)
    assert 'rel="next"' not in latest and 'Latest ›' in latest


def test_an_old_link_follows_a_report_into_the_archive(client, reports_dir):
    write_report(reports_dir, f'archive/intelligence-reports/mika-china-{day(5)}.md', 'Archived')
    app_module.indexer.scan_reports()
    response = client.get(f'/reports/mika-china-{day(5)}_md')
    assert response.status_code == 302
    assert response.headers['Location'].endswith(
        f'/reports/archive_intelligence-reports_mika-china-{day(5)}_md'
    )
    assert client.get('/reports/no-such-report_md').status_code == 404


def test_a_design_note_has_no_series_links(client, reports_dir):
    write_report(reports_dir, 'README.md', 'Read me')
    app_module.indexer.scan_reports()
    page = client.get('/reports/README_md').get_data(as_text=True)
    assert 'series-nav' not in page.split('</style>')[1]
