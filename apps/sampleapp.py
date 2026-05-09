#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# AsynapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
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

# Shared state file — both backend 9000 and 9001 read/write this
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "db", "chat_state.json")

# User credentials file
USERS_FILE = os.path.join(os.path.dirname(__file__), "..", "db", "users.json")

# Heartbeat timeout — peers not seen within this window are evicted
PEER_TIMEOUT_SECONDS = 120

# Keep enough transcript to survive browser refreshes without letting the
# shared JSON state grow forever.
MAX_MESSAGE_HISTORY = 200

# ---------------------------------------------------------------------------
# File-based shared state utilities (cross-process safe)
# ---------------------------------------------------------------------------


def _default_state():
    return {
        "active_peers": {},
        "message_queues": {},
        "message_history": {},
        "sessions": {},
    }


def _ensure_state_shape(state):
    state.setdefault("active_peers", {})
    state.setdefault("message_queues", {})
    state.setdefault("message_history", {})
    state.setdefault("sessions", {})
    return state


def _append_history(state, username, message_entry):
    state.setdefault("message_history", {})
    state["message_history"].setdefault(username, [])
    state["message_history"][username].append(message_entry)
    if len(state["message_history"][username]) > MAX_MESSAGE_HISTORY:
        state["message_history"][username] = state["message_history"][username][
            -MAX_MESSAGE_HISTORY:
        ]


def _read_state():
    # Read the shared state from db/chat_state.json.

    default = _default_state()
    try:
        with open(STATE_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)  # shared (read) lock
            content = f.read()
            fcntl.flock(f, fcntl.LOCK_UN)
            if not content.strip():
                return default
            return _ensure_state_shape(json.loads(content))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_state(state):
    # Write the shared state to db/chat_state.json atomically.

    # Ensure db/ directory exists
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # exclusive (write) lock
        json.dump(state, f, indent=2)
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


def _read_modify_write(modifier_fn):
    """Atomic read-modify-write cycle on the shared state file.

    Opens the file with an exclusive lock, reads current state,
    calls modifier_fn(state) which mutates it in place, then
    writes back. This prevents race conditions between the two
    backend processes.

    :param modifier_fn (callable): Function that receives the state
        dict and mutates it. May return a value which is passed back
        to the caller.
    :returns: Whatever modifier_fn returns.
    """
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    default = _default_state()

    # Open in r+ mode (read+write) to hold lock across read and write
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
        # File doesn't exist yet — create it
        state = default
        result = modifier_fn(state)
        _write_state(state)
        return result


