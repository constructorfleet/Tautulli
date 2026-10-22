# -*- coding: utf-8 -*-

"""Tests for the Prometheus metrics endpoint.

The collector's data sources are injected, so none of this needs a Plex server.
"""

import threading
import urllib.error
import urllib.request

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from plexpy import prometheus


# Minimal session fixtures. Each one exercises a different label branch.

TRANSCODE_SESSION = {
    'session_id': 'abc123', 'ip_address': '10.0.0.5', 'ip_address_public': '1.2.3.4',
    'friendly_name': 'Jon', 'username': 'jsnow', 'full_title': 'GoT - Hardhome',
    'product': 'Plex Web', 'product_version': '4.1.2-abcdef', 'platform': 'windows',
    'stream_video_resolution': '1080', 'stream_video_full_resolution': '1080p',
    'container': 'mkv', 'stream_video_decision': 'transcode',
    'transcode_decision': 'transcode', 'transcode_hw_decode': 'h264',
    'transcode_hw_encode': '', 'media_type': 'episode', 'audio_codec': 'eac3',
    'audio_profile': '', 'stream_audio_codec': 'aac', 'quality_profile': '8 Mbps 1080p',
    'progress_percent': '42', 'state': 'playing', 'relayed': 0, 'secure': 1,
    'bandwidth': '12000', 'location': 'wan',
}

MUSIC_SESSION = {
    'session_id': 'def456', 'ip_address': '192.168.1.9', 'ip_address_public': '5.6.7.8',
    'friendly_name': 'Arya', 'username': 'astark', 'full_title': 'Rains of Castamere',
    'product': 'Plexamp', 'product_version': '4.9.0', 'platform': 'osx',
    'stream_video_resolution': '', 'stream_video_full_resolution': '',
    'container': 'flac', 'stream_video_decision': '', 'transcode_decision': 'copy',
    'transcode_hw_decode': '', 'transcode_hw_encode': '', 'media_type': 'track',
    'audio_codec': 'flac', 'audio_profile': '', 'stream_audio_codec': 'flac',
    'quality_profile': 'Original', 'progress_percent': '7', 'state': 'paused',
    'relayed': 1, 'secure': 0, 'bandwidth': '1400', 'location': 'lan',
}

DIRECT_PLAY_SESSION = {
    'session_id': 'ghi789', 'ip_address': '10.0.0.7', 'ip_address_public': '',
    'friendly_name': 'Bran', 'username': 'bstark', 'full_title': 'Dune',
    'product': 'Plex for Roku', 'product_version': '2.5.1-999', 'platform': 'Roku',
    'stream_video_resolution': '4k', 'stream_video_full_resolution': '4k',
    'container': 'mp4', 'stream_video_decision': 'direct play',
    'transcode_decision': 'direct play', 'transcode_hw_decode': '',
    'transcode_hw_encode': '', 'media_type': 'movie', 'audio_codec': 'truehd',
    'audio_profile': 'atmos', 'stream_audio_codec': 'truehd',
    'quality_profile': 'Original', 'progress_percent': '99', 'state': 'buffering',
    'relayed': 0, 'secure': 1, 'bandwidth': '80000', 'location': 'lan',
}

ALL_SESSIONS = [TRANSCODE_SESSION, MUSIC_SESSION, DIRECT_PLAY_SESSION]

SECTIONS = [
    {'section_id': 1, 'section_name': 'Movies', 'section_type': 'movie',
     'count': 1842, 'parent_count': None, 'child_count': None},
    {'section_id': 2, 'section_name': 'TV Shows', 'section_type': 'show',
     'count': 62, 'parent_count': 240, 'child_count': 3745},
    {'section_id': 3, 'section_name': 'Music', 'section_type': 'artist',
     'count': 512, 'parent_count': 1300, 'child_count': 14000},
]


def collector(sessions=None, sections=None, geoip=None):
    activity = {'stream_count': str(len(sessions or [])), 'sessions': list(sessions or [])}
    return prometheus.TautulliCollector(
        activity_fetcher=lambda: activity,
        library_fetcher=lambda: list(sections or []),
        geoip_fetcher=geoip or (lambda ip: None),
    )


def samples(coll):
    """Flattens a collection into {metric_name: [(labels, value), ...]}."""
    registry = CollectorRegistry()
    registry.register(coll)
    out = {}
    for metric in registry.collect():
        out[metric.name] = [(s.labels, s.value) for s in metric.samples]
    return out


