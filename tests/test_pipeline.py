"""The Pipeline page: today's reports against the schedule learnt from history"""
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import app as app_module
from conftest import add_report, add_signal, write_report
from modules import pipeline

DAY = date(2026, 10, 23)  # a Friday


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute)


def weeks_ago(n):
    return DAY - timedelta(weeks=n)


def row(series='mika-usa', day=DAY, loaded=None, risk=50, signals=10, sources=5, density=None):
    return {'day': day, 'series': series, 'country': 'USA', 'loaded': loaded or at(day, 5),
            'risk': risk, 'density': signals if density is None else density,
            'signals': signals, 'sources': sources}


def by_series(lines):
    return {line['series']: line for line in lines}


def test_a_series_is_expected_when_it_came_on_two_of_the_last_three_same_weekdays():
    arrivals = {
        'weekly': {weeks_ago(1): at(weeks_ago(1), 6), weeks_ago(3): at(weeks_ago(3), 7, 30)},
        'once': {weeks_ago(2): at(weeks_ago(2), 6)},
        'other-day': {DAY - timedelta(days=1): at(DAY, 6), DAY - timedelta(days=8): at(DAY, 6)},
    }
    due = pipeline.expected(arrivals, DAY)
    assert set(due) == {'weekly'}
    assert due['weekly'] == at(DAY, 8)  # the usual (median) arrival plus two hours


def test_one_late_arrival_does_not_push_the_due_time_out():
    times = {weeks_ago(1): at(weeks_ago(1), 5), weeks_ago(2): at(weeks_ago(2), 23),
             weeks_ago(3): at(weeks_ago(3), 5, 10)}
    assert pipeline.expected({'mika-usa': times}, DAY)['mika-usa'] == at(DAY, 7, 10)


def test_states_late_waiting_ok_and_a_failed_load():
    files = {
        'daily-brief': {weeks_ago(1): at(weeks_ago(1), 8), weeks_ago(2): at(weeks_ago(2), 8)},
        'mika-usa': {weeks_ago(1): at(weeks_ago(1), 5), weeks_ago(2): at(weeks_ago(2), 5), DAY: at(DAY, 5)},
        'mika-china': {DAY: at(DAY, 4)},  # file arrived, row never did
        'rss-digest': {weeks_ago(1): at(weeks_ago(1), 21), weeks_ago(2): at(weeks_ago(2), 21)},
    }
    rows = [row('mika-usa', weeks_ago(1)), row('mika-usa', weeks_ago(2)), row('mika-usa')]
    lines = by_series(pipeline.day_status(DAY, at(DAY, 12), files, rows))
    assert lines['daily-brief']['state'] == 'late' and lines['daily-brief']['late'] == ['file']
    assert lines['mika-usa']['state'] == 'ok'
    assert lines['mika-china']['state'] == 'late' and lines['mika-china']['late'] == ['database row']
    assert not lines['mika-china']['expected']
    assert lines['rss-digest']['state'] == 'waiting'


def test_a_country_file_waits_for_its_row_for_the_grace_period():
    files = {'mika-china': {DAY: at(DAY, 11)}}
    lines = by_series(pipeline.day_status(DAY, at(DAY, 12), files, []))
    assert lines['mika-china']['state'] == 'waiting'
    assert lines['mika-china']['waiting'] == ['database row']


@pytest.mark.parametrize('changes, state, issues', [
    ({}, 'ok', []),
    ({'signals': 0}, 'empty', ['no signals']),
    ({'sources': 0}, 'partial', ['no sources']),
    ({'risk': None}, 'partial', ['no risk score']),
    ({'density': 12}, 'partial', ['signal count says 12']),
])
def test_what_a_loaded_row_is_missing(changes, state, issues):
    files = {'mika-usa': {DAY: at(DAY, 5)}}
    lines = by_series(pipeline.day_status(DAY, at(DAY, 12), files, [row(**changes)]))
    assert (lines['mika-usa']['state'], lines['mika-usa']['issues']) == (state, issues)


def test_health_lists_late_and_empty_reports():
    summary = {'lines': [{'series': 'a', 'state': 'late'}, {'series': 'b', 'state': 'empty'},
                         {'series': 'c', 'state': 'partial'}]}
    assert pipeline.health(summary) == {'late': ['a'], 'empty': ['b'], 'ok': False}


