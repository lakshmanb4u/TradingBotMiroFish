"""Schwab OAuth2 PKCE auth flow + token manager.

Usage (one-time interactive):
    python schwab_auth.py

This will open the Schwab auth URL, you paste back the redirect URL,
and it saves tokens to ~/.schwab_tokens.json.
Subsequent runs auto-refresh silently.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import requests

CLIENT_ID = os.environ.get("SCHWAB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SCHWAB_CLIENT_SECRET", "")
CALLBACK_URL = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
AUTH_URL = os.environ.get("SCHWAB_AUTH_URL", "https://api.schwabapi.com/v1/oauth/authorize")
TOKEN_URL = os.environ.get("SCHWAB_TOKEN_URL", "https://api.schwabapi.com/v1/oauth/token")
TOKEN_FILE = Path.home() / ".schwab_tokens.json"


def _load_tokens() -> dict[str, Any]:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return {}


def _save_tokens(tokens: dict[str, Any]) -> None:
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    TOKEN_FILE.chmod(0o600)


def _basic_auth_header() -> str:
    creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return f"Basic {creds}"


def _refresh_access_token(refresh_token: str) -> dict[str, Any]:
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_token() -> str:
    """Return a valid access token, refreshing if needed."""
    tokens = _load_tokens()

    if not tokens:
        raise RuntimeError(
            "No Schwab tokens found. Run `python schwab_auth.py` interactively to authenticate."
        )

    expires_at = tokens.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return tokens["access_token"]

    # Refresh
    print("[schwab_auth] Refreshing access token...")
    new_tokens = _refresh_access_token(tokens["refresh_token"])
    new_tokens["expires_at"] = time.time() + new_tokens.get("expires_in", 1800)
    # Preserve refresh token if not returned
    if "refresh_token" not in new_tokens:
        new_tokens["refresh_token"] = tokens["refresh_token"]
    _save_tokens(new_tokens)
    print("[schwab_auth] Token refreshed.")
    return new_tokens["access_token"]


def interactive_login() -> None:
    """One-time interactive OAuth2 PKCE login."""
    # PKCE
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
        "scope": "readonly",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("\n=== Schwab OAuth2 Login ===")
    print("Opening browser... if it doesn't open, visit:")
    print(f"\n  {auth_url}\n")
    webbrowser.open(auth_url)

    redirect_response = input("Paste the full redirect URL here: ").strip()
    parsed = urllib.parse.urlparse(redirect_response)
    code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        raise ValueError("No auth code found in redirect URL.")

    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK_URL,
            "code_verifier": code_verifier,
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()
    tokens["expires_at"] = time.time() + tokens.get("expires_in", 1800)
    _save_tokens(tokens)
    print(f"\n✅ Tokens saved to {TOKEN_FILE}")
    print(f"   Access token expires in ~{tokens.get('expires_in', 1800)//60} minutes")
    print(f"   Refresh token: {'present' if 'refresh_token' in tokens else 'NOT present'}")


if __name__ == "__main__":
    interactive_login()
