#!/usr/bin/env python3
"""Antigravity Quota Dashboard Server.

Provides:
  - GET /: Web dashboard (responsive, dual-limit 5h/weekly, theme toggleable)
  - GET /api/quota: Aggregated 5-hour and Weekly limits matching `agy /usage`
  - Zero pip dependencies
"""

import argparse
from datetime import datetime, timezone
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse

DEFAULT_PROJECT = "aicode-consumers"

# Cache quota responses for 10 seconds to avoid spamming agy
_cache_lock = threading.Lock()
_cached_quota = None
_cached_time = 0
CACHE_TTL = 10


def fetch_quota_from_agy() -> dict:
    """Run `agy.exe -p "/usage" --output-format json` to get official structured usage."""
    cmd = ["agy.exe", "-p", "/usage", "--output-format", "json"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise RuntimeError(f"agy error (code {res.returncode}): {res.stderr}")

    payload = json.loads(res.stdout)
    data = payload.get("command", {}).get("data", {})
    groups = data.get("groups", [])

    # Pools definition
    pools = {
        "claude": {
            "id": "claude",
            "name": "Claude & GPT models",
            "shortName": "Claude & GPT",
            "description": "Claude 3.5/3.7/5.5, GPT-OSS",
            "h5": None,
            "weekly": None,
        },
        "gemini": {
            "id": "gemini",
            "name": "Gemini Models",
            "shortName": "Gemini 2.5 / 3.x",
            "description": "Flash, Pro & Thinking",
            "h5": None,
            "weekly": None,
        },
        "argon": {
            "id": "argon",
            "name": "Gemini 4 Argon",
            "shortName": "Gemini 4 Argon",
            "description": "Argon High / Medium / Low",
            "h5": None,
            "weekly": None,
        },
    }

    for g in groups:
        g_name = (g.get("name") or "").lower()
        target_key = None
        if "claude" in g_name or "gpt" in g_name:
            target_key = "claude"
        elif "argon" in g_name:
            target_key = "argon"
        elif "gemini" in g_name:
            target_key = "gemini"

        if not target_key:
            continue

        for b in g.get("buckets", []):
            window = b.get("window", "").lower()
            frac = float(b.get("remaining_fraction", 1.0))
            bucket_info = {
                "id": b.get("id"),
                "name": b.get("name"),
                "remainingFraction": frac,
                "remainingPercent": round(frac * 100),
                "resetTime": b.get("reset_time"),
                "description": b.get("description", ""),
            }
            if "5h" in window:
                pools[target_key]["h5"] = bucket_info
            elif "week" in window:
                pools[target_key]["weekly"] = bucket_info

    # Find overall bottleneck
    all_buckets = []
    for p in pools.values():
        if p["h5"]:
            all_buckets.append((p["shortName"] + " (5小時)", p["h5"]))
        if p["weekly"]:
            all_buckets.append((p["shortName"] + " (週間)", p["weekly"]))

    all_buckets.sort(key=lambda x: x[1]["remainingFraction"])
    bottleneck = None
    if all_buckets:
        lowest_name, lowest_bucket = all_buckets[0]
        bottleneck = {
            "title": lowest_name,
            "remainingPercent": lowest_bucket["remainingPercent"],
            "resetTime": lowest_bucket["resetTime"],
            "description": lowest_bucket.get("description", ""),
        }

    return {
        "project": DEFAULT_PROJECT,
        "tierName": "Google AI Pro",
        "fetchedAt": int(time.time() * 1000),
        "pools": pools,
        "bottleneck": bottleneck,
    }


def fetch_quota_summary() -> dict:
    global _cached_quota, _cached_time
    now = time.time()
    with _cache_lock:
        if _cached_quota and (now - _cached_time < CACHE_TTL):
            return _cached_quota

    try:
        result = fetch_quota_from_agy()
    except Exception as e:
        print(f"[ERROR] Failed to fetch from agy: {e}")
        raise

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
        if "GET /api/quota" in args[0] and "200" in args[1]:
            return
        super().log_message(format, *args)


def run_server(port=8890, host="0.0.0.0"):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((host, port), DashboardHandler) as httpd:
        print(f"[OK] Antigravity Dashboard running on http://{host}:{port}")
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
