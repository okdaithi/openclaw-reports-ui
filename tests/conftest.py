"""Point the app at a throwaway reports folder and database before it is imported."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_WORK = Path(tempfile.mkdtemp(prefix='ocr-tests-'))
os.environ['OPENCLAW_REPORTS_DIR'] = str(_WORK / 'reports')
os.environ['OPENCLAW_DB_PATH'] = str(_WORK / 'intelligence.db')
(_WORK / 'reports').mkdir()
with sqlite3.connect(_WORK / 'intelligence.db') as _db:
    _db.execute('CREATE TABLE reports (id INTEGER PRIMARY KEY, title TEXT)')

import app as app_module  # noqa: E402  (needs the environment above)


@pytest.fixture(autouse=True)
def rescan_every_request(monkeypatch):
    """Tests write files between requests; don't wait for the scan interval"""
    monkeypatch.setattr('modules.report_indexer.SCAN_INTERVAL', 0)


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    """An empty reports folder the app reads from for one test"""
    folder = tmp_path / 'reports'
    folder.mkdir()
    monkeypatch.setattr(app_module.indexer, 'reports_dir', folder)
    return folder


@pytest.fixture
def client(reports_dir):
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


def write_report(folder: Path, relative: str, title: str, body: str = 'Body text.') -> Path:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'# {title}\n\n{body}\n', encoding='utf-8')
    return path


SCHEMA = (Path(__file__).parent / 'schema.sql').read_text(encoding='utf-8')


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An empty copy of the intelligence database's schema, used by the app"""
    path = tmp_path / 'intelligence.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
    monkeypatch.setattr(app_module.db_browser, 'db_path', str(path))
    return path


def add_report(db_path, country, report_date, risk, report_type='country'):
    """A row in `reports`; returns its id"""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            '''INSERT INTO reports (report_date, report_type, country, generation_timestamp,
                   model_used, risk_score, json_path, markdown_path)
               VALUES (?, ?, ?, '2026-10-21T00:00:00', 'test', ?, 'x.json', 'x.md')''',
            (report_date, report_type, country, risk),
        )
        return cursor.lastrowid


def add_signal(db_path, report_id, category, impact=50, bias='negative', title='A signal'):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            '''INSERT INTO signals (report_id, category, title, impact_score, directional_bias)
               VALUES (?, ?, ?, ?, ?)''',
            (report_id, category, title, impact, bias),
        )
