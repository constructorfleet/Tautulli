# -*- coding: utf-8 -*-

#  This file is part of Tautulli.
#
#  Tautulli is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Tautulli is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Tautulli.  If not, see <http://www.gnu.org/licenses/>.

"""Prometheus metrics endpoint on a dedicated port.

Metric and label names copy Varken's Tautulli collector. Set
PROMETHEUS_METRIC_PREFIX to 'varken_tautulli' and existing Grafana dashboards
keep working. That is why some of the metric suffixes below look odd.
"""

import re
import threading

import plexpy
from plexpy import helpers
from plexpy import libraries
from plexpy import logger
from plexpy import plextv
from plexpy import pmsconnect

try:
    from prometheus_client import CollectorRegistry
    from prometheus_client.core import GaugeMetricFamily
    from prometheus_client.exposition import start_wsgi_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

_INVALID_METRIC_CHAR_RE = re.compile(r'[^a-zA-Z0-9_]')

# Varken falls back to these when a geo lookup fails.
_PLACEHOLDER_LATITUDE = 37.234332396
_PLACEHOLDER_LONGITUDE = -115.80666344
_PLACEHOLDER_CITY = '\U0001F47D'

_GEOIP_CACHE_SECONDS = 86400

_PLATFORM_OVERRIDES = {
    'osx': 'macOS',
    'windows': 'Windows'
}

# Varken truncates build suffixes for these platforms only.
_VERSION_SPLIT_PLATFORMS = ('Roku', 'osx', 'windows')

_PLAYER_STATES = {
    'playing': 0,
    'paused': 1,
    'buffering': 3
}

_SESSION_LABELS = [
    'type', 'session_id', 'ip_address', 'friendly_name', 'username', 'title',
    'product', 'platform', 'product_version', 'quality', 'video_decision',
    'transcode_decision', 'transcode_hw_decoding', 'transcode_hw_encoding',
    'media_type', 'audio_codec', 'audio_profile', 'stream_audio_codec',
    'quality_profile', 'progress_percent', 'region_code', 'location',
    'full_location', 'latitude', 'longitude', 'player_state', 'device_type',
    'relayed', 'secure', 'server'
]

_STREAM_STAT_LABELS = ['type', 'server']
_LIBRARY_LABELS = ['type', 'server', 'section_name', 'section_type']

_SERVER = None
_THREAD = None
_LOCK = threading.Lock()


def sanitize_metric_name(name):
    sanitized = _INVALID_METRIC_CHAR_RE.sub('_', name.lower()).strip('_')
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized
    return sanitized


class _TTLCache(object):
    """Collapses bursts of scrapes into a single upstream fetch."""

    def __init__(self, loader):
        self._loader = loader
        self._lock = threading.Lock()
        self._value = None
        self._expires_at = 0

    def get(self, ttl):
        with self._lock:
            now = helpers.timestamp()
            if self._value is None or now >= self._expires_at:
                self._value = self._loader()
                self._expires_at = now + max(ttl, 0)
            return self._value


