import os
import sys
import subprocess
import uuid
import asyncio
import yt_dlp
import httpx
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict

app = FastAPI()

# Pure ASGI Middleware to strip Web Station Alias prefix (/cut) before routing
class StripAliasMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/cut"):
                scope["path"] = path[4:] or "/"
                if "raw_path" in scope:
                    try:
                        raw_path_str = scope["raw_path"].decode("utf-8")
                        if raw_path_str.startswith("/cut"):
                            scope["raw_path"] = raw_path_str[4:].encode("utf-8") or b"/"
                    except Exception:
                        pass
        await self.app(scope, receive, send)

app.add_middleware(StripAliasMiddleware)

# Allow CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "../downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# YouTube periodically returns transient "The page needs to be reloaded"
# responses.  Let yt-dlp retry extraction and fragments, and allow it to
# choose the clients that are valid for the current YouTube session instead
# of forcing the old tv/android combination.
YOUTUBE_YDL_OPTIONS = {
    "noplaylist": True,
    "retries": 3,
    "fragment_retries": 3,
    "extractor_retries": 3,
    "socket_timeout": 30,
    "concurrent_fragment_downloads": 4,
    "extractor_args": {"youtube": {"player_client": ["android"]}},
}


def make_youtube_ydl_options():
    options = dict(YOUTUBE_YDL_OPTIONS)
    if os.path.exists("cookies.txt"):
        options["cookiefile"] = "cookies.txt"
    return options


def is_youtube_url(url: str) -> bool:
    lowered = url.lower()
    return "youtube.com" in lowered or "youtu.be" in lowered

def is_threads_url(url: str) -> bool:
    lowered = url.lower()
    return "threads.net" in lowered or "threads.com" in lowered

class ClipRequest(BaseModel):
    url: str
    segments: List[Dict[str, str]] = []
    quality: str = "best"
    title: str = "video"
    crop_vertical: bool = False

# ?典?隞餃??脣??
tasks = {}

def run_clip_task(task_id: str, base_command: list, filepath: str, out_filename: str, url: str, segments: list, crop_vertical: bool = False):
    try:
        import subprocess
        out_log_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_stdout.log")
        err_log_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_stderr.log")
        
        valid_segments = []
        for seg in segments:
            st = seg.get("start_time", "").strip()
            et = seg.get("end_time", "").strip()
            if st or et:
                valid_segments.append((st if st else "0", et))
                
        if len(valid_segments) <= 1:
            command = list(base_command)
            if valid_segments:
                command.append("--force-keyframes-at-cuts")
                st, et = valid_segments[0]
                if et:
                    command.extend(["--download-sections", f"*{st}-{et}"])
                else:
                    command.extend(["--download-sections", f"*{st}-"])
            command.extend(["-o", filepath, url])
            
            with open(out_log_path, "w", encoding="utf-8") as out_f, open(err_log_path, "w", encoding="utf-8") as err_f:
                process = subprocess.run(command, stdout=out_f, stderr=err_f)
            
            if process.returncode != 0:
                raise Exception("yt-dlp failed (single clip)")
            
            if not os.path.exists(filepath):
                if os.path.exists(filepath + ".part"):
                    os.replace(filepath + ".part", filepath)
                else:
                    raise Exception("Video file was not generated.")
        else:
            part_files = []
            ext = "mp3" if out_filename.endswith(".mp3") else "mp4"
            for i, (st, et) in enumerate(valid_segments):
                part_filepath = os.path.join(DOWNLOAD_DIR, f"{task_id}_part{i}.{ext}")
                command = list(base_command)
                command.append("--force-keyframes-at-cuts")
                if et:
                    command.extend(["--download-sections", f"*{st}-{et}"])
                else:
                    command.extend(["--download-sections", f"*{st}-"])
                command.extend(["-o", part_filepath, url])
                
                with open(out_log_path, "a", encoding="utf-8") as out_f, open(err_log_path, "a", encoding="utf-8") as err_f:
                    process = subprocess.run(command, stdout=out_f, stderr=err_f)
                if process.returncode != 0:
                    raise Exception(f"yt-dlp failed on part {i}")
                    
                if not os.path.exists(part_filepath):
                    if os.path.exists(part_filepath + ".part"):
                        os.replace(part_filepath + ".part", part_filepath)
                part_files.append(part_filepath)
                
            concat_txt_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_concat.txt")
            with open(concat_txt_path, "w", encoding="utf-8") as f:
                for part in part_files:
                    f.write(f"file '{os.path.basename(part)}'\n")
            
            ffmpeg_command = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
                "-i", concat_txt_path, "-c", "copy"
            ]
            if not out_filename.endswith(".mp3"):
                ffmpeg_command.extend(["-movflags", "+faststart"])
            ffmpeg_command.append(filepath)
            with open(out_log_path, "a", encoding="utf-8") as out_f, open(err_log_path, "a", encoding="utf-8") as err_f:
                process = subprocess.run(ffmpeg_command, stdout=out_f, stderr=err_f)
                
            if process.returncode != 0:
                raise Exception("ffmpeg concat failed")
                
            try:
                os.remove(concat_txt_path)
                for part in part_files:
                    if os.path.exists(part):
                        os.remove(part)
            except:
                pass
                
        if crop_vertical and filepath.endswith(".mp4") and os.path.exists(filepath):
            cropped_filepath = filepath.replace(".mp4", "_cropped.mp4")
            crop_cmd = [
                "ffmpeg", "-y", "-i", filepath,
                "-vf", "crop=ih*4/3:ih",
                "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                cropped_filepath
            ]
            with open(out_log_path, "a", encoding="utf-8") as out_f, open(err_log_path, "a", encoding="utf-8") as err_f:
                process = subprocess.run(crop_cmd, stdout=out_f, stderr=err_f)
            if process.returncode == 0 and os.path.exists(cropped_filepath):
                os.replace(cropped_filepath, filepath)

        # Clean up logs on success
        try:
            if os.path.exists(out_log_path):
                os.remove(out_log_path)
            if os.path.exists(err_log_path):
                os.remove(err_log_path)
        except:
            pass
                
        tasks[task_id] = {
            "status": "completed",
            "filepath": filepath,
            "out_filename": out_filename
        }
    except Exception as e:
        tasks[task_id] = {
            "status": "failed",
            "error": str(e)
        }
        with open("error.log", "a", encoding="utf-8") as f_log:
            f_log.write(f"=== SYSTEM ERROR ===\nTask: {task_id}\nURL: {url}\nError: {str(e)}\n\n")

