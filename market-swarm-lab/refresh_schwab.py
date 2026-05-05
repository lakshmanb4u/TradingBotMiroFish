#!/usr/bin/env python3
import json
import base64
import requests
import time
from pathlib import Path

TOKEN_FILE = Path.home() / ".schwab_tokens.json"

# Load existing tokens
tokens = json.loads(TOKEN_FILE.read_text())
refresh_token = tokens.get("refresh_token", "")

# Auth header
CLIENT_ID = "hhJuJtOaM8K418w4UGdvC27WuLTYOgwpzFgAvxdr1UUWG0xX"
CLIENT_SECRET = "r9MZz1FYt3sBQNSCVGwlyCk43P26B9ziImixGkzQKmh7D4mqZDN7qByicz4eaJ6A"
creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

# Try refresh
resp = requests.post(
    "https://api.schwabapi.com/v1/oauth/token",
    headers={
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    },
    timeout=15,
)

print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")

if resp.status_code == 200:
    new_tokens = resp.json()
    new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
    TOKEN_FILE.write_text(json.dumps(new_tokens, indent=2))
    print("✅ Token refreshed successfully!")
else:
    print("❌ Token refresh failed. Need manual re-auth.")
    print(f"\nVisit this URL in your browser:")
    print(f"https://api.schwabapi.com/v1/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri=https%3A%2F%2F127.0.0.1")
