import ipaddress
import os
import re
import shutil
import socket
import uuid
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, after_this_request, jsonify, render_template, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
if os.environ.get('TRUST_PROXY') == '1':
    # Enable only behind the single Nginx proxy; keep port 3000 private.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=os.environ.get('REDIS_URL', 'memory://'),
)

# ── Security config ────────────────────────────────────────────────────────────
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024  # 2 KB max body — a URL is < 200 bytes

DOWNLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# ── Startup check: ffmpeg ──────────────────────────────────────────────────────
if not shutil.which('ffmpeg'):
    import warnings
    warnings.warn(
        '⚠️  WARNING: ffmpeg not found in PATH. Audio conversion will fail. '
        'Install ffmpeg: https://ffmpeg.org/download.html',
        RuntimeWarning,
    )

# ── URL validation ─────────────────────────────────────────────────────────────
_YOUTUBE_URL_RE = re.compile(
    r'^https?://'
    r'(?:(?:www\.|m\.)?youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)'
    r'[\w\-]{11}',
    re.IGNORECASE,
)

# Matches /playlist?list=... and /watch?v=...&list=... (but not /watch?v=... alone)
_PLAYLIST_URL_RE = re.compile(
    r'^https?://(?:www\.|m\.)?youtube\.com/'
    r'(?:playlist\?list=([\w\-]+)|watch\?(?:.*&)?list=([\w\-]+))',
    re.IGNORECASE,
)

_YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}

# Private / loopback IP ranges — blocked to prevent SSRF
_PRIVATE_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),   # link-local
    ipaddress.ip_network('::1/128'),           # IPv6 loopback
    ipaddress.ip_network('fc00::/7'),          # IPv6 private
]


def is_valid_youtube_url(url: str) -> bool:
    return is_youtube_url(url) and bool(_YOUTUBE_URL_RE.match(url.strip()))


def is_playlist_url(url: str) -> bool:
    """Check if the URL is a YouTube playlist."""
    return is_youtube_url(url) and bool(_PLAYLIST_URL_RE.search(url.strip()))


def is_youtube_url(url: str) -> bool:
    """Allow only HTTPS links served by an expected YouTube hostname."""
    try:
        parsed = urlparse(url.strip())
        return (
            parsed.scheme == 'https'
            and parsed.hostname in _YOUTUBE_HOSTS
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except (AttributeError, ValueError):
        return False


def is_safe_url(url: str) -> bool:
    """Resolve the URL hostname and reject private/loopback IPs (SSRF guard)."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return False
        resolved = socket.getaddrinfo(hostname, None)
        for result in resolved:
            addr_str = result[4][0]
            addr = ipaddress.ip_address(addr_str)
            if any(addr in net for net in _PRIVATE_NETWORKS):
                return False
        return True
    except Exception:
        return False


def friendly_error(exc: Exception) -> str:
    """Map common yt-dlp exceptions to user-friendly messages."""
    msg = str(exc).lower()
    if 'age' in msg or 'sign in' in msg:
        return 'This video is age-restricted and cannot be downloaded.'
    if 'private' in msg:
        return 'This video is private and cannot be accessed.'
    if 'unavailable' in msg or 'not available' in msg:
        return 'This video is unavailable in your region or has been removed.'
    if 'copyright' in msg:
        return 'This video has been blocked due to a copyright claim.'
    if 'live' in msg and 'ended' not in msg:
        return 'Live streams cannot be converted. Try again after the stream ends.'
    if 'ffmpeg' in msg or 'ffprobe' in msg:
        return 'ffmpeg is not installed on the server. Please contact the administrator.'
    return f'Conversion failed: {exc}'


# ── Security headers ───────────────────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "script-src 'self' https://unpkg.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


def _base_ydl_opts():
    """Return the base yt-dlp options shared across routes."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://www.youtube.com/',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            }
        },
    }
    node_path = shutil.which('node')
    if node_path:
        opts['js_runtimes'] = {'node': {'path': node_path}}
    return opts


