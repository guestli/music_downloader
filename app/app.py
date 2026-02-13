import os
import requests
from fastapi import FastAPI, BackgroundTasks, Query
from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from bs4 import BeautifulSoup

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
        soup = BeautifulSoup(html, "lxml")
        cover_tag = soup.find("meta", {"property": "og:image"})
        cover_url = cover_tag['content'] if cover_tag else None
        desc_tag = soup.find("meta", {"name": "description"})
        lyrics = desc_tag['content'] if desc_tag else ""
        return cover_url, lyrics
    except Exception as e:
        print("[fetch_lyrics_and_cover error]", e)
        return None, ""

def download_and_tag(keyword: str, platform: str = "youtube", file_format: str = "mp3"):
    try:
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

    except Exception as e:
        print("[download_and_tag error]", e)


@app.post("/download")
def start_download(
    keyword: str,
    platform: str = Query("youtube", description="youtube, bilibili, soundcloud, niconico, vimeo, mixcloud, bandcamp"),
    file_format: str = Query("mp3", description="mp3, m4a, opus, wav, flac, aac"),
    bg: BackgroundTasks = None
):
    if bg is None:
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()

    bg.add_task(download_and_tag, keyword, platform, file_format)
    print(f"[Download request] keyword={keyword}, platform={platform}, format={file_format}")
    return {"msg": "started"}
