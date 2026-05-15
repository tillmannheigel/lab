"""Flask web app for the hybrid YouTube transcriber."""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import auth_bp, require_auth
from transcriber import TranscriberError, transcribe


app = Flask(__name__)

# Trust X-Forwarded-* from the platform proxy (Fly.io, Render, etc.) so that
# request.url_root reflects the public HTTPS URL.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Allow the static GitHub Pages UI (and any other configured origin) to call us.
# Override with CORS_ORIGINS env var, e.g. "https://user.github.io,https://example.com".
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
CORS(
    app,
    resources={
        r"/api/*": {"origins": _origins},
        r"/auth/me": {"origins": _origins},
        r"/auth/logout": {"origins": _origins},
    },
)

app.register_blueprint(auth_bp)


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/transcribe")
@require_auth
def api_transcribe():
    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing 'url' field."}), 400

    langs_raw = (data.get("languages") or "").strip()
    preferred_langs = [s.strip() for s in langs_raw.split(",") if s.strip()] or None

    whisper_model = (data.get("whisper_model") or "base").strip()
    allow_whisper = str(data.get("allow_whisper", "true")).lower() not in {
        "0",
        "false",
        "no",
    }

    try:
        result = transcribe(
            url,
            preferred_langs=preferred_langs,
            whisper_model=whisper_model,
            allow_whisper=allow_whisper,
        )
    except TranscriberError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:  # pragma: no cover - surface unexpected failures
        return jsonify({"error": f"Unexpected error: {exc}"}), 500

    return jsonify(
        {
            "video_id": result.video_id,
            "language": result.language,
            "source": result.source,
            "text": result.text,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
