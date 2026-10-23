"""Entities (people, organisations, places) from the database's entity tables.

The agent's loader (v1.5+) records, for each report, the entities its signals
name (entity_mentions), leaving out the report's own country. These
functions read them for the Entities, Entity, Network and Compare pages.
Every function takes a window of days ending at `end`.
"""
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

from modules import connections, countries

Connect = Callable[[], sqlite3.Connection]
_SAME_COUNTRY = {'US': 'USA'}
_NAME_TO_CODE = {name: code for code, (name, _) in countries.COUNTRIES.items()}


def window(end: Optional[date], days: int):
    end = end or date.today()
    days = max(1, min(days, 400))
    return end - timedelta(days=days - 1), end, days


def _rows(connect: Connect, sql: str, params=()) -> List[tuple]:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def entity_list(connect: Connect, end: Optional[date] = None, days: int = 30) -> Dict:
    """Entities mentioned in the window, most reports first, with a count per day"""
    start, end, days = window(end, days)
    rows = _rows(connect, '''
        SELECT e.name, e.type, COUNT(DISTINCT m.report_id), SUM(m.mention_count),
               AVG(m.importance_score), MAX(substr(r.report_date, 1, 10)), COUNT(DISTINCT r.country)
        FROM entities e
        JOIN entity_mentions m ON m.entity_id = e.id
        JOIN reports r ON r.id = m.report_id
        WHERE substr(r.report_date, 1, 10) BETWEEN ? AND ?
        GROUP BY e.id
        ORDER BY 3 DESC, 4 DESC, e.name''', (start.isoformat(), end.isoformat()))
    daily = defaultdict(lambda: [0] * days)
    for name, day, mentions in _rows(connect, '''
            SELECT e.name, substr(r.report_date, 1, 10), SUM(m.mention_count)
            FROM entities e JOIN entity_mentions m ON m.entity_id = e.id JOIN reports r ON r.id = m.report_id
            WHERE substr(r.report_date, 1, 10) BETWEEN ? AND ?
            GROUP BY 1, 2''', (start.isoformat(), end.isoformat())):
        n = (date.fromisoformat(day) - start).days
        if 0 <= n < days:
            daily[name][n] += mentions
    found = [{
        'name': name, 'type': kind, 'reports': reports, 'mentions': mentions,
        'importance': round(importance, 1) if importance is not None else None,
        'last': last, 'countries': country_count, 'daily': daily[name],
    } for name, kind, reports, mentions, importance, last, country_count in rows]
    return {'start': start, 'end': end, 'days': days, 'entities': found,
            'types': sorted({e['type'] for e in found})}


def pattern_for(name: str) -> 're.Pattern':
    """How to find an entity in a signal's text: the same terms the map and
    the loader use, or the name as a whole word"""
    code = _NAME_TO_CODE.get(name)
    if code:
        return connections._COUNTRY_RE[code]
    if name in connections.ENTITY_TERMS:
        return re.compile(connections.ENTITY_TERMS[name])
    return re.compile(r'\b' + re.escape(name) + r'\b')


