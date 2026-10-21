"""
Database Browser - Read-only SQLite browser for intelligence database
"""
import os
import re
import sqlite3
from typing import List, Dict, Optional, Tuple
from pathlib import Path

DEFAULT_DB_PATH = os.environ.get(
    "OPENCLAW_DB_PATH", "/home/dg/openclaw-reports/intelligence.db"
)

# SQLite identifiers we generate ourselves are always drawn from an
# allowlist (sqlite_master / PRAGMA table_info), but we still restrict
# the character set as defense in depth before interpolating them into
# SQL text, since table/column names can't be bound as query parameters.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _quote_identifier(name: str) -> str:
    """Double-quote a validated SQLite identifier"""
    return '"' + name.replace('"', '""') + '"'


class DatabaseBrowser:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._schema_cache = None

    def _connect(self) -> sqlite3.Connection:
        """Create read-only connection"""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def get_tables(self) -> List[str]:
        """Get all table and view names"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            # Get tables
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type IN ('table', 'view')
                AND name NOT LIKE 'sqlite_%'
                ORDER BY type DESC, name
            """)

            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tables
        except Exception as e:
            print(f"Error getting tables: {e}")
            return []

    def _validate_table_name(self, table_name: str) -> bool:
        """Only allow table/view names that actually exist in the database"""
        if not table_name or not _SAFE_IDENTIFIER.match(table_name):
            return False
        return table_name in self.get_tables()

    def get_table_info(self, table_name: str) -> Dict:
        """Get table schema and row count"""
        if not self._validate_table_name(table_name):
            return {'name': table_name, 'columns': [], 'row_count': 0, 'error': 'Unknown table'}

        quoted = _quote_identifier(table_name)
        try:
            conn = self._connect()
            cursor = conn.cursor()

            # Get schema
            cursor.execute(f"PRAGMA table_info({quoted})")
            columns = []
            for row in cursor.fetchall():
                columns.append({
                    'name': row[1],
                    'type': row[2],
                    'notnull': bool(row[3]),
                    'pk': bool(row[5])
                })

            # Get row count
            cursor.execute(f"SELECT COUNT(*) FROM {quoted}")
            row_count = cursor.fetchone()[0]

            # Check if it's a view
            cursor.execute("""
                SELECT type FROM sqlite_master
                WHERE name = ? AND type = 'view'
            """, (table_name,))
            is_view = cursor.fetchone() is not None

            conn.close()

            return {
                'name': table_name,
                'columns': columns,
                'row_count': row_count,
                'is_view': is_view
            }
        except Exception as e:
            print(f"Error getting table info for {table_name}: {e}")
            return {'name': table_name, 'columns': [], 'row_count': 0, 'error': str(e)}

    def query_table(self, table_name: str, limit: Optional[int] = 100, offset: int = 0,
                    order_by: Optional[str] = None, filters: Optional[Dict] = None,
                    descending: bool = True) -> Tuple[List[Dict], int]:
        """Query table with pagination and optional filtering (limit=None: all rows)"""
        if not self._validate_table_name(table_name):
            return [], 0

        table_info = self.get_table_info(table_name)
        valid_columns = {c['name'] for c in table_info['columns']}
        quoted_table = _quote_identifier(table_name)

        try:
            conn = self._connect()
            cursor = conn.cursor()

            # Build query
            query = f"SELECT * FROM {quoted_table}"
            params = []

            # Add filters (column names validated against the real schema)
            if filters:
                conditions = []
                for col, val in filters.items():
                    if val and col in valid_columns:
                        conditions.append(f"{_quote_identifier(col)} LIKE ?")
                        params.append(f"%{val}%")
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

            # Get total count
            count_query = f"SELECT COUNT(*) FROM ({query})"
            cursor.execute(count_query, params)
            total = cursor.fetchone()[0]

            # Add ordering (column name validated against the real schema)
            if order_by and order_by in valid_columns:
                direction = "DESC" if descending else "ASC"
                query += f" ORDER BY {_quote_identifier(order_by)} {direction}"

            # Add pagination
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])

            # Execute query
            cursor.execute(query, params)

            # Convert rows to dicts
            rows = []
            for row in cursor.fetchall():
                rows.append(dict(row))

            conn.close()
            return rows, total
        except Exception as e:
            print(f"Error querying table {table_name}: {e}")
            return [], 0

    def execute_select(self, query: str, max_rows: Optional[int] = None) -> Tuple[List[Dict], Optional[str]]:
        """Execute a SELECT query (read-only, validated).

        With max_rows, at most max_rows + 1 rows are fetched, so a caller can
        tell the result was cut short without reading all of it.
        """
        # Security: ensure it's a single SELECT statement
        query_stripped = query.strip().rstrip(';')
        if ';' in query_stripped:
            return [], "Only a single statement is allowed"

        query_upper = query_stripped.upper()
        if not query_upper.startswith('SELECT'):
            return [], "Only SELECT queries are allowed"

        # Block dangerous keywords (word-boundary match so e.g. "updated_at"
        # as a column name isn't mistaken for the UPDATE keyword)
        dangerous_keywords = ['DELETE', 'UPDATE', 'INSERT', 'DROP', 'ALTER', 'CREATE', 'ATTACH', 'PRAGMA']
        for keyword in dangerous_keywords:
            if re.search(rf'\b{keyword}\b', query_upper):
                return [], f"Query contains forbidden keyword: {keyword}"

        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(query_stripped)

            # Convert to dicts
            fetched = cursor.fetchall() if max_rows is None else cursor.fetchmany(max_rows + 1)
            rows = [dict(row) for row in fetched]

            conn.close()
            return rows, None
        except Exception as e:
            return [], str(e)

    def get_db_stats(self) -> Dict:
        """Get database statistics"""
        try:
            # Get database file size
            db_size = Path(self.db_path).stat().st_size

            # Get table stats
            tables = self.get_tables()
            table_stats = {}
            for table in tables:
                info = self.get_table_info(table)
                table_stats[table] = {
                    'rows': info['row_count'],
                    'columns': len(info['columns']),
                    'is_view': info.get('is_view', False)
                }

            return {
                'size_mb': round(db_size / 1024 / 1024, 2),
                'tables': len([t for t in table_stats.values() if not t['is_view']]),
                'views': len([t for t in table_stats.values() if t['is_view']]),
                'total_rows': sum(t['rows'] for t in table_stats.values() if not t['is_view']),
                'table_stats': table_stats
            }
        except Exception as e:
            print(f"Error getting DB stats: {e}")
            return {}