def one_value(coll, name):
    found = samples(coll)[name]
    assert len(found) == 1, '{} produced {} samples'.format(name, len(found))
    return found[0][1]


def session_labels(coll, username):
    for labels, _ in samples(coll)['tautulli_hash']:
        if labels['username'] == username:
            return labels
    raise AssertionError('no session series for {}'.format(username))


class TestMetricNaming:
    def test_default_prefix(self, tautulli_config):
        assert 'tautulli_stream_count' in samples(collector())

    def test_varken_prefix_reproduces_varken_metric_names(self, tautulli_config):
        """The drop-in contract: these are the names Varken's exporter emitted."""
        tautulli_config.PROMETHEUS_METRIC_PREFIX = 'varken_tautulli'
        got = samples(collector(sessions=ALL_SESSIONS, sections=SECTIONS))
        for name in ('varken_tautulli_stream_count', 'varken_tautulli_total_bandwidth',
                     'varken_tautulli_wan_bandwidth', 'varken_tautulli_lan_bandwidth',
                     'varken_tautulli_transcode_streams', 'varken_tautulli_direct_play_streams',
                     'varken_tautulli_direct_streams', 'varken_tautulli_hash',
                     'varken_tautulli_total', 'varken_tautulli_seasons',
                     'varken_tautulli_episodes', 'varken_tautulli_artists',
                     'varken_tautulli_albums', 'varken_tautulli_tracks'):
            assert name in got, name

    def test_empty_prefix_gives_bare_names(self, tautulli_config):
        tautulli_config.PROMETHEUS_METRIC_PREFIX = ''
        assert 'stream_count' in samples(collector())

    @pytest.mark.parametrize('prefix, expected', [
        ('tautulli', 'tautulli_stream_count'),
        ('My Server!!', 'my_server_stream_count'),
        ('a.b-c', 'a_b_c_stream_count'),
        ('2nd_server', '_2nd_server_stream_count'),
        ('!!!', 'stream_count'),
    ])
    def test_prefix_sanitization(self, tautulli_config, prefix, expected):
        tautulli_config.PROMETHEUS_METRIC_PREFIX = prefix
        assert expected in samples(collector())


class TestSanitizeMetricName:
    @pytest.mark.parametrize('raw, expected', [
        ('Tautulli', 'tautulli'),
        ('varken_tautulli', 'varken_tautulli'),
        ('a b c', 'a_b_c'),
        ('9lives', '_9lives'),
        ('__edges__', 'edges'),
        ('', ''),
        ('%%%', ''),
    ])
    def test_sanitize(self, raw, expected):
        assert prometheus.sanitize_metric_name(raw) == expected


class TestStreamStats:
    def test_decision_counts(self, tautulli_config):
        coll = collector(sessions=ALL_SESSIONS)
        assert one_value(coll, 'tautulli_stream_count') == 3
        assert one_value(coll, 'tautulli_transcode_streams') == 1
        assert one_value(coll, 'tautulli_direct_streams') == 1
        assert one_value(coll, 'tautulli_direct_play_streams') == 1

    def test_bandwidth_split_by_location(self, tautulli_config):
        coll = collector(sessions=ALL_SESSIONS)
        assert one_value(coll, 'tautulli_wan_bandwidth') == 12000
        assert one_value(coll, 'tautulli_lan_bandwidth') == 81400
        assert one_value(coll, 'tautulli_total_bandwidth') == 93400

    def test_missing_bandwidth_counts_as_zero(self, tautulli_config):
        session = dict(TRANSCODE_SESSION, bandwidth=None)
        assert one_value(collector(sessions=[session]), 'tautulli_total_bandwidth') == 0

    def test_idle_server_reports_zeros(self, tautulli_config):
        coll = collector(sessions=[])
        assert one_value(coll, 'tautulli_stream_count') == 0
        assert one_value(coll, 'tautulli_total_bandwidth') == 0
        assert samples(coll)['tautulli_hash'] == []

    def test_stream_stat_labels(self, tautulli_config):
        labels = samples(collector())['tautulli_stream_count'][0][0]
        assert labels == {'type': 'current_stream_stats', 'server': 'Winterfell'}


class TestServerLabel:
    def test_falls_back_to_pms_name(self, tautulli_config):
        assert samples(collector())['tautulli_stream_count'][0][0]['server'] == 'Winterfell'

    def test_explicit_label_wins(self, tautulli_config):
        tautulli_config.PROMETHEUS_SERVER_LABEL = 'plex-01'
        assert samples(collector())['tautulli_stream_count'][0][0]['server'] == 'plex-01'