def entity_detail(connect: Connect, name: str, end: Optional[date] = None, days: int = 90) -> Optional[Dict]:
    """One entity over the window: mentions per day, by country, what it is
    mentioned with, the reports and the signals that name it"""
    start, end, days = window(end, days)
    found = _rows(connect, 'SELECT id, name, type, first_seen_date, last_seen_date, total_mentions FROM entities WHERE name = ?',
                  (name,))
    if not found:
        return None
    entity_id, name, kind, first_seen, last_seen, total = found[0]
    span = (start.isoformat(), end.isoformat())
    mentions = _rows(connect, '''
        SELECT r.id, substr(r.report_date, 1, 10), r.country, m.mention_count, m.importance_score, m.context_tags
        FROM entity_mentions m JOIN reports r ON r.id = m.report_id
        WHERE m.entity_id = ? AND substr(r.report_date, 1, 10) BETWEEN ? AND ?
        ORDER BY r.report_date DESC, r.country''', (entity_id, *span))
    timeline = [0] * days
    by_country = defaultdict(lambda: {'reports': 0, 'mentions': 0})
    reports = []
    for report_id, day, country, count, importance, tags in mentions:
        country = _SAME_COUNTRY.get(country, country)
        n = (date.fromisoformat(day) - start).days
        if 0 <= n < days:
            timeline[n] += count
        by_country[country]['reports'] += 1
        by_country[country]['mentions'] += count
        reports.append({'id': report_id, 'date': day, 'country': country, 'mentions': count,
                        'importance': importance, 'tags': (tags or '').split(',') if tags else []})
    related = _rows(connect, '''
        SELECT e2.name, e2.type, COUNT(DISTINCT m1.report_id)
        FROM entity_mentions m1
        JOIN entity_mentions m2 ON m2.report_id = m1.report_id AND m2.entity_id <> m1.entity_id
        JOIN entities e2 ON e2.id = m2.entity_id
        JOIN reports r ON r.id = m1.report_id
        WHERE m1.entity_id = ? AND substr(r.report_date, 1, 10) BETWEEN ? AND ?
        GROUP BY e2.id ORDER BY 3 DESC, e2.name LIMIT 15''', (entity_id, *span))

    # The signals that name it, in the reports that mention it
    pattern = pattern_for(name)
    report_ids = [r['id'] for r in reports]
    signals = []
    if report_ids:
        marks = ','.join('?' * len(report_ids))
        for day, country, category, title, summary, impact, bias in _rows(connect, f'''
                SELECT substr(r.report_date, 1, 10), r.country, s.category, s.title, s.summary,
                       s.impact_score, s.directional_bias
                FROM signals s JOIN reports r ON r.id = s.report_id
                WHERE s.report_id IN ({marks})
                ORDER BY r.report_date DESC, s.impact_score IS NULL, s.impact_score DESC''', report_ids):
            text = ' '.join(t for t in (title, summary) if t)
            if pattern.search(text):
                signals.append({'date': day, 'country': _SAME_COUNTRY.get(country, country), 'category': category,
                                'title': title, 'summary': summary, 'impact': impact, 'bias': bias})
    return {
        'name': name, 'type': kind, 'first_seen': first_seen, 'last_seen': last_seen, 'total': total,
        'start': start, 'end': end, 'days': days, 'timeline': timeline,
        'reports': reports,
        'by_country': sorted(({'code': code, 'name': countries.COUNTRY_NAMES.get(code, code), **counts}
                              for code, counts in by_country.items()), key=lambda c: (-c['reports'], c['name'])),
        'related': [{'name': n, 'type': t, 'reports': c} for n, t, c in related],
        'signals': signals[:25],
        'signal_count': len(signals),
        'covered_code': _NAME_TO_CODE.get(name),
    }


