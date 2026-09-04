document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('convert-form');
    const urlInput = document.getElementById('youtube-url');
    const statusMessage = document.getElementById('status-message');
    const submitBtn = document.getElementById('submit-btn');
    const playlistPanel = document.getElementById('playlist-panel');
    const playlistTitle = document.getElementById('playlist-title');
    const playlistCount = document.getElementById('playlist-count');
    const playlistTracks = document.getElementById('playlist-tracks');

    let currentPlaylistData = null;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const url = urlInput.value.trim();

        if (!url) {
            showStatus('Please enter a valid YouTube URL', 'error');
            return;
        }

        // Basic URL validation
        try {
            const parsedUrl = new URL(url);
            if (!parsedUrl.hostname.includes('youtube.com') && !parsedUrl.hostname.includes('youtu.be')) {
                showStatus('This does not look like a valid YouTube link', 'error');
                return;
            }
        } catch (_) {
            showStatus('Invalid URL format', 'error');
            return;
        }

        // Hide any previous playlist panel
        hidePlaylistPanel();
        hideStatus();
        setLoading(true);

        // ── Stage 1: Scan the URL ──────────────────────────────────────
        try {
            const infoResponse = await fetch('/playlist/info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });

            if (!infoResponse.ok) {
                let errMsg = 'Failed to scan URL.';
                try {
                    const errData = await infoResponse.json();
                    errMsg = errData.error || errMsg;
                } catch {}
                throw new Error(errMsg);
            }

            const info = await infoResponse.json();

            if (info.type === 'playlist') {
                showPlaylistPreview(info, url);
                setLoading(false);
                return;
            }

            // Single video — proceed directly to download
            await downloadSingle(url, submitBtn, () => setLoading(true), () => setLoading(false));

        } catch (error) {
            showStatus(error.message, 'error');
        } finally {
            setLoading(false);
        }
    });

    // ── Single video download ──────────────────────────────────────────
    async function downloadSingle(url, loadingBtn, setBtnLoading, clearBtnLoading) {
        hideStatus();
        setBtnLoading();

        try {
            const response = await fetch('/convert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });

            if (!response.ok) {
                let errMsg = 'Failed to convert video.';
                try {
                    const errorData = await response.json();
                    errMsg = errorData.error || errMsg;
                } catch {}
                throw new Error(errMsg);
            }

            await triggerDownload(response);
            showStatus('Download complete!', 'success');
            urlInput.value = '';

            return true;
        } catch (error) {
            showStatus(error.message, 'error');
            return false;
        } finally {
            clearBtnLoading();
        }
    }

    // ── Playlist preview ───────────────────────────────────────────────
    function showPlaylistPreview(info, url) {
        currentPlaylistData = { ...info, url };
        playlistTitle.textContent = info.title || 'YouTube Playlist';
        playlistCount.textContent = `${info.track_count} track${info.track_count !== 1 ? 's' : ''}`;

        playlistTracks.innerHTML = '';
        info.tracks.forEach((track, index) => {
            const li = document.createElement('li');
            li.className = 'track-item';
            li.id = `track-${track.video_id}`;
            li.innerHTML = `
                <div class="track-info">
                    <span class="track-index">${index + 1}</span>
                    <span class="track-title" title="${escapeHtml(track.title)}">${escapeHtml(track.title)}</span>
                </div>
                <button class="track-dl-btn" data-video-id="${track.video_id}" title="Download MP3">
                    <i class="ph-bold ph-download-simple"></i>
                    <i class="ph-bold ph-spinner-gap spinner track-spinner"></i>
                </button>
            `;
            playlistTracks.appendChild(li);
        });

        playlistPanel.classList.remove('hidden');
    }

    function hidePlaylistPanel() {
        playlistPanel.classList.add('hidden');
        currentPlaylistData = null;
    }

    // ── Per-track download (event delegation) ──────────────────────────
    playlistTracks.addEventListener('click', async (e) => {
        const btn = e.target.closest('.track-dl-btn');
        if (!btn) return;

        const videoId = btn.dataset.videoId;
        if (!videoId) return;

        const li = document.getElementById(`track-${videoId}`);
        const videoUrl = `https://www.youtube.com/watch?v=${videoId}`;

        // Set loading on just this button
        btn.classList.add('is-loading');
        btn.disabled = true;
        hideStatus();

        try {
            const response = await fetch('/convert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: videoUrl })
            });

            if (!response.ok) {
                let errMsg = 'Failed to convert video.';
                try {
                    const errorData = await response.json();
                    errMsg = errorData.error || errMsg;
                } catch {}
                throw new Error(errMsg);
            }

            await triggerDownload(response);

            // Mark as downloaded
            btn.classList.remove('is-loading');
            btn.classList.add('is-downloaded');
            btn.disabled = false;
            btn.title = 'Downloaded — click to download again';
            btn.querySelector('.ph-download-simple').className = 'ph-bold ph-check-circle';

        } catch (error) {
            btn.classList.remove('is-loading');
            btn.disabled = false;
            showStatus(error.message, 'error');
        }
    });

    // ── Utility: trigger file download from response ──────────────────
    function triggerDownload(response) {
        let filename = 'audio.mp3';
        const disposition = response.headers.get('Content-Disposition');
        if (disposition && disposition.indexOf('attachment') !== -1) {
            const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
            const matches = filenameRegex.exec(disposition);
            if (matches != null && matches[1]) {
                filename = matches[1].replace(/['"]/g, '');
            }
        }

        return response.blob().then(blob => {
            const downloadUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = downloadUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(downloadUrl);
            document.body.removeChild(a);
        });
    }

    // ── Utility: escape HTML ──────────────────────────────────────────
    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── UI state helpers ──────────────────────────────────────────────
    function setLoading(isLoading) {
        if (isLoading) {
            form.classList.add('is-loading');
            urlInput.disabled = true;
            submitBtn.disabled = true;
        } else {
            form.classList.remove('is-loading');
            urlInput.disabled = false;
            submitBtn.disabled = false;
        }
    }

    function showStatus(message, type) {
        statusMessage.className = 'status-message';
        statusMessage.classList.add(`status-${type}`);

        const iconPrefix = type === 'success' ? 'ph-check-circle' : 'ph-warning-circle';

        statusMessage.innerHTML = `<i class="ph-bold ${iconPrefix}"></i> <span>${message}</span>`;
    }

    function hideStatus() {
        statusMessage.className = 'status-message hidden';
        statusMessage.innerHTML = '';
    }
});