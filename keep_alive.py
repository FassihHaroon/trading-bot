#!/usr/bin/env python3
"""
keep_alive.py — Production-grade keep-alive pinger for Render free-tier services.

Sends randomised, browser-like HTTP requests so the service never sleeps.
Jitter, User-Agent rotation, varied paths and realistic headers minimise the
chance of detection as a bot.

DISCLAIMER: Artificially preventing a free-tier service from spinning down
may violate Render's Terms of Service. Use responsibly and consider upgrading
to a paid plan for production workloads.

Usage (env-var):
    TARGET_URL=https://your-app.onrender.com python keep_alive.py

Usage (CLI):
    python keep_alive.py --url https://your-app.onrender.com --interval 720

Environment variables:
    TARGET_URL       Base URL of the service to keep alive (required)
    PING_INTERVAL    Base interval in seconds (default: 720 = 12 min)
    JITTER_PCT       ± jitter percentage 0-100 (default: 25)
    LOG_FILE         Optional path to a log file (default: stdout only)
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import random
import sys
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Logging ────────────────────────────────────────────────────────────────────

def _build_logger(log_file: Optional[str] = None) -> logging.Logger:
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    return logging.getLogger("keep_alive")


# ── Realistic browser User-Agents ─────────────────────────────────────────────

_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox on Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    # Safari on iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

# Paths to ping — rotated to look like organic navigation
_PATHS = [
    "/api/health",
    "/api/health",         # weight health check higher
    "/api/health",
    "/api/symbols",
    "/api/paper-trade/summary",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8,fr;q=0.5",
    "en-US,en;q=0.9,de;q=0.7",
]


def _realistic_headers(base_url: str) -> dict[str, str]:
    """Return headers that mimic a real browser visit."""
    return {
        "User-Agent":      random.choice(_USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Cache-Control":   random.choice(["no-cache", "max-age=0"]),
        "Referer":         base_url + "/",
        "DNT":             "1",
    }


# ── Session with retry ─────────────────────────────────────────────────────────

def _build_session(retries: int = 3) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://",  adapter)
    session.mount("https://", adapter)
    return session


# ── Interval helpers ───────────────────────────────────────────────────────────

def _jittered_interval(base_seconds: float, jitter_pct: float) -> float:
    """
    Return base_seconds ± jitter_pct %.
    e.g. base=720, jitter=25 → 540–900 s (9–15 min).
    """
    delta = base_seconds * (jitter_pct / 100.0)
    return base_seconds + random.uniform(-delta, delta)


def _backoff(attempt: int, base: float = 30.0, cap: float = 600.0) -> float:
    """Exponential backoff with full jitter, capped at `cap` seconds."""
    exp = min(base * (2 ** attempt), cap)
    return random.uniform(0, exp)


# ── Main pinger ────────────────────────────────────────────────────────────────

class KeepAlivePinger:
    def __init__(
        self,
        target_url: str,
        interval: float = 720.0,
        jitter_pct: float = 25.0,
        timeout: float = 15.0,
        log_file: Optional[str] = None,
    ):
        self.base_url    = target_url.rstrip("/")
        self.interval    = interval
        self.jitter_pct  = jitter_pct
        self.timeout     = timeout
        self.log         = _build_logger(log_file)
        self._session    = _build_session()
        self._ping_count = 0
        self._fail_count = 0

    def _ping(self) -> bool:
        path = random.choice(_PATHS)
        url  = self.base_url + path
        headers = _realistic_headers(self.base_url)

        # Small random pre-delay (0–3 s) simulates human latency
        time.sleep(random.uniform(0.1, 3.0))

        try:
            resp = self._session.get(url, headers=headers, timeout=self.timeout)
            ok = resp.status_code < 500
            self.log.info(
                "Ping #%d → %s  [%d]  %.0fms",
                self._ping_count + 1, path, resp.status_code,
                resp.elapsed.total_seconds() * 1000,
            )
            return ok
        except requests.exceptions.Timeout:
            self.log.warning("Ping #%d timed out after %.0fs", self._ping_count + 1, self.timeout)
            return False
        except requests.exceptions.ConnectionError as exc:
            self.log.warning("Ping #%d connection error: %s", self._ping_count + 1, exc)
            return False
        except Exception as exc:
            self.log.error("Ping #%d unexpected error: %s", self._ping_count + 1, exc)
            return False

    def run(self) -> None:
        self.log.info(
            "Keep-alive started → %s  (interval ~%.0fs ± %g%%)",
            self.base_url, self.interval, self.jitter_pct,
        )
        consecutive_failures = 0

        while True:
            self._ping_count += 1
            success = self._ping()

            if success:
                consecutive_failures = 0
                self._fail_count = 0
            else:
                consecutive_failures += 1
                self._fail_count     += 1
                wait = _backoff(consecutive_failures - 1)
                self.log.warning(
                    "%d consecutive failure(s) — backing off %.0fs before retry.",
                    consecutive_failures, wait,
                )
                time.sleep(wait)
                continue  # retry immediately instead of waiting full interval

            sleep_for = _jittered_interval(self.interval, self.jitter_pct)
            self.log.info(
                "Next ping in %.0fs (%.1f min)  |  total pings=%d  failures=%d",
                sleep_for, sleep_for / 60, self._ping_count, self._fail_count,
            )
            time.sleep(sleep_for)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Keep a Render free-tier service alive with realistic HTTP pings.",
    )
    p.add_argument(
        "--url", default=os.getenv("TARGET_URL", ""),
        help="Base URL of the service (env: TARGET_URL)",
    )
    p.add_argument(
        "--interval", type=float,
        default=float(os.getenv("PING_INTERVAL", "720")),
        help="Base ping interval in seconds (env: PING_INTERVAL, default: 720)",
    )
    p.add_argument(
        "--jitter", type=float,
        default=float(os.getenv("JITTER_PCT", "25")),
        help="Interval jitter percentage (env: JITTER_PCT, default: 25)",
    )
    p.add_argument(
        "--timeout", type=float, default=15.0,
        help="Request timeout in seconds (default: 15)",
    )
    p.add_argument(
        "--log-file", default=os.getenv("LOG_FILE", ""),
        help="Optional log file path (env: LOG_FILE)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if not args.url:
        print("ERROR: --url or TARGET_URL environment variable is required.", file=sys.stderr)
        sys.exit(1)

    pinger = KeepAlivePinger(
        target_url  = args.url,
        interval    = args.interval,
        jitter_pct  = args.jitter,
        timeout     = args.timeout,
        log_file    = args.log_file or None,
    )

    try:
        pinger.run()
    except KeyboardInterrupt:
        print("\nStopped by user.")
