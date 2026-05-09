#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# AsynapRous release — Central Backend (Control Plane)
#
# This module handles global state: authentication, peer discovery, and
# channel management.  It runs on the SERVER machine behind the proxy.
# Clients never talk to this directly — the proxy forwards to it.
#

import sys
import os
import json
import base64
import hashlib
import uuid
import time
import fcntl

from daemon import AsynapRous

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

# Shared state file — global peer directory
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "db", "chat_state.json")

# User credentials file
USERS_FILE = os.path.join(os.path.dirname(__file__), "..", "db", "users.json")

# Heartbeat timeout — peers not seen within this window are evicted
PEER_TIMEOUT_SECONDS = 120

# ---------------------------------------------------------------------------
# File-based shared state utilities (cross-process safe)
# ---------------------------------------------------------------------------


def _default_state():
    return {
        "active_peers": {},
        "sessions": {},
        "channels": {"#general": []},
    }


def _ensure_state_shape(state):
    state.setdefault("active_peers", {})
    state.setdefault("sessions", {})
    state.setdefault("channels", {"#general": []})
    return state


def _read_state():
    default = _default_state()
    try:
        with open(STATE_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            content = f.read()
            fcntl.flock(f, fcntl.LOCK_UN)
            if not content.strip():
                return default
            return _ensure_state_shape(json.loads(content))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2)
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