class TautulliCollector(object):
    """Builds Varken-shaped gauges at scrape time."""

    def __init__(self, activity_fetcher=None, library_fetcher=None, geoip_fetcher=None):
        # The fetchers are injectable so tests do not need a Plex server.
        self._activity_cache = _TTLCache(activity_fetcher or self._fetch_activity)
        self._library_cache = _TTLCache(library_fetcher or self._fetch_libraries)
        self._geoip_fetcher = geoip_fetcher or self._fetch_geoip
        self._geoip_cache = {}
        self._geoip_lock = threading.Lock()

    @property
    def _prefix(self):
        return sanitize_metric_name(plexpy.CONFIG.PROMETHEUS_METRIC_PREFIX)

    def _name(self, suffix):
        prefix = self._prefix
        return '{}_{}'.format(prefix, suffix) if prefix else suffix

    @property
    def _server_label(self):
        return plexpy.CONFIG.PROMETHEUS_SERVER_LABEL or plexpy.CONFIG.PMS_NAME or ''

    def _fetch_activity(self):
        pms_connect = pmsconnect.PmsConnect(token=plexpy.CONFIG.PMS_TOKEN)
        activity = pms_connect.get_current_activity()
        # get_current_activity() returns [] when it cannot parse the PMS
        # response. Zero streams would look like an idle server, so fail.
        if not isinstance(activity, dict) or 'sessions' not in activity:
            raise ValueError("no session data returned by the Plex Media Server")
        return activity

    def _fetch_libraries(self):
        return libraries.Libraries().get_section_counts() or []

    def _fetch_geoip(self, ip_address):
        return plextv.PlexTV().get_geoip_lookup(ip_address)

    def _lookup_geoip(self, ip_address):
        """Returns a cached plex.tv geo lookup, or None.

        Cached for a day so that scraping does not hammer plex.tv.
        """
        if not plexpy.CONFIG.PROMETHEUS_GEOIP_LOOKUP or not ip_address:
            return None

        now = helpers.timestamp()
        with self._geoip_lock:
            cached = self._geoip_cache.get(ip_address)
            if cached and now < cached[0]:
                return cached[1]

        try:
            geo_info = self._geoip_fetcher(ip_address)
        except Exception as e:
            logger.warn("Tautulli Prometheus :: GeoIP lookup failed for %s: %s", ip_address, e)
            geo_info = None

        # Negative results are cached too, so a bad IP is not retried every scrape.
        with self._geoip_lock:
            self._geoip_cache[ip_address] = (now + _GEOIP_CACHE_SECONDS, geo_info)

        return geo_info

    def collect(self):
        # Let exceptions through. A failed scrape drops 'up' to 0; exporting
        # zeros would look like an idle server.
        try:
            activity = self._activity_cache.get(plexpy.CONFIG.PROMETHEUS_CACHE_SECONDS)
            sections = self._library_cache.get(plexpy.CONFIG.PROMETHEUS_LIBRARY_CACHE_SECONDS)
        except Exception as e:
            logger.error("Tautulli Prometheus :: Scrape failed: %s", e)
            raise

        sessions = activity.get('sessions') or []

        yield from self._stream_stat_metrics(sessions)
        yield from self._session_metrics(sessions)
        yield from self._library_metrics(sections)

    def _stream_stat_metrics(self, sessions):
        counts = {
            'stream_count': len(sessions),
            'transcode_streams': 0,
            'direct_play_streams': 0,
            'direct_streams': 0,
            'total_bandwidth': 0,
            'lan_bandwidth': 0,
            'wan_bandwidth': 0
        }

        for s in sessions:
            decision = s.get('transcode_decision')
            if decision == 'transcode':
                counts['transcode_streams'] += 1
            elif decision == 'copy':
                counts['direct_streams'] += 1
            else:
                counts['direct_play_streams'] += 1

            bandwidth = helpers.cast_to_int(s.get('bandwidth'))
            counts['total_bandwidth'] += bandwidth
            if s.get('location') == 'lan':
                counts['lan_bandwidth'] += bandwidth
            else:
                counts['wan_bandwidth'] += bandwidth

        labels = ['current_stream_stats', self._server_label]
        for suffix, value in counts.items():
            gauge = GaugeMetricFamily(self._name(suffix),
                                      'Tautulli {}'.format(suffix.replace('_', ' ')),
                                      labels=_STREAM_STAT_LABELS)
            gauge.add_metric(labels, value)
            yield gauge

    def _session_metrics(self, sessions):
        # Called '_hash' to match Varken, which puts an md5 in this field. An
        # md5 is not a float, so Varken always emits 1. It is an info metric.
        gauge = GaugeMetricFamily(self._name('hash'),
                                  'Tautulli active session info',
                                  labels=_SESSION_LABELS)

        for s in sessions:
            gauge.add_metric(self._session_label_values(s), 1)

        yield gauge

    def _session_label_values(self, s):
        platform = s.get('platform') or ''
        platform_name = _PLATFORM_OVERRIDES.get(platform, platform)

        product_version = s.get('product_version') or ''
        if platform in _VERSION_SPLIT_PLATFORMS:
            product_version = product_version.split('-')[0]

        transcode_decision = s.get('transcode_decision') or ''
        if transcode_decision == 'copy':
            transcode_decision = 'direct stream'

        video_decision = s.get('stream_video_decision') or ''
        if video_decision == 'copy':
            video_decision = 'direct stream'
        elif not video_decision:
            video_decision = 'Music'

        geo = self._lookup_geoip(s.get('ip_address_public')) or {}
        city = geo.get('city')
        region = geo.get('region') or ''
        latitude = geo.get('latitude')
        longitude = geo.get('longitude')
        if latitude is None or longitude is None:
            latitude = _PLACEHOLDER_LATITUDE
            longitude = _PLACEHOLDER_LONGITUDE

        label_values = {
            'type': 'Session',
            'session_id': s.get('session_id') or '',
            'ip_address': s.get('ip_address') or '',
            'friendly_name': s.get('friendly_name') or '',
            'username': s.get('username') or '',
            'title': s.get('full_title') or '',
            'product': s.get('product') or '',
            'platform': platform_name,
            'product_version': product_version,
            'quality': _stream_quality(s),
            'video_decision': video_decision.title(),
            'transcode_decision': transcode_decision.title(),
            # Tautulli gives a codec name like 'h264'. Varken's label is 0/1.
            'transcode_hw_decoding': _bool_label(s.get('transcode_hw_decode')),
            'transcode_hw_encoding': _bool_label(s.get('transcode_hw_encode')),
            'media_type': (s.get('media_type') or '').title(),
            'audio_codec': (s.get('audio_codec') or '').upper(),
            'audio_profile': (s.get('audio_profile') or '').upper(),
            'stream_audio_codec': (s.get('stream_audio_codec') or '').upper(),
            'quality_profile': s.get('quality_profile') or '',
            'progress_percent': str(s.get('progress_percent') or ''),
            # plex.tv exposes no subdivision ISO code, so this is the country code.
            'region_code': geo.get('code') or '',
            'location': city or _PLACEHOLDER_CITY,
            'full_location': '{} - {}'.format(region, city) if city else '',
            'latitude': str(latitude),
            'longitude': str(longitude),
            'player_state': _player_state(s.get('state')),
            'device_type': platform_name,
            'relayed': str(helpers.cast_to_int(s.get('relayed'))),
            'secure': str(helpers.cast_to_int(s.get('secure'))),
            'server': self._server_label
        }

        return [label_values[label] for label in _SESSION_LABELS]

    def _library_metrics(self, sections):
        suffixes = ('total', 'seasons', 'episodes', 'artists', 'albums', 'tracks')
        gauges = {
            suffix: GaugeMetricFamily(self._name(suffix),
                                      'Tautulli library {}'.format(suffix),
                                      labels=_LIBRARY_LABELS)
            for suffix in suffixes
        }

        for section in sections:
            section_type = section.get('section_type') or ''
            labels = ['library_stats', self._server_label,
                      section.get('section_name') or '', section_type]

            count = helpers.cast_to_int(section.get('count'))
            parent_count = helpers.cast_to_int(section.get('parent_count'))
            child_count = helpers.cast_to_int(section.get('child_count'))

            gauges['total'].add_metric(labels, count)

            if section_type == 'show':
                gauges['seasons'].add_metric(labels, parent_count)
                gauges['episodes'].add_metric(labels, child_count)
            elif section_type == 'artist':
                gauges['artists'].add_metric(labels, count)
                gauges['albums'].add_metric(labels, parent_count)
                gauges['tracks'].add_metric(labels, child_count)

        yield from gauges.values()


