"""Data for the Geographic page: each country's risk and signals, day by day.

The page draws the map in the browser from static/geo/world.json and
this module's JSON; the browser does the windowing (signals over the last
N days, risk carried forward from the latest score) so moving the day
slider needs no further requests.
"""
import sqlite3
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Optional

from modules import countries
from modules.charts import CATEGORIES, as_date

_SAME_COUNTRY = {'US': 'USA'}
MAX_DAYS = 120


def day_list(end: date, days: int) -> List[date]:
    return [end - timedelta(days=n) for n in range(days - 1, -1, -1)]


def timeline(connect: Callable[[], sqlite3.Connection], reports: Iterable[Dict],
             end: Optional[date] = None, days: int = 60) -> Dict:
    """Per country, per day: the risk score of that day's report, signal
    counts by category and bias, and the report to link to"""
    end = end or date.today()
    days = max(1, min(days, MAX_DAYS))
    dates = day_list(end, days)
    index = {d: n for n, d in enumerate(dates)}
    start = dates[0].isoformat()

    found = {}
    for code in countries.COUNTRIES:
        info = countries.describe(code)
        found[code] = {
            'name': info['name'],
            'slug': info['slug'],
            'risk': [None] * days,
            'signals': [[0] * len(CATEGORIES) for _ in dates],
            'negative': [0] * days,
            'positive': [0] * days,
            'reports': [None] * days,
            'latest': None,
        }

    conn = connect()
    try:
        risk_rows = conn.execute(
            '''SELECT country, substr(report_date, 1, 10), risk_score FROM reports
               WHERE report_type = 'country' AND substr(report_date, 1, 10) >= ?''',
            (start,)).fetchall()
        signal_rows = conn.execute(
            '''SELECT r.country, substr(r.report_date, 1, 10), s.category, s.directional_bias,
                      COUNT(*)
               FROM signals s JOIN reports r ON r.id = s.report_id
               WHERE substr(r.report_date, 1, 10) >= ?
               GROUP BY 1, 2, 3, 4''', (start,)).fetchall()
    finally:
        conn.close()

    for country, raw_date, risk in risk_rows:
        entry = found.get(_SAME_COUNTRY.get(country, country))
        day = as_date(raw_date)
        if entry is None or day not in index or risk is None:
            continue
        entry['risk'][index[day]] = risk
    for country, raw_date, category, bias, count in signal_rows:
        entry = found.get(_SAME_COUNTRY.get(country, country))
        day = as_date(raw_date)
        if entry is None or day not in index or category not in CATEGORIES:
            continue
        n = index[day]
        entry['signals'][n][CATEGORIES.index(category)] += count
        if bias == 'negative':
            entry['negative'][n] += count
        elif bias == 'positive':
            entry['positive'][n] += count

    series = {f"mika-{entry['slug']}": entry for entry in found.values()}
    for report in reports:
        entry = series.get(report['series'])
        if entry is None or report['kind'] != 'report' or not report['report_date']:
            continue
        link = {'id': report['safe_id'], 'date': report['report_date'].isoformat(),
                'title': report['title'] or report['filename']}
        if report['report_date'] in index:
            entry['reports'][index[report['report_date']]] = link
        if report['report_date'] <= end and (entry['latest'] is None or link['date'] >= entry['latest']['date']):
            entry['latest'] = link

    return {
        'end': end.isoformat(),
        'days': [d.isoformat() for d in dates],
        'categories': list(CATEGORIES),
        'countries': found,
    }


def top_signals(connect: Callable[[], sqlite3.Connection], code: str, end: date,
                days: int = 7, limit: int = 12) -> List[Dict]:
    """A country's signals over the `days` up to `end`: highest impact first,
    then the newest"""
    codes = [code] + [old for old, new in _SAME_COUNTRY.items() if new == code]
    start = (end - timedelta(days=days - 1)).isoformat()
    marks = ','.join('?' * len(codes))
    conn = connect()
    try:
        rows = conn.execute(
            f'''SELECT substr(r.report_date, 1, 10), s.category, s.title, s.summary,
                       s.impact_score, s.directional_bias
                FROM signals s JOIN reports r ON r.id = s.report_id
                WHERE r.country IN ({marks})
                  AND substr(r.report_date, 1, 10) BETWEEN ? AND ?
                ORDER BY s.impact_score IS NULL, s.impact_score DESC, r.report_date DESC, s.id
                LIMIT ?''', (*codes, start, end.isoformat(), limit)).fetchall()
    finally:
        conn.close()
    return [{'date': d, 'category': category, 'title': title, 'summary': summary,
             'impact': impact, 'bias': bias}
            for d, category, title, summary, impact, bias in rows]
