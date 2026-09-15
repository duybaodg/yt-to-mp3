import glob
import ipaddress
import os
import re
import shutil
import socket
import time
import uuid
from urllib.parse import urlparse

import yt_dlp
from flask import (
    Flask,
    after_this_request,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
if os.environ.get('TRUST_PROXY') == '1':
    # Enable only behind the single Nginx proxy; keep port 3000 private.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
REDIS_URL = os.environ.get('REDIS_URL')
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=REDIS_URL or 'memory://',
)

# ── Security config ────────────────────────────────────────────────────────────
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024  # 2 KB max body — a URL is < 200 bytes

DOWNLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
MAX_DOWNLOAD_MB = int(os.environ.get('MAX_DOWNLOAD_MB', '100'))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024
MAX_DURATION_MINUTES = int(os.environ.get('MAX_DURATION_MINUTES', '90'))
MAX_DURATION_SECONDS = MAX_DURATION_MINUTES * 60
STALE_DOWNLOAD_SECONDS = int(os.environ.get('STALE_DOWNLOAD_MINUTES', '10')) * 60
MAX_PLAYLIST_ITEMS = int(os.environ.get('MAX_PLAYLIST_ITEMS', '50'))
MAX_ACTIVE_JOBS = int(os.environ.get('MAX_ACTIVE_JOBS', '10'))
JOB_TIMEOUT_SECONDS = int(os.environ.get('JOB_TIMEOUT_SECONDS', '300'))
JOB_QUEUE_TTL_SECONDS = MAX_ACTIVE_JOBS * JOB_TIMEOUT_SECONDS
JOB_RESULT_TTL_SECONDS = STALE_DOWNLOAD_SECONDS
JOB_SLOT_SECONDS = (
    JOB_QUEUE_TTL_SECONDS + JOB_TIMEOUT_SECONDS + JOB_RESULT_TTL_SECONDS
)
JOB_STATUS_RATE_LIMIT = f'{MAX_ACTIVE_JOBS * 36} per minute'
JOB_SLOTS_KEY = 'yt-convert:active-jobs'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

_RESERVE_JOB_SLOT = '''
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
'''

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
_JOB_FILE_RE = re.compile(
    r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.',
    re.IGNORECASE,
)

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


class MediaLimitError(Exception):
    """Raised when remote media exceeds this service's resource budget."""


def _redis_connection():
    if not REDIS_URL:
        raise RedisError('REDIS_URL is required for conversion jobs')
    return Redis.from_url(REDIS_URL)


def _conversion_queue():
    return Queue('conversions', connection=_redis_connection())


def _reserve_job_slot(job_id):
    now = int(time.time())
    return bool(_redis_connection().eval(
        _RESERVE_JOB_SLOT,
        1,
        JOB_SLOTS_KEY,
        now,
        MAX_ACTIVE_JOBS,
        now + JOB_SLOT_SECONDS,
        job_id,
        JOB_SLOT_SECONDS,
    ))


def _release_job_slot(job_id):
    try:
        _redis_connection().zrem(JOB_SLOTS_KEY, str(job_id))
    except RedisError as exc:
        app.logger.error('Could not release job slot %s: %s', job_id, exc)


def _enqueue_job(function, url):
    job_id = str(uuid.uuid4())
    if not _reserve_job_slot(job_id):
        return None
    try:
        _conversion_queue().enqueue(
            function,
            url,
            job_id,
            job_id=job_id,
            job_timeout=JOB_TIMEOUT_SECONDS,
            ttl=JOB_QUEUE_TTL_SECONDS,
            result_ttl=JOB_RESULT_TTL_SECONDS,
            failure_ttl=JOB_RESULT_TTL_SECONDS,
        )
    except Exception:
        _release_job_slot(job_id)
        raise
    return job_id


def _enforce_media_limits(info, *, incomplete=False):
    if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming'}:
        raise MediaLimitError('Live streams cannot be converted.')
    if info.get('duration') and info['duration'] > MAX_DURATION_SECONDS:
        raise MediaLimitError(
            f'Videos longer than {MAX_DURATION_MINUTES} minutes cannot be converted.'
        )


def _enforce_download_limit(status):
    if (status.get('downloaded_bytes') or 0) > MAX_DOWNLOAD_BYTES:
        raise MediaLimitError(
            f'The selected audio exceeds the {MAX_DOWNLOAD_MB} MB download limit.'
        )