class TestSessionLabels:
    def test_full_label_set(self, tautulli_config):
        labels = session_labels(collector(sessions=ALL_SESSIONS), 'jsnow')
        assert set(labels) == set(prometheus._SESSION_LABELS)

    def test_value_is_always_one(self, tautulli_config):
        for _, value in samples(collector(sessions=ALL_SESSIONS))['tautulli_hash']:
            assert value == 1

    def test_transcoding_session_labels(self, tautulli_config):
        labels = session_labels(collector(sessions=ALL_SESSIONS), 'jsnow')
        assert labels['type'] == 'Session'
        assert labels['title'] == 'GoT - Hardhome'
        assert labels['media_type'] == 'Episode'
        assert labels['transcode_decision'] == 'Transcode'
        assert labels['video_decision'] == 'Transcode'
        assert labels['quality'] == '1080p'
        assert labels['audio_codec'] == 'EAC3'
        assert labels['stream_audio_codec'] == 'AAC'
        assert labels['player_state'] == '0'
        assert labels['secure'] == '1'
        assert labels['relayed'] == '0'

    def test_copy_becomes_direct_stream(self, tautulli_config):
        labels = session_labels(collector(sessions=ALL_SESSIONS), 'astark')
        assert labels['transcode_decision'] == 'Direct Stream'

    def test_blank_video_decision_becomes_music(self, tautulli_config):
        labels = session_labels(collector(sessions=ALL_SESSIONS), 'astark')
        assert labels['video_decision'] == 'Music'

    @pytest.mark.parametrize('platform, expected', [
        ('osx', 'macOS'),
        ('windows', 'Windows'),
        ('Roku', 'Roku'),
        ('Android', 'Android'),
        ('', ''),
    ])
    def test_platform_overrides(self, tautulli_config, platform, expected):
        session = dict(TRANSCODE_SESSION, platform=platform)
        labels = session_labels(collector(sessions=[session]), 'jsnow')
        assert labels['platform'] == expected
        assert labels['device_type'] == expected

    @pytest.mark.parametrize('platform, version, expected', [
        ('windows', '4.1.2-abcdef', '4.1.2'),
        ('osx', '1.2.3-xyz', '1.2.3'),
        ('Roku', '2.5.1-999', '2.5.1'),
        ('Android', '9.9.9-keepme', '9.9.9-keepme'),
        ('Chrome', '1.0-build7', '1.0-build7'),
    ])
    def test_product_version_truncated_only_on_some_platforms(
            self, tautulli_config, platform, version, expected):
        session = dict(TRANSCODE_SESSION, platform=platform, product_version=version)
        labels = session_labels(collector(sessions=[session]), 'jsnow')
        assert labels['product_version'] == expected

    @pytest.mark.parametrize('state, expected', [
        ('playing', '0'),
        ('paused', '1'),
        ('buffering', '3'),
        ('PLAYING', '0'),
        ('error', 'error'),
        ('', ''),
    ])
    def test_player_state(self, tautulli_config, state, expected):
        session = dict(TRANSCODE_SESSION, state=state)
        labels = session_labels(collector(sessions=[session]), 'jsnow')
        assert labels['player_state'] == expected

    @pytest.mark.parametrize('decode, encode, expect_decode, expect_encode', [
        ('h264', '', '1', '0'),
        ('', 'hevc', '0', '1'),
        ('h264', 'hevc', '1', '1'),
        ('', '', '0', '0'),
        (None, None, '0', '0'),
    ])
    def test_hardware_transcode_flags(self, tautulli_config, decode, encode,
                                      expect_decode, expect_encode):
        session = dict(TRANSCODE_SESSION,
                       transcode_hw_decode=decode, transcode_hw_encode=encode)
        labels = session_labels(collector(sessions=[session]), 'jsnow')
        assert labels['transcode_hw_decoding'] == expect_decode
        assert labels['transcode_hw_encoding'] == expect_encode

    def test_missing_keys_do_not_raise(self, tautulli_config):
        """Sessions from older or partially masked payloads must still export."""
        labels = session_labels(collector(sessions=[{'username': 'ghost'}]), 'ghost')
        assert set(labels) == set(prometheus._SESSION_LABELS)
        assert labels['title'] == ''


