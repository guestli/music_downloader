import os
import requests
from fastapi import FastAPI, BackgroundTasks, Query, Request
from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from bs4 import BeautifulSoup
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pathlib import Path
from fastapi import WebSocket
import uuid
import asyncio
import time

tasks = {}
from fastapi.staticfiles import StaticFiles
app = FastAPI()

MUSIC_DIR = os.getenv("MUSIC_DIR", "./music")
NAVIDROME_URL = os.getenv("NAVIDROME_URL")
NAVIDROME_USER = os.getenv("NAVIDROME_USER")
NAVIDROME_PASS = os.getenv("NAVIDROME_PASS")
os.makedirs(MUSIC_DIR, exist_ok=True)

# 支持平台
PLATFORMS = ["youtube", "bilibili", "soundcloud", "niconico", "vimeo", "mixcloud", "bandcamp"]

# 支持格式
FORMATS = ["mp3", "m4a", "opus", "wav", "flac", "aac"]

# 模板目录
CURRENT_DIR = Path(__file__).resolve().parent  # /app
TEMPLATES_DIR = Path("/app/templates")        # Docker里绝对路径
print("Templates path:", TEMPLATES_DIR)
print("Files:", list(TEMPLATES_DIR.glob("*")))

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    tasks[task_id] = {
        "ws": websocket,
        "loop": asyncio.get_running_loop()
    }
    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except:
        tasks.pop(task_id, None)

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "platforms": PLATFORMS,
        "formats": FORMATS
    })

def trigger_rescan():
    if NAVIDROME_URL:
        try:
            requests.post(
                f"{NAVIDROME_URL}/api/rescan",
                auth=(NAVIDROME_USER, NAVIDROME_PASS),
                timeout=10,
            )
        except Exception as e:
            print("[Navidrome rescan error]", e)


def fetch_lyrics_and_cover(yt_url):
    try:
        html = requests.get(yt_url, timeout=5).text
        soup = BeautifulSoup(html, "html.parser")
        cover_tag = soup.find("meta", {"property": "og:image"})
        cover_url = cover_tag['content'] if cover_tag else None
        desc_tag = soup.find("meta", {"name": "description"})
        lyrics = desc_tag['content'] if desc_tag else ""
        return cover_url, lyrics
    except Exception as e:
        print("[fetch_lyrics_and_cover error]", e)
        return None, ""

def download_and_tag(task_id: str, keyword: str, platform: str = "youtube", file_format: str = "mp3"):
    try:
        def send(msg: dict):
            info = tasks.get(task_id)
            if not info:
                return False
            try:
                asyncio.run_coroutine_threadsafe(info["ws"].send_json(msg), info["loop"])
                return True
            except Exception:
                return False

        def progress_hook(d):
            if d["status"] == "downloading":
                percent = d.get("_percent_str", "").strip()
                speed = d.get("_speed_str", "")
                send({"type": "progress", "percent": percent, "speed": speed})

            elif d["status"] == "finished":
                send({"type": "progress", "percent": "100%", "speed": ""})
        for _ in range(100):
            if task_id in tasks:
                break
            time.sleep(0.1)
        platform = platform.lower()
        file_format = file_format.lower()
        if platform not in PLATFORMS:
            platform = "youtube"
        if file_format not in FORMATS:
            file_format = "mp3"

        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': False,
            'verbose': True,
            'extract_flat': False,
            'progress_hooks': [progress_hook],
        }

        # -------------------------
        # 平台搜索逻辑
        # -------------------------
        if platform == "youtube":
            keyword = f"ytsearch1:{keyword}"
        elif platform == "soundcloud":
            keyword = f"scsearch1:{keyword}"
        else:
            keyword = keyword  # 直接 URL 或网页关键字

        # 先获取信息，不下载
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(keyword, download=False)
            if 'entries' in info:
                info = info['entries'][0]

            title = str(info.get('title', 'unknown'))
            artist = str(info.get('uploader', 'unknown'))
            url = info.get('webpage_url')

            safe_title = "".join(c for c in title if c not in '/\\?%*:|"<>')
            safe_artist = "".join(c for c in artist if c not in '/\\?%*:|"<>')
            filename = f"{safe_artist} - {safe_title}.{file_format}"
            filepath = os.path.join(MUSIC_DIR, filename)

        # 更新下载选项
        ydl_opts.update({
            'outtmpl': filepath,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': file_format,
                'preferredquality': '192',
            }],
        })

        # 执行下载
        with YoutubeDL(ydl_opts) as ydl2:
            ydl2.download([url])

        # 抓封面和歌词
        cover_url, lyrics = fetch_lyrics_and_cover(url)
        cover_data = None
        if cover_url:
            try:
                cover_data = requests.get(cover_url, timeout=5).content
            except:
                cover_data = None

        # 写 ID3 标签
        try:
            audio = EasyID3(filepath)
            audio['title'] = title
            audio['artist'] = artist
            audio.save()
            if cover_data:
                audio = ID3(filepath)
                audio['APIC'] = APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=cover_data
                )
                audio.save()
        except Exception as e:
            print("[ID3 tag error]", e)

        # 写歌词
        if lyrics:
            lyrics_path = os.path.join(MUSIC_DIR, f"{safe_artist} - {safe_title}.lrc")
            with open(lyrics_path, 'w', encoding='utf-8') as f:
                f.write(lyrics)

        # 触发 Navidrome 扫描
        trigger_rescan()

        print(f"[Download finished] {filepath}")
        # 发送完成消息
        send({"type": "done", "path": filepath})

    except Exception as e:
        print("[download_and_tag error]", e)
        # 发送错误消息
        send({"type": "error", "msg": str(e)})


@app.post("/download")
def start_download(
    keyword: str,
    platform: str = "youtube",
    file_format: str = "mp3",
    bg: BackgroundTasks = None
):
    task_id = str(uuid.uuid4())

    if bg is None:
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()

    bg.add_task(download_and_tag, task_id, keyword, platform, file_format)

    return {"task_id": task_id}