def _cleanup_job(job_id):
    for path in glob.glob(os.path.join(DOWNLOAD_FOLDER, f'{job_id}.*')):
        try:
            os.remove(path)
        except OSError as exc:
            app.logger.error('Error removing temp file %s: %s', path, exc)


def _job_error(message):
    return {'status': 'failed', 'error': message}


def _cleanup_stale_downloads():
    # ponytail: mtime cleanup is the fallback for killed workers; use durable
    # object storage if completed downloads must survive container restarts.
    cutoff = time.time() - STALE_DOWNLOAD_SECONDS
    for entry in os.scandir(DOWNLOAD_FOLDER):
        try:
            if (
                _JOB_FILE_RE.match(entry.name)
                and entry.is_file()
                and entry.stat().st_mtime < cutoff
            ):
                os.remove(entry.path)
                _release_job_slot(entry.name.split('.', 1)[0])
        except OSError as exc:
            app.logger.error('Error removing stale temp file %s: %s', entry.path, exc)


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
    return 'Conversion failed. Please try another video.'


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


def _playlist_info_job(url, job_id):
    try:
        valid_url = is_valid_youtube_url(url) or is_playlist_url(url)
        if not valid_url or not is_safe_url(url):
            return _job_error('Please provide a valid YouTube URL.')

        ydl_opts = _base_ydl_opts()
        ydl_opts.update({
            'extract_flat': True,
            'noplaylist': False,
            'playlistend': MAX_PLAYLIST_ITEMS + 1,
        })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return _job_error('Could not retrieve video information.')

        if 'entries' in info and info.get('_type') != 'video':
            entries = [entry for entry in info.get('entries') or [] if entry]
            if len(entries) > MAX_PLAYLIST_ITEMS:
                return _job_error(
                    f'Playlists are limited to {MAX_PLAYLIST_ITEMS} tracks.'
                )
            tracks = [{
                'video_id': entry['id'],
                'title': entry.get('title', 'Unknown'),
            } for entry in entries if entry.get('id')]
            return {
                'status': 'ready',
                'type': 'playlist',
                'title': info.get('title', 'Playlist'),
                'track_count': len(tracks),
                'tracks': tracks,
            }

        return {
            'status': 'ready',
            'type': 'video',
            'title': info.get('title', 'Unknown'),
            'video_id': info.get('id', ''),
        }
    except yt_dlp.utils.DownloadError as exc:
        app.logger.error('yt-dlp DownloadError in playlist info job: %s', exc)
        return _job_error(friendly_error(exc))
    except Exception as exc:
        app.logger.error('Unexpected error in playlist info job: %s', exc)
        return _job_error('An unexpected error occurred.')
    finally:
        _release_job_slot(job_id)


def _conversion_job(url, job_id):
    output_template = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.%(ext)s')
    output_file = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.mp3')
    succeeded = False

    try:
        if not is_valid_youtube_url(url) or not is_safe_url(url):
            return _job_error('Please provide a valid YouTube video URL.')

        _cleanup_stale_downloads()
        ydl_opts = _base_ydl_opts()
        ydl_opts.update({
            'format': 'bestaudio/best',
            'match_filter': _enforce_media_limits,
            'max_filesize': MAX_DOWNLOAD_BYTES,
            'outtmpl': output_template,
            'progress_hooks': [_enforce_download_limit],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'noplaylist': True,
        })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'audio') if info else 'audio'

        if (
            not os.path.exists(output_file)
            or os.path.getsize(output_file) > MAX_DOWNLOAD_BYTES
        ):
            return _job_error(
                'The audio is unavailable or exceeds the '
                f'{MAX_DOWNLOAD_MB} MB download limit.'
            )

        safe_title = re.sub(r'[^\w\s\-]', '', title, flags=re.UNICODE).strip()
        succeeded = True
        return {
            'status': 'ready',
            'type': 'conversion',
            'download_name': f'{safe_title}.mp3' if safe_title else 'audio.mp3',
        }
    except MediaLimitError as exc:
        return _job_error(str(exc))
    except yt_dlp.utils.DownloadError as exc:
        app.logger.error('yt-dlp DownloadError in conversion job: %s', exc)
        return _job_error(friendly_error(exc))
    except Exception as exc:
        app.logger.error('Unexpected error during conversion job: %s', exc)
        return _job_error('An unexpected error occurred. Please try again.')
    finally:
        if not succeeded:
            _cleanup_job(job_id)
            _release_job_slot(job_id)