def _stream_quality(s):
    """Mirrors Varken's quality label derivation."""
    resolution = s.get('stream_video_resolution') or ''
    if not resolution:
        return (s.get('container') or '').upper()
    if resolution.lower() in ('sd', '4k'):
        return resolution.upper()
    return s.get('stream_video_full_resolution') or resolution + 'p'


def _bool_label(value):
    return '1' if value else '0'


def _player_state(state):
    """Maps a player state to Varken's numeric code. Unknown states pass through."""
    state = (state or '').lower()
    return str(_PLAYER_STATES.get(state, state))


def start():
    global _SERVER, _THREAD

    if not plexpy.CONFIG.PROMETHEUS_ENABLED:
        return

    with _LOCK:
        if _SERVER is not None:
            return

        if not PROMETHEUS_AVAILABLE:
            logger.error("Tautulli Prometheus :: prometheus_client is not available. "
                         "Prometheus metrics are disabled.")
            return

        host = plexpy.CONFIG.PROMETHEUS_HOST or '0.0.0.0'
        port = plexpy.CONFIG.PROMETHEUS_PORT

        # HTTP_PORT is still unset this early in startup, so check the config too.
        if port in (plexpy.HTTP_PORT, plexpy.CONFIG.HTTP_PORT):
            logger.error("Tautulli Prometheus :: Metrics port %d conflicts with the web server port. "
                         "Prometheus metrics are disabled.", port)
            return

        registry = CollectorRegistry()
        registry.register(TautulliCollector())

        try:
            _SERVER, _THREAD = start_wsgi_server(port, addr=host, registry=registry)
        except Exception as e:
            logger.error("Tautulli Prometheus :: Failed to start metrics endpoint on %s:%d: %s",
                         host, port, e)
            _SERVER = _THREAD = None
            return

        logger.info("Tautulli Prometheus :: Metrics endpoint started on http://%s:%d/metrics", host, port)


def stop():
    global _SERVER, _THREAD

    with _LOCK:
        if _SERVER is None:
            return

        logger.info("Tautulli Prometheus :: Stopping metrics endpoint...")
        try:
            _SERVER.shutdown()
            _SERVER.server_close()
        except Exception as e:
            logger.warn("Tautulli Prometheus :: Error while stopping metrics endpoint: %s", e)

        _SERVER = _THREAD = None


def restart():
    stop()
    start()
