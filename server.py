#!/usr/bin/env python3
"""Antigravity Quota Dashboard Server.

Provides:
  - GET /: Web dashboard (responsive, iOS widget / cyber theme toggleable)
  - GET /api/quota: Aggregated pools and raw buckets
  - Zero external dependencies (stdlib only: http.server, urllib, json, ctypes)
"""

import argparse
from datetime import datetime, timezone
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CRED_TARGET = "gemini:antigravity"
TOKEN_FILE = "~/.gemini/antigravity-cli/antigravity-oauth-token"
CLOUDCODE_BASE = "https://daily-cloudcode-pa.googleapis.com"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Known client ID & secret for agy
CLIENT_ID = (
    "1071006060591-tmamvjhkpk6hnoc4obog32sub2o3la7l.apps.googleusercontent.com"
)
CLIENT_SECRET = "GOCSPX-K5-smj-L7S_1-smjL7SL7S_1"

UA = (
    "antigravity/cli/1.4.12 (aidev_client; os_type=windows; arch=amd64; "
    "cl=1010700967; auth_method=consumer)"
)

DEFAULT_PROJECT = "aicode-consumers"

# Cache quota responses for 10 seconds to protect upstream quota
_cache_lock = threading.Lock()
_cached_quota = None
_cached_time = 0
CACHE_TTL = 10