def _read_modify_write(modifier_fn):
    """Atomic read-modify-write cycle on the shared state file."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    default = _default_state()

    try:
        with open(STATE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            content = f.read()
            if not content.strip():
                state = default
            else:
                state = _ensure_state_shape(json.loads(content))

            result = modifier_fn(state)

            f.seek(0)
            f.truncate()
            json.dump(state, f, indent=2)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
            return result
    except FileNotFoundError:
        state = default
        result = modifier_fn(state)
        _write_state(state)
        return result


def _cleanup_stale_peers(state):
    """Remove peers whose last_seen is older than PEER_TIMEOUT_SECONDS."""
    now = time.time()
    stale = []
    for username, info in state.get("active_peers", {}).items():
        last_seen = info.get("last_seen", info.get("joined_at", 0))
        if now - last_seen > PEER_TIMEOUT_SECONDS:
            stale.append(username)

    for username in stale:
        del state["active_peers"][username]
        print(
            "[CentralApp] Evicted stale peer: {} (timeout {}s)".format(
                username, PEER_TIMEOUT_SECONDS
            )
        )

    return stale


# ---------------------------------------------------------------------------
# User credentials
# ---------------------------------------------------------------------------


def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"admin": "admin123", "alice": "password1", "bob": "password2"}


USERS = load_users()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def generate_session_token():
    return uuid.uuid4().hex


def validate_session(headers):
    cookie_str = headers.get("cookie", "")
    cookies = {}
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookies[k.strip()] = v.strip()
    token = cookies.get("sessionid", "")
    if not token:
        return None

    state = _read_state()
    return state.get("sessions", {}).get(token, None)


def validate_basic_auth(headers):
    auth_header = headers.get("authorization", "")
    if not auth_header.lower().startswith("basic "):
        return None
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        if USERS.get(username) == password:
            return username
    except Exception:
        pass
    return None


def require_auth(headers):
    user = validate_session(headers)
    if user:
        return user
    return validate_basic_auth(headers)


def unauthorized_result():
    return json.dumps(
        {
            "status": "error",
            "message": "Authentication required",
            "__status__": 401,
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# AsynapRous application instance
# ---------------------------------------------------------------------------

app = AsynapRous()


# ===================================================================
# ROUTES — Control Plane
# ===================================================================


# --- Login ---

@app.route("/login", methods=["PUT", "POST"])
def login(headers="guest", body="anonymous"):

    print("[CentralApp] Logging in {} to {}".format(headers, body))

    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = data.get("username", "")
    password = data.get("password", "")

    if username and password and USERS.get(username) == password:
        token = generate_session_token()

        def _add_session(state):
            state.setdefault("sessions", {})
            state["sessions"][token] = username

        _read_modify_write(_add_session)

        result = {
            "status": "ok",
            "message": "Welcome, {}!".format(username),
            "username": username,
            "__set_cookie__": "sessionid={}; HttpOnly; Path=/".format(token),
        }
        print("[CentralApp] Session created for {} -> {}".format(username, token))
        return json.dumps(result).encode("utf-8")
    else:
        result = {
            "status": "error",
            "message": "Invalid credentials",
            "__status__": 401,
        }
        return json.dumps(result).encode("utf-8")


# --- Hello (session validation) ---

@app.route("/hello", methods=["POST", "PUT", "GET"])
def hello(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    print("[CentralApp] Valid User {} accessed /hello".format(user))
    result = {
        "status": "ok",
        "message": "Hello, {}! You are authenticated.".format(user),
        "user": user,
    }
    return json.dumps(result).encode("utf-8")


# --- Register Node (replaces /submit-info) ---
# The UI sends {local_port: 3001}.  The Central Backend extracts
# the client's real LAN IP from proxy-injected X-Forwarded-For or
# falls back to the socket's remote address.

@app.route("/register-node", methods=["POST"])
def register_node(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    local_port = data.get("local_port", 3001)

    # Extract the client's real LAN IP
    # Priority: X-Forwarded-For > X-Real-Ip > fallback
    client_ip = ""
    if hasattr(headers, "get"):
        # X-Forwarded-For may contain: "client_ip, proxy_ip"
        xff = headers.get("x-forwarded-for", "")
        if xff:
            client_ip = xff.split(",")[0].strip()
        if not client_ip:
            client_ip = headers.get("x-real-ip", "")
        if not client_ip:
            # Fallback: try to extract from Host or use a provided field
            client_ip = data.get("ip", "")

    if not client_ip:
        client_ip = "127.0.0.1"  # last resort fallback

    def _register(state):
        state.setdefault("active_peers", {})
        now = time.time()
        state["active_peers"][user] = {
            "ip": client_ip,
            "port": int(local_port),
            "joined_at": now,
            "last_seen": now,
        }

    _read_modify_write(_register)

    print("[CentralApp] Registered node: {} -> {}:{}".format(user, client_ip, local_port))
    result = {
        "status": "ok",
        "message": "Node '{}' registered at {}:{}".format(user, client_ip, local_port),
        "peer": {
            "username": user,
            "ip": client_ip,
            "port": int(local_port),
        },
    }
    return json.dumps(result).encode("utf-8")


# --- Get Peer List (Fault-Tolerant Discovery) ---

@app.route("/get-list", methods=["GET"])
def get_list(headers="guest", body="anonymous"):

    def _get_and_clean(state):
        _cleanup_stale_peers(state)
        peers = []
        for username, info in state.get("active_peers", {}).items():
            peers.append(
                {
                    "username": username,
                    "ip": info.get("ip", "127.0.0.1"),
                    "port": info.get("port", 3001),
                }
            )
        return peers

    peers = _read_modify_write(_get_and_clean)

    print("[CentralApp] get_list called, {} peers".format(len(peers)))
    result = {
        "status": "ok",
        "peers": peers,
        "count": len(peers),
    }
    return json.dumps(result).encode("utf-8")


# --- Heartbeat (called by local backend periodically) ---

@app.route("/heartbeat", methods=["POST"])
def heartbeat(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    def _beat(state):
        if user in state.get("active_peers", {}):
            state["active_peers"][user]["last_seen"] = time.time()

    _read_modify_write(_beat)

    return json.dumps({"status": "ok"}).encode("utf-8")


# --- Logout ---

@app.route("/logout", methods=["POST"])
def logout(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    cookie_str = headers.get("cookie", "") if hasattr(headers, "get") else ""
    session_token = ""
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() == "sessionid":
                session_token = v.strip()

    def _remove_user(state):
        state.get("active_peers", {}).pop(user, None)
        if session_token:
            state.get("sessions", {}).pop(session_token, None)

    _read_modify_write(_remove_user)

    print("[CentralApp] User '{}' logged out".format(user))
    result = {
        "status": "ok",
        "message": "{} has been logged out".format(user),
        "__set_cookie__": "sessionid=; Max-Age=0; Path=/",
    }
    return json.dumps(result).encode("utf-8")


# --- Channel Management ---

@app.route('/join-channel', methods=['POST'])
def join_channel(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except Exception:
        payload = {}
    channel = payload.get("channel", "").strip()
    if not channel:
        result = {"status": "error", "message": "Missing channel name"}
        return json.dumps(result).encode("utf-8")

    if not channel.startswith("#"):
        channel = "#" + channel

    def _join(state):
        state.setdefault("channels", {"#general": []})
        if channel not in state["channels"]:
            state["channels"][channel] = []
        if user not in state["channels"][channel]:
            state["channels"][channel].append(user)

    _read_modify_write(_join)

    print("[CentralApp] User '{}' joined channel '{}'".format(user, channel))
    result = {
        "status": "ok",
        "message": "{} joined {}".format(user, channel),
        "channel": channel,
    }
    return json.dumps(result).encode("utf-8")


@app.route('/get-channels', methods=['GET'])
def get_channels(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    state = _read_state()
    channels = state.get("channels", {"#general": []})

    result = {
        "status": "ok",
        "channels": channels,
    }
    return json.dumps(result).encode("utf-8")


# ===================================================================
# Entry point
# ===================================================================


def create_central_app(ip, port):

    app.prepare_address(ip, port)
    app.run()
