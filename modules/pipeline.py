"""Is the agent's pipeline keeping up? Today's reports against the usual schedule.

The schedule is learnt from history rather than read from the agent's
configuration: a series is expected on a day when it arrived on at least
MIN_SEEN of the last LOOKBACK_WEEKS same weekdays, by its usual (median)
time of arrival then plus GRACE; the median, so one late run or a file
edited afterwards doesn't push the due time out. A report counts once for its file (the markdown in
the reports folder) and, for country reports, once for its database row.
"""
import re
import statistics
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from modules import countries

LOOKBACK_WEEKS = 3
MIN_SEEN = 2
GRACE = timedelta(hours=2)
HISTORY_DAYS = 7

_VERSION = re.compile(r'^#\s*Version:\s*(\S+)', re.MULTILINE)
_SERIES_BY_CODE = {code: f'mika-{slug}' for code, (_, slug) in countries.COUNTRIES.items()}


def _now() -> datetime:
    return datetime.now()


def local_time(utc_text: Optional[str]) -> Optional[datetime]:
    """SQLite's datetime('now') text (UTC) as naive local time"""
    if not utc_text:
        return None
    try:
        moment = datetime.fromisoformat(str(utc_text).replace('Z', ''))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def expected(arrivals: Dict[str, Dict[date, datetime]], day: date) -> Dict[str, datetime]:
    """Series expected on `day`, each with the time it is due by"""
    due = {}
    for series, days in arrivals.items():
        seen = [days[d] for d in (day - timedelta(weeks=n) for n in range(1, LOOKBACK_WEEKS + 1))
                if d in days]
        if len(seen) >= MIN_SEEN:
            usual = statistics.median_low(moment.time() for moment in seen)
            by = datetime.combine(day, usual) + GRACE
            due[series] = min(by, datetime.combine(day, time(23, 59)))
    return due


def file_arrivals(reports: Iterable[Dict]) -> Dict[str, Dict[date, datetime]]:
    """series -> report date -> when its file was written (earliest copy)"""
    found: Dict[str, Dict[date, datetime]] = {}
    for report in reports:
        if report['kind'] != 'report' or not report['report_date']:
            continue
        days = found.setdefault(report['series'], {})
        day = report['report_date']
        days[day] = min(days.get(day, report['modified']), report['modified'])
    return found


def database_rows(connect: Callable[[], sqlite3.Connection], since: date) -> List[Dict]:
    """Country report rows from `since`, with their signal and source counts"""
    conn = connect()
    try:
        rows = conn.execute(
            '''SELECT r.report_date, r.country, r.created_at, r.risk_score, r.signal_density,
                      (SELECT COUNT(*) FROM signals s WHERE s.report_id = r.id) AS signals,
                      (SELECT COUNT(*) FROM sources s WHERE s.report_id = r.id) AS sources
               FROM reports r
               WHERE r.report_type = 'country' AND r.report_date >= ?''',
            (since.isoformat(),)).fetchall()
    finally:
        conn.close()
    found = []
    for report_date, country, created_at, risk, density, signals, sources in rows:
        try:
            day = date.fromisoformat(str(report_date))
        except ValueError:
            continue
        found.append({
            'day': day,
            'series': _SERIES_BY_CODE.get(country, f'db-{country}'),
            'country': country,
            'loaded': local_time(created_at),
            'risk': risk,
            'density': density,
            'signals': signals,
            'sources': sources,
        })
    return found


def problems(row: Dict) -> List[str]:
    """What is missing from a loaded database row"""
    found = []
    if row['signals'] == 0:
        found.append('no signals')
    elif row['density'] is not None and int(row['density']) != row['signals']:
        found.append(f"signal count says {int(row['density'])}")
    if row['risk'] is None:
        found.append('no risk score')
    if row['sources'] == 0:
        found.append('no sources')
    return found