@app.route('/playlist/info', methods=['POST'])
@limiter.limit('10 per minute', deduct_when=lambda response: response.status_code < 400)
def playlist_info():
    """Scan a YouTube URL and return playlist info if it's a playlist,
    or single-video info if it's a regular video."""
    data = request.get_json(silent=True)
    if not data or 'url' not in data:
        return jsonify({'error': 'URL is required'}), 400

    url = str(data['url']).strip()

    if not (is_valid_youtube_url(url) or is_playlist_url(url)):
        return jsonify({'error': 'Please provide a valid YouTube URL.'}), 400

    if not is_safe_url(url):
        return jsonify({'error': 'Please provide a valid YouTube URL.'}), 400

    # Build options to extract flat playlist info (no download)
    ydl_opts = _base_ydl_opts()
    ydl_opts['extract_flat'] = True
    ydl_opts['noplaylist'] = False

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return jsonify({'error': 'Could not retrieve video information.'}), 422

        # Check if it's a playlist
        if 'entries' in info and info.get('_type') != 'video':
            entries = info.get('entries') or []
            tracks = []
            for entry in entries:
                if entry and entry.get('id'):
                    tracks.append({
                        'video_id': entry['id'],
                        'title': entry.get('title', 'Unknown'),
                    })
            return jsonify({
                'type': 'playlist',
                'title': info.get('title', 'Playlist'),
                'track_count': len(tracks),
                'tracks': tracks,
            })
        else:
            return jsonify({
                'type': 'video',
                'title': info.get('title', 'Unknown'),
                'video_id': info.get('id', ''),
            })

    except yt_dlp.utils.DownloadError as exc:
        app.logger.error('yt-dlp DownloadError in playlist_info: %s', exc)
        return jsonify({'error': friendly_error(exc)}), 422
    except Exception as exc:
        app.logger.error('Unexpected error in playlist_info: %s', exc)
        return jsonify({'error': 'An unexpected error occurred.'}), 500


@app.route('/convert', methods=['POST'])
@limiter.limit('5 per minute', deduct_when=lambda response: response.status_code < 400)
def convert():
    """Download a single YouTube video as MP3."""
    data = request.get_json(silent=True)
    if not data or 'url' not in data:
        return jsonify({'error': 'URL is required'}), 400

    url = str(data['url']).strip()

    # Validate
    if not is_valid_youtube_url(url):
        return jsonify({'error': 'Please provide a valid YouTube video URL.'}), 400

    # SSRF guard
    if not is_safe_url(url):
        return jsonify({'error': 'Please provide a valid YouTube URL.'}), 400

    job_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.%(ext)s')
    output_file = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.mp3')

    ydl_opts = _base_ydl_opts()
    ydl_opts.update({
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'audio') if info else 'audio'

        if not os.path.exists(output_file):
            return jsonify({'error': 'Failed to convert video to MP3. Ensure ffmpeg is installed.'}), 500

        @after_this_request
        def remove_file(response):
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception as err:
                app.logger.error('Error removing temp file %s: %s', output_file, err)
            return response

        safe_title = re.sub(r'[^\w\s\-]', '', title, flags=re.UNICODE).strip()
        download_name = f'{safe_title}.mp3' if safe_title else 'audio.mp3'

        return send_file(
            output_file,
            as_attachment=True,
            download_name=download_name,
            mimetype='audio/mpeg',
        )

    except yt_dlp.utils.DownloadError as exc:
        app.logger.error('yt-dlp DownloadError: %s', exc)
        if os.path.exists(output_file):
            os.remove(output_file)
        return jsonify({'error': friendly_error(exc)}), 422

    except Exception as exc:
        app.logger.error('Unexpected error during conversion: %s', exc)
        if os.path.exists(output_file):
            os.remove(output_file)
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


# ── Payload too large handler ──────────────────────────────────────────────────
@app.errorhandler(413)
def payload_too_large(e):
    return jsonify({'error': 'Request body too large.'}), 413


if __name__ == '__main__':
    app.run(debug=True, port=3000)
