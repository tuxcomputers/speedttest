import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'speedtest'))


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch):
    """Point the agent modules at a throwaway SQLite file and a clean host
    identity for each test."""
    monkeypatch.setenv('SQLITE_PATH', str(tmp_path / 'speedtest.db'))
    monkeypatch.setenv('HOST_HOSTNAME', 'testhost')
    monkeypatch.setenv('HOST_HASH', 'abc123def456abcd')
    monkeypatch.delenv('DB_HOST', raising=False)
    return tmp_path
