# YouTube Transcriber

A small Flask web app that transcribes YouTube videos. It tries to fetch the
existing YouTube captions first, and falls back to local
[OpenAI Whisper](https://github.com/openai/whisper) if no captions are
available.

## Requirements

- Python 3.10+
- [`ffmpeg`](https://ffmpeg.org/) on `PATH` (required by Whisper / yt-dlp)

## Setup

```shell
cd youtube-transcriber
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```shell
python app.py
```

Then open <http://127.0.0.1:5000>.

Set `PORT` to use a different port:

```shell
PORT=8000 python app.py
```

## API

`POST /api/transcribe` with JSON body:

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "languages": "en,de",
  "whisper_model": "base",
  "allow_whisper": true
}
```

Response:

```json
{
  "video_id": "dQw4w9WgXcQ",
  "language": "en",
  "source": "captions",
  "text": "We're no strangers to love..."
}
```

`source` is either `captions` (fetched from YouTube) or `whisper` (transcribed
locally).

## Notes

- Whisper model sizes: `tiny`, `base`, `small`, `medium`, `large`. Larger is
  more accurate but much slower and downloads a bigger model on first use.
- The first Whisper run downloads the model weights — that initial call may
  take a while.
- Captions are usually instant; Whisper transcription scales with video length.
