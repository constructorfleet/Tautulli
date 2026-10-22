# -*- coding: utf-8 -*-

"""Tests for Libraries.get_section_counts(), which the Prometheus exporter reads."""

import sqlite3

import pytest

from plexpy import libraries

# Copied from the library_sections DDL in plexpy/__init__.py.
SCHEMA = (
    "CREATE TABLE library_sections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "server_id TEXT, section_id INTEGER, section_name TEXT, section_type TEXT, agent TEXT, "
    "thumb TEXT, custom_thumb_url TEXT, art TEXT, custom_art_url TEXT, "
    "count INTEGER, parent_count INTEGER, child_count INTEGER, is_active INTEGER DEFAULT 1, "
    "do_notify INTEGER DEFAULT 1, do_notify_created INTEGER DEFAULT 1, keep_history INTEGER DEFAULT 1, "
    "deleted_section INTEGER DEFAULT 0, UNIQUE(server_id, section_id))"
)

ROWS = [
    # name, type, count, parent, child, is_active, deleted
    ('Movies', 'movie', 1842, None, None, 1, 0),
    ('TV Shows', 'show', 62, 240, 3745, 1, 0),
    ('Retired', 'movie', 5, None, None, 0, 0),
    ('Removed', 'movie', 7, None, None, 1, 1),
]


class _FakeDatabase:
    """Stands in for MonitorDatabase against an in-memory SQLite file."""

    def __init__(self, conn):
        self._conn = conn

    def select(self, query, args=None):
        return [dict(row) for row in self._conn.execute(query, args or [])]


@pytest.fixture
def section_db(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.executemany(
        "INSERT INTO library_sections (server_id, section_id, section_name, section_type, "
        "count, parent_count, child_count, is_active, deleted_section) "
        "VALUES ('srv', ?, ?, ?, ?, ?, ?, ?, ?)",
        [(i, name, type_, count, parent, child, active, deleted)
         for i, (name, type_, count, parent, child, active, deleted) in enumerate(ROWS, 1)]
    )
    monkeypatch.setattr(libraries.database, 'MonitorDatabase', lambda: _FakeDatabase(conn))
    yield conn
    conn.close()


def test_returns_active_sections(section_db):
    got = libraries.Libraries().get_section_counts()
    assert [s['section_name'] for s in got] == ['Movies', 'TV Shows']


def test_counts_are_returned(section_db):
    got = {s['section_name']: s for s in libraries.Libraries().get_section_counts()}
    assert got['Movies']['count'] == 1842
    assert got['Movies']['parent_count'] is None
    assert got['TV Shows']['parent_count'] == 240
    assert got['TV Shows']['child_count'] == 3745


def test_inactive_and_deleted_sections_are_excluded(section_db):
    names = [s['section_name'] for s in libraries.Libraries().get_section_counts()]
    assert 'Retired' not in names
    assert 'Removed' not in names


def test_query_error_returns_empty_list(monkeypatch):
    class _Broken:
        def select(self, query, args=None):
            raise sqlite3.OperationalError('no such table')

    monkeypatch.setattr(libraries.database, 'MonitorDatabase', lambda: _Broken())
    assert libraries.Libraries().get_section_counts() == []
