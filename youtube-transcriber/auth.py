"""GitHub OAuth with a username allowlist.

Flow:
  1. Frontend redirects user to GET /auth/login
  2. Backend redirects to GitHub's authorize endpoint with a signed state
  3. GitHub redirects back to /auth/callback with code + state
  4. Backend exchanges code for an access token, fetches the user, checks
     the allowlist, then redirects to the frontend with a signed session
     token in the URL fragment (#token=...).
  5. Frontend stores that token and sends it as `Authorization: Bearer ...`
     on subsequent API calls.

No server-side session store — both state and session are itsdangerous
signed tokens, so the backend stays stateless.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import urllib.request
from functools import wraps
from typing import Optional

from flask import Blueprint, jsonify, redirect, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

_GH_AUTHORIZE = "https://github.com/login/oauth/authorize"
_GH_TOKEN = "https://github.com/login/oauth/access_token"  # noqa: S105
_GH_USER = "https://api.github.com/user"

_STATE_TTL = 600  # 10 minutes
_SESSION_TTL = 24 * 3600  # 24 hours


def _serializer(salt: str) -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET env var is required for auth.")
    return URLSafeTimedSerializer(secret, salt=salt)


def _allowed_users() -> set[str]:
    raw = os.environ.get("ALLOWED_USERS", "")
    return {u.strip().lower() for u in raw.split(",") if u.strip()}


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "").rstrip("/")


def _redirect_uri() -> str:
    backend = os.environ.get("BACKEND_URL", "").rstrip("/")
    if not backend:
        # Fall back to whatever Flask sees — fine in development.
        backend = request.url_root.rstrip("/")
    return backend + "/auth/callback"


def _back_to_frontend(fragment: str) -> "redirect":
    target = _frontend_url() or "/"
    return redirect(target + "#" + fragment)


@auth_bp.get("/login")
def login():
    client_id = os.environ.get("GITHUB_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "OAuth not configured (missing GITHUB_CLIENT_ID)."}), 500

    nonce = secrets.token_urlsafe(16)
    state = _serializer("oauth-state").dumps(nonce)

    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "state": state,
        "scope": "read:user",
        "allow_signup": "false",
    }
    return redirect(_GH_AUTHORIZE + "?" + urllib.parse.urlencode(params))


@auth_bp.get("/callback")
def callback():
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code or not state:
        return _back_to_frontend("error=" + urllib.parse.quote("Missing code/state"))

    try:
        _serializer("oauth-state").loads(state, max_age=_STATE_TTL)
    except (BadSignature, SignatureExpired):
        return _back_to_frontend("error=" + urllib.parse.quote("Invalid or expired state"))

    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return _back_to_frontend("error=" + urllib.parse.quote("OAuth not configured"))

    # Exchange code -> access token
    token_body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": _redirect_uri(),
        }
    ).encode()
    token_req = urllib.request.Request(
        _GH_TOKEN,
        data=token_body,
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return _back_to_frontend("error=" + urllib.parse.quote(f"Token exchange failed: {exc}"))

    access_token = token_data.get("access_token")
    if not access_token:
        msg = token_data.get("error_description") or "No access token returned"
        return _back_to_frontend("error=" + urllib.parse.quote(msg))

    # Fetch user
    user_req = urllib.request.Request(
        _GH_USER,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "youtube-transcriber",
        },
    )
    try:
        with urllib.request.urlopen(user_req, timeout=10) as resp:
            user = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return _back_to_frontend("error=" + urllib.parse.quote(f"User fetch failed: {exc}"))

    login_name = (user.get("login") or "").lower()
    allowed = _allowed_users()
    if not allowed:
        return _back_to_frontend(
            "error=" + urllib.parse.quote("Allowlist empty — set ALLOWED_USERS on the backend")
        )
    if login_name not in allowed:
        return _back_to_frontend(
            "error=" + urllib.parse.quote(f"User '{login_name}' is not on the allowlist")
        )

    session_token = _serializer("session").dumps({"login": login_name})
    return _back_to_frontend("token=" + urllib.parse.quote(session_token))


def _extract_bearer(req) -> Optional[str]:
    auth_header = req.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip() or None
    return None


def _verify(token: str) -> Optional[dict]:
    try:
        return _serializer("session").loads(token, max_age=_SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # When auth is disabled (no SESSION_SECRET configured), let calls
        # through — useful for local dev. Production deploys must set it.
        if not os.environ.get("SESSION_SECRET"):
            return fn(*args, **kwargs)
        token = _extract_bearer(request)
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        data = _verify(token)
        if not data:
            return jsonify({"error": "Invalid or expired session"}), 401
        request.user = data  # type: ignore[attr-defined]
        return fn(*args, **kwargs)

    return wrapper


@auth_bp.get("/me")
def me():
    if not os.environ.get("SESSION_SECRET"):
        return jsonify({"authenticated": True, "user": None, "auth_required": False})
    token = _extract_bearer(request)
    if not token:
        return jsonify({"authenticated": False, "auth_required": True})
    data = _verify(token)
    if not data:
        return jsonify({"authenticated": False, "auth_required": True})
    return jsonify({"authenticated": True, "user": data.get("login"), "auth_required": True})


@auth_bp.post("/logout")
def logout():
    # Stateless — client just drops the token. This endpoint exists so the
    # frontend can call it for symmetry.
    return jsonify({"ok": True})
