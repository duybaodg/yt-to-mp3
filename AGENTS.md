# Repository Guidelines

## Project Structure & Module Organization

`app.py` contains the Flask application, URL validation, rate limiting, yt-dlp configuration, and download routes. Browser code lives in `static/` (`script.js` and `style.css`), while `templates/index.html` defines the Jinja-rendered page. Security-focused pytest coverage is in `tests/test_security.py`. `docs/` contains planning notes. Treat `downloads/`, `venv/`, `__pycache__/`, generated ZIP files, and MP3 files as local runtime artifacts; do not commit them.

## Build, Test, and Development Commands

- `python -m venv venv && source venv/bin/activate` creates and activates a local environment.
- `pip install -r requirements.txt` installs Flask, Flask-Limiter, yt-dlp, and Gunicorn. Install `pytest` separately for development if it is not already available.
- `python app.py` starts the development server at `http://localhost:3000` with debug mode enabled.
- `pytest -v` runs the complete test suite; `pytest tests/test_security.py -v` runs the current security tests directly.
- `gunicorn app:app` runs the application through the production WSGI server.

Audio conversion also requires `ffmpeg` on `PATH`. Node.js is optional and is detected automatically for yt-dlp JavaScript support.

## Coding Style & Naming Conventions

Use four spaces for Python and follow PEP 8: `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for constants, and short route handlers with shared behavior in helpers. Keep JavaScript at four-space indentation with `camelCase` names, and use kebab-case for CSS classes and HTML IDs. Preserve the existing single-quote style in Python and JavaScript. No formatter or linter is configured, so keep changes consistent with surrounding code and avoid unrelated reformatting.

## Testing Guidelines

Use pytest classes named `Test...` and functions named `test_...`. Add focused tests for changes to validation, SSRF protection, rate limits, response headers, error handling, or request-size controls. Mock network/download behavior when possible; tests should not depend on live YouTube responses or create lasting files in `downloads/`.

## Commit & Pull Request Guidelines

This checkout has no Git history, so no established commit convention can be inferred. Use concise imperative subjects, such as `Fix playlist URL validation`. Pull requests should explain behavior changes, list verification commands, link relevant issues, and include screenshots for visible UI changes. Call out security or configuration impacts explicitly.
