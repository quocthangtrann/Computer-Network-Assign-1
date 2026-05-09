#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# AsynapRous release — Local Backend (Data Plane)
#
# This module handles local message queues and direct P2P communication.
# It runs on EACH CLIENT machine.  The browser UI talks to this on
# localhost:3001.  Other peers' Local Backends send messages here via
# POST /receive-message.
#

import sys
import os
import json
import base64
import uuid
import time
import urllib.request
import urllib.error
import threading

from daemon import AsynapRous

# ---------------------------------------------------------------------------
# Configuration (set via create_local_app arguments)
# ---------------------------------------------------------------------------

# The proxy URL pointing to the Central Backend
CENTRAL_URL = "http://192.168.1.4:8080"

# The username of the currently logged-in user on this node
LOCAL_USERNAME = ""

# Auth token for Central API calls
LOCAL_AUTH_TOKEN = ""

# ---------------------------------------------------------------------------
# In-memory state (no shared JSON file — each node is independent)
# ---------------------------------------------------------------------------

# Peer cache: {"bob": {"ip": "192.168.1.11", "port": 3001}, ...}
LOCAL_PEER_CACHE = {}

# Local message queue: [msg_obj, msg_obj, ...]
LOCAL_MESSAGE_QUEUE = []

# Message history for transcript restoration
LOCAL_MESSAGE_HISTORY = []

# Maximum history size
MAX_MESSAGE_HISTORY = 500

# Lock for thread-safe queue access
_queue_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Auth helpers (validate incoming requests from the local browser)
# ---------------------------------------------------------------------------

# User credentials — loaded from the same file as central
USERS_FILE = os.path.join(os.path.dirname(__file__), "..", "db", "users.json")


def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"admin": "admin123", "alice": "password1", "bob": "password2"}


USERS = load_users()


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
# Peer cache management
# ---------------------------------------------------------------------------


def refresh_peer_cache():
    """Fetch the latest peer list from the Central Backend."""
    global LOCAL_PEER_CACHE

    try:
        req = urllib.request.Request(CENTRAL_URL + "/get-list")
        if LOCAL_AUTH_TOKEN:
            req.add_header("Authorization", "Basic " + LOCAL_AUTH_TOKEN)
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            peers = data.get("peers", [])
            for p in peers:
                username = p.get("username")
                ip = p.get("ip", "")
                port = p.get("port", 3001)
                if username and ip:
                    LOCAL_PEER_CACHE[username] = {"ip": ip, "port": int(port)}
            print("[LocalApp] Refreshed peer cache: {} peers".format(len(LOCAL_PEER_CACHE)))
    except Exception as e:
        print("[LocalApp] Failed to refresh peer cache from Central: {}".format(e))
        print("[LocalApp] Using existing cache with {} peers".format(len(LOCAL_PEER_CACHE)))


# ---------------------------------------------------------------------------
# Message queue helpers (thread-safe)
# ---------------------------------------------------------------------------


def enqueue_message(msg_obj):
    """Add a message to the local queue and history."""
    global LOCAL_MESSAGE_QUEUE, LOCAL_MESSAGE_HISTORY
    with _queue_lock:
        LOCAL_MESSAGE_QUEUE.append(msg_obj)
        LOCAL_MESSAGE_HISTORY.append(msg_obj)
        if len(LOCAL_MESSAGE_HISTORY) > MAX_MESSAGE_HISTORY:
            LOCAL_MESSAGE_HISTORY = LOCAL_MESSAGE_HISTORY[-MAX_MESSAGE_HISTORY:]


def drain_queue():
    """Drain and return all queued messages."""
    global LOCAL_MESSAGE_QUEUE
    with _queue_lock:
        messages = list(LOCAL_MESSAGE_QUEUE)
        LOCAL_MESSAGE_QUEUE = []
    return messages


# ---------------------------------------------------------------------------
# AsynapRous application instance
# ---------------------------------------------------------------------------

app = AsynapRous()


# ===================================================================
# ROUTES — Data Plane
# ===================================================================


# --- Send Peer (Direct P2P via urllib) ---