class TestStreamQuality:
    @pytest.mark.parametrize('session, expected', [
        ({'stream_video_resolution': '1080', 'stream_video_full_resolution': '1080p'}, '1080p'),
        ({'stream_video_resolution': '', 'container': 'flac'}, 'FLAC'),
        ({'stream_video_resolution': '4k', 'stream_video_full_resolution': '4k'}, '4K'),
        ({'stream_video_resolution': '4K'}, '4K'),
        ({'stream_video_resolution': 'sd'}, 'SD'),
        ({'stream_video_resolution': 'SD'}, 'SD'),
        ({'stream_video_resolution': '720'}, '720p'),
        ({}, ''),
    ])
    def test_quality_derivation(self, session, expected):
        assert prometheus._stream_quality(session) == expected


class TestLibraryMetrics:
    def test_movie_library_reports_total_only(self, tautulli_config):
        got = samples(collector(sections=[SECTIONS[0]]))
        assert got['tautulli_total'] == [
            ({'type': 'library_stats', 'server': 'Winterfell',
              'section_name': 'Movies', 'section_type': 'movie'}, 1842)
        ]
        assert got['tautulli_seasons'] == []
        assert got['tautulli_artists'] == []

    def test_show_library_reports_seasons_and_episodes(self, tautulli_config):
        got = samples(collector(sections=[SECTIONS[1]]))
        assert got['tautulli_total'][0][1] == 62
        assert got['tautulli_seasons'][0][1] == 240
        assert got['tautulli_episodes'][0][1] == 3745
        assert got['tautulli_tracks'] == []

    def test_artist_library_reports_music_counts(self, tautulli_config):
        got = samples(collector(sections=[SECTIONS[2]]))
        assert got['tautulli_artists'][0][1] == 512
        assert got['tautulli_albums'][0][1] == 1300
        assert got['tautulli_tracks'][0][1] == 14000
        assert got['tautulli_episodes'] == []

    def test_null_counts_become_zero(self, tautulli_config):
        section = {'section_id': 9, 'section_name': 'Empty', 'section_type': 'show',
                   'count': None, 'parent_count': None, 'child_count': None}
        got = samples(collector(sections=[section]))
        assert got['tautulli_total'][0][1] == 0
        assert got['tautulli_seasons'][0][1] == 0

    def test_no_libraries(self, tautulli_config):
        assert samples(collector(sections=[]))['tautulli_total'] == []


class TestGeoIP:
    DENVER = {'city': 'Denver', 'code': 'US', 'region': 'Colorado',
              'latitude': 39.7392, 'longitude': -104.9903}

    def test_disabled_uses_placeholders(self, tautulli_config):
        calls = []
        coll = collector(sessions=[TRANSCODE_SESSION],
                         geoip=lambda ip: calls.append(ip) or self.DENVER)
        labels = session_labels(coll, 'jsnow')
        assert calls == []
        assert labels['location'] == prometheus._PLACEHOLDER_CITY
        assert labels['latitude'] == str(prometheus._PLACEHOLDER_LATITUDE)
        assert labels['longitude'] == str(prometheus._PLACEHOLDER_LONGITUDE)
        assert labels['region_code'] == ''
        assert labels['full_location'] == ''

    def test_enabled_populates_geo_labels(self, tautulli_config):
        tautulli_config.PROMETHEUS_GEOIP_LOOKUP = 1
        coll = collector(sessions=[TRANSCODE_SESSION], geoip=lambda ip: self.DENVER)
        labels = session_labels(coll, 'jsnow')
        assert labels['location'] == 'Denver'
        assert labels['region_code'] == 'US'
        assert labels['full_location'] == 'Colorado - Denver'
        assert labels['latitude'] == '39.7392'
        assert labels['longitude'] == '-104.9903'

    def test_unresolved_ip_falls_back_to_placeholders(self, tautulli_config):
        tautulli_config.PROMETHEUS_GEOIP_LOOKUP = 1
        coll = collector(sessions=[TRANSCODE_SESSION], geoip=lambda ip: None)
        assert session_labels(coll, 'jsnow')['location'] == prometheus._PLACEHOLDER_CITY

    def test_lookup_failure_does_not_break_the_scrape(self, tautulli_config):
        tautulli_config.PROMETHEUS_GEOIP_LOOKUP = 1

        def boom(ip):
            raise RuntimeError('plex.tv unreachable')

        coll = collector(sessions=[TRANSCODE_SESSION], geoip=boom)
        assert session_labels(coll, 'jsnow')['location'] == prometheus._PLACEHOLDER_CITY

    def test_blank_public_ip_skips_lookup(self, tautulli_config):
        tautulli_config.PROMETHEUS_GEOIP_LOOKUP = 1
        calls = []
        coll = collector(sessions=[DIRECT_PLAY_SESSION],
                         geoip=lambda ip: calls.append(ip) or self.DENVER)
        session_labels(coll, 'bstark')
        assert calls == []

    def test_results_are_cached_per_ip(self, tautulli_config):
        tautulli_config.PROMETHEUS_GEOIP_LOOKUP = 1
        tautulli_config.PROMETHEUS_CACHE_SECONDS = 0
        calls = []
        coll = collector(sessions=ALL_SESSIONS,
                         geoip=lambda ip: calls.append(ip) or self.DENVER)
        for _ in range(3):
            samples(coll)
        # Two distinct public IPs; the third session has none.
        assert sorted(calls) == ['1.2.3.4', '5.6.7.8']

    def test_failed_lookups_are_also_cached(self, tautulli_config):
        tautulli_config.PROMETHEUS_GEOIP_LOOKUP = 1
        tautulli_config.PROMETHEUS_CACHE_SECONDS = 0
        calls = []
        coll = collector(sessions=[TRANSCODE_SESSION],
                         geoip=lambda ip: calls.append(ip) or None)
        for _ in range(3):
            samples(coll)
        assert calls == ['1.2.3.4']


