# YouTube Transcriber

A small Flask web app that transcribes YouTube videos. It tries to fetch the
existing YouTube captions first, and falls back to local
[OpenAI Whisper](https://github.com/openai/whisper) if no captions are
available.

Two deploy modes:

- **All-in-one local**: Flask serves both the UI (`templates/index.html`) and
  the API. Good for personal use.
- **Split deploy**: static UI on **GitHub Pages** (`pages/`), Flask API on
  any container host (Fly.io, Render, Hugging Face Spaces, …). The UI talks
  to the API via CORS.

## Requirements

- Python 3.10+
- [`ffmpeg`](https://ffmpeg.org/) on `PATH` (required by yt-dlp / Whisper)

## Run locally (all-in-one)

```shell
cd youtube-transcriber
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. Set `PORT=8000` to use another port.

## Split deploy: Pages UI + Fly.io backend

### 1. Deploy the backend to Fly.io

```shell
cd youtube-transcriber
fly launch --no-deploy --copy-config        # edit app name in fly.toml first
fly secrets set CORS_ORIGINS="https://<your-user>.github.io"
fly deploy
```

The `Dockerfile` installs `ffmpeg` and starts the app via `gunicorn` on port
8080. Adjust `[[vm]] memory` in `fly.toml` if you use larger Whisper models
(`small`+ → 2 GB recommended).

Any container host works — Render, Railway, Hugging Face Spaces, your own
server. The only requirements are: expose the Flask app, set
`CORS_ORIGINS` to your Pages origin, install `ffmpeg`.

### 2. Publish the UI to GitHub Pages

1. In **Settings → Pages**, set the source to **GitHub Actions**.
2. Push to `main`. The workflow at `.github/workflows/pages.yml` deploys
   `youtube-transcriber/pages/` to `https://<your-user>.github.io/<repo>/`.
3. Open the Pages site, click **Change** next to "Backend", and paste your
   Fly.io URL (e.g. `https://youtube-transcriber-xyz.fly.dev`). It's stored
   in `localStorage`.

To hard-code the backend URL for everyone, edit
`youtube-transcriber/pages/config.js`:

```js
window.YTT_API_BASE = "https://your-backend.fly.dev";
```

The UI also accepts a `?api=` query param, which overrides storage:

```
https://<your-user>.github.io/<repo>/?api=https://your-backend.fly.dev
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

`source` is either `captions` (fetched from YouTube) or `whisper`
(transcribed locally). `GET /api/health` returns `{"status": "ok"}`.

## Authentication (GitHub OAuth + allowlist)

Optional but recommended for any public deploy. When `SESSION_SECRET` is
set, `/api/transcribe` requires a valid Bearer token, which the UI obtains
via a GitHub OAuth round-trip. Only GitHub logins listed in
`ALLOWED_USERS` are accepted.

### One-time GitHub setup

1. Open <https://github.com/settings/developers> → **New OAuth App**.
2. Fill in:
   - **Homepage URL**: `https://<your-user>.github.io/<repo>/`
   - **Authorization callback URL**: `https://<your-backend>.fly.dev/auth/callback`
3. Note the **Client ID**, generate a **Client Secret**.

### Backend env vars

```shell
fly secrets set \
    SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    GITHUB_CLIENT_ID="..." \
    GITHUB_CLIENT_SECRET="..." \
    ALLOWED_USERS="your-gh-login,another-gh-login" \
    BACKEND_URL="https://your-backend.fly.dev" \
    FRONTEND_URL="https://<your-user>.github.io/<repo>" \
    CORS_ORIGINS="https://<your-user>.github.io"
```

Leave `SESSION_SECRET` unset to bypass auth entirely (useful for local
dev — the UI shows "Auth disabled on backend").

### How it works

1. User clicks **Login with GitHub** → frontend redirects to
   `GET /auth/login` on the backend.
2. Backend redirects to GitHub's authorize endpoint with a signed,
   short-lived state.
3. GitHub redirects back to `GET /auth/callback`; backend exchanges the
   code for a user, checks `ALLOWED_USERS`, then redirects to
   `FRONTEND_URL#token=<signed-session>`.
4. Frontend stores the token in `localStorage` and sends it as
   `Authorization: Bearer …` on every API call.

No server-side session store — both state and session are signed with
`itsdangerous` and expire (10 min / 24 h).

## Configuration

| Env var               | Default | Description                                                   |
|-----------------------|---------|---------------------------------------------------------------|
| `PORT`                | `5000`  | Port the app listens on.                                      |
| `CORS_ORIGINS`        | `*`     | Comma-separated allowed origins for `/api/*` and `/auth/me`.  |
| `SESSION_SECRET`      | unset   | Required to enable auth. Signs state + session tokens.        |
| `GITHUB_CLIENT_ID`    | unset   | GitHub OAuth App client ID.                                   |
| `GITHUB_CLIENT_SECRET`| unset   | GitHub OAuth App client secret.                               |
| `ALLOWED_USERS`       | unset   | Comma-separated GitHub logins allowed to sign in.             |
| `BACKEND_URL`         | unset   | Public base URL of the backend (for OAuth `redirect_uri`).    |
| `FRONTEND_URL`        | unset   | Public URL of the Pages UI (where to redirect after OAuth).   |

## Notes

- Whisper model sizes: `tiny`, `base`, `small`, `medium`, `large`. Larger is
  more accurate but slower and downloads bigger weights on first use.
- The first Whisper run downloads model weights — that call may take a
  while. Mount a volume in production if you don't want to redownload on
  every machine restart.
- Captions are usually instant; Whisper transcription scales with video
  length.
