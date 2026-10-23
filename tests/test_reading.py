"""Search, the report page (contents, highlighting) and what changed between reports."""
import os
import re
import time
from datetime import date, timedelta

import app as app_module
from conftest import write_report
from modules.changes import compare, points
from modules.markdown_renderer import MarkdownRenderer

TODAY = date.today()


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def age(path, hours: int) -> None:
    """Give a file an mtime `hours` ago (files written together share one)"""
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


def titles(page: str) -> list[str]:
    return re.findall(r'<a href="/reports/[^"]+"[^>]*>([^<]+)</a>', page)


# --- search -------------------------------------------------------------------------


def test_search_lists_the_most_matches_first(client, reports_dir):
    # The report with fewer matches is the newer file, so ranking must beat recency
    age(write_report(reports_dir, f'mika-usa-{day(0)}.md', 'USA', body='Tariffs once.'), 1)
    age(write_report(reports_dir, f'mika-china-{day(1)}.md', 'China', body='Tariffs, tariffs, tariffs.'), 5)
    write_report(reports_dir, f'mika-uk-{day(2)}.md', 'UK', body='Nothing relevant.')
    page = client.get('/reports?q=tariffs').get_data(as_text=True)
    assert titles(page) == ['China', 'USA']
    assert '3 matches' in page and '1 match' in page
    by_date = client.get('/reports?q=tariffs&sort=date').get_data(as_text=True)
    assert titles(by_date) == ['USA', 'China']


def test_a_match_in_the_title_ranks_above_a_few_in_the_text(client, reports_dir):
    age(write_report(reports_dir, f'mika-china-{day(0)}.md', 'Rupiah watch', body='Nothing.'), 5)
    age(write_report(reports_dir, f'mika-uk-{day(0)}.md', 'UK', body='rupiah rupiah'), 1)
    page = client.get('/reports?q=rupiah').get_data(as_text=True)
    assert titles(page) == ['Rupiah watch', 'UK']


def test_snippets_mark_each_match_and_escape_the_report_text(client, reports_dir):
    body = ' '.join(['Filler sentence here.'] * 20).join(['Oil <script>x</script> first. ', ' Oil second.'])
    body += ' ' + ' '.join(['More filler.'] * 20) + ' Oil third. ' + ' '.join(['Pad.'] * 40) + ' Oil fourth.'
    write_report(reports_dir, f'mika-uae-{day(0)}.md', 'UAE', body=body)
    page = client.get('/reports?q=oil').get_data(as_text=True)
    assert page.count('<small class="snippet">') == 3  # at most three passages
    assert '<mark>Oil</mark>' in page
    assert '<script>x</script>' not in page and '&lt;script&gt;' in page


def test_search_links_open_the_report_with_the_term_marked(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='The plenum met.')
    page = client.get('/reports?q=plenum').get_data(as_text=True)
    assert f'/reports/mika-china-{day(0)}_md?q=plenum' in page
    report = client.get(f'/reports/mika-china-{day(0)}_md?q=plenum').get_data(as_text=True)
    assert 'Showing matches for "plenum"' in report
    assert 'var terms = ["plenum"];' in report


def test_the_type_filter_still_applies_to_a_search(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='Oil.')
    write_report(reports_dir, f'rss-digest-{day(0)}.md', 'Digest', body='Oil.')
    page = client.get('/reports?q=oil&type=RSS+Digest').get_data(as_text=True)
    assert titles(page) == ['Digest']


# --- the report page ------------------------------------------------------------------


LONG_REPORT = '# Title\n\n## Geopolitics\n\nText.\n\n### Detail\n\nMore.\n\n## Economy\n\nText.\n\n## Gaps\n\nText.\n'


def test_contents_list_the_headings_below_the_title():
    html, contents = MarkdownRenderer().render_with_contents(LONG_REPORT, strip_title=True)
    assert contents == [
        (1, 'geopolitics', 'Geopolitics'), (2, 'detail', 'Detail'),
        (1, 'economy', 'Economy'), (1, 'gaps', 'Gaps'),
    ]
    assert '<h2 id="geopolitics">' in html and '<h1' not in html


def test_a_long_report_gets_a_contents_bar_and_a_short_one_does_not(client, reports_dir):
    (reports_dir / f'mika-china-{day(0)}.md').write_text(LONG_REPORT, encoding='utf-8')
    write_report(reports_dir, f'mika-usa-{day(0)}.md', 'Short', body='## Only one\n\nText.')
    app_module.indexer.scan_reports()
    long_page = client.get(f'/reports/mika-china-{day(0)}_md').get_data(as_text=True)
    assert '<div class="report-contents" role="navigation" aria-label="Contents">' in long_page
    assert '<a href="#economy">Economy</a>' in long_page
    assert '<a href="#top">Back to top</a>' in long_page
    # The report is not squeezed beside a sidebar
    assert 'report-layout' not in long_page
    short_page = client.get(f'/reports/mika-usa-{day(0)}_md').get_data(as_text=True)
    assert 'class="report-contents"' not in short_page