def _cleanup_stale_peers(state):
    # Remove peers whose last_seen is older than PEER_TIMEOUT_SECONDS.
    # Called inside a _read_modify_write so state is already locked.
    now = time.time()
    stale = []
    for username, info in state.get("active_peers", {}).items():
        last_seen = info.get("last_seen", info.get("joined_at", 0))
        if now - last_seen > PEER_TIMEOUT_SECONDS:
            stale.append(username)

    for username in stale:
        del state["active_peers"][username]
        # Clean up their message queue
        state.get("message_queues", {}).pop(username, None)
        print(
            "[SampleApp] Evicted stale peer: {} (timeout {}s)".format(
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
# Virtual IP assignment
# ---------------------------------------------------------------------------

_virtual_ip_counter = 0


def _assign_virtual_ip(username):

    # Use hash for deterministic assignment
    h = hashlib.md5(username.encode()).hexdigest()
    octet3 = int(h[:2], 16)  # 0-255
    octet4 = int(h[2:4], 16) or 1  # 1-255 (avoid .0)
    return "192.168.{}.{}".format(octet3, octet4)


# ---------------------------------------------------------------------------
# Auth helpers (read sessions from shared state file)
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


# AsynapRous application instance

app = AsynapRous()

# Task 2.2 — Login: session cookie auth (RFC 6265)


@app.route("/login", methods=["PUT", "POST"])
def login(headers="guest", body="anonymous"):

    print("[SampleApp] Logging in {} to {}".format(headers, body))

    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = data.get("username", "")
    password = data.get("password", "")

    if username and password and USERS.get(username) == password:
        token = generate_session_token()

        # Store session in shared state file
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
        print("[SampleApp] Session created for {} -> {}".format(username, token))
        return json.dumps(result).encode("utf-8")
    else:
        result = {
            "status": "error",
            "message": "Invalid credentials",
            "__status__": 401,
        }
        return json.dumps(result).encode("utf-8")


# Task 2.2 — Hello: protected route (cookie OR Basic Auth)


@app.route("/hello", methods=["POST", "PUT", "GET"])
def hello(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        print("[SampleApp] Unauthenticated access to /hello — returning 401")
        result = {
            "status": "error",
            "message": "Authentication required",
            "__status__": 401,
        }
        return json.dumps(result).encode("utf-8")

    print("[SampleApp] Valid User {} accessed /hello".format(user))
    result = {
        "status": "ok",
        "message": "Hello, {}! You are authenticated.".format(user),
        "user": user,
    }
    return json.dumps(result).encode("utf-8")


# Echo — development helper


@app.route("/echo", methods=["POST"])
def echo(headers="guest", body="anonymous"):

    print("[SampleApp] received body {}".format(body))
    try:
        message = json.loads(body)
        data = {"status": "ok", "received": message}
    except json.JSONDecodeError:
        data = {"status": "error", "error": "Invalid JSON"}
    return json.dumps(data).encode("utf-8")


# Peer registration — Centralized Hub (replaces old P2P tracker)


@app.route("/submit-info", methods=["POST"])
def submit_info(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    print("[SampleApp] submit_info body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = data.get("username", "") or user

    # Assign a virtual IP
    virtual_ip = _assign_virtual_ip(username)

    def _register(state):
        state.setdefault("active_peers", {})
        state.setdefault("message_queues", {})
        state.setdefault("message_history", {})
        now = time.time()
        state["active_peers"][username] = {
            "virtual_ip": virtual_ip,
            "joined_at": now,
            "last_seen": now,
        }
        # Create message queue if not exists
        if username not in state["message_queues"]:
            state["message_queues"][username] = []
        state["message_history"].setdefault(username, [])

    _read_modify_write(_register)

    print("[SampleApp] Registered peer: {} -> {}".format(username, virtual_ip))
    result = {
        "status": "ok",
        "message": "Peer '{}' registered with Virtual IP {}".format(
            username, virtual_ip
        ),
        "peer": {"username": username, "virtual_ip": virtual_ip},
    }
    return json.dumps(result).encode("utf-8")


@app.route("/get-list", methods=["GET"])
def get_list(headers="guest", body="anonymous"):

    # Clean up stale peers before returning the list
    def _get_and_clean(state):
        _cleanup_stale_peers(state)
        peers = []
        for username, info in state.get("active_peers", {}).items():
            peers.append(
                {
                    "username": username,
                    "virtual_ip": info.get("virtual_ip", ""),
                }
            )
        return peers

    peers = _read_modify_write(_get_and_clean)

    print("[SampleApp] get_list called, {} peers".format(len(peers)))
    result = {
        "status": "ok",
        "peers": peers,
        "count": len(peers),
    }
    return json.dumps(result).encode("utf-8")


# Chat Phase — Centralized Message Broker


@app.route("/send-peer", methods=["POST"])
def send_peer(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    print("[SampleApp] send_peer body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    to_user = data.get("to", "")
    msg_text = data.get("msg", "")
    sender = data.get("from", user)

    if not to_user:
        result = {"status": "error", "message": "Missing 'to' field"}
        return json.dumps(result).encode("utf-8")

    if not msg_text:
        result = {"status": "error", "message": "Missing 'msg' field"}
        return json.dumps(result).encode("utf-8")

    message_entry = {
        "id": uuid.uuid4().hex,
        "from": sender,
        "to": to_user,
        "msg": msg_text,
        "type": "direct",
        "ts": time.time(),
    }

    def _enqueue(state):
        state.setdefault("message_queues", {})
        peers = state.get("active_peers", {})

        # Check target exists
        if to_user not in peers:
            return "not_found"

        # Append to target's queue
        state["message_queues"].setdefault(to_user, [])
        state["message_queues"][to_user].append(message_entry)
        _append_history(state, to_user, message_entry)

        # Also append to sender's own queue so they see their sent message
        state["message_queues"].setdefault(sender, [])
        state["message_queues"][sender].append(message_entry)
        if sender != to_user:
            _append_history(state, sender, message_entry)

        return "ok"

    status = _read_modify_write(_enqueue)

    if status == "not_found":
        result = {"status": "error", "message": "Peer '{}' not found".format(to_user)}
        return json.dumps(result).encode("utf-8")

    result = {
        "status": "ok",
        "message": "Message delivered to {}".format(to_user),
        "delivered": True,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/broadcast-peer", methods=["POST"])
def broadcast_peer(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    print("[SampleApp] broadcast_peer body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    msg_text = data.get("msg", "")
    sender = data.get("from", user)

    if not msg_text:
        result = {"status": "error", "message": "Missing 'msg' field"}
        return json.dumps(result).encode("utf-8")

    message_entry = {
        "id": uuid.uuid4().hex,
        "from": sender,
        "to": "broadcast",
        "msg": msg_text,
        "type": "broadcast",
        "ts": time.time(),
    }

    def _broadcast(state):
        state.setdefault("message_queues", {})
        state.setdefault("active_peers", {})
        count = 0
        # Append to ALL users' queues (including sender so they see it)
        for username in state["active_peers"]:
            state["message_queues"].setdefault(username, [])
            state["message_queues"][username].append(message_entry)
            _append_history(state, username, message_entry)
            count += 1
        return count

    count = _read_modify_write(_broadcast)

    result = {
        "status": "ok",
        "message": "Broadcast delivered to {} peer(s)".format(count),
        "total_peers": count,
    }
    return json.dumps(result).encode("utf-8")


# Message History — restore transcript after browser refresh


@app.route("/message-history", methods=["GET"])
def message_history(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    state = _read_state()
    messages = list(state.get("message_history", {}).get(user, []))

    result = {
        "status": "ok",
        "messages": messages,
        "count": len(messages),
    }
    return json.dumps(result).encode("utf-8")


# Message Polling — AJAX short polling endpoint


@app.route("/fetch-messages", methods=["GET"])
def fetch_messages(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    def _drain_and_heartbeat(state):
        # Update last_seen — this IS the heartbeat
        if user in state.get("active_peers", {}):
            state["active_peers"][user]["last_seen"] = time.time()

        # Evict stale peers who stopped polling
        _cleanup_stale_peers(state)

        # Drain message queue
        state.setdefault("message_queues", {})
        queue = state["message_queues"].get(user, [])
        messages = list(queue)
        state["message_queues"][user] = []
        return messages

    messages = _read_modify_write(_drain_and_heartbeat)

    result = {
        "status": "ok",
        "messages": messages,
        "count": len(messages),
    }
    return json.dumps(result).encode("utf-8")


# Explicit Logout — remove peer, clear queue, delete session


@app.route("/logout", methods=["POST"])
def logout(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    # Extract session token from cookie to delete it
    cookie_str = headers.get("cookie", "") if hasattr(headers, "get") else ""
    session_token = ""
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() == "sessionid":
                session_token = v.strip()

    def _remove_user(state):
        # Remove from active peers
        state.get("active_peers", {}).pop(user, None)
        # Clear message queue
        state.get("message_queues", {}).pop(user, None)
        # Delete session
        if session_token:
            state.get("sessions", {}).pop(session_token, None)

    _read_modify_write(_remove_user)

    print("[SampleApp] User '{}' logged out and removed from active peers".format(user))
    result = {
        "status": "ok",
        "message": "{} has been logged out".format(user),
        "__set_cookie__": "sessionid=; Max-Age=0; Path=/",
    }
    return json.dumps(result).encode("utf-8")


# Entry point


def create_sampleapp(ip, port, role=None):

    app.prepare_address(ip, port)
    app.run()
