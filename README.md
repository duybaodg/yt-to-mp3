# Tunecast

Tunecast is a small Flask web app that converts YouTube videos to 192 kbps MP3 files. It accepts individual videos and playlists, previews playlist tracks, and lets users download each track separately.

## Features

- Supports YouTube video, Shorts, live-recording, embed, and playlist URLs
- Converts audio with `yt-dlp` and `ffmpeg`
- Runs conversions asynchronously with Redis and RQ
- Limits download size, duration, playlist length, and concurrent jobs
- Validates YouTube hosts and blocks private-network targets to reduce SSRF risk
- Deletes completed downloads after delivery and cleans up stale files

## Quick start with Docker

Docker Compose starts the web app, conversion worker, and Redis:

```sh
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000), paste a YouTube URL, and select **Convert to MP3**.

Stop the services with:

```sh
docker compose down
```

## Local development

Use Python 3.13 and install `ffmpeg` and Redis locally. Node.js is optional but improves compatibility with YouTube's JavaScript-based extraction.

```sh
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt pytest
```

Start each process in a separate terminal:

```sh
# Terminal 1
redis-server
```

```sh
# Terminal 2
source venv/bin/activate
export REDIS_URL=redis://localhost:6379/0
rq worker --url "$REDIS_URL" conversions
```

```sh
# Terminal 3
source venv/bin/activate
export REDIS_URL=redis://localhost:6379/0
python app.py
```

The development server runs at [http://localhost:3000](http://localhost:3000).

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `REDIS_URL` | None | Redis connection URL; required for conversion jobs |
| `MAX_DOWNLOAD_MB` | `100` | Maximum source or converted file size |
| `MAX_DURATION_MINUTES` | `90` | Maximum video duration |
| `MAX_PLAYLIST_ITEMS` | `50` | Maximum tracks returned for a playlist |
| `MAX_ACTIVE_JOBS` | `10` | Maximum admitted scan and conversion jobs |
| `JOB_TIMEOUT_SECONDS` | `300` | Worker timeout for one job |
| `STALE_DOWNLOAD_MINUTES` | `10` | Age at which abandoned files are removed |
| `TRUST_PROXY` | Unset | Set to `1` only behind the configured single reverse proxy |

Live streams and upcoming streams are rejected. A finished stream may be converted once YouTube exposes it as a regular video.

## How it works

The browser submits a URL to Flask, which validates it and queues the work in Redis. An RQ worker uses `yt-dlp` and `ffmpeg` to inspect or convert the media while the browser polls for status. Finished MP3 files are served once and then removed.

## Tests

```sh
python -m pytest -v
```

The test suite covers URL validation, SSRF protection, request limits, security headers, queue behavior, media limits, and temporary-file cleanup without downloading live YouTube content.

## Deployment

The included Docker Compose stack is designed to run behind Nginx. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the Ubuntu, Cloudflare, GHCR, and GitHub Actions setup.

## Project structure

```text
app.py                 Flask routes, validation, queue jobs, and conversion logic
static/                Browser JavaScript and CSS
templates/index.html   Web interface
tests/                 Pytest security and job-flow coverage
deploy/nginx.conf      Production reverse-proxy configuration
compose.yaml           App, worker, and Redis services
```

## Responsible use

Only download media you own or have permission to use. You are responsible for complying with YouTube's terms and applicable copyright law.
