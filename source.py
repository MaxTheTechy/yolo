import re

YOUTUBE_PATTERN = re.compile(r"(youtube\.com|youtu\.be)")


def is_youtube_url(url):
    return bool(YOUTUBE_PATTERN.search(url))


def resolve_stream_url(url):
    """Resolve a YouTube page URL to a direct stream URL OpenCV can open.
    Non-YouTube URLs (e.g. real RTSP camera URLs) pass through unchanged."""
    if not is_youtube_url(url):
        return url

    import yt_dlp

    ydl_opts = {"quiet": True, "format": "best[ext=mp4]/best", "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info["url"]
