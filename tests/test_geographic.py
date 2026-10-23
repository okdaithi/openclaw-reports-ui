"""The Geographic page: the map file, the per-day data and the signal list"""
import json
from datetime import date, timedelta
from pathlib import Path

from conftest import add_report, add_signal, write_report
from modules import countries

TODAY = date.today()
WORLD = Path(__file__).resolve().parent.parent / 'static' / 'geo' / 'world.json'


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def test_the_map_has_every_covered_country_inside_its_frame():
    world = json.loads(WORLD.read_text())
    by_id = {c['id']: c for c in world['countries']}
    assert set(countries.COUNTRIES) <= set(by_id)
    for code in countries.COUNTRIES:
        x, y = by_id[code]['c']
        assert 0 < x < world['width'] and 0 < y < world['height'], code
        assert by_id[code]['d'].startswith('M')
    # Russia crosses the 180th meridian; its outline must not jump across the map
    assert world['width'] * 0.6 < by_id['RUS']['c'][0] < world['width'] * 0.85


def test_the_page_loads_the_map_script_and_is_in_the_nav(client, db):
    page = client.get('/geographic').get_data(as_text=True)
    assert 'js/geographic.js' in page
    assert 'data-world="/static/geo/world.json?v=' in page and 'data-api="/api/geo"' in page
    assert '<a href="/geographic" class="active" aria-current="page">Geographic</a>' in page


def test_api_geo_gives_risk_signals_and_reports_per_day(client, db, reports_dir):
    china = add_report(db, 'CHN', day(1), risk=60)
    add_signal(db, china, 'security', bias='negative')
    add_signal(db, china, 'security', bias='positive')
    add_signal(db, china, 'macro', bias=None)
    old_us = add_report(db, 'US', day(2), risk=40)        # 'US' counts as the USA
    add_signal(db, old_us, 'technology')
    add_report(db, 'CHN', day(0), risk=None)              # not scored
    write_report(reports_dir, f'mika-china-{day(1)}.md', 'China yesterday')
    write_report(reports_dir, f'mika-china-{day(5)}.md', 'China earlier')

    body = client.get('/api/geo?days=10').get_json()
    assert body['days'][-1] == day(0) and len(body['days']) == 10
    cats = body['categories']
    cn = body['countries']['CHN']
    assert cn['risk'][-2:] == [60, None]
    assert cn['signals'][-2][cats.index('security')] == 2
    assert cn['signals'][-2][cats.index('macro')] == 1
    assert (cn['negative'][-2], cn['positive'][-2]) == (1, 1)
    assert cn['reports'][-2]['id'] == f'mika-china-{day(1)}_md'
    assert cn['reports'][-6]['title'] == 'China earlier'
    assert cn['latest']['date'] == day(1)
    us = body['countries']['USA']
    assert us['risk'][-3] == 40 and us['signals'][-3][cats.index('technology')] == 1
    assert body['countries']['IRL']['risk'] == [None] * 10


def test_api_geo_limits_the_days_and_reads_an_end_date(client, db):
    assert len(client.get('/api/geo?days=5000').get_json()['days']) == 120
    body = client.get(f'/api/geo?days=3&end={day(4)}').get_json()
    assert body['days'] == [day(6), day(5), day(4)]


def test_top_signals_come_highest_impact_first_within_the_window(client, db):
    recent = add_report(db, 'JPN', day(1), risk=30)
    add_signal(db, recent, 'macro', impact=None, title='Unscored')
    add_signal(db, recent, 'security', impact=80, title='Big')
    add_signal(db, recent, 'security', impact=20, title='Small')
    old = add_report(db, 'JPN', day(20), risk=30)
    add_signal(db, old, 'security', impact=99, title='Too old')
    titles = [s['title'] for s in client.get(f'/api/geo/JPN/signals?end={day(0)}&days=7').get_json()['signals']]
    assert titles == ['Big', 'Small', 'Unscored']
    # A window ending before yesterday leaves yesterday's signals out
    earlier = client.get(f'/api/geo/JPN/signals?end={day(2)}&days=30').get_json()['signals']
    assert [s['title'] for s in earlier] == ['Too old']
    assert client.get('/api/geo/XXX/signals').status_code == 404