def _read_raw_token():
    """Read token JSON from Windows Credential Manager or fallback file."""
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class CREDENTIAL(ctypes.Structure):
                _fields_ = [
                    ("Flags", wt.DWORD),
                    ("Type", wt.DWORD),
                    ("TargetName", wt.LPWSTR),
                    ("Comment", wt.LPWSTR),
                    ("LastWritten", wt.FILETIME),
                    ("CredentialBlobSize", wt.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                    ("Persist", wt.DWORD),
                    ("AttributeCount", wt.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wt.LPWSTR),
                    ("UserName", wt.LPWSTR),
                ]

            advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            pcred = ctypes.POINTER(CREDENTIAL)()
            if advapi.CredReadW(
                wt.LPCWSTR(CRED_TARGET), 1, 0, ctypes.byref(pcred)
            ):
                cred = pcred.contents
                raw = ctypes.string_at(
                    cred.CredentialBlob, cred.CredentialBlobSize
                )
                advapi.CredFree(pcred)
                return json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"[WARN] Credential manager read failed: {e}")

    try:
        path = os.path.expanduser(TOKEN_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Token file read failed: {e}")
    return None


def _refresh_access_token(refresh_token: str) -> dict:
    body = json.dumps(
        {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(OAUTH_TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "token_type": payload.get("token_type", "Bearer"),
        "expires_in": payload.get("expires_in", 3600),
    }


def get_valid_access_token() -> str:
    cred_data = _read_raw_token()
    if not cred_data:
        raise RuntimeError("No Antigravity credentials found.")
    token_obj = cred_data.get("token") or {}
    access_token = token_obj.get("access_token")
    return access_token


def call_cloudcode_api(path: str, body: dict, access_token: str) -> dict:
    url = CLOUDCODE_BASE + path
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": UA,
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {"ideType": "ANTIGRAVITY", "platform": "WINDOWS", "pluginType": "GEMINI"},
            separators=(",", ":"),
        ),
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {detail}")


def fetch_quota_summary() -> dict:
    global _cached_quota, _cached_time
    now = time.time()
    with _cache_lock:
        if _cached_quota and (now - _cached_time < CACHE_TTL):
            return _cached_quota

    token = get_valid_access_token()

    # 1. ensure project
    project = DEFAULT_PROJECT
    tier_name = "Google AI Pro"
    try:
        load_res = call_cloudcode_api("/v1internal:loadCodeAssist", {}, token)
        if load_res.get("cloudaicompanionProject"):
            project = load_res["cloudaicompanionProject"]
        if load_res.get("paidTier", {}).get("name"):
            tier_name = load_res["paidTier"]["name"]
        elif load_res.get("currentTier", {}).get("name"):
            tier_name = load_res["currentTier"]["name"]
    except Exception as e:
        print(f"[WARN] loadCodeAssist error: {e}")

    # 2. fetch quota
    quota_res = call_cloudcode_api(
        "/v1internal:retrieveUserQuota", {"project": project}, token
    )
    buckets = quota_res.get("buckets", [])

    # Grouping pools
    groups = {
        "claude": {"models": [], "fractions": [], "resets": []},
        "argon": {"models": [], "fractions": [], "resets": []},
        "gemini": {"models": [], "fractions": [], "resets": []},
    }

    for b in buckets:
        m_id = (b.get("modelId") or "").lower()
        frac = float(b.get("remainingFraction", 1.0))
        reset = b.get("resetTime")

        entry = {
            "modelId": b.get("modelId"),
            "tokenType": b.get("tokenType"),
            "remainingFraction": frac,
            "remainingPercent": round(frac * 100),
            "resetTime": reset,
        }

        if m_id.startswith("claude-"):
            groups["claude"]["models"].append(entry)
            groups["claude"]["fractions"].append(frac)
            if reset:
                groups["claude"]["resets"].append(reset)
        elif m_id.startswith("gemini-4-argon") or m_id.startswith("gpt-oss"):
            groups["argon"]["models"].append(entry)
            groups["argon"]["fractions"].append(frac)
            if reset:
                groups["argon"]["resets"].append(reset)
        elif m_id.startswith("gemini-") or m_id.startswith("chat_") or m_id.startswith("tab_"):
            groups["gemini"]["models"].append(entry)
            groups["gemini"]["fractions"].append(frac)
            if reset:
                groups["gemini"]["resets"].append(reset)

    def summarize_pool(p_id, name, short_name, grp):
        fractions = grp["fractions"]
        min_frac = min(fractions) if fractions else 1.0
        # Reset time: take earliest or latest valid ISO
        resets = sorted(grp["resets"])
        earliest_reset = resets[0] if resets else None

        return {
            "id": p_id,
            "name": name,
            "shortName": short_name,
            "remainingFraction": min_frac,
            "remainingPercent": round(min_frac * 100),
            "resetTime": earliest_reset,
            "models": sorted(grp["models"], key=lambda x: x["modelId"]),
        }

    pools = {
        "claude": summarize_pool(
            "claude", "Claude 3.5 / 3.7 / 5.5", "Claude", groups["claude"]
        ),
        "argon": summarize_pool(
            "argon", "Gemini 4 Argon & GPT-OSS", "Argon", groups["argon"]
        ),
        "gemini": summarize_pool(
            "gemini", "Gemini 2.5 / 3.x Flash & Pro", "Gemini", groups["gemini"]
        ),
    }

    # Find minimum bottleneck pool
    sorted_pools = sorted(pools.values(), key=lambda x: x["remainingFraction"])
    bottleneck = sorted_pools[0] if sorted_pools else None

    result = {
        "project": project,
        "tierName": tier_name,
        "fetchedAt": int(time.time() * 1000),
        "pools": pools,
        "bottleneck": bottleneck,
        "rawBucketsCount": len(buckets),
    }

    with _cache_lock:
        _cached_quota = result
        _cached_time = now

    return result


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        web_dir = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=web_dir, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/quota":
            try:
                data = fetch_quota_summary()
                body = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err_body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
            return
        elif parsed.path == "/":
            self.path = "/index.html"
            return super().do_GET()
        else:
            return super().do_GET()

    def log_message(self, format, *args):
        # Concise logging
        if "GET /api/quota" in args[0] and "200" in args[1]:
            return
        super().log_message(format, *args)


def run_server(port=8890, host="0.0.0.0"):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((host, port), DashboardHandler) as httpd:
        print(f"[OK] Antigravity Dashboard running on http://{host}:{port}")
        print(f"[OK] Tailscale URL: http://100.122.156.90:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Web Dashboard")
    parser.add_argument("--port", type=int, default=8890, help="Listen port (default: 8890)")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host (default: 0.0.0.0)")
    args = parser.parse_args()
    run_server(port=args.port, host=args.host)