class TestScrapeFailure:
    def test_fetch_error_propagates(self, tautulli_config):
        def boom():
            raise RuntimeError('PMS unreachable')

        coll = prometheus.TautulliCollector(activity_fetcher=boom,
                                            library_fetcher=lambda: [])
        with pytest.raises(RuntimeError):
            samples(coll)

    @pytest.mark.parametrize('bad', [[], None, {}, {'stream_count': '0'}])
    def test_unparseable_activity_is_not_treated_as_idle(self, tautulli_config, bad,
                                                         monkeypatch):
        """get_current_activity() returns [] on parse failure; that is not zero streams."""
        monkeypatch.setattr(prometheus.pmsconnect, 'PmsConnect',
                            lambda **kw: _FakePms(bad))
        coll = prometheus.TautulliCollector(library_fetcher=lambda: [])
        with pytest.raises(ValueError):
            samples(coll)

    def test_genuine_idle_server_still_scrapes(self, tautulli_config, monkeypatch):
        monkeypatch.setattr(prometheus.pmsconnect, 'PmsConnect',
                            lambda **kw: _FakePms({'stream_count': '0', 'sessions': []}))
        coll = prometheus.TautulliCollector(library_fetcher=lambda: [])
        assert one_value(coll, 'tautulli_stream_count') == 0

    def test_library_error_propagates(self, tautulli_config):
        def boom():
            raise RuntimeError('database locked')

        coll = prometheus.TautulliCollector(activity_fetcher=lambda: {'sessions': []},
                                            library_fetcher=boom)
        with pytest.raises(RuntimeError):
            samples(coll)


class _FakePms:
    def __init__(self, activity):
        self._activity = activity

    def get_current_activity(self):
        return self._activity


class TestTTLCache:
    def test_caches_within_ttl(self):
        calls = []
        cache = prometheus._TTLCache(lambda: calls.append(1) or len(calls))
        assert cache.get(300) == 1
        assert cache.get(300) == 1
        assert len(calls) == 1

    def test_refetches_after_expiry(self, monkeypatch):
        clock = [1000]
        monkeypatch.setattr(prometheus.helpers, 'timestamp', lambda: clock[0])
        calls = []
        cache = prometheus._TTLCache(lambda: calls.append(1) or len(calls))
        assert cache.get(60) == 1
        clock[0] += 59
        assert cache.get(60) == 1
        clock[0] += 1
        assert cache.get(60) == 2
        assert len(calls) == 2

    def test_zero_ttl_always_refetches(self):
        calls = []
        cache = prometheus._TTLCache(lambda: calls.append(1) or len(calls))
        cache.get(0)
        cache.get(0)
        assert len(calls) == 2

    def test_concurrent_scrapes_collapse_to_one_fetch(self):
        calls = []
        barrier = threading.Barrier(8)

        def slow_loader():
            calls.append(1)
            return len(calls)

        cache = prometheus._TTLCache(slow_loader)

        def worker():
            barrier.wait()
            cache.get(300)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1

    def test_error_does_not_poison_the_cache(self):
        state = {'fail': True}

        def loader():
            if state['fail']:
                raise RuntimeError('nope')
            return 'good'

        cache = prometheus._TTLCache(loader)
        with pytest.raises(RuntimeError):
            cache.get(300)
        state['fail'] = False
        assert cache.get(300) == 'good'


