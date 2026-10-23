"""Entities: the list, an entity's page, the network, the compare view, and
the map's links read from entity_mentions"""
import sqlite3
from datetime import date, timedelta

import pytest

from conftest import add_report, write_report
from modules import connections, entities

TODAY = date.today()


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def entity(db_path, name, kind='organization'):
    with sqlite3.connect(db_path) as conn:
        found = conn.execute('SELECT id FROM entities WHERE name = ?', (name,)).fetchone()
        if found:
            return found[0]
        return conn.execute("INSERT INTO entities (name, type, first_seen_date) VALUES (?, ?, '2026-01-01')",
                            (name, kind)).lastrowid


def mention(db_path, report_id, name, kind='organization', count=1, importance=50.0, tags='security'):
    entity_id = entity(db_path, name, kind)
    with sqlite3.connect(db_path) as conn:
        conn.execute('''INSERT INTO entity_mentions (report_id, entity_id, mention_count, importance_score, context_tags)
                        VALUES (?, ?, ?, ?, ?)''', (report_id, entity_id, count, importance, tags))


def signal(db_path, report_id, title, category='security'):
    with sqlite3.connect(db_path) as conn:
        conn.execute('INSERT INTO signals (report_id, category, title) VALUES (?, ?, ?)', (report_id, category, title))


@pytest.fixture
def world(db):
    """China and USA reports over the last days, mentioning NATO, Iran and each other"""
    china = add_report(db, 'CHN', day(1), risk=50)
    usa = add_report(db, 'USA', day(1), risk=60)
    usa_old = add_report(db, 'USA', day(3), risk=55)
    old = add_report(db, 'RUS', day(100), risk=40)
    mention(db, china, 'NATO', count=2)
    mention(db, china, 'USA', kind='country', count=3)
    mention(db, usa, 'NATO')
    mention(db, usa, 'Iran', kind='country', count=2)
    mention(db, usa, 'China', kind='country')
    mention(db, usa_old, 'Iran', kind='country')
    mention(db, old, 'NATO')                        # outside every window used here
    signal(db, usa, 'NATO ministers meet')
    signal(db, usa, 'Tehran warns')
    signal(db, usa, 'Unrelated')
    return {'china': china, 'usa': usa, 'db': db}


def connect(db):
    return lambda: sqlite3.connect(db)


def test_the_list_counts_reports_countries_and_days(world):
    listing = entities.entity_list(connect(world['db']), days=30)
    by_name = {e['name']: e for e in listing['entities']}
    assert set(by_name) == {'NATO', 'USA', 'Iran', 'China'}
    assert (by_name['NATO']['reports'], by_name['NATO']['countries'], by_name['NATO']['mentions']) == (2, 2, 3)
    assert by_name['Iran']['reports'] == 2 and sum(by_name['Iran']['daily']) == 3
    assert by_name['Iran']['daily'][-2] == 2 and by_name['Iran']['daily'][-4] == 1


def test_an_entity_page_has_its_countries_companions_and_signals(world, client, reports_dir):
    write_report(reports_dir, f'mika-usa-{day(1)}.md', 'USA report')
    detail = entities.entity_detail(connect(world['db']), 'Iran', days=30)
    assert detail['by_country'] == [{'code': 'USA', 'name': 'USA', 'reports': 2, 'mentions': 3}]
    assert {r['name'] for r in detail['related']} == {'NATO', 'China'}
    assert [s['title'] for s in detail['signals']] == ['Tehran warns']   # "Tehran" names Iran
    assert entities.entity_detail(connect(world['db']), 'Nobody') is None
    page = client.get('/entity/Iran?days=30').get_data(as_text=True)
    assert 'Tehran warns' in page and 'Whose reports mention it' in page
    assert f'/reports/mika-usa-{day(1)}_md' in page
    assert client.get('/entity/Nobody').status_code == 404


def test_the_network_joins_entities_with_the_countries_whose_reports_name_them(world, client):
    graph = entities.network(connect(world['db']), days=30)
    ids = {n['id'] for n in graph['nodes']}
    assert {'NATO', 'Iran', 'China', 'USA'} <= ids
    weights = {(l['source'], l['target']): l['weight'] for l in graph['links']}
    assert weights[('China', 'NATO')] == 2        # China's report names NATO; a USA report names both
    assert weights[('Iran', 'USA')] == 2          # two USA reports name Iran
    assert weights[('China', 'USA')] == 2         # each names the other once
    china = next(n for n in graph['nodes'] if n['id'] == 'China')
    assert china['slug'] == 'china' and china['type'] == 'country'
    assert client.get('/api/network?days=30').get_json()['links']


def test_map_links_come_from_entity_mentions_when_there_are_some(world):
    result = connections.links(connect(world['db']), TODAY, 7)
    assert result['from'] == 'entity_mentions'
    link = next(l for l in result['links'] if {l['a'], l['b']} == {'CHN', 'USA'})
    assert (link['ab'], link['ba']) == (3, 1) and link['entities'] == ['NATO']


def test_compare_shows_both_countries_and_what_they_share(world, client):
    page = client.get('/compare?a=CHN&b=USA&days=30').get_data(as_text=True)
    assert '3 mentions of USA in China&#39;s reports' in page or "3 mentions of USA in China's reports" in page
    assert 'Both mention' in page and '>NATO <span class="text-secondary">1 / 1</span>' in page
    assert 'class="line side-1"' not in page or 'class="line side-2"' in page
    assert client.get('/compare?a=XXX&b=YYY').status_code == 200    # falls back to China and the USA


def test_the_entities_page_filters_by_type(world, client):
    page = client.get('/entities?days=30&type=organization').get_data(as_text=True)
    assert '>NATO</a>' in page and '>Iran</a>' not in page
    assert client.get('/entities?days=30').status_code == 200


def test_a_country_page_lists_its_entities_and_sources(world, client):
    with sqlite3.connect(world['db']) as conn:
        conn.execute("INSERT INTO sources (report_id, url, domain) VALUES (?, 'https://a.example/x', 'a.example')", (world['china'],))
    page = client.get('/country/china').get_data(as_text=True)
    assert 'Mentions most' in page and '>NATO <span class="text-secondary">1</span>' in page
    assert 'a.example' in page and 'Connected to, last 14 days' in page