def test_the_loader_version_comes_from_the_script_header(tmp_path):
    script = tmp_path / 'insert.sh'
    script.write_text('#!/bin/bash\n# Version: 1.4\n')
    assert pipeline.loader_version(script) == '1.4'
    assert pipeline.loader_version(tmp_path / 'missing.sh') is None


def test_load_times_are_read_as_utc():
    local = pipeline.local_time('2026-10-22 21:01:43')
    offset = datetime(2026, 10, 22, 21, 1, 43).astimezone().utcoffset()
    assert local == datetime(2026, 10, 22, 21, 1, 43) + offset
    assert pipeline.local_time(None) is None and pipeline.local_time('null') is None


# The page and /healthz, on files and rows written for the test

def written_at(path, moment):
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))


def loaded_at(db_path, report_id, moment):
    """created_at as the loader writes it: datetime('now'), in UTC"""
    utc = moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(db_path) as conn:
        conn.execute('UPDATE reports SET created_at = ? WHERE id = ?', (utc, report_id))


@pytest.fixture
def schedule(reports_dir, db, monkeypatch):
    """USA reports at 05:00 and the brief at 08:00 on the last two Fridays;
    today USA has come and loaded without sources, the brief hasn't come"""
    monkeypatch.setattr(pipeline, '_now', lambda: at(DAY, 12))
    for n in (1, 2):
        day = weeks_ago(n)
        written_at(write_report(reports_dir, f'mika-usa-{day}.md', 'USA'), at(day, 5))
        written_at(write_report(reports_dir, f'daily-brief-{day}.md', 'Brief'), at(day, 8))
        report = add_report(db, 'USA', day.isoformat(), risk=40)
        loaded_at(db, report, at(day, 5, 1))
    written_at(write_report(reports_dir, f'mika-usa-{DAY}.md', 'USA'), at(DAY, 5))
    today = add_report(db, 'USA', DAY.isoformat(), risk=None)
    add_signal(db, today, 'security')
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE reports SET signal_density = 1 WHERE id = ?', (today,))
    loaded_at(db, today, at(DAY, 5, 1))
    return db


def test_the_pipeline_page_shows_late_and_partial_reports(client, schedule):
    page = client.get(f'/pipeline?date={DAY}').get_data(as_text=True)
    assert 'Pipeline, Fri 23 Oct 2026' in page
    assert 'Daily Brief' in page and 'file not in' in page
    assert 'no risk score, no sources' in page
    assert '1 late' in page and '1 gaps' in page


def test_healthz_is_degraded_while_a_report_is_late(client, schedule):
    response = client.get('/healthz')
    body = response.get_json()
    assert response.status_code == 200
    assert body['status'] == 'degraded'
    assert body['pipeline'] == {'late': ['daily-brief'], 'empty': [], 'ok': False}
    assert client.get('/healthz?strict=1').status_code == 503


def test_healthz_is_ok_once_everything_is_in(client, schedule, reports_dir):
    written_at(write_report(reports_dir, f'daily-brief-{DAY}.md', 'Brief'), at(DAY, 8))
    body = client.get('/healthz?strict=1').get_json()
    assert body['status'] == 'ok' and body['pipeline']['ok']


def test_the_pipeline_page_works_without_a_database(client, reports_dir, monkeypatch):
    monkeypatch.setattr(app_module.db_browser, 'db_path', str(reports_dir / 'missing.db'))
    write_report(reports_dir, f'daily-brief-{date.today()}.md', 'Brief')
    response = client.get('/pipeline')
    assert response.status_code == 200
    assert 'Database unavailable' in response.get_data(as_text=True)


def test_the_signal_grid_says_when_no_signal_has_an_impact_score(client, db):
    report = add_report(db, 'CHN', date.today().isoformat(), risk=10)
    add_signal(db, report, 'security', impact=None)
    add_signal(db, report, 'macro', impact=40)
    add_signal(db, report, 'macro', impact=None)
    page = client.get('/charts').get_data(as_text=True)
    assert '1 signal; no impact scores' in page
    assert '2 signals; average impact 40 (1 scored)' in page
