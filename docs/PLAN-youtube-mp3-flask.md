# PLAN: YouTube to MP3 Converter (Flask)

## Overview
A web application built with Python and Flask that allows users to input a YouTube Video URL, converts the video to MP3 format using `yt-dlp` and `ffmpeg`, and provides a direct download link to the user.

## Project Type
WEB / BACKEND

## Success Criteria
- User can input a valid YouTube URL.
- Application validates the URL format.
- Application successfully extracts audio and converts it to MP3 using `yt-dlp` and `ffmpeg`.
- User receives the MP3 file as a downloadable attachment.
- Temporary files are cleanly removed after serving to prevent disk space exhaustion.

## Tech Stack
- **Backend:** Python + Flask (Routing, file serving, simple API)
- **Audio Processing:** `yt-dlp` (Video grabbing), `ffmpeg` (Audio extraction/conversion)
- **Frontend:** Vanilla HTML/CSS/JavaScript (Simple UI with fetch/XHR for loading states)
- **Styling:** Custom CSS or simple Tailwind CDN for an aesthetics-first, modern interface.

## File Structure
```text
/
├── app.py                 # Main Flask application and routes
├── requirements.txt       # Python dependencies (Flask, yt-dlp)
├── templates/
│   └── index.html         # Frontend UI
├── static/
│   ├── style.css          # UI Styling & Animations
│   └── script.js          # Client-side validation & loading states
└── downloads/             # Temporary directory for converted MP3s (should be gitignored)
```

## Task Breakdown

### Task 1: Initialize Project & Setup Dependencies
- **Agent:** `backend-specialist`
- **Skills:** `python-flask-development`
- **INPUT:** Project requirements
- **OUTPUT:** `requirements.txt` and basic `app.py` structure.
- **VERIFY:** Running `flask run` starts the server without errors.

### Task 2: Implement YouTube to MP3 Backend Logic
- **Agent:** `backend-specialist`
- **Skills:** `python-patterns`
- **INPUT:** YouTube URL
- **OUTPUT:** Python service utility function using `yt-dlp` to download and convert the video to an `.mp3` file inside the `downloads/` directory.
- **VERIFY:** Calling the service via a temporary Python script successfully saves an MP3 file to disk.

### Task 3: Develop Modern Frontend UI
- **Agent:** `frontend-specialist`
- **Skills:** `frontend-design`, `ui-ux-pro-max`
- **INPUT:** UI requirements
- **OUTPUT:** `templates/index.html` and `static/style.css` featuring a sleek, responsive input form with premium design aesthetics.
- **VERIFY:** The webpage renders correctly in the browser without UI bugs.

### Task 4: Integration & Client-Side Interactivity
- **Agent:** `backend-specialist` (working with `frontend-specialist`)
- **Skills:** `python-flask-development`
- **INPUT:** `app.py` logic and frontend templates
- **OUTPUT:** Complete POST route in `app.py` that handles the form submission, calls the `yt-dlp` service, and returns the MP3 using `send_file`. `script.js` added to handle loading spinners on the frontend while extraction occurs.
- **VERIFY:** Submitting a YouTube URL via the web UI successfully downloads the MP3 file to the user's machine.

### Task 5: Safety, Error Handling & Cleanup
- **Agent:** `backend-specialist`
- **Skills:** `backend-dev-guidelines`
- **INPUT:** Complete conversion flow
- **OUTPUT:** Robust error catching (e.g., invalid URLs, age-restricted videos) handled gracefully and relayed to the UI. Implement an `after_this_request` hook or background task to delete the MP3 file from the server once sent.
- **VERIFY:** Invalid URLs show user-friendly error messages on the UI. Downloaded files are verified to be removed from the server filesystem.

## Phase X: Verification
- [ ] **Lint & Type Check:** Python code follows PEP 8 standards. 
- [ ] **Security:** User input (URL) is strictly validated to prevent command injection. 
- [ ] **System Dependencies:** Ensure `ffmpeg` requirement is clearly documented and checked at runtime.
- [ ] **Build/Run:** End-to-end functionality starts seamlessly on local machine.
- [ ] **UX/UI Check:** Visual aesthetics meet the premium, high-quality standard (No generic styles).