def day_status(day: date, now: datetime, files: Dict[str, Dict[date, datetime]],
               rows: List[Dict]) -> List[Dict]:
    """One line per series expected or arrived on `day`"""
    db_arrivals: Dict[str, Dict[date, datetime]] = {}
    todays_rows = {}
    for row in rows:
        if row['loaded']:
            db_arrivals.setdefault(row['series'], {})[row['day']] = row['loaded']
        if row['day'] == day:
            todays_rows[row['series']] = row
    file_due = expected(files, day)
    db_due = expected(db_arrivals, day)

    country_series = set(_SERIES_BY_CODE.values())
    lines = []
    names = set(file_due) | set(db_due) | set(todays_rows) | {
        series for series, days in files.items() if day in days}
    for series in names:
        file_at = files.get(series, {}).get(day)
        row = todays_rows.get(series)
        is_country = series in country_series or row is not None
        # A country file with no row by GRACE after it arrived: the load failed
        row_due = db_due.get(series) or (file_at + GRACE if is_country and file_at else None)
        late, waiting = [], []
        if series in file_due and file_at is None:
            (late if now > file_due[series] else waiting).append('file')
        if row_due and row is None:
            (late if now > row_due else waiting).append('database row')
        issues = problems(row) if row else []
        if late:
            state = 'late'
        elif row is not None and row['signals'] == 0:
            state = 'empty'
        elif issues:
            state = 'partial'
        elif waiting:
            state = 'waiting'
        else:
            state = 'ok'
        dues = [d for d in (file_due.get(series), db_due.get(series)) if d]
        lines.append({
            'series': series,
            'is_country': is_country,
            'expected': bool(dues),
            'due': min(dues) if dues else None,
            'file_at': file_at,
            'row': row,
            'late': late,
            'waiting': waiting,
            'issues': issues,
            'state': state,
        })
    lines.sort(key=lambda line: (line['due'] or line['file_at'] or datetime.max, line['series']))
    return lines


def overview(reports: List[Dict], connect: Optional[Callable[[], sqlite3.Connection]],
             now: Optional[datetime] = None, day: Optional[date] = None) -> Dict:
    """The pipeline page: one day in detail and a summary of the days before
    (`connect` None: files only, as when the database won't open)"""
    now = now or _now()
    day = day or now.date()
    since = day - timedelta(weeks=LOOKBACK_WEEKS, days=HISTORY_DAYS)
    files = file_arrivals(reports)
    rows = database_rows(connect, since) if connect else []
    lines = day_status(day, now, files, rows)
    history = []
    for back in range(1, HISTORY_DAYS + 1):
        past = day - timedelta(days=back)
        past_lines = day_status(past, max(now, datetime.combine(past, time.max)), files, rows)
        history.append({
            'day': past,
            'expected': sum(line['expected'] for line in past_lines),
            'arrived': sum(1 for line in past_lines if line['file_at'] or line['row']),
            'late': [line['series'] for line in past_lines if line['late']],
            'empty': [line['series'] for line in past_lines if line['state'] == 'empty'],
            'partial': sum(line['state'] == 'partial' for line in past_lines),
        })
    loads = [row['loaded'] for row in rows if row['loaded']]
    return {
        'day': day,
        'now': now,
        'lines': lines,
        'counts': {state: sum(line['state'] == state for line in lines)
                   for state in ('ok', 'partial', 'empty', 'late', 'waiting')},
        'history': history,
        'last_load': max(loads) if loads else None,
    }


def health(summary: Dict) -> Dict:
    """For /healthz: what is late or loaded empty today"""
    late = [line['series'] for line in summary['lines'] if line['state'] == 'late']
    empty = [line['series'] for line in summary['lines'] if line['state'] == 'empty']
    return {'late': late, 'empty': empty, 'ok': not late and not empty}


def loader_version(script: Path) -> Optional[str]:
    """The '# Version:' line of the agent's database insert script"""
    try:
        with open(script, encoding='utf-8', errors='replace') as handle:
            head = handle.read(4096)
    except OSError:
        return None
    match = _VERSION.search(head)
    return match.group(1) if match else None
