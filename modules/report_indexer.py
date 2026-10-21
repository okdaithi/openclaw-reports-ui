"""
Report Indexer - Scans and indexes markdown reports from OpenClaw
"""
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict, Optional

DEFAULT_REPORTS_DIR = os.environ.get("OPENCLAW_REPORTS_DIR", "/home/dg/openclaw-reports")

# Reports are named <series>-<YYYY-MM-DD>.md (e.g. mika-china-2026-10-21.md)
# and move from the top folder into archive/ after a few days, so the series
# and the date come from the file name, not from its folder or mtime.
_DATED_NAME = re.compile(r'^(?P<series>.+?)[-_](?P<date>\d{4}-\d{2}-\d{2})(?P<rest>.*)\.md$')
_TITLE_BYTES = 4096
SNIPPETS = 3
SCAN_INTERVAL = 30  # seconds between re-scans of the reports folder
_SNIPPET_CONTEXT = 70
_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*#*\s*$')

# Dated files that describe the system rather than report on the world
_SYSTEM_SERIES = {'config-overview', 'weekly_report_status'}

# Readable names for series whose slug doesn't title-case well
_SERIES_LABELS = {
    'daily-brief': 'Daily Brief',
    'daily-brief-hermes-test': 'Daily Brief (Hermes test)',
    'mika-executive-summary': 'Executive Summary',
    'mika-usa': 'USA',
    'mika-uk': 'UK',
    'mika-uae': 'UAE',
    'rss-digest': 'RSS Digest',
    'twitter-digest': 'Twitter Digest',
    'macro_strategy': 'Macro Strategy',
}


def series_label(series: str) -> str:
    """Readable name for a series slug (mika-south-korea -> South Korea)"""
    if series in _SERIES_LABELS:
        return _SERIES_LABELS[series]
    name = series[len('mika-'):] if series.startswith('mika-') else series
    return re.sub(r'[-_]+', ' ', name).title()


def parse_name(filename: str) -> Dict:
    """Series, report date and kind ('report', 'test' or 'system') from a file name"""
    match = _DATED_NAME.match(filename)
    if not match:
        # Undated files are design notes and logs, not reports
        return {'series': None, 'report_date': None, 'kind': 'system'}
    try:
        report_date = date.fromisoformat(match['date'])
    except ValueError:
        return {'series': None, 'report_date': None, 'kind': 'system'}
    series = match['series']
    if series in _SYSTEM_SERIES:
        kind = 'system'
    elif 'test' in series or match['rest']:
        kind = 'test'
    else:
        kind = 'report'
    return {'series': series, 'report_date': report_date, 'kind': kind}


