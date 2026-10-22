# -*- coding: utf-8 -*-

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tautulli runs from the repo root with its dependencies vendored in lib/.
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'lib'))

import plexpy  # noqa: E402
import plexpy.config  # noqa: E402
import plexpy.plextv  # noqa: E402
import plexpy.pmsconnect  # noqa: E402


@pytest.fixture(autouse=True)
def no_outbound_calls(monkeypatch):
    """Blocks real Plex and plex.tv connections and reports any attempt.

    The attempt list is checked after the test because _lookup_geoip()
    swallows every exception, which would otherwise hide the mistake.
    """
    attempts = []

    def forbidden(name):
        def _raise(*args, **kwargs):
            attempts.append(name)
            raise AssertionError('test tried to reach {}; inject a fake instead'.format(name))
        return _raise

    monkeypatch.setattr(plexpy.plextv, 'PlexTV', forbidden('plex.tv'))
    monkeypatch.setattr(plexpy.pmsconnect, 'PmsConnect', forbidden('the Plex Media Server'))

    yield

    assert not attempts, 'test reached for {}'.format(', '.join(sorted(set(attempts))))


@pytest.fixture
def unused_port():
    """Reserves a free loopback port, then releases it for the server to bind."""
    import socket

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def tautulli_config(tmp_path, monkeypatch):
    """Installs a real Config backed by a throwaway ini file."""
    cfg = plexpy.config.Config(str(tmp_path / 'config.ini'))
    cfg.PMS_NAME = 'Winterfell'
    monkeypatch.setattr(plexpy, 'CONFIG', cfg)
    monkeypatch.setattr(plexpy, 'HTTP_PORT', 8181)
    return cfg
