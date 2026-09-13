"""
Security tests for the YouTube MP3 Converter Flask app.
Run with: pytest tests/test_security.py -v
"""
import json
import os
import subprocess
import sys
import pytest

from app import app as flask_app


class TestProxyConfiguration:
    @pytest.mark.parametrize('enabled', ['0', '1'])
    def test_forwarded_client_ip_requires_proxy_opt_in(self, enabled):
        subprocess.run([sys.executable, '-c', '''
import os
from flask import request
from flask_limiter.util import get_remote_address
from app import app

@app.route('/proxy-check')
def proxy_check():
    return {'ip': get_remote_address(), 'scheme': request.scheme}

response = app.test_client().get('/proxy-check', headers={
    'X-Forwarded-For': '198.51.100.99, 203.0.113.10',
    'X-Forwarded-Proto': 'https',
})
expected = ({'ip': '203.0.113.10', 'scheme': 'https'}
            if os.environ['TRUST_PROXY'] == '1'
            else {'ip': '127.0.0.1', 'scheme': 'http'})
assert response.get_json() == expected
'''], env={**os.environ, 'TRUST_PROXY': enabled, 'REDIS_URL': 'memory://'}, check=True)


@pytest.fixture()
def client():
    flask_app.config['TESTING'] = True
    # Disable rate limiting during tests (each test gets fresh state anyway)
    flask_app.config['RATELIMIT_ENABLED'] = False
    with flask_app.test_client() as c:
        yield c


# ── Security Headers ────────────────────────────────────────────────────────────

class TestSecurityHeaders:
    def test_x_content_type_options(self, client):
        r = client.get('/')
        assert r.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_x_frame_options(self, client):
        r = client.get('/')
        assert r.headers.get('X-Frame-Options') == 'DENY'

    def test_xss_protection(self, client):
        r = client.get('/')
        assert '1; mode=block' in r.headers.get('X-XSS-Protection', '')

    def test_referrer_policy(self, client):
        r = client.get('/')
        assert r.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'

    def test_csp_present(self, client):
        r = client.get('/')
        csp = r.headers.get('Content-Security-Policy', '')
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp


# ── Input Validation ────────────────────────────────────────────────────────────

class TestInputValidation:
    def _post(self, client, url):
        return client.post(
            '/convert',
            data=json.dumps({'url': url}),
            content_type='application/json',
        )

    def test_empty_body_returns_400(self, client):
        r = client.post('/convert', data='', content_type='application/json')
        assert r.status_code == 400

    def test_missing_url_field_returns_400(self, client):
        r = client.post(
            '/convert',
            data=json.dumps({'foo': 'bar'}),
            content_type='application/json',
        )
        assert r.status_code == 400

    def test_non_youtube_url_blocked(self, client):
        r = self._post(client, 'https://google.com/something')
        assert r.status_code == 400
        assert 'valid YouTube' in r.get_json()['error']

    @pytest.mark.parametrize('url', [
        'https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ',
        'https://youtube.com@evil.test/watch?v=dQw4w9WgXcQ',
        'http://youtube.com/watch?v=dQw4w9WgXcQ',
        'https://youtube.com:444/watch?v=dQw4w9WgXcQ',
    ])
    def test_only_secure_youtube_hosts_allowed(self, client, url):
        assert self._post(client, url).status_code == 400

    def test_plain_text_blocked(self, client):
        r = self._post(client, 'not-a-url-at-all')
        assert r.status_code == 400

    def test_http_injection_blocked(self, client):
        # Attempts to smuggle a newline / extra header
        r = self._post(client, 'https://youtube.com/watch?v=abc\r\nX-Injected: evil')
        assert r.status_code == 400

    def test_playlist_info_rejects_non_youtube_url(self, client):
        r = client.post('/playlist/info', json={'url': 'https://example.com/video'})
        assert r.status_code == 400


# ── SSRF Protection ─────────────────────────────────────────────────────────────

class TestSSRFProtection:
    def _post(self, client, url):
        return client.post(
            '/convert',
            data=json.dumps({'url': url}),
            content_type='application/json',
        )

    def test_loopback_ipv4_blocked(self, client):
        # Even if regex matched somehow, the IP guard should block 127.x.x.x
        # Construct a fake "youtube.com" style path that hits the SSRF guard
        # We test via is_safe_url directly here
        from app import is_safe_url
        assert is_safe_url('http://127.0.0.1/anything') is False

    def test_private_range_blocked(self, client):
        from app import is_safe_url
        assert is_safe_url('http://192.168.1.1/') is False
        assert is_safe_url('http://10.0.0.1/') is False
        assert is_safe_url('http://172.16.0.1/') is False

    def test_youtube_allowed(self, client):
        from app import is_safe_url
        assert is_safe_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ') is True


# ── Request Size Limit ───────────────────────────────────────────────────────────

class TestRequestSizeLimit:
    def test_oversized_body_returns_413(self, client):
        # Send a body much larger than 2 KB
        huge_payload = 'x' * 10_000
        r = client.post(
            '/convert',
            data=json.dumps({'url': huge_payload}),
            content_type='application/json',
        )
        assert r.status_code == 413


# ── Payload Size Limit ─────────────────────────────────────────────────────────