class ReportIndexer:
    def __init__(self, reports_dir: str = None):
        self.reports_dir = Path(reports_dir or DEFAULT_REPORTS_DIR)
        self._index = []
        self._content_cache = {}
        self._title_cache = {}
        self._scanned_at = 0.0
        self._scanned_dir = None
        self._lock = threading.Lock()
        # Full-text index of every report, kept in memory and updated file by
        # file as reports change (0.3 s to build for 676 files in Oct 2026)
        self._fts = sqlite3.connect(':memory:', check_same_thread=False)
        self._fts.execute(
            "CREATE VIRTUAL TABLE docs USING fts5("
            "path UNINDEXED, name, title, body, tokenize='porter unicode61')"
        )
        self._fts_mtimes = {}

    def refresh(self) -> List[Dict]:
        """The index, re-scanned when it is more than SCAN_INTERVAL seconds old"""
        if (time.monotonic() - self._scanned_at >= SCAN_INTERVAL
                or self._scanned_dir != self.reports_dir):
            self.scan_reports()
        return self._index

    def scan_reports(self) -> List[Dict]:
        """Recursively scan for markdown reports (always; see refresh())"""
        with self._lock:
            found = self._scan()
            self._update_fts(found)
            # Swap the new list in whole, so a request never sees half a scan
            self._index = found
            self._scanned_at = time.monotonic()
            self._scanned_dir = self.reports_dir
        return self._index

    def _scan(self) -> List[Dict]:
        found = []
        if not self.reports_dir.exists():
            return found
        
        for md_file in self.reports_dir.rglob("*.md"):
            try:
                stat = md_file.stat()
                
                # Extract metadata from filename
                filename = md_file.name
                relative_path = md_file.relative_to(self.reports_dir)
                
                # Determine report type from path/filename
                report_type = self._classify_report(str(relative_path))
                named = parse_name(filename)
                if named['kind'] == 'system':
                    report_type = 'System document'
                elif named['kind'] == 'test':
                    report_type = 'Test run'
                
                found.append({
                    **named,
                    'series_label': series_label(named['series']) if named['series'] else None,
                    'title': self._read_title(md_file, stat.st_mtime),
                    'filename': filename,
                    'path': str(md_file),
                    'relative_path': str(relative_path),
                    'folder': str(relative_path.parent),
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime),
                    'mtime': stat.st_mtime,
                    'type': report_type,
                    'safe_id': self._safe_filename(str(relative_path))
                })
            except Exception as e:
                print(f"Error indexing {md_file}: {e}")
                continue
        
        # Sort by modification time (newest first)
        found.sort(key=lambda x: x['modified'], reverse=True)
        return found

    def _update_fts(self, found: List[Dict]) -> None:
        """Add new and changed reports to the full-text index, drop removed ones"""
        current = {r['path']: r for r in found}
        with self._fts:
            for path in set(self._fts_mtimes) - set(current):
                self._fts.execute('DELETE FROM docs WHERE path = ?', (path,))
                del self._fts_mtimes[path]
            for path, report in current.items():
                if self._fts_mtimes.get(path) == report['mtime']:
                    continue
                if path in self._fts_mtimes:
                    self._fts.execute('DELETE FROM docs WHERE path = ?', (path,))
                self._fts.execute(
                    'INSERT INTO docs (path, name, title, body) VALUES (?, ?, ?, ?)',
                    (path, f"{report['filename']} {report['folder']}", report['title'],
                     self.read_content(path)),
                )
                self._fts_mtimes[path] = report['mtime']

    def _read_title(self, path: Path, mtime: float) -> str:
        """First heading of a report (most use H1, executive summaries H2),
        read from its first few KB and cached by mtime"""
        cached = self._title_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        title = ''
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                head = f.read(_TITLE_BYTES)
            for line in head.split('\n'):
                heading = _HEADING.match(line)
                if heading:
                    title = heading[1]
                    break
        except OSError:
            pass
        self._title_cache[path] = (mtime, title)
        return title

    def find_moved(self, safe_id: str) -> Optional[Dict]:
        """A report whose link changed because it moved into an archive folder"""
        suffix = '_' + safe_id
        for report in self._index:
            if report['safe_id'].endswith(suffix):
                return report
        return None

    def series_reports(self, series: str) -> List[Dict]:
        """Reports of one series, oldest first"""
        found = [r for r in self._index if r['series'] == series]
        return sorted(found, key=lambda r: (r['report_date'], r['modified']))

    def neighbours(self, report: Dict) -> Dict:
        """The previous and next report of the same series"""
        if not report.get('series'):
            return {'previous': None, 'next': None, 'count': 0}
        reports = self.series_reports(report['series'])
        at = next(i for i, r in enumerate(reports) if r['path'] == report['path'])
        return {
            'previous': reports[at - 1] if at > 0 else None,
            'next': reports[at + 1] if at + 1 < len(reports) else None,
            'count': len(reports),
        }

    def latest(self, series: str) -> Optional[Dict]:
        """The newest report of a series"""
        reports = self.series_reports(series)
        return reports[-1] if reports else None

    def on_date(self, day: date) -> List[Dict]:
        """Reports (not tests or system files) dated `day`"""
        return [r for r in self._index if r['report_date'] == day and r['kind'] == 'report']
    
    def _classify_report(self, path: str) -> str:
        """Classify report type based on path/filename"""
        path_lower = path.lower()
        
        if 'daily-brief' in path_lower:
            return 'Daily Brief'
        elif 'mika' in path_lower and 'executive' in path_lower:
            return 'Executive Summary'
        elif 'mika' in path_lower:
            return 'Country Report'
        elif 'twitter' in path_lower:
            return 'Twitter Digest'
        elif 'rss-digest' in path_lower:
            return 'RSS Digest'
        elif 'macro_strategy' in path_lower or 'investment' in path_lower:
            return 'Investment'
        elif 'structured' in path_lower:
            return 'Structured Data'
        else:
            return 'Other'
    
    def _safe_filename(self, path: str) -> str:
        """Create safe identifier from path"""
        return re.sub(r'[^a-zA-Z0-9_-]', '_', path)
    
    def get_report(self, safe_id: str) -> Optional[Dict]:
        """Get report by safe ID"""
        for report in self._index:
            if report['safe_id'] == safe_id:
                return report
        return None
    
    def read_content(self, file_path: str) -> str:
        """Read markdown content from file"""
        try:
            mtime = os.stat(file_path).st_mtime
            cached = self._content_cache.get(file_path)
            if cached and cached[0] == mtime:
                return cached[1]

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self._content_cache[file_path] = (mtime, content)
                return content
        except Exception as e:
            return f"Error reading file: {e}"
    
    def search(self, query: str) -> List[Dict]:
        """Reports containing every word or "quoted phrase" of `query`, best first.

        Uses the full-text index (words match their other forms: "tariff"
        finds "tariffs"), ranked by BM25 with the title counting 5 times and
        the file name and folder 3 times as much as the text. Each hit carries
        `hits` (times the terms appear), `match_type` (title, filename or
        content), `score` and up to SNIPPETS `snippets`, each a list of
        (text, is_match) parts so a page can mark the matches without trusting
        the report's text as HTML.
        """
        if not query:
            return self._index

        terms = search_terms(query)
        if not terms:
            return []
        fts_query = ' '.join('"' + term.replace('"', '') + '"' for term in terms)
        try:
            with self._lock:
                ranked = self._fts.execute(
                    'SELECT path, bm25(docs, 0, 3, 5, 1) FROM docs WHERE docs MATCH ?',
                    (fts_query,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        rank = {path: -score for path, score in ranked}
        needle = term_pattern(terms)

        results = []
        for report in self._index:
            if report['path'] not in rank:
                continue
            content = self.read_content(report['path'])
            spans = [m.span() for m in needle.finditer(content)]
            if report['title'] and needle.search(report['title']):
                match_type = 'title'
            elif needle.search(report['filename']):
                match_type = 'filename'
            elif spans:
                match_type = 'content'
            else:
                match_type = 'path'
            results.append({
                **report,
                'match_type': match_type,
                'hits': len(spans),
                'score': rank[report['path']],
                'snippets': _snippets(content, spans),
            })
        results.sort(key=lambda r: (r['score'], r['modified']), reverse=True)
        return results

    def get_stats(self) -> Dict:
        """Get statistics about indexed reports"""
        if not self._index:
            return {}
        
        total_size = sum(r['size'] for r in self._index)
        types = {}
        for r in self._index:
            types[r['type']] = types.get(r['type'], 0) + 1
        
        return {
            'total_reports': len(self._index),
            'total_size_mb': round(total_size / 1024 / 1024, 2),
            'by_type': types,
            'oldest': min(r['modified'] for r in self._index),
            'newest': max(r['modified'] for r in self._index)
        }


def search_terms(query: str) -> List[str]:
    """Words and "quoted phrases" of a search, in order"""
    terms = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query):
        term = (phrase or word).strip()
        # Punctuation alone can't be searched for
        if re.search(r'\w', term):
            terms.append(term)
    return terms


def term_pattern(terms: List[str]) -> 're.Pattern':
    """A regex marking each term in text; a single word also matches its
    longer forms ("tariff" marks "tariffs"), as the full-text index does"""
    parts = []
    for term in sorted(terms, key=len, reverse=True):
        escaped = r'\s+'.join(re.escape(word) for word in term.split())
        if ' ' not in term:
            escaped += r'\w*'
        parts.append(r'\b' + escaped)
    return re.compile('|'.join(parts), re.IGNORECASE)


def _snippets(content: str, spans: List[tuple]) -> List[List[tuple]]:
    """Up to SNIPPETS non-overlapping passages around the matches in `spans`"""
    found = []
    last_end = -1
    for start, end in spans:
        if len(found) == SNIPPETS:
            break
        if start < last_end:
            continue  # already shown in the previous passage
        lo = max(0, start - _SNIPPET_CONTEXT)
        hi = min(len(content), end + _SNIPPET_CONTEXT)
        parts = []
        cursor = lo
        for m_start, m_end in spans:
            if m_end <= lo or m_start >= hi:
                continue
            m_start, m_end = max(m_start, lo), min(m_end, hi)
            if m_start > cursor:
                parts.append((_flatten(content[cursor:m_start]), False))
            parts.append((content[m_start:m_end], True))
            cursor = m_end
        if cursor < hi:
            parts.append((_flatten(content[cursor:hi]), False))
        if lo > 0:
            parts.insert(0, ('…', False))
        if hi < len(content):
            parts.append(('…', False))
        found.append(parts)
        last_end = hi
    return found


def _flatten(text: str) -> str:
    """Markdown fragment as one line of plain-ish text"""
    return re.sub(r'\s+', ' ', text.replace('**', '').replace('#', ''))
