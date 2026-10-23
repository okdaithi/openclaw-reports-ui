"""Links between the countries the agent covers, from what their reports share.

The database's own entity tables (entity_mentions, v_entity_cooccurrence)
stopped being written in February 2026 (68 mentions, the latest dated
2026-02-22), so links are worked out from current data instead:

- mentions: a signal in one country's report that names another covered
  country (by name, adjective, capital or leader; COUNTRY_TERMS);
- shared sources: the same URL cited by both countries' reports;
- shared entities: a named actor or place outside the 16 (Iran, NATO, the
  Strait of Hormuz, ...) that both countries' signals mention. The names
  are ENTITY_TERMS plus the entities table's, so anything the agent records
  there counts too.

Everything is over a window of days up to `end`, like the map's rings.
"""
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from modules import countries

_SAME_COUNTRY = {'US': 'USA'}

# Words that name each covered country. Case-sensitive, so "US" isn't "us".
COUNTRY_TERMS = {
    'USA': r'United States|\bU\.S\.(?:A\.)?|\bUSA?\b|\bAmericans?\b|White House|Pentagon|Washington(?! Post)|Trump',
    'CHN': r'China|Chinese|Beijing|\bPRC\b|Xi Jinping',
    'RUS': r'Russia|Russian|Moscow|Kremlin|Putin',
    'DEU': r'Germany|German\b|Berlin|Bundesbank|Merz\b',
    'GBR': r'United Kingdom|\bUK\b|\bU\.K\.|Britain|British|Starmer|Downing Street',
    'AUS': r'Australia|Australian|Canberra|Albanese',
    'IND': r'\bIndia\b|\bIndian\b(?! Ocean)|New Delhi|\bModi\b',
    'JPN': r'Japan|Japanese|Tokyo',
    'KOR': r'South Korea|Seoul|\bROK\b|Republic of Korea',
    'IDN': r'Indonesia|Jakarta|Prabowo',
    'BRA': r'Brazil|Bras[ií]lia|\bLula\b',
    'ZAF': r'South Africa|Pretoria|Ramaphosa',
    'SAU': r'Saudi|Riyadh',
    'TUR': r'Turkey|T[üu]rkiye|Turkish|Ankara|Erdo[gğ]an',
    'ARE': r'\bUAE\b|United Arab Emirates|Emirati|Abu Dhabi|Dubai',
    'IRL': r'Ireland|\bIrish\b|Dublin',
}

# Named actors and places outside the covered countries: name -> pattern
ENTITY_TERMS = {
    'Iran': r'\bIran(?:ian)?\b|Tehran',
    'Israel': r'\bIsrael(?:i)?\b',
    'Ukraine': r'Ukrain',
    'Taiwan': r'Taiwan',
    'Gaza': r'\bGaza\b|Hamas',
    'North Korea': r'North Korea|Pyongyang|\bDPRK\b',
    'Venezuela': r'Venezuel',
    'Pakistan': r'Pakistan',
    'Qatar': r'\bQatar',
    'Strait of Hormuz': r'Hormuz',
    'Red Sea': r'Red Sea|Houthi',
    'South China Sea': r'South China Sea|Philippine',
    'European Union': r'European Union|\bEU\b|Brussels|European Commission',
    'NATO': r'\bNATO\b',
    'OPEC+': r'\bOPEC',
    'G7': r'\bG7\b|\bG-7\b',
    'G20': r'\bG20\b|\bG-20\b',
    'BRICS': r'\bBRICS\b',
    'IMF': r'\bIMF\b|International Monetary Fund',
    'United Nations': r'United Nations|\bUN\b|Security Council',
    'WTO': r'\bWTO\b|World Trade Organization',
    'Federal Reserve': r'Federal Reserve|\bthe Fed\b',
    'ASEAN': r'\bASEAN\b',
    'AUKUS': r'\bAUKUS\b',
    'Quad': r'\bthe Quad\b',
}

# entities-table types that name actors (not the agent's own tooling)
_ENTITY_TYPES = ('organization', 'individual', 'policy', 'event', 'country')
_COVERED_NAMES = {name.casefold() for name, _ in countries.COUNTRIES.values()} | {'usa', 'us', 'china', 'russia'}

MAX_LINKS = 30
MIN_WEIGHT = 3
SOURCE_WEIGHT = 2      # a source both reports cite counts as two mentions
ENTITY_WEIGHT = 1


def _compile(terms: Dict[str, str]) -> Dict[str, 're.Pattern']:
    return {name: re.compile(pattern) for name, pattern in terms.items()}


_COUNTRY_RE = _compile(COUNTRY_TERMS)


def entity_patterns(conn: sqlite3.Connection) -> Dict[str, 're.Pattern']:
    """ENTITY_TERMS plus the names in the entities table"""
    terms = dict(ENTITY_TERMS)
    try:
        rows = conn.execute(
            f"SELECT name FROM entities WHERE type IN ({','.join('?' * len(_ENTITY_TYPES))})",
            _ENTITY_TYPES).fetchall()
    except sqlite3.Error:
        rows = []
    known = {name.casefold() for name in terms}
    for (name,) in rows:
        if not name or name.casefold() in known or name.casefold() in _COVERED_NAMES or len(name) < 3:
            continue
        terms[name] = r'\b' + re.escape(name) + r'\b'
    return _compile(terms)