def test_file_details_are_folded_away_and_icons_are_not_the_only_label(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China')
    app_module.indexer.scan_reports()
    page = client.get(f'/reports/mika-china-{day(0)}_md').get_data(as_text=True)
    assert '<details class="card report-details">' in page
    for icon in ('📁', '📅', '📊'):
        assert icon not in page


# --- what changed ------------------------------------------------------------------------


YESTERDAY = """# China

## Security
- PLA drills near Taiwan continue across the Taiwan Strait with carrier groups.
  **Confidence: High**
- Cyber intrusion attributed to PRC-linked group against regional ministry.

## Economy
- Property sector slump deepens as developer defaults spread across provinces.
"""

TODAY_REPORT = """# China

## Security
- Carrier groups keep PLA drills going near Taiwan and across the Strait.

## Economy
- Export controls proposed on forty technologies including chips and solar.
"""


def test_points_join_a_bullets_continuation_lines():
    assert points(YESTERDAY)[0] == (
        'Security',
        'PLA drills near Taiwan continue across the Taiwan Strait with carrier groups. Confidence: High',
    )


def test_compare_sorts_points_into_new_continuing_and_dropped():
    changes = compare(YESTERDAY, TODAY_REPORT)
    assert changes['counts'] == {'added': 1, 'dropped': 2, 'kept': 1, 'new_total': 2}
    assert changes['added'] == [('Economy', [
        'Export controls proposed on forty technologies including chips and solar.'])]
    assert [section for section, _ in changes['dropped']] == ['Security', 'Economy']


def test_the_changes_page_compares_with_the_previous_report(client, reports_dir):
    (reports_dir / f'mika-china-{day(1)}.md').write_text(YESTERDAY, encoding='utf-8')
    (reports_dir / f'mika-china-{day(0)}.md').write_text(TODAY_REPORT, encoding='utf-8')
    app_module.indexer.scan_reports()
    report = client.get(f'/reports/mika-china-{day(0)}_md').get_data(as_text=True)
    assert f'href="/reports/mika-china-{day(0)}_md/changes"' in report
    page = client.get(f'/reports/mika-china-{day(0)}_md/changes').get_data(as_text=True)
    assert '1 new point, 1 continuing, 2 no longer mentioned.' in page
    assert 'Export controls proposed' in page
    # The first report of a series has nothing to compare with
    first = client.get(f'/reports/mika-china-{day(1)}_md/changes')
    assert first.status_code == 302 and first.headers['Location'].endswith(f'/reports/mika-china-{day(1)}_md')


# --- full-text search ----------------------------------------------------------------


def test_every_word_must_appear_but_not_side_by_side(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='Export rules tightened on chips.')
    write_report(reports_dir, f'mika-usa-{day(0)}.md', 'USA', body='Chips are cheap.')
    assert titles(client.get('/reports?q=chips+export').get_data(as_text=True)) == ['China']


def test_a_quoted_phrase_must_appear_as_written(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='New export controls on chips.')
    write_report(reports_dir, f'mika-usa-{day(0)}.md', 'USA', body='Controls on export of chips.')
    page = client.get('/reports?q=%22export+controls%22').get_data(as_text=True)
    assert titles(page) == ['China']
    assert '<mark>export controls</mark>' in page


def test_a_word_finds_its_other_forms(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='New tariffs announced.')
    page = client.get('/reports?q=tariff').get_data(as_text=True)
    assert titles(page) == ['China'] and '<mark>tariffs</mark>' in page


def test_punctuation_alone_finds_nothing_without_an_error(client, reports_dir):
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China')
    for query in ('%22', '-', '*', 'AND', '%22%22'):
        response = client.get(f'/reports?q={query}')
        assert response.status_code == 200, query


def test_the_index_follows_files_that_change_or_go(client, reports_dir):
    path = write_report(reports_dir, f'mika-china-{day(0)}.md', 'China', body='Lithium.')
    assert titles(client.get('/reports?q=lithium').get_data(as_text=True)) == ['China']
    path.write_text('# China\n\nCobalt now.\n', encoding='utf-8')
    age(path, 0)  # a new mtime
    assert titles(client.get('/reports?q=lithium').get_data(as_text=True)) == []
    assert titles(client.get('/reports?q=cobalt').get_data(as_text=True)) == ['China']
    path.unlink()
    assert titles(client.get('/reports?q=cobalt').get_data(as_text=True)) == []


def test_the_folder_is_rescanned_only_after_the_interval(reports_dir, monkeypatch):
    monkeypatch.setattr('modules.report_indexer.SCAN_INTERVAL', 3600)
    indexer = app_module.indexer
    indexer.scan_reports()
    write_report(reports_dir, f'mika-china-{day(0)}.md', 'China')
    assert indexer.refresh() == []  # still the scan from a moment ago
    indexer._scanned_at -= 3600
    assert [r['title'] for r in indexer.refresh()] == ['China']
