"""Chart data from the intelligence database, laid out for inline SVG.

The pages draw these with plain SVG in the templates (no chart library and no
CDN, like the rest of the app). The data needs some care, as found on
2026-10-21:
- most risk scores were 0 (227 of 315 country rows): the loader stored 0
  when a report had no score. Loader v1.3 (2026-10-22) stores NULL and its
  backfill turns the old zeros into NULL; either way the lines leave them out
  and count them as "not scored";
- a few report dates are the text 'null' or a full timestamp, so only the
  first 10 characters are used and anything that isn't a date is skipped;
- the USA appears as both 'US' and 'USA'.
"""
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

from modules.countries import COUNTRY_NAMES

_SAME_COUNTRY = {'US': 'USA'}
CATEGORIES = ('geopolitics', 'macro', 'technology', 'supply_chain', 'security', 'governance')

# Size of one small risk chart, in SVG units
WIDTH, HEIGHT, PAD = 280, 90, 4


def as_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def risk_trends(connect: Callable[[], sqlite3.Connection], days: Optional[int],
                today: Optional[date] = None) -> Dict:
    """One small line chart per country from v_country_risk_trend.

    `days` limits the charts to the last so many days (None: everything).
    """
    today = today or date.today()
    conn = connect()
    try:
        rows = conn.execute(
            'SELECT country, report_date, risk_score FROM v_country_risk_trend'
        ).fetchall()
    finally:
        conn.close()

    start = today - timedelta(days=days - 1) if days else None
    scores = defaultdict(dict)
    unscored = defaultdict(int)
    skipped = 0
    for country, raw_date, score in rows:
        day = as_date(raw_date)
        if day is None or country is None:
            skipped += 1
            continue
        if start and day < start:
            continue
        country = _SAME_COUNTRY.get(country, country)
        if not score:
            unscored[country] += 1
            continue
        scores[country][day] = score  # one score a day; a later row wins

    # The axis starts at the first score shown, so a range reaching back past
    # the data doesn't squeeze it into the right-hand edge
    all_days = [d for series in scores.values() for d in series]
    first = min(all_days) if all_days else (start or today)
    span = max(1, (today - first).days)

    def x(day: date) -> float:
        return round(PAD + (WIDTH - 2 * PAD) * (day - first).days / span, 1)

    def y(score: float) -> float:
        return round(HEIGHT - PAD - (HEIGHT - 2 * PAD) * score / 100, 1)

    charts = []
    for country in sorted(set(scores) | set(unscored), key=lambda c: COUNTRY_NAMES.get(c, c)):
        series = sorted(scores[country].items())
        points = [(x(day), y(score), day, score) for day, score in series]
        latest = series[-1] if series else None
        previous = series[-2] if len(series) > 1 else None
        charts.append({
            'code': country,
            'name': COUNTRY_NAMES.get(country, country),
            'points': points,
            'path': ' '.join(f'{px},{py}' for px, py, _, _ in points),
            'latest': latest,
            'change': latest[1] - previous[1] if latest and previous else None,
            'unscored': unscored[country],
        })
    return {
        'charts': charts,
        'first': first,
        'last': today,
        'scored': sum(len(s) for s in scores.values()),
        'unscored': sum(unscored.values()),
        'skipped': skipped,
        'width': WIDTH,
        'height': HEIGHT,
        'grid': [y(level) for level in (25, 50, 75)],
    }


def signal_grid(connect: Callable[[], sqlite3.Connection],
                end: Optional[date] = None, days: int = 7) -> Dict:
    """Signals per category and report day for the `days` days up to `end`.

    v_signal_heatmap_7d gives the same counts for the last 7 days without
    the days; this keeps them so the grid can show when signals came in.
    """
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    conn = connect()
    try:
        rows = conn.execute(
            '''SELECT s.category, r.report_date, s.impact_score, s.directional_bias
               FROM signals s JOIN reports r ON s.report_id = r.id'''
        ).fetchall()
    finally:
        conn.close()

    cells = defaultdict(lambda: {'count': 0, 'impact': 0, 'scored': 0, 'negative': 0, 'positive': 0})
    latest = None
    for category, raw_date, impact, bias in rows:
        day = as_date(raw_date)
        if day is None:
            continue
        latest = max(latest, day) if latest else day
        if not start <= day <= end:
            continue
        cell = cells[(category, day)]
        cell['count'] += 1
        if impact is not None:
            cell['impact'] += impact
            cell['scored'] += 1
        cell['negative'] += bias == 'negative'
        cell['positive'] += bias == 'positive'

    day_list = [start + timedelta(days=n) for n in range(days)]
    most = max((c['count'] for c in cells.values()), default=0)
    grid = []
    for category in CATEGORIES:
        row = []
        for day in day_list:
            cell = cells.get((category, day))
            if cell:
                row.append({
                    'count': cell['count'],
                    # None when no signal has an impact score (section-derived ones don't)
                    'avg_impact': round(cell['impact'] / cell['scored']) if cell['scored'] else None,
                    'scored': cell['scored'],
                    'negative': cell['negative'],
                    'positive': cell['positive'],
                    'shade': round(0.15 + 0.85 * cell['count'] / most, 2),
                })
            else:
                row.append(None)
        grid.append((category, row))
    return {
        'days': day_list,
        'grid': grid,
        'total': sum(c['count'] for c in cells.values()),
        'latest': latest,
        'end': end,
    }


def heatmap_view(connect: Callable[[], sqlite3.Connection]) -> List[Dict]:
    """The database's own 7-day summary (v_signal_heatmap_7d)"""
    conn = connect()
    try:
        return [dict(row) for row in conn.execute('SELECT * FROM v_signal_heatmap_7d')]
    finally:
        conn.close()


def recent_signals(connect: Callable[[], sqlite3.Connection], country: str,
                   days: int = 14, limit: int = 25, today: Optional[date] = None) -> List[Dict]:
    """A country's signals from the last `days` days, highest impact first
    (signals without an impact score after the rest, newest first)"""
    today = today or date.today()
    codes = [code for code, same in _SAME_COUNTRY.items() if same == country] + [country]
    conn = connect()
    try:
        rows = conn.execute(
            f"""SELECT r.report_date, s.category, s.title, s.summary, s.impact_score,
                       s.directional_bias, s.time_horizon
                FROM signals s JOIN reports r ON r.id = s.report_id
                WHERE r.country IN ({','.join('?' * len(codes))}) AND r.report_date >= ?
                ORDER BY s.impact_score IS NULL, s.impact_score DESC, r.report_date DESC
                LIMIT ?""",
            (*codes, (today - timedelta(days=days - 1)).isoformat(), limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