@app.route("/send-peer", methods=["POST"])
def send_peer(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    to_user = data.get("to", "")
    msg_text = data.get("msg", "")

    if not to_user or not msg_text:
        result = {"status": "error", "message": "Missing 'to' or 'msg'"}
        return json.dumps(result).encode("utf-8")

    # Look up target in cache; refresh if not found
    if to_user not in LOCAL_PEER_CACHE:
        refresh_peer_cache()

    if to_user not in LOCAL_PEER_CACHE:
        result = {"status": "error", "message": "Peer '{}' not found in directory".format(to_user)}
        return json.dumps(result).encode("utf-8")

    peer_info = LOCAL_PEER_CACHE[to_user]
    target_url = "http://{}:{}/receive-message".format(peer_info["ip"], peer_info["port"])

    message_entry = {
        "id": uuid.uuid4().hex,
        "from": user,
        "to": to_user,
        "msg": msg_text,
        "type": "direct",
        "ts": time.time(),
    }

    try:
        # POST message directly to target peer's Local Backend
        payload = json.dumps(message_entry).encode("utf-8")
        req = urllib.request.Request(target_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                # Also enqueue locally so sender sees their own message
                enqueue_message(message_entry)

                print("[LocalApp] P2P message sent: {} -> {} via {}".format(
                    user, to_user, target_url))
                result = {
                    "status": "ok",
                    "message": "Direct P2P message delivered to {}".format(to_user),
                    "delivered": True,
                }
                return json.dumps(result).encode("utf-8")

    except Exception as e:
        print("[LocalApp] P2P Send FAILED to {}: {}".format(target_url, e))
        result = {"status": "error", "message": "P2P connection to {} failed: {}".format(to_user, e)}
        return json.dumps(result).encode("utf-8")


# --- Receive Message (Incoming P2P from other nodes) ---

@app.route("/receive-message", methods=["POST"])
def receive_message(headers="guest", body="anonymous"):
    """Endpoint hit by OTHER peers' Local Backends.
    No auth required — this is a server-to-server call."""

    try:
        msg_entry = json.loads(body) if body else {}
    except Exception:
        return json.dumps({"status": "error", "message": "Invalid JSON"}).encode("utf-8")

    if not msg_entry.get("from") or not msg_entry.get("msg"):
        return json.dumps({"status": "error", "message": "Invalid message"}).encode("utf-8")

    # Store in local queue — browser will pick it up via /fetch-messages
    enqueue_message(msg_entry)

    print("[LocalApp] Received P2P message from '{}': {}".format(
        msg_entry.get("from"), msg_entry.get("msg", "")[:50]))
    return json.dumps({"status": "ok"}).encode("utf-8")


# --- Fetch Messages (Browser polls this) ---

@app.route("/fetch-messages", methods=["GET"])
def fetch_messages(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    # Also send heartbeat to Central Backend (non-blocking)
    _send_heartbeat_async()

    messages = drain_queue()

    result = {
        "status": "ok",
        "messages": messages,
        "count": len(messages),
    }
    return json.dumps(result).encode("utf-8")


# --- Message History ---

@app.route("/message-history", methods=["GET"])
def message_history(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    with _queue_lock:
        messages = list(LOCAL_MESSAGE_HISTORY)

    result = {
        "status": "ok",
        "messages": messages,
        "count": len(messages),
    }
    return json.dumps(result).encode("utf-8")


# --- Broadcast (Fan-out to all peers via P2P) ---

@app.route("/broadcast-peer", methods=["POST"])
def broadcast_peer(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    msg_text = data.get("msg", "")
    if not msg_text:
        result = {"status": "error", "message": "Missing 'msg'"}
        return json.dumps(result).encode("utf-8")

    message_entry = {
        "id": uuid.uuid4().hex,
        "from": user,
        "to": "broadcast",
        "msg": msg_text,
        "type": "broadcast",
        "ts": time.time(),
    }

    # Enqueue locally first
    enqueue_message(message_entry)

    # Refresh cache then fan-out
    refresh_peer_cache()

    delivered = 0
    for peer_name, peer_info in LOCAL_PEER_CACHE.items():
        if peer_name == user:
            continue  # don't send to self (already enqueued)
        target_url = "http://{}:{}/receive-message".format(peer_info["ip"], peer_info["port"])
        try:
            payload = json.dumps(message_entry).encode("utf-8")
            req = urllib.request.Request(target_url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    delivered += 1
        except Exception as e:
            print("[LocalApp] Broadcast to {} failed: {}".format(peer_name, e))

    print("[LocalApp] Broadcast delivered to {}/{} peers".format(delivered, len(LOCAL_PEER_CACHE) - 1))
    result = {
        "status": "ok",
        "message": "Broadcast delivered to {} peer(s)".format(delivered),
        "total_peers": delivered,
    }
    return json.dumps(result).encode("utf-8")


# --- Send Channel Message (Fan-out to channel members via P2P) ---

@app.route("/send-channel", methods=["POST"])
def send_channel(headers="guest", body="anonymous"):

    user = require_auth(headers)
    if not user:
        return unauthorized_result()

    try:
        payload_data = json.loads(body) if isinstance(body, str) else body
    except Exception:
        payload_data = {}
    channel = payload_data.get("channel", "").strip()
    msg_text = payload_data.get("msg", "").strip()

    if not channel or not msg_text:
        result = {"status": "error", "message": "Missing channel or msg"}
        return json.dumps(result).encode("utf-8")

    if not channel.startswith("#"):
        channel = "#" + channel

    msg_entry = {
        "id": uuid.uuid4().hex,
        "from": user,
        "to": channel,
        "channel": channel,
        "msg": msg_text,
        "type": "channel",
        "ts": time.time(),
    }

    # Enqueue locally first
    enqueue_message(msg_entry)

    # Get channel members from Central Backend
    members = []
    try:
        req = urllib.request.Request(CENTRAL_URL + "/get-channels")
        if LOCAL_AUTH_TOKEN:
            req.add_header("Authorization", "Basic " + LOCAL_AUTH_TOKEN)
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            channels = data.get("channels", {})
            members = channels.get(channel, [])
    except Exception as e:
        print("[LocalApp] Failed to get channel members: {}".format(e))

    # Refresh peer cache for addresses
    refresh_peer_cache()

    # Fan-out to all channel members
    delivered = 0
    for member in members:
        if member == user:
            continue  # already enqueued locally
        if member not in LOCAL_PEER_CACHE:
            continue
        peer_info = LOCAL_PEER_CACHE[member]
        target_url = "http://{}:{}/receive-message".format(peer_info["ip"], peer_info["port"])
        try:
            p = json.dumps(msg_entry).encode("utf-8")
            req = urllib.request.Request(target_url, data=p, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    delivered += 1
        except Exception as e:
            print("[LocalApp] Channel send to {} failed: {}".format(member, e))

    print("[LocalApp] Channel '{}' message from '{}' -> {} members".format(
        channel, user, delivered))
    result = {
        "status": "ok",
        "message": "Delivered to {} member(s) in {}".format(delivered, channel),
    }
    return json.dumps(result).encode("utf-8")


# ---------------------------------------------------------------------------
# Heartbeat (non-blocking, runs in background thread)
# ---------------------------------------------------------------------------

_last_heartbeat = 0


def _send_heartbeat_async():
    """Send a heartbeat to Central Backend in a background thread."""
    global _last_heartbeat
    now = time.time()
    # Only send every 10 seconds to avoid flooding
    if now - _last_heartbeat < 10:
        return
    _last_heartbeat = now

    def _beat():
        try:
            req = urllib.request.Request(CENTRAL_URL + "/heartbeat", method="POST")
            req.add_header("Content-Type", "application/json")
            if LOCAL_AUTH_TOKEN:
                req.add_header("Authorization", "Basic " + LOCAL_AUTH_TOKEN)
            req.data = json.dumps({}).encode("utf-8")
            with urllib.request.urlopen(req, timeout=2):
                pass
        except Exception:
            pass

    t = threading.Thread(target=_beat, daemon=True)
    t.start()


# ===================================================================
# Entry point
# ===================================================================


def create_local_app(ip, port, central_url="http://192.168.1.4:8080"):
    global CENTRAL_URL

    CENTRAL_URL = central_url
    print("[LocalApp] Starting Local P2P Backend on {}:{}".format(ip, port))
    print("[LocalApp] Central Backend URL: {}".format(CENTRAL_URL))

    # Initial peer cache load
    refresh_peer_cache()

    app.prepare_address(ip, port)
    app.run()
