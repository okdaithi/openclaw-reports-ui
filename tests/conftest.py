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
