"""
JWT-based authentication for the trading bot dashboard.
Users stored in logs/users.json (bcrypt-hashed passwords, never plaintext).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import jwt

logger = logging.getLogger(__name__)

_USERS_FILE = Path("logs/users.json")
_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 7
_MIN_PASSWORD_LEN = 6
_MIN_USERNAME_LEN = 3

_lock = threading.Lock()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _secret() -> str:
    s = os.getenv("JWT_SECRET", "")
    if not s:
        logger.warning("JWT_SECRET not set — using insecure default. Set it as an env var!")
        return "trading-bot-dev-secret-CHANGE-IN-PRODUCTION"
    return s


def _load_users() -> dict:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_users(users: dict) -> None:
    _USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

def register_user(username: str, password: str) -> tuple[bool, str]:
    """Returns (success, message)."""
    username = username.strip().lower()
    if len(username) < _MIN_USERNAME_LEN:
        return False, f"Username must be at least {_MIN_USERNAME_LEN} characters"
    if len(password) < _MIN_PASSWORD_LEN:
        return False, f"Password must be at least {_MIN_PASSWORD_LEN} characters"
    # allow email addresses and simple alphanumeric usernames
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-.@")
    if not all(c in allowed for c in username):
        return False, "Username may only contain letters, numbers, and . _ - @ characters"
    with _lock:
        users = _load_users()
        if username in users:
            return False, "Username already taken"
        users[username] = {
            "username": username,
            "password_hash": _hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_users(users)
    logger.info("New user registered: %s", username)
    return True, "Account created successfully"


def authenticate_user(username: str, password: str) -> tuple[bool, str]:
    """Returns (success, token_or_error_message)."""
    username = username.strip().lower()
    with _lock:
        users = _load_users()
    user = users.get(username)
    if not user or not _verify_password(password, user["password_hash"]):
        return False, "Invalid username or password"
    token = _create_token(username)
    logger.info("User logged in: %s", username)
    return True, token


def decode_token(token: str) -> Optional[str]:
    """Returns username if token is valid and not expired, else None."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.PyJWTError:
        return None


def _create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": username, "exp": expire}, _secret(), algorithm=_ALGORITHM)