class TestServerLifecycle:
    @pytest.fixture(autouse=True)
    def _always_stop(self):
        yield
        prometheus.stop()

    def test_disabled_does_not_listen(self, tautulli_config):
        tautulli_config.PROMETHEUS_ENABLED = 0
        prometheus.start()
        assert prometheus._SERVER is None

    def test_port_clash_with_web_server_is_refused(self, tautulli_config):
        tautulli_config.PROMETHEUS_ENABLED = 1
        tautulli_config.PROMETHEUS_PORT = tautulli_config.HTTP_PORT
        prometheus.start()
        assert prometheus._SERVER is None

    def test_serves_metrics(self, tautulli_config, monkeypatch, unused_port):
        monkeypatch.setattr(prometheus.pmsconnect, 'PmsConnect',
                            lambda **kw: _FakePms({'stream_count': '1',
                                                   'sessions': [TRANSCODE_SESSION]}))
        monkeypatch.setattr(prometheus.libraries, 'Libraries',
                            lambda: _FakeLibraries(SECTIONS))
        tautulli_config.PROMETHEUS_ENABLED = 1
        tautulli_config.PROMETHEUS_HOST = '127.0.0.1'
        tautulli_config.PROMETHEUS_PORT = unused_port

        prometheus.start()
        body = _scrape(unused_port)

        assert 'tautulli_stream_count{server="Winterfell",type="current_stream_stats"} 1.0' in body
        assert 'tautulli_hash{' in body
        assert 'tautulli_total{' in body

    def test_start_is_idempotent(self, tautulli_config, unused_port):
        tautulli_config.PROMETHEUS_ENABLED = 1
        tautulli_config.PROMETHEUS_HOST = '127.0.0.1'
        tautulli_config.PROMETHEUS_PORT = unused_port
        prometheus.start()
        first = prometheus._SERVER
        prometheus.start()
        assert prometheus._SERVER is first

    def test_stop_closes_the_port(self, tautulli_config, unused_port):
        tautulli_config.PROMETHEUS_ENABLED = 1
        tautulli_config.PROMETHEUS_HOST = '127.0.0.1'
        tautulli_config.PROMETHEUS_PORT = unused_port
        prometheus.start()
        prometheus.stop()
        assert prometheus._SERVER is None
        with pytest.raises(urllib.error.URLError):
            _scrape(unused_port)

    def test_failed_scrape_returns_500(self, tautulli_config, monkeypatch, unused_port):
        monkeypatch.setattr(prometheus.pmsconnect, 'PmsConnect',
                            lambda **kw: _FakePms([]))
        monkeypatch.setattr(prometheus.libraries, 'Libraries',
                            lambda: _FakeLibraries([]))
        tautulli_config.PROMETHEUS_ENABLED = 1
        tautulli_config.PROMETHEUS_HOST = '127.0.0.1'
        tautulli_config.PROMETHEUS_PORT = unused_port
        tautulli_config.PROMETHEUS_CACHE_SECONDS = 0

        prometheus.start()
        with pytest.raises(urllib.error.HTTPError) as exc:
            _scrape(unused_port)
        assert exc.value.code == 500

    def test_client_missing_disables_endpoint(self, tautulli_config, monkeypatch,
                                             unused_port):
        monkeypatch.setattr(prometheus, 'PROMETHEUS_AVAILABLE', False)
        tautulli_config.PROMETHEUS_ENABLED = 1
        tautulli_config.PROMETHEUS_HOST = '127.0.0.1'
        tautulli_config.PROMETHEUS_PORT = unused_port
        prometheus.start()
        assert prometheus._SERVER is None


class _FakeLibraries:
    def __init__(self, sections):
        self._sections = sections

    def get_section_counts(self):
        return self._sections


def _scrape(port):
    with urllib.request.urlopen('http://127.0.0.1:{}/metrics'.format(port), timeout=10) as r:
        return r.read().decode()


def test_exposition_output_is_parseable(tautulli_config):
    """generate_latest() rejects duplicate or malformed metric names."""
    text = generate_latest(_registry(collector(sessions=ALL_SESSIONS, sections=SECTIONS)))
    assert b'tautulli_stream_count' in text
    assert text.count(b'# TYPE tautulli_hash gauge') == 1


def _registry(coll):
    registry = CollectorRegistry()
    registry.register(coll)
    return registry