def network(connect: Connect, end: Optional[date] = None, days: int = 30, limit: int = 60) -> Dict:
    """Nodes (entities, and the countries whose reports mention them) and
    links (in how many reports two of them appear together)"""
    start, end, days = window(end, days)
    rows = _rows(connect, '''
        SELECT m.report_id, r.country, e.name, e.type, m.mention_count
        FROM entity_mentions m JOIN entities e ON e.id = m.entity_id JOIN reports r ON r.id = m.report_id
        WHERE substr(r.report_date, 1, 10) BETWEEN ? AND ?''', (start.isoformat(), end.isoformat()))
    per_report = defaultdict(set)
    nodes: Dict[str, Dict] = {}

    def node(name, kind, report_id, mentions):
        code = _NAME_TO_CODE.get(name)
        entry = nodes.setdefault(name, {'id': name, 'type': kind, 'reports': set(), 'mentions': 0, 'code': code,
                                        'slug': countries.COUNTRIES[code][1] if code else None})
        entry['reports'].add(report_id)
        entry['mentions'] += mentions

    for report_id, country, name, kind, count in rows:
        country = _SAME_COUNTRY.get(country, country)
        own = countries.COUNTRY_NAMES.get(country)
        node(name, kind, report_id, count)
        per_report[report_id].add(name)
        if own:
            # The report's own country is not recorded as a mention; it is
            # still what the report is about, so it joins the graph
            node(own, 'country', report_id, 0)
            per_report[report_id].add(own)

    weights = defaultdict(set)
    for report_id, names in per_report.items():
        ordered = sorted(names)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                weights[(a, b)].add(report_id)

    ranked = sorted(nodes.values(), key=lambda n: (-len(n['reports']), n['id']))[:limit]
    keep = {n['id'] for n in ranked}
    links = [{'source': a, 'target': b, 'weight': len(ids)}
             for (a, b), ids in weights.items() if a in keep and b in keep]
    links.sort(key=lambda l: -l['weight'])
    return {
        'start': start.isoformat(), 'end': end.isoformat(), 'days': days,
        'nodes': [{**n, 'reports': len(n['reports'])} for n in ranked],
        'links': links,
    }


def country_entities(connect: Connect, code: str, end: Optional[date] = None, days: int = 30,
                     limit: int = 10) -> List[Dict]:
    """The entities a country's reports mention most over the window"""
    start, end, days = window(end, days)
    codes = [code] + [old for old, new in _SAME_COUNTRY.items() if new == code]
    marks = ','.join('?' * len(codes))
    return [{'name': n, 'type': t, 'reports': r, 'mentions': m} for n, t, r, m in _rows(connect, f'''
        SELECT e.name, e.type, COUNT(DISTINCT m.report_id), SUM(m.mention_count)
        FROM entity_mentions m JOIN entities e ON e.id = m.entity_id JOIN reports r ON r.id = m.report_id
        WHERE r.country IN ({marks}) AND substr(r.report_date, 1, 10) BETWEEN ? AND ?
        GROUP BY e.id ORDER BY 3 DESC, 4 DESC, e.name LIMIT ?''', (*codes, start.isoformat(), end.isoformat(), limit))]


def country_sources(connect: Connect, code: str, end: Optional[date] = None, days: int = 30,
                    limit: int = 8) -> List[Dict]:
    """The domains a country's reports cite most over the window"""
    start, end, days = window(end, days)
    codes = [code] + [old for old, new in _SAME_COUNTRY.items() if new == code]
    marks = ','.join('?' * len(codes))
    return [{'domain': d, 'citations': c, 'reports': r} for d, c, r in _rows(connect, f'''
        SELECT s.domain, COUNT(*), COUNT(DISTINCT s.report_id)
        FROM sources s JOIN reports r ON r.id = s.report_id
        WHERE r.country IN ({marks}) AND substr(r.report_date, 1, 10) BETWEEN ? AND ?
          AND s.domain IS NOT NULL AND s.domain <> 'unknown'
        GROUP BY s.domain ORDER BY 2 DESC, s.domain LIMIT ?''', (*codes, start.isoformat(), end.isoformat(), limit))]


def category_counts(connect: Connect, code: str, end: Optional[date] = None, days: int = 30) -> Dict[str, int]:
    """Signals per category in a country's reports over the window"""
    start, end, days = window(end, days)
    codes = [code] + [old for old, new in _SAME_COUNTRY.items() if new == code]
    marks = ','.join('?' * len(codes))
    return dict(_rows(connect, f'''
        SELECT s.category, COUNT(*) FROM signals s JOIN reports r ON r.id = s.report_id
        WHERE r.country IN ({marks}) AND substr(r.report_date, 1, 10) BETWEEN ? AND ?
        GROUP BY 1''', (*codes, start.isoformat(), end.isoformat())))