def _queued_response(function, url):
    _cleanup_stale_downloads()
    try:
        job_id = _enqueue_job(function, url)
    except (RedisError, OSError) as exc:
        app.logger.error('Could not enqueue conversion job: %s', exc)
        response = jsonify({'error': 'Conversion service is temporarily unavailable.'})
        response.status_code = 503
        response.headers['Retry-After'] = '10'
        return response

    if job_id is None:
        response = jsonify({
            'error': 'The conversion queue is full. Please try again shortly.'
        })
        response.status_code = 503
        response.headers['Retry-After'] = '10'
        return response

    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'status_url': url_for('job_status', job_id=job_id),
        'expires_in': JOB_SLOT_SECONDS,
    }), 202


@app.route('/playlist/info', methods=['POST'])
@limiter.limit('10 per minute')
def playlist_info():
    """Queue a YouTube URL scan and return its status URL."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'url' not in data:
        return jsonify({'error': 'URL is required'}), 400

    url = str(data['url']).strip()

    if not (is_valid_youtube_url(url) or is_playlist_url(url)):
        return jsonify({'error': 'Please provide a valid YouTube URL.'}), 400

    if not is_safe_url(url):
        return jsonify({'error': 'Please provide a valid YouTube URL.'}), 400

    return _queued_response(_playlist_info_job, url)


@app.route('/convert', methods=['POST'])
@limiter.limit('5 per minute')
def convert():
    """Queue a single YouTube video for MP3 conversion."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'url' not in data:
        return jsonify({'error': 'URL is required'}), 400

    url = str(data['url']).strip()

    # Validate
    if not is_valid_youtube_url(url):
        return jsonify({'error': 'Please provide a valid YouTube video URL.'}), 400

    # SSRF guard
    if not is_safe_url(url):
        return jsonify({'error': 'Please provide a valid YouTube URL.'}), 400

    return _queued_response(_conversion_job, url)


@app.route('/jobs/<uuid:job_id>')
@limiter.limit(JOB_STATUS_RATE_LIMIT)
def job_status(job_id):
    try:
        job = Job.fetch(str(job_id), connection=_redis_connection())
        status = job.get_status(refresh=True)
    except NoSuchJobError:
        return jsonify({'error': 'Conversion job not found.'}), 404
    except RedisError as exc:
        app.logger.error('Could not read conversion job: %s', exc)
        return jsonify({'error': 'Conversion service is temporarily unavailable.'}), 503

    if status == 'finished':
        result = dict(job.result or _job_error('Conversion failed.'))
        if result.get('type') == 'conversion' and result.get('status') == 'ready':
            result['download_url'] = url_for('download_job', job_id=job_id)
        return jsonify(result)
    if status in {'failed', 'stopped', 'canceled'}:
        return jsonify(_job_error('Conversion failed. Please try again.'))
    return jsonify({'status': 'running' if status == 'started' else 'queued'})


@app.route('/downloads/<uuid:job_id>')
@limiter.limit('10 per minute')
def download_job(job_id):
    job_id = str(job_id)
    try:
        job = Job.fetch(job_id, connection=_redis_connection())
        status = job.get_status(refresh=True)
    except NoSuchJobError:
        return jsonify({'error': 'Conversion job not found.'}), 404
    except RedisError as exc:
        app.logger.error('Could not read conversion job: %s', exc)
        return jsonify({'error': 'Conversion service is temporarily unavailable.'}), 503

    result = job.result or {}
    output_file = os.path.join(DOWNLOAD_FOLDER, f'{job_id}.mp3')
    if status != 'finished' or result.get('type') != 'conversion':
        return jsonify({'error': 'Conversion is not ready.'}), 409
    if not os.path.exists(output_file):
        _release_job_slot(job_id)
        return jsonify({'error': 'The download has expired.'}), 410

    @after_this_request
    def remove_files(response):
        _cleanup_job(job_id)
        _release_job_slot(job_id)
        return response

    return send_file(
        output_file,
        as_attachment=True,
        download_name=result.get('download_name', 'audio.mp3'),
        mimetype='audio/mpeg',
    )


# ── Payload too large handler ──────────────────────────────────────────────────
@app.errorhandler(413)
def payload_too_large(e):
    return jsonify({'error': 'Request body too large.'}), 413


if __name__ == '__main__':
    app.run(debug=True, port=3000)
