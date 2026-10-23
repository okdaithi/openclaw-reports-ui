"""Links between countries, the category filter and the Charts page's day slider"""
import sqlite3
from datetime import date, timedelta

import pytest

from conftest import add_report
from modules import connections

TODAY = date.today()


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def signal(db_path, report_id, title, category='geopolitics', summary=None, impact=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute('INSERT INTO signals (report_id, category, title, summary, impact_score) VALUES (?, ?, ?, ?, ?)',
                     (report_id, category, title, summary, impact))


def source(db_path, report_id, url):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO sources (report_id, url, domain) VALUES (?, ?, 'example.com')", (report_id, url))


@pytest.mark.parametrize('text, own, found', [
    ('Beijing and Washington extend the truce', 'JPN', ['CHN', 'USA']),
    ('The US Treasury said', 'CHN', ['USA']),
    ('talk to us about it', 'CHN', []),                  # "us" is not the USA
    ('Indian Ocean shipping', 'AUS', []),                # nor is the Indian Ocean India
    ('India and South Korea sign a deal', 'JPN', ['IND', 'KOR']),
    ('China says China will', 'CHN', []),                # a country doesn't link to itself
    ('The Washington Post reports', 'CHN', []),
])
def test_signals_name_countries_by_name_capital_or_adjective(text, own, found):
    assert sorted(connections.mentioned(text, own)) == found


def test_links_add_up_mentions_shared_sources_and_shared_entities(db):
    china = add_report(db, 'CHN', day(1), risk=50)
    usa = add_report(db, 'USA', day(2), risk=50)
    japan = add_report(db, 'JPN', day(1), risk=50)
    signal(db, china, 'Beijing warns Washington over chips')
    signal(db, china, 'Taiwan drills continue')
    signal(db, usa, 'Talks with China stall', summary='Taiwan arms sale approved')
    signal(db, japan, 'Nothing about anyone')
    source(db, china, 'https://example.com/shared/')
    source(db, usa, 'https://example.com/shared')       # the same page
    source(db, japan, 'https://example.com/other')
    old = add_report(db, 'RUS', day(30), risk=50)
    for n in range(3):                                        # outside the window
        signal(db, old, f'Moscow and Beijing meet {n}')

    found = connections.links(lambda: sqlite3.connect(db), TODAY, 7)['links']
    assert [(l['a'], l['b']) for l in found] == [('CHN', 'USA')]
    link = found[0]
    assert (link['ab'], link['ba'], link['sources'], link['entities']) == (1, 1, 1, ['Taiwan'])
    assert link['weight'] == 1 + 1 + connections.SOURCE_WEIGHT + connections.ENTITY_WEIGHT


def test_the_entities_table_adds_names_to_look_for(db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO entities (name, type, first_seen_date) VALUES ('Nexperia', 'organization', '2026-01-01')")
        conn.execute("INSERT INTO entities (name, type, first_seen_date) VALUES ('OpenClaw Gateway', 'platform', '2026-01-01')")
    names = connections.entity_patterns(sqlite3.connect(db))
    assert 'Nexperia' in names and 'OpenClaw Gateway' not in names and 'Iran' in names


def test_the_link_api_and_its_evidence(client, db):
    china = add_report(db, 'CHN', day(0), risk=50)
    usa = add_report(db, 'USA', day(0), risk=50)
    for n in range(3):
        signal(db, china, f'US tariff move {n}', category='macro')
    signal(db, usa, 'Iran talks', summary='No mention of the other')
    source(db, china, 'https://news.example/a')
    source(db, usa, 'https://news.example/a')
    body = client.get('/api/geo/links?days=7').get_json()
    assert body['links'][0]['a'] == 'CHN' and body['links'][0]['weight'] == 3 + connections.SOURCE_WEIGHT
    evidence = client.get('/api/geo/links/CHN/USA?days=7').get_json()
    assert evidence['signal_count'] == 3 and evidence['signals'][0]['country'] == 'CHN'
    assert evidence['sources'] == [{'url': 'https://news.example/a', 'domain': 'example.com'}]
    assert client.get('/api/geo/links/CHN/CHN').status_code == 404
    assert client.get('/api/geo/links/CHN/XXX').status_code == 404


def test_top_signals_can_be_one_category(client, db):
    report = add_report(db, 'JPN', day(0), risk=30)
    signal(db, report, 'A security one', category='security', impact=10)
    signal(db, report, 'A macro one', category='macro', impact=90)
    only = client.get('/api/geo/JPN/signals?category=security').get_json()['signals']
    assert [s['title'] for s in only] == ['A security one']
    assert client.get('/api/geo/JPN/signals?category=nonsense').status_code == 400


def test_chart_signal_days_cover_the_window_cell_by_cell(client, db):
    report = add_report(db, 'CHN', day(2), risk=30)
    signal(db, report, 'one', category='security', impact=60)
    signal(db, report, 'two', category='security', impact=None)
    body = client.get(f'/api/charts/signals?days=5&end={day(0)}').get_json()
    assert body['days'] == [day(4), day(3), day(2), day(1), day(0)]
    assert body['cells']['security'][2] == [2, 60, 1, 0, 0]
    assert body['cells']['security'][0] is None and body['cells']['macro'] == [None] * 5


def test_the_charts_page_carries_what_the_slider_needs(client, db):
    add_report(db, 'CHN', day(3), risk=40)
    add_report(db, 'CHN', day(1), risk=55)
    page = client.get('/charts').get_data(as_text=True)
    assert 'id="chart-time" hidden' in page and 'js/charts.js' in page
    assert f'data-scores=\'[["{day(3)}", 40], ["{day(1)}", 55]]\'' in page
    assert 'id="risk-grid"' in page and 'id="grid-body"' in page