@app.post("/api/clip")
def create_clip(req: ClipRequest, background_tasks: BackgroundTasks):
    if not req.url:
        raise HTTPException(status_code=400, detail="Missing required URL")

    task_id = uuid.uuid4().hex
    ext = "mp3" if req.quality == "mp3" else "mp4"
    filename = f"{task_id}.{ext}"
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    base_command = [
        sys.executable, "-m", "yt_dlp",
        "--verbose",
        "--js-runtimes", "deno",
        "--force-ipv4",
        "--no-playlist",
        "--retries", "3",
        "--fragment-retries", "3",
        "--extractor-retries", "3",
        "--socket-timeout", "30",
        "--extractor-args", "youtube:player_client=android"
    ]
    if os.path.exists("cookies.txt"):
        base_command.extend(["--cookies", "cookies.txt"])
        
    has_end_t = False
    if req.segments:
        for seg in req.segments:
            if seg.get("end_time", "").strip():
                has_end_t = True
                
    if has_end_t:
        base_command.extend([
            "--downloader-args", "ffmpeg_i:-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        ])
        
    if req.quality == "mp3":
        base_command.extend([
            "-N", "16",
            "-x", "--audio-format", "mp3",
            "-f", "ba/bestaudio/best"
        ])
    else:
        format_str = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best"
        if req.quality in ["1080", "720", "480"]:
            format_str = f"bv*[height<={req.quality}][ext=mp4]+ba[ext=m4a]/b[height<={req.quality}][ext=mp4]/best"
        base_command.extend([
            "-N", "16",
            "-f", format_str
        ])
    
    safe_title = "".join(c if c.isalnum() or c in " -_[]()" else "_" for c in req.title).strip()
    if not safe_title:
        safe_title = "video"
        
    use_clipping = False
    if req.segments:
        for seg in req.segments:
            if seg.get("start_time", "").strip() or seg.get("end_time", "").strip():
                use_clipping = True

    out_filename = f"{safe_title}.{ext}"
    if use_clipping:
        if len(req.segments) > 1:
            out_filename = f"{safe_title}_multi_clips.{ext}"
        elif len(req.segments) == 1:
            st = req.segments[0].get("start_time", "").strip()
            et = req.segments[0].get("end_time", "").strip()
            start_label = st.replace(":", "-") if st else "start"
            end_label = et.replace(":", "-") if et else "end"
            out_filename = f"{safe_title}_{start_label}_{end_label}.{ext}"

    tasks[task_id] = {"status": "processing", "error": None}
    
    background_tasks.add_task(
        run_clip_task, 
        task_id, base_command, filepath, out_filename, 
        req.url, req.segments, req.crop_vertical
    )
    
    return {"task_id": task_id, "status": "processing"}

@app.get("/api/status/{task_id}")
def get_task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.get("/api/download/{task_id}")
def download_task_file(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="Task is not completed yet")
        
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Video file does not exist on disk")
        
    return FileResponse(
        path=filepath,
        filename=task["out_filename"],
        media_type="audio/mpeg" if task["out_filename"].endswith(".mp3") else "video/mp4"
    )

@app.get("/api/logs")
async def get_logs():
    if os.path.exists("error.log"):
        with open("error.log", "r", encoding="utf-8") as f:
            return {"logs": f.read()}
    return {"logs": "No logs recorded yet."}

@app.get("/api/info")
async def get_info(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")
        
    ydl_opts = make_youtube_ydl_options()
    ydl_opts.update({'quiet': True, 'no_warnings': True})

    try:
        info = None
        last_error = None
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                break
            except Exception as e:
                last_error = e
                if attempt < 2 and is_youtube_url(url):
                    await asyncio.sleep(1 + attempt)
        if info is None:
            raise last_error

        extractor = info.get('extractor', '').lower()
        title = info.get('title', 'Unknown Title')
        duration = info.get('duration', 0)
        stream_url = None
        audio_url = None
        platform = "unknown"
        bvid = None

        if 'youtube' in extractor:
            platform = "youtube"
            formats = info.get('formats', [])
            # Prefer a progressive MP4 for browser preview.  YouTube often
            # exposes video/audio as separate DASH streams, which cannot be
            # played by the single native <video> element used by the UI.
            progressive = [
                f for f in formats
                if f.get('ext') == 'mp4'
                and f.get('vcodec') not in (None, 'none')
                and f.get('acodec') not in (None, 'none')
                and f.get('url')
            ]
            if progressive:
                stream_url = max(progressive, key=lambda f: f.get('height') or 0).get('url')
            else:
                stream_url = info.get('url')
        elif 'bilibili' in extractor:
            platform = "bilibili"
            bvid = info.get('id')
            formats = info.get('formats', [])
            for f in formats:
                vcodec = f.get('vcodec', '')
                acodec = f.get('acodec', '')
                if vcodec and vcodec != 'none' and (not acodec or acodec == 'none'):
                    if 'avc' in vcodec.lower() or 'h264' in vcodec.lower():
                        stream_url = f.get('url')
                        break
            if not stream_url:
                for f in formats:
                    if f.get('vcodec') not in (None, 'none') and f.get('acodec') in (None, 'none'):
                        stream_url = f.get('url')
                        break
            for f in formats:
                if f.get('vcodec') in (None, 'none') and f.get('acodec') not in (None, 'none'):
                    audio_url = f.get('url')
                    break
        elif 'facebook' in extractor:
            platform = "facebook"
            stream_url = info.get('url')
        elif 'threads' in extractor or 'instagram' in extractor:
            platform = "threads" if is_threads_url(url) else "instagram"
            stream_url = info.get('url')
        else:
            platform = extractor if extractor else "generic"
            stream_url = info.get('url')

        return {
            "title": title,
            "duration": duration,
            "stream_url": stream_url,
            "audio_url": audio_url,
            "platform": platform,
            "bvid": bvid
        }
    except Exception as e:
        if "facebook.com" in url or "fb.watch" in url:
            return {
                "title": "Facebook Video",
                "duration": 0,
                "stream_url": None,
                "audio_url": None,
                "platform": "facebook",
                "bvid": None
            }
        if is_threads_url(url):
            return {
                "title": "Threads Video",
                "duration": 0,
                "stream_url": None,
                "audio_url": None,
                "platform": "threads",
                "bvid": None
            }
        if "instagram.com" in url:
            return {
                "title": "Instagram Video",
                "duration": 0,
                "stream_url": None,
                "audio_url": None,
                "platform": "instagram",
                "bvid": None
            }
        print("Error extracting info:", str(e))
        if is_youtube_url(url):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "youtube_extraction_failed",
                    "message": f"YouTube Extraction Failed: {str(e)}",
                },
            )
        raise HTTPException(
            status_code=502,
            detail={"code": "media_extraction_failed", "message": str(e)},
        )


@app.get("/api/health")
async def health_check():
    """Lightweight check used to distinguish NAS/API connectivity failures."""
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        probe = os.path.join(DOWNLOAD_DIR, ".healthcheck")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return {"status": "ok", "storage": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "nas_storage_unavailable", "message": str(e)},
        )

