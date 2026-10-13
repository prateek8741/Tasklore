"""YouTube links -> the videos to turn into playbooks.

Gemini reads public YouTube videos directly from their URL (see gemini.youtube_part), so nothing is downloaded.
This module only works out WHICH videos a link means, and their title, channel, length and visibility.

- Single videos work without any key (YouTube's public oEmbed endpoint gives title + channel).
- Playlists, video length and private/unlisted detection need YOUTUBE_API_KEY (YouTube Data API v3,
  a Google API, not an AI service). It lives in Secret Manager, like the Gemini key.
"""
import os
import re
from urllib.parse import parse_qs, urlparse

import httpx

API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
DATA_API = "https://www.googleapis.com/youtube/v3"
MAX_PLAYLIST_VIDEOS = 50
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeError(Exception):
    """A problem the user can act on (bad link, private video, missing key)."""


def parse(url: str) -> dict:
    """Return {"video_id": str|None, "playlist_id": str|None} for any common YouTube link form."""
    u = urlparse(url.strip() if "://" in url else "https://" + url.strip())
    host = (u.hostname or "").removeprefix("www.").removeprefix("m.").removeprefix("music.")
    q = parse_qs(u.query)
    video = playlist = None
    if host == "youtu.be":
        video = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if u.path == "/watch":
            video = q.get("v", [None])[0]
        elif m := re.match(r"^/(shorts|embed|live|v)/([^/?#]+)", u.path):
            video = m.group(2)
        playlist = q.get("list", [None])[0]
    else:
        raise YouTubeError("That doesn't look like a YouTube link. Paste a link from youtube.com or youtu.be.")
    if video and not _ID.match(video):
        video = None
    if not video and not playlist:
        raise YouTubeError("Couldn't find a video or playlist in that link.")
    return {"video_id": video, "playlist_id": playlist}


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _duration_s(iso: str) -> int:
    """ISO 8601 duration from the Data API, e.g. PT1H2M3S -> 3723."""
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _get(path: str, **params) -> dict:
    r = httpx.get(f"{DATA_API}/{path}", params={**params, "key": API_KEY}, timeout=15)
    if r.status_code == 404:
        raise YouTubeError("YouTube couldn't find that playlist. Is it public?")
    if r.status_code >= 400:
        raise YouTubeError(f"YouTube Data API error ({r.status_code}). Check the YouTube API key.")
    return r.json()


def _details(ids: list[str]) -> dict[str, dict]:
    """Title, channel, length and visibility for up to 50 videos per call (1 quota unit each call)."""
    out = {}
    for i in range(0, len(ids), 50):
        data = _get("videos", part="snippet,contentDetails,status", id=",".join(ids[i:i + 50]), maxResults=50)
        for v in data.get("items", []):
            out[v["id"]] = {"video_id": v["id"], "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
                            "duration_s": _duration_s(v["contentDetails"].get("duration", "")),
                            "privacy": v["status"].get("privacyStatus", "public")}
    return out


def _oembed(video_id: str) -> dict:
    r = httpx.get("https://www.youtube.com/oembed", params={"url": watch_url(video_id), "format": "json"}, timeout=10)
    if r.status_code >= 400:
        raise YouTubeError("This video doesn't exist or is private, so Gemini can't open it. "
                           "Download it and use Upload instead.")
    d = r.json()
    return {"video_id": video_id, "title": d.get("title", ""), "channel": d.get("author_name", ""),
            "duration_s": None, "privacy": "public"}


def video(video_id: str) -> dict:
    if API_KEY:
        found = _details([video_id]).get(video_id)
        if not found:
            raise YouTubeError("This video doesn't exist or is private, so Gemini can't open it.")
        return found
    return _oembed(video_id)


def playlist(playlist_id: str) -> dict:
    if not API_KEY:
        raise YouTubeError("Playlist import needs a YouTube Data API key on the server. Paste single video links for now.")
    meta = _get("playlists", part="snippet", id=playlist_id).get("items", [])
    if not meta:
        raise YouTubeError("YouTube couldn't find that playlist. Is it public?")
    ids, token = [], None
    while len(ids) < MAX_PLAYLIST_VIDEOS:
        page = _get("playlistItems", part="contentDetails", playlistId=playlist_id, maxResults=50,
                    **({"pageToken": token} if token else {}))
        ids += [it["contentDetails"]["videoId"] for it in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    details = _details(ids[:MAX_PLAYLIST_VIDEOS])
    return {"title": meta[0]["snippet"]["title"], "channel": meta[0]["snippet"]["channelTitle"],
            "videos": [details[i] for i in ids[:MAX_PLAYLIST_VIDEOS] if i in details],  # deleted/private items drop out
            "total": len(ids)}
