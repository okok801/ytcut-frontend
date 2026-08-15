document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('clip-form');
    const urlInput = document.getElementById('url');
    const videoPreview = document.getElementById('video-preview');
    const previewInfo = document.getElementById('preview-info');
    const videoTitleSpan = previewInfo.querySelector('.video-title');
    const previewControls = document.getElementById('preview-controls');
    const setStartBtn = document.getElementById('set-start-btn');
    const setEndBtn = document.getElementById('set-end-btn');
    const segmentsContainer = document.getElementById('segments-container');
    const addSegmentBtn = document.getElementById('add-segment-btn');
    const submitBtn = document.getElementById('submit-btn');
    const btnText = submitBtn.querySelector('.btn-text');
    const loader = submitBtn.querySelector('.loader');
    const statusMessage = document.getElementById('status-message');

    // 固定為您的 NAS 公網 API 網址
    const API_BASE = 'https://okok802.synology.me/cut/';

    let currentVideoElement = null;
    let urlTimeout = null;

    // Helper to format seconds to HH:MM:SS
    function formatTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return [h, m, s].map(v => v.toString().padStart(2, '0')).join(':');
    }

    // Handle URL input for preview
    urlInput.addEventListener('input', () => {
        clearTimeout(urlTimeout);
        const url = urlInput.value.trim();

        if (!url) {
            resetPreview();
            return;
        }

        // Debounce API requests so we don't spam requests while typing
        urlTimeout = setTimeout(async () => {
            showStatus('正在解析影片資訊...', 'info');
            resetPreview();

            try {
                const response = await fetch(`${API_BASE}api/info?url=${encodeURIComponent(url)}`);
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || '無法解析此影片連結');
                }

                const data = await response.json();
                
                // Show title
                videoTitleSpan.textContent = data.title;
                previewInfo.classList.remove('hidden');

                if (data.stream_url) {
                    // Render native video tag via local proxy (allows seeking & time capture)
                    const typeParam = data.platform === 'bilibili' ? '&type=video' : '';
                    const proxiedUrl = `${API_BASE}api/stream?url=${encodeURIComponent(data.stream_url)}${typeParam}`;
                    let htmlContent = `
                        <video id="native-player" controls preload="metadata">
                            <source src="${proxiedUrl}" type="video/mp4">
                            您的瀏覽器不支援 HTML5 影片播放。
                        </video>
                    `;
                    
                    // If Bilibili has an audio track, render a hidden audio element
                    if (data.platform === 'bilibili' && data.audio_url) {
                        const proxiedAudioUrl = `${API_BASE}api/stream?url=${encodeURIComponent(data.audio_url)}&type=audio`;
                        htmlContent += `
                            <audio id="sync-audio" preload="metadata" style="display: none;">
                                <source src="${proxiedAudioUrl}" type="audio/mp4">
                            </audio>
                        `;
                    }
                    
                    videoPreview.innerHTML = htmlContent;
                    videoPreview.classList.remove('hidden');
                    previewControls.classList.remove('hidden');
                    
                    const videoEl = document.getElementById('native-player');
                    currentVideoElement = videoEl;

                    if (data.platform === 'bilibili' && data.audio_url) {
                        const audioEl = document.getElementById('sync-audio');
                        
                        // Sync Video & Audio controls
                        videoEl.addEventListener('play', () => audioEl.play());
                        videoEl.addEventListener('pause', () => audioEl.pause());
                        videoEl.addEventListener('seeking', () => {
                            audioEl.currentTime = videoEl.currentTime;
                        });
                        videoEl.addEventListener('seeked', () => {
                            audioEl.currentTime = videoEl.currentTime;
                        });
                        videoEl.addEventListener('ratechange', () => {
                            audioEl.playbackRate = videoEl.playbackRate;
                        });
                        
                        // Periodic sync check to prevent drifting (max 0.3s difference)
                        videoEl.addEventListener('timeupdate', () => {
                            if (Math.abs(audioEl.currentTime - videoEl.currentTime) > 0.3) {
                                audioEl.currentTime = videoEl.currentTime;
                            }
                        });
                        
                        showStatus('解析成功！已載入 Bilibili 影音同步播放器（支援一鍵設定時間）。', 'success');
                    } else if (data.platform === 'bilibili') {
                        showStatus('解析成功！已載入 Bilibili 預覽播放器。（無聲版，支援一鍵設定時間）', 'success');
                    } else {
                        showStatus('解析成功！已載入影片播放器。', 'success');
                    }
                } 
                else if (data.platform === 'bilibili' && data.bvid) {
                    // Fallback Bilibili: Render mobile-friendly HTML5 player iframe
                    videoPreview.innerHTML = `
                        <iframe src="https://www.bilibili.com/blackboard/html5mobileplayer.html?bvid=${data.bvid}&danmaku=0&autoplay=0" scrolling="no" border="0" frameborder="no" framespacing="0" allowfullscreen="true"></iframe>
                    `;
                    videoPreview.classList.remove('hidden');
                    previewControls.classList.add('hidden');
                    currentVideoElement = null;
                    showStatus('已載入 Bilibili 備用預覽播放器。（請手動填寫時間）', 'success');
                }
                else {
                    throw new Error('未支援的平台或解析失敗');
                }

            } catch (error) {
                console.error(error);
                showStatus(`預覽載入失敗: ${error.message}，請確認後端 NAS 連線狀態。`, 'error');
                resetPreview();
            }
        }, 1000);
    });

    // Control buttons to set start/end times from native player
    setStartBtn.addEventListener('click', () => {
        if (currentVideoElement) {
            const timeStr = formatTime(currentVideoElement.currentTime);
            const rows = segmentsContainer.querySelectorAll('.segment-row');
            if (rows.length > 0) {
                rows[rows.length - 1].querySelector('.start-time').value = timeStr;
            }
        }
    });

    setEndBtn.addEventListener('click', () => {
        if (currentVideoElement) {
            const timeStr = formatTime(currentVideoElement.currentTime);
            const rows = segmentsContainer.querySelectorAll('.segment-row');
            if (rows.length > 0) {
                rows[rows.length - 1].querySelector('.end-time').value = timeStr;
            }
        }
    });

    function createSegmentRow() {
        const row = document.createElement('div');
        row.className = 'time-inputs segment-row';
        row.style.alignItems = 'center';
        row.style.marginBottom = '0.5rem';
        row.innerHTML = `
            <div class="input-group" style="margin-bottom: 0;">
                <label>開始時間</label>
                <input type="text" class="start-time" placeholder="00:00:00">
            </div>
            <div class="input-group" style="margin-bottom: 0;">
                <label>結束時間</label>
                <input type="text" class="end-time" placeholder="到結尾">
            </div>
            <button type="button" class="remove-segment-btn" style="background: none; border: none; color: #ff6b6b; font-size: 1.5rem; cursor: pointer; padding: 0.5rem; width: auto; margin-top: 1.2rem;">✖</button>
        `;
        
        row.querySelector('.remove-segment-btn').addEventListener('click', () => {
            row.remove();
            updateRemoveButtons();
        });
        segmentsContainer.appendChild(row);
        updateRemoveButtons();
    }

    // Handle adding segments
    addSegmentBtn.addEventListener('click', () => {
        createSegmentRow();
    });
    function updateRemoveButtons() {
        const rows = segmentsContainer.querySelectorAll('.segment-row');
        rows.forEach((row, index) => {
            const btn = row.querySelector('.remove-segment-btn');
            if (rows.length === 1) {
                btn.classList.add('hidden');
            } else {
                btn.classList.remove('hidden');
            }
        });
    }

    updateRemoveButtons();

    function resetPreview() {
        videoPreview.innerHTML = '';
        videoPreview.classList.add('hidden');
        previewInfo.classList.add('hidden');
        videoTitleSpan.textContent = '';
        previewControls.classList.add('hidden');
        currentVideoElement = null;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const url = urlInput.value;
        const quality = document.getElementById('quality') ? document.getElementById('quality').value : 'best';
        const videoTitle = videoTitleSpan.textContent.trim() || 'video';

        // Gather all segments
        const segments = [];
        const rows = segmentsContainer.querySelectorAll('.segment-row');
        rows.forEach(row => {
            const st = row.querySelector('.start-time').value.trim();
            const et = row.querySelector('.end-time').value.trim();
            if (st || et) {
                segments.push({ start_time: st, end_time: et });
            }
        });
        
        // If empty, default to full video download
        if (segments.length === 0) {
            segments.push({ start_time: "", end_time: "" });
        }

        // Reset UI
        statusMessage.className = 'hidden';
        submitBtn.disabled = true;
        btnText.classList.add('hidden');
        loader.classList.remove('hidden');

        showStatus('處理中，正在向伺服器發送請求...', 'info');

        try {
            const response = await fetch(`${API_BASE}api/clip`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    url: url,
                    segments: segments,
                    quality: quality,
                    title: videoTitle
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `請求失敗 (${response.status})`);
            }

            const initData = await response.json();
            const taskId = initData.task_id;
            
            showStatus('已成功排程！伺服器正在後台處理影片下載與剪輯，請耐心等候...', 'info');

            // 輪詢狀態
            const pollInterval = setInterval(async () => {
                try {
                    const statusRes = await fetch(`${API_BASE}api/status/${taskId}`);
                    if (!statusRes.ok) return;
                    
                    const task = await statusRes.json();
                    if (task.status === 'completed') {
                        clearInterval(pollInterval);
                        showStatus('剪輯完成！正在開始下載影片...', 'success');
                        
                        // 下載檔案
                        const fileRes = await fetch(`${API_BASE}api/download/${taskId}`);
                        const blob = await fileRes.blob();
                        const downloadUrl = window.URL.createObjectURL(blob);
                        
                        let safeTitle = videoTitle.replace(/[\\/:*?"<>|]/g, '_');
                        let filename = `${safeTitle}.mp4`;
                        if (segments.length > 1) {
                            filename = `${safeTitle}_multi_clips.mp4`;
                        } else if (segments.length === 1 && (segments[0].start_time || segments[0].end_time)) {
                            const startLabel = segments[0].start_time ? segments[0].start_time.replace(/:/g,'-') : 'start';
                            const endLabel = segments[0].end_time ? segments[0].end_time.replace(/:/g,'-') : 'end';
                            filename = `${safeTitle}_${startLabel}_${endLabel}.mp4`;
                        }
                        
                        const a = document.createElement('a');
                        a.style.display = 'none';
                        a.href = downloadUrl;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        
                        window.URL.revokeObjectURL(downloadUrl);
                        a.remove();
                        
                        // 恢復按鈕狀態
                        submitBtn.disabled = false;
                        btnText.classList.remove('hidden');
                        loader.classList.add('hidden');
                    } else if (task.status === 'failed') {
                        clearInterval(pollInterval);
                        showStatus(`擷取失敗：${task.error}`, 'error');
                        submitBtn.disabled = false;
                        btnText.classList.remove('hidden');
                        loader.classList.add('hidden');
                    }
                } catch (pollErr) {
                    console.error('Polling error:', pollErr);
                }
            }, 3000); // 每 3 秒輪詢一次

        } catch (error) {
            console.error('Error:', error);
            showStatus(`發生錯誤: ${error.message}`, 'error');
        } finally {
            submitBtn.disabled = false;
            btnText.classList.remove('hidden');
            loader.classList.add('hidden');
        }
    });

    function showStatus(message, type) {
        statusMessage.textContent = message;
        statusMessage.className = `status-${type}`;
        statusMessage.classList.remove('hidden');
    }
});