def _signals(conn, start: date, end: date, codes: Optional[Iterable[str]] = None):
    rows = conn.execute(
        '''SELECT s.id, r.country, substr(r.report_date, 1, 10), s.category, s.title, s.summary,
                  s.primary_entities
           FROM signals s JOIN reports r ON r.id = s.report_id
           WHERE substr(r.report_date, 1, 10) BETWEEN ? AND ?''',
        (start.isoformat(), end.isoformat())).fetchall()
    wanted = set(codes) if codes else None
    for sid, country, day, category, title, summary, entities in rows:
        country = _SAME_COUNTRY.get(country, country)
        if country not in countries.COUNTRIES or (wanted and country not in wanted):
            continue
        text = ' '.join(t for t in (title, summary, entities) if t)
        yield {'id': sid, 'country': country, 'date': day, 'category': category,
               'title': title, 'summary': summary, 'text': text}


def _sources(conn, start: date, end: date) -> Dict[str, Dict[str, str]]:
    """country -> url -> domain, for the window"""
    found: Dict[str, Dict[str, str]] = defaultdict(dict)
    rows = conn.execute(
        '''SELECT r.country, s.url, s.domain FROM sources s JOIN reports r ON r.id = s.report_id
           WHERE s.url LIKE 'http%' AND substr(r.report_date, 1, 10) BETWEEN ? AND ?''',
        (start.isoformat(), end.isoformat())).fetchall()
    for country, url, domain in rows:
        country = _SAME_COUNTRY.get(country, country)
        if country in countries.COUNTRIES:
            found[country][url.rstrip('/')] = domain
    return found


def _window(end: date, days: int) -> Tuple[date, date]:
    days = max(1, min(days, 60))
    return end - timedelta(days=days - 1), end


def mentioned(text: str, own: str) -> List[str]:
    """Covered countries other than `own` that a signal's text names"""
    return [code for code, pattern in _COUNTRY_RE.items() if code != own and pattern.search(text)]


def links(connect: Callable[[], sqlite3.Connection], end: date, days: int = 7) -> Dict:
    """Country pairs with their mention, shared-source and shared-entity counts"""
    start, end = _window(end, days)
    conn = connect()
    try:
        patterns = entity_patterns(conn)
        signals = list(_signals(conn, start, end))
        sources = _sources(conn, start, end)
    finally:
        conn.close()

    mentions = defaultdict(int)                  # (from, to) -> signals
    entities = defaultdict(lambda: defaultdict(int))   # country -> entity -> signals
    for signal in signals:
        for other in mentioned(signal['text'], signal['country']):
            mentions[(signal['country'], other)] += 1
        for name, pattern in patterns.items():
            if pattern.search(signal['text']):
                entities[signal['country']][name] += 1

    found = []
    codes = sorted(countries.COUNTRIES)
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            ab, ba = mentions[(a, b)], mentions[(b, a)]
            shared_sources = len(set(sources.get(a, {})) & set(sources.get(b, {})))
            shared = sorted(set(entities[a]) & set(entities[b]),
                            key=lambda name: -(entities[a][name] + entities[b][name]))
            weight = ab + ba + SOURCE_WEIGHT * shared_sources + ENTITY_WEIGHT * len(shared)
            if weight < MIN_WEIGHT:
                continue
            found.append({'a': a, 'b': b, 'weight': weight, 'ab': ab, 'ba': ba,
                          'sources': shared_sources, 'entities': shared[:8],
                          'entity_count': len(shared)})
    found.sort(key=lambda link: (-link['weight'], link['a'], link['b']))
    return {'start': start.isoformat(), 'end': end.isoformat(), 'links': found[:MAX_LINKS]}


def evidence(connect: Callable[[], sqlite3.Connection], a: str, b: str, end: date,
             days: int = 7, limit: int = 15) -> Dict:
    """What links two countries: signals naming the other, shared sources and entities"""
    start, end = _window(end, days)
    conn = connect()
    try:
        patterns = entity_patterns(conn)
        signals = list(_signals(conn, start, end, (a, b)))
        sources = _sources(conn, start, end)
    finally:
        conn.close()

    naming = []
    counts = {a: defaultdict(int), b: defaultdict(int)}
    for signal in signals:
        other = b if signal['country'] == a else a
        if _COUNTRY_RE[other].search(signal['text']):
            naming.append({key: signal[key] for key in ('country', 'date', 'category', 'title', 'summary')})
        for name, pattern in patterns.items():
            if pattern.search(signal['text']):
                counts[signal['country']][name] += 1
    naming.sort(key=lambda s: (s['date'], s['country']), reverse=True)
    shared_urls = sorted(set(sources.get(a, {})) & set(sources.get(b, {})))
    shared = sorted(set(counts[a]) & set(counts[b]), key=lambda n: -(counts[a][n] + counts[b][n]))
    return {
        'a': a, 'b': b, 'start': start.isoformat(), 'end': end.isoformat(),
        'signals': naming[:limit],
        'signal_count': len(naming),
        'sources': [{'url': url, 'domain': sources[a][url]} for url in shared_urls][:limit],
        'entities': [{'name': n, 'a': counts[a][n], 'b': counts[b][n]} for n in shared],
    }