@app.get("/api/debug")
async def debug_network():
    import socket
    import urllib.request
    import shutil
    
    results = {}
    
    # 1. Test ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
            results["ffmpeg"] = f"Success (Path: {ffmpeg_path}, version: {r.stdout.splitlines()[0] if r.stdout else 'unknown'})"
        except Exception as e:
            results["ffmpeg"] = f"Failed to run: {str(e)}"
    else:
        results["ffmpeg"] = "Failed: ffmpeg binary not found in PATH!"

    # 1.5. Test node (JavaScript runtime for yt-dlp signature extraction)
    node_path = shutil.which("node")
    if node_path:
        try:
            r = subprocess.run(["node", "-v"], capture_output=True, text=True)
            results["node"] = f"Success (Path: {node_path}, version: {r.stdout.strip() if r.stdout else 'unknown'})"
        except Exception as e:
            results["node"] = f"Failed to run: {str(e)}"
    else:
        results["node"] = "Failed: node binary not found in PATH!"

    # 1.6. Test deno (Default JavaScript runtime for yt-dlp signature extraction)
    deno_path = shutil.which("deno")
    if deno_path:
        try:
            r = subprocess.run(["deno", "--version"], capture_output=True, text=True)
            results["deno"] = f"Success (Path: {deno_path}, version: {r.stdout.splitlines()[0] if r.stdout else 'unknown'})"
        except Exception as e:
            results["deno"] = f"Failed to run: {str(e)}"
    else:
        results["deno"] = "Failed: deno binary not found in PATH!"

    # 2. Test DNS
    try:
        r_ytdlp = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, text=True)
        results["yt_dlp_version"] = r_ytdlp.stdout.strip() if r_ytdlp.returncode == 0 else f"Failed to get: {r_ytdlp.stderr}"
    except Exception as e:
        results["yt_dlp_version"] = f"Failed to run: {str(e)}"

    # Check if yt-dlp-ejs is installed and loadable in Python
    try:
        import importlib.util
        results["yt_dlp_ejs_installed"] = importlib.util.find_spec("yt_dlp_ejs") is not None
    except Exception as e:
        results["yt_dlp_ejs_installed"] = f"Error checking: {str(e)}"

    import os
    results["container_PATH"] = os.environ.get("PATH", "")

    try:
        ip = socket.gethostbyname("google.com")
        results["dns_google"] = f"Success (IP: {ip})"
    except Exception as e:
        results["dns_google"] = f"Failed: {str(e)}"
        
    try:
        ip = socket.gethostbyname("youtube.com")
        results["dns_youtube"] = f"Success (IP: {ip})"
    except Exception as e:
        results["dns_youtube"] = f"Failed: {str(e)}"

    # 3. Test HTTP Connection
    try:
        with urllib.request.urlopen("https://www.google.com", timeout=3) as r:
            results["http_google"] = f"Success (Status: {r.status})"
    except Exception as e:
        results["http_google"] = f"Failed: {str(e)}"
        
    try:
        with urllib.request.urlopen("https://www.youtube.com", timeout=3) as r:
            results["http_youtube"] = f"Success (Status: {r.status})"
    except Exception as e:
        results["http_youtube"] = f"Failed: {str(e)}"

    # 4. Read Dockerfile inside container to verify version
    try:
        if os.path.exists("/app/Dockerfile"):
            with open("/app/Dockerfile", "r", encoding="utf-8") as f:
                results["dockerfile_content"] = f.read()
        else:
            results["dockerfile_content"] = "Dockerfile not found at /app/Dockerfile"
    except Exception as e:
        results["dockerfile_content"] = f"Failed to read: {str(e)}"

    # 4.5. Test write permission to downloads directory
    try:
        test_write_path = os.path.join(DOWNLOAD_DIR, "test_write.txt")
        with open(test_write_path, "w", encoding="utf-8") as f:
            f.write("write_success")
        if os.path.exists(test_write_path):
            os.remove(test_write_path)
            results["write_permission"] = "Success (Writable)"
        else:
            results["write_permission"] = "Failed (File not created)"
    except Exception as e:
        results["write_permission"] = f"Failed (Error: {str(e)})"

    # 5. Run a 1-second test clip download to verify yt-dlp and ffmpeg end-to-end
    try:
        test_url = "https://www.youtube.com/watch?v=HfCoNu4CNFI"
        test_filepath = os.path.join(DOWNLOAD_DIR, "test_clip_debug.mp4")
        if os.path.exists(test_filepath):
            os.remove(test_filepath)
            
        test_command = [
            sys.executable, "-m", "yt_dlp",
            "--verbose",
            "--js-runtimes", "deno",
            "--force-ipv4",
            "--download-sections", "*00:00:01-00:00:02",
            "--force-keyframes-at-cuts",
            "--downloader-args", "ffmpeg_i:-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
            "-o", test_filepath,
            test_url
        ]
        
        test_out = "test_ytdlp_stdout.log"
        test_err = "test_ytdlp_stderr.log"
        
        with open(test_out, "w", encoding="utf-8") as out_f, open(test_err, "w", encoding="utf-8") as err_f:
            process = subprocess.run(test_command, stdout=out_f, stderr=err_f, timeout=60)
            
        err_content = ""
        if os.path.exists(test_err):
            with open(test_err, "r", encoding="utf-8") as err_f:
                err_content = err_f.read()
                
        out_content = ""
        if os.path.exists(test_out):
            with open(test_out, "r", encoding="utf-8") as out_f:
                out_content = out_f.read()
                
        results["test_clip_run"] = {
            "returncode": process.returncode,
            "stdout": out_content,
            "stderr": err_content,
            "file_created": os.path.exists(test_filepath)
        }
        
        # Cleanup
        if os.path.exists(test_filepath):
            os.remove(test_filepath)
    except Exception as e:
        results["test_clip_run"] = f"Test run crashed: {str(e)}"

    return results

