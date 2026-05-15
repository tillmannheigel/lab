"""Hybrid YouTube transcriber: tries captions first, falls back to Whisper."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)


_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_SHORT_HOSTS = {"youtu.be"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TranscriberError(Exception):
    """Raised when transcription fails for a known reason."""


@dataclass
class TranscriptResult:
    video_id: str
    language: Optional[str]
    source: str  # "captions" or "whisper"
    text: str


def extract_video_id(url_or_id: str) -> str:
    """Accepts a full YouTube URL or a bare 11-char video ID."""
    value = url_or_id.strip()
    if _ID_RE.match(value):
        return value

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()

    if host in _SHORT_HOSTS:
        candidate = parsed.path.lstrip("/")
        if _ID_RE.match(candidate):
            return candidate

    if host in _YT_HOSTS:
        # /watch?v=ID
        qs = parse_qs(parsed.query)
        if "v" in qs and _ID_RE.match(qs["v"][0]):
            return qs["v"][0]
        # /embed/ID, /shorts/ID, /live/ID, /v/ID
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
            if _ID_RE.match(parts[1]):
                return parts[1]

    raise TranscriberError(f"Could not parse a YouTube video ID from: {url_or_id!r}")


def _fetch_captions(video_id: str, preferred_langs: list[str]) -> TranscriptResult:
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as exc:
        raise TranscriberError(f"No captions available: {exc}") from exc

    try:
        transcript = transcript_list.find_transcript(preferred_langs)
    except NoTranscriptFound:
        transcript = next(iter(transcript_list), None)
        if transcript is None:
            raise TranscriberError("No transcripts listed for this video.")

    entries = transcript.fetch()
    text = " ".join(entry["text"].strip() for entry in entries if entry.get("text"))
    text = re.sub(r"\s+", " ", text).strip()
    return TranscriptResult(
        video_id=video_id,
        language=transcript.language_code,
        source="captions",
        text=text,
    )


def _fetch_whisper(video_id: str, model_name: str) -> TranscriptResult:
    # Lazy imports — Whisper + yt-dlp are heavy and only needed for the fallback.
    import whisper  # type: ignore[import-not-found]
    import yt_dlp  # type: ignore[import-not-found]

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }
            ],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as exc:
            raise TranscriberError(f"Audio download failed: {exc}") from exc

        audio_path = os.path.join(tmpdir, f"{video_id}.mp3")
        if not os.path.exists(audio_path):
            # yt-dlp picked a different extension; find whatever ended up there.
            files = [f for f in os.listdir(tmpdir) if f.startswith(video_id)]
            if not files:
                raise TranscriberError("Audio file not found after download.")
            audio_path = os.path.join(tmpdir, files[0])

        model = whisper.load_model(model_name)
        result = model.transcribe(audio_path)

    return TranscriptResult(
        video_id=video_id,
        language=result.get("language"),
        source="whisper",
        text=(result.get("text") or "").strip(),
    )


def transcribe(
    url_or_id: str,
    preferred_langs: Optional[list[str]] = None,
    whisper_model: str = "base",
    allow_whisper: bool = True,
) -> TranscriptResult:
    """Return a transcript, preferring YouTube captions and falling back to Whisper."""
    video_id = extract_video_id(url_or_id)
    langs = preferred_langs or ["en", "de"]

    try:
        return _fetch_captions(video_id, langs)
    except TranscriberError:
        if not allow_whisper:
            raise

    return _fetch_whisper(video_id, whisper_model)