# Media stream proxy to bypass YouTube's IP lock when deployed on NAS
@app.get("/api/stream")
async def stream_proxy(url: str, request: Request, type: str = None):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # Bilibili stream requires Referer header, otherwise returns 403 Forbidden
    if "bilibili" in url or "bilivideo" in url:
        headers["Referer"] = "https://www.bilibili.com"

    # Facebook's signed CDN URLs are commonly rejected without the same
    # navigation context used to obtain them.
    lowered_url = url.lower()
    if "facebook.com" in lowered_url or "fbcdn.net" in lowered_url:
        headers["Referer"] = "https://www.facebook.com/"
        headers["Origin"] = "https://www.facebook.com"

    # Pass the browser's range header (essential for seeking!)
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    try:
        client = httpx.AsyncClient(follow_redirects=True)
        # Send streaming request to GoogleVideo servers
        req = client.build_request("GET", url, headers=headers)
        r = await client.send(req, stream=True)

        # Do not send an HTML error page with a video content type.  Returning
        # the upstream status lets the browser trigger the frontend fallback.
        if r.status_code >= 400:
            detail = (await r.aread())[:300].decode("utf-8", errors="replace")
            await r.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=r.status_code,
                detail=f"Upstream video server returned HTTP {r.status_code}: {detail}",
            )

        # Forward important response headers
        headers_to_forward = ["content-type", "content-length", "content-range", "accept-ranges"]
        response_headers = {k: v for k, v in r.headers.items() if k.lower() in headers_to_forward}
        
        # Force correct Content-Type for Bilibili .m4s streams so the browser's video tag can play it
        if type == "video":
            response_headers["content-type"] = "video/mp4"
        elif type == "audio":
            response_headers["content-type"] = "audio/mp4"
            
        response_headers["Access-Control-Allow-Origin"] = "*"

        # Cleanup client connection when streaming finishes
        async def stream_generator():
            try:
                async for chunk in r.aiter_bytes(chunk_size=1024*128):
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            status_code=r.status_code,
            headers=response_headers
        )
    except Exception as e:
        print("Proxy error:", str(e))
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

# Mount frontend files at the root (placed after API routes to avoid overriding them)
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")
else:
    print(f"Warning: Frontend directory not found at {frontend_path}")
