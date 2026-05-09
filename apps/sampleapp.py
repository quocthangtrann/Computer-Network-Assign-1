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

"""
app.sampleapp
~~~~~~~~~~~~~~~~~

This module implements the two backend roles used by the hybrid P2P chat app.

Architecture:
  - tracker role: central backend behind the proxy. It exposes global APIs
    such as /login, /submit-info, /get-list, and /client-info.
  - peer role: local backend on each client machine. It exposes local APIs
    such as /connect-peer, /send-peer, /broadcast-peer, and /receive-message.
  - Chat messages are sent peer-to-peer by the local backend opening a direct
    TCP/HTTP connection to another peer's local backend.

Authentication (Task 2.2):
  - /login (PUT)  → session cookie  (RFC 6265: Set-Cookie)
  - /hello (POST) → requires cookie OR Basic Auth (RFC 7617 / RFC 2617)

REST endpoints (Task 2.3):
  GET  /get-list
  POST /submit-info
  POST /add-list
  POST /connect-peer
  POST /send-peer
  POST /broadcast-peer
  GET  /get-messages
  GET  /get-channels
  POST /get-channel-messages
  POST /broadcast-channel
  DELETE /leave-channel

All handlers receive (headers: CaseInsensitiveDict, body: str) and return
JSON-encoded bytes.
"""

import os
import json
import base64
import uuid
import socket
import threading
import time

from daemon import AsynapRous

# ---------------------------------------------------------------------------
# In-memory stores (peer list, sessions, messages, channels)
# ---------------------------------------------------------------------------

# Registered peers: list of {"username": str, "ip": str, "port": str}
peer_list = []

# Active sessions: {token: username}
# Populated when a user logs in (/login) and used by cookie auth.
sessions = {}

# Message log: list of {"from": str, "msg": str, "ts": float}
messages = []

# Channels: {channel_name: [{"username": str, "ip": str, "port": str}, …]}
channels = {}

# Thread lock for shared state mutations
_lock = threading.Lock()

USERS_FILE = os.path.join(os.path.dirname(__file__), "..", "db", "users.json")


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
    """Generate a cryptographically random session token.

    Uses uuid4 (128-bit random) which is sufficiently unpredictable for a
    course project. Production systems would use secrets.token_hex().

    :returns (str): Hex string session token.
    """
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
    return sessions.get(token, None)


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


# P2P message helper


def send_to_peer(peer_ip, peer_port, payload_bytes):
    """Send a raw payload to a remote peer over TCP.

    Opens a short-lived TCP connection to (peer_ip, peer_port), sends the
    payload, and reads back the response. Used by /send-peer and
    /broadcast-peer to implement the direct peer-to-peer chat phase.

    :param peer_ip (str): Target peer IP address.
    :param peer_port (int): Target peer port number.
    :param payload_bytes (bytes): Raw bytes to send (typically a raw HTTP POST).
    :returns (bytes | None): Response bytes, or None on error.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((peer_ip, int(peer_port)))
        s.sendall(payload_bytes)
        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
        s.close()
        return response
    except Exception as e:
        print("[SampleApp] send_to_peer error: {}".format(e))
        return None


def detect_local_ip(target_host="8.8.8.8", target_port=80):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target_host, int(target_port)))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        print("[SampleApp] detect_local_ip error: {}".format(e))
        return "127.0.0.1"


# AsynapRous application instance

app = AsynapRous()

# Task 2.2 — Login: session cookie auth (RFC 6265)


@app.route("/login", methods=["PUT", "POST"])
def login(headers="guest", body="anonymous"):
    # Handle user login and issue a session cookie (Task 2.2).

    print("[SampleApp] Logging in {} to {}".format(headers, body))

    # Parse JSON body
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = data.get("username", "")
    password = data.get("password", "")

    # Validate credentials against the user store
    if username and password and USERS.get(username) == password:
        # Generate a session token and store it
        token = generate_session_token()
        with _lock:
            sessions[token] = username

        result = {
            "status": "ok",
            "message": "Welcome, {}!".format(username),
            "username": username,
            # Signal to HttpAdapter to inject Set-Cookie header
            "__set_cookie__": "sessionid={}; HttpOnly; Path=/".format(token),
        }
        print("[SampleApp] Session created for {} → {}".format(username, token))
        return json.dumps(result).encode("utf-8")
    else:
        # Invalid credentials — return 401 sentinel
        result = {
            "status": "error",
            "message": "Invalid credentials",
            "__status__": 401,
        }
        return json.dumps(result).encode("utf-8")


# Task 2.2 — Hello: protected route (cookie OR Basic Auth)


@app.route("/hello", methods=["POST", "PUT", "GET"])
def hello(headers="guest", body="anonymous"):
    # Access a protected route using session cookie or Basic Auth (Task 2.2).

    user = require_auth(headers)
    if not user:
        print("[SampleApp] Unauthenticated access to /hello — returning 401")
        # Return 401 sentinel; HttpAdapter will send WWW-Authenticate header
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
    """Echo the request body back as JSON (development/testing helper).

    :param headers: HTTP headers.
    :param body: Raw request body.
    :returns (bytes): JSON round-trip of the received body.
    """
    print("[SampleApp] received body {}".format(body))
    try:
        message = json.loads(body)
        data = {"status": "ok", "received": message}
    except json.JSONDecodeError:
        data = {"status": "error", "error": "Invalid JSON"}
    return json.dumps(data).encode("utf-8")


@app.route("/client-info", methods=["GET"])
def client_info(headers="guest", body="anonymous"):
    forwarded_for = headers.get("x-forwarded-for", "")
    real_ip = headers.get("x-real-ip", "")
    client_ip = forwarded_for.split(",", 1)[0].strip() or real_ip.strip()
    result = {
        "status": "ok",
        "client_ip": client_ip,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/local-info", methods=["POST", "GET"])
def local_info(headers="guest", body="anonymous"):
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    target_host = data.get("target_host") or "8.8.8.8"
    target_port = data.get("target_port") or 80
    local_ip = detect_local_ip(target_host, target_port)
    result = {
        "status": "ok",
        "local_ip": local_ip,
    }
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Peer registration (Initialization Phase)


@app.route("/submit-info", methods=["POST"])
def submit_info(headers="guest", body="anonymous"):
    # Register a peer's network information with the tracker (Task 2.3).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] submit_info body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = data.get("username", "unknown")
    peer_ip = data.get("ip", "")
    peer_port = data.get("port", "")

    if not peer_ip or not peer_port:
        result = {"status": "error", "message": "ip and port are required"}
        return json.dumps(result).encode("utf-8")

    # Add peer to registry (avoid duplicates by username)
    peer_entry = {"username": username, "ip": peer_ip, "port": peer_port}
    with _lock:
        # Remove old entry for this username if present
        global peer_list
        peer_list = [p for p in peer_list if p.get("username") != username]
        peer_list.append(peer_entry)

    print("[SampleApp] Registered peer: {}".format(peer_entry))
    result = {
        "status": "ok",
        "message": "Peer '{}' registered at {}:{}".format(username, peer_ip, peer_port),
        "peer": peer_entry,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/get-list", methods=["GET"])
def get_list(headers="guest", body="anonymous"):
    # Return the list of all registered peers (Task 2.3 — Initialization Phase).

    print("[SampleApp] get_list called, {} peers".format(len(peer_list)))
    result = {
        "status": "ok",
        "peers": peer_list,
        "count": len(peer_list),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/add-list", methods=["POST"])
def add_list(headers="guest", body="anonymous"):
    # Add a peer entry to the registry (Task 2.3).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] add_list body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = data.get("username", "unknown")
    peer_ip = data.get("ip", "")
    peer_port = data.get("port", "")

    peer_entry = {"username": username, "ip": peer_ip, "port": peer_port}
    with _lock:
        global peer_list
        peer_list = [p for p in peer_list if p.get("username") != username]
        peer_list.append(peer_entry)

    result = {"status": "ok", "added": peer_entry, "total": len(peer_list)}
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Chat Phase: direct peer-to-peer messaging


@app.route("/connect-peer", methods=["POST"])
def connect_peer(headers="guest", body="anonymous"):
    # Connect to a remote peer and check if it is reachable (Task 2.3).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] connect_peer body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    target_ip = data.get("ip", "127.0.0.1")
    target_port = data.get("port", "2026")

    # Build a lightweight health request to the remote peer's local backend.
    raw_request = (
        "GET /peer-info HTTP/1.1\r\n"
        "Host: {}:{}\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).format(target_ip, target_port)

    response = send_to_peer(target_ip, target_port, raw_request.encode())

    if response:
        # Extract JSON body from HTTP response
        try:
            parts = response.decode("utf-8", errors="replace").split("\r\n\r\n", 1)
            remote_data = json.loads(parts[1]) if len(parts) > 1 else {}
            result = {
                "status": "ok",
                "peer_alive": True,
                "remote_peer": remote_data.get("peer", {}),
                "message": "Peer at {}:{} is online".format(target_ip, target_port),
            }
        except Exception as e:
            result = {
                "status": "ok",
                "peer_alive": True,
                "remote_peer": {},
                "message": "Connected to {}:{}".format(target_ip, target_port),
            }
    else:
        result = {
            "status": "error",
            "peer_alive": False,
            "message": "Could not connect to {}:{}".format(target_ip, target_port),
        }

    return json.dumps(result).encode("utf-8")


@app.route("/peer-info", methods=["GET"])
def peer_info(headers="guest", body="anonymous"):
    result = {
        "status": "ok",
        "peer": {
            "message": "local peer backend is reachable",
        },
    }
    return json.dumps(result).encode("utf-8")


@app.route("/send-peer", methods=["POST"])
def send_peer(headers="guest", body="anonymous"):
    # Send a direct message to a specific peer (Task 2.3 — Chat Phase).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] send_peer body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    to_user = data.get("to", "")
    msg_text = data.get("msg", "")
    sender = data.get("from", "anonymous")

    # Find target peer in registry (or use explicit ip/port)
    target_ip = data.get("ip", "")
    target_port = data.get("port", "")

    if not target_ip or not target_port:
        # Look up peer by username
        with _lock:
            for p in peer_list:
                if p.get("username") == to_user:
                    target_ip = p["ip"]
                    target_port = p["port"]
                    break

    if not target_ip:
        result = {"status": "error", "message": "Peer '{}' not found".format(to_user)}
        return json.dumps(result).encode("utf-8")

    # Record message locally so poller can show it immediately (no optimistic render needed)
    entry = {"from": sender, "to": to_user, "msg": msg_text, "ts": time.time()}
    with _lock:
        messages.append(entry)

    # browser gets a response immediately without waiting for Bob's server.
    def _deliver_async(ip, port, s, m):
        """Background worker: send message to peer TCP, do not block caller."""
        msg_payload = json.dumps({"from": s, "msg": m})
        raw_request = (
            "POST /receive-message HTTP/1.1\r\n"
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{}"
        ).format(ip, port, len(msg_payload), msg_payload)
        send_to_peer(ip, port, raw_request.encode())

    t = threading.Thread(
        target=_deliver_async,
        args=(target_ip, target_port, sender, msg_text),
        daemon=True,
    )
    t.start()

    # Return immediately — browser shows the message via polling within 2 s
    result = {
        "status": "ok",
        "message": "Message queued for {} at {}:{}".format(
            to_user, target_ip, target_port
        ),
        "queued": True,
        "delivered": False,
    }
    return json.dumps(result).encode("utf-8")


# ---------------------------------------------------------------------------


@app.route("/broadcast-peer", methods=["POST"])
def broadcast_peer(headers="guest", body="anonymous"):
    """Broadcast a message to ALL known peers (Task 2.3 — Chat Phase).

    Fan-out: iterates the peer_list and calls send_to_peer for each one.
    Uses a thread per peer so the broadcast is non-blocking.

    Accepts JSON body: {"msg": "hello everyone", "from": "alice"}

    :param headers: HTTP headers.
    :param body: JSON body.
    :returns (bytes): JSON summary of delivery attempts.
    """
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] broadcast_peer body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    msg_text = data.get("msg", "")
    sender = data.get("from", "anonymous")

    # Record in local message log
    entry = {"from": sender, "to": "broadcast", "msg": msg_text, "ts": time.time()}
    request_peers = data.get("peers")
    if isinstance(request_peers, list):
        source_peers = request_peers
    else:
        with _lock:
            source_peers = list(peer_list)

    with _lock:
        messages.append(entry)

    # Never connect back to the sender's own peer entry. In callback mode
    # that would deadlock until timeout because this request is still being handled.
    targets = [
        p
        for p in source_peers
        if p.get("username") != sender and p.get("ip") and p.get("port")
    ]

    delivered = []
    failed = []

    def _send(peer):
        """Inner worker to send to one peer in its own thread."""
        msg_payload = json.dumps({"from": sender, "msg": msg_text})
        raw_request = (
            "POST /receive-message HTTP/1.1\r\n"
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{}"
        ).format(peer["ip"], peer["port"], len(msg_payload), msg_payload)

        resp = send_to_peer(peer["ip"], peer["port"], raw_request.encode())
        with _lock:
            if resp:
                delivered.append(peer["username"])
            else:
                failed.append(peer["username"])

    threads = []
    for peer in targets:
        t = threading.Thread(target=_send, args=(peer,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=5)

    result = {
        "status": "ok",
        "message": "Broadcast sent",
        "delivered": delivered,
        "failed": failed,
        "total_peers": len(targets),
    }
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Message log


@app.route("/get-messages", methods=["GET"])
def get_messages(headers="guest", body="anonymous"):

    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] get_messages called")
    with _lock:
        msg_snapshot = list(messages)
    result = {
        "status": "ok",
        "messages": msg_snapshot,
        "count": len(msg_snapshot),
    }
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Channel management


@app.route("/get-channels", methods=["GET"])
def get_channels(headers="guest", body="anonymous"):
    # Return all available channels and their member lists (Task 2.3).

    print("[SampleApp] get_channels called")
    with _lock:
        ch_snapshot = {ch: list(members) for ch, members in channels.items()}
    result = {
        "status": "ok",
        "channels": ch_snapshot,
        "count": len(ch_snapshot),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/get-channel-messages", methods=["POST"])
def get_channel_messages(headers="guest", body="anonymous"):
    # Return messages belonging to a specific channel (Task 2.3).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] get_channel_messages body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = data.get("channel", "general")

    # Filter message log for messages addressed to this channel
    with _lock:
        ch_messages = [m for m in messages if m.get("to") == channel_name]

    result = {
        "status": "ok",
        "channel": channel_name,
        "messages": ch_messages,
        "count": len(ch_messages),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/broadcast-channel", methods=["POST"])
def broadcast_channel(headers="guest", body="anonymous"):
    # Broadcast a message to all peers in a specific channel (Task 2.3).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] broadcast_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = data.get("channel", "general")
    msg_text = data.get("msg", "")
    sender = data.get("from", "anonymous")

    # Record message locally with to=channel_name so /get-channel-messages returns it
    entry = {"from": sender, "to": channel_name, "msg": msg_text, "ts": time.time()}
    with _lock:
        messages.append(entry)
        targets = [p for p in peer_list if p.get("username") != sender]

    delivered = []
    failed = []

    def _send_channel(peer):
        """Background worker: deliver channel message to one peer."""
        msg_payload = json.dumps(
            {"from": sender, "channel": channel_name, "msg": msg_text}
        )
        raw_request = (
            "POST /receive-message HTTP/1.1\r\n"
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{}"
        ).format(peer["ip"], peer["port"], len(msg_payload), msg_payload)
        resp = send_to_peer(peer["ip"], peer["port"], raw_request.encode())
        if resp:
            delivered.append(peer.get("username"))
        else:
            failed.append(peer.get("username"))

    # Fire all deliveries concurrently in daemon threads
    threads = [
        threading.Thread(target=_send_channel, args=(p,), daemon=True) for p in targets
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)

    result = {
        "status": "ok",
        "channel": channel_name,
        "delivered": delivered,
        "failed": failed,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/leave-channel", methods=["DELETE"])
def leave_channel(headers="guest", body="anonymous"):
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] leave_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = data.get("channel", "general")
    username = data.get("username", "")

    with _lock:
        if channel_name in channels:
            channels[channel_name] = [
                p for p in channels[channel_name] if p.get("username") != username
            ]
            if not channels[channel_name]:
                del channels[channel_name]

    result = {
        "status": "ok",
        "message": "{} left channel {}".format(username, channel_name),
    }
    return json.dumps(result).encode("utf-8")


# Receive incoming P2P message (called by other peers)


@app.route("/receive-message", methods=["POST"])
def receive_message(headers="guest", body="anonymous"):

    print("[SampleApp] receive_message body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    sender = data.get("from", "unknown")
    msg_text = data.get("msg", "")
    channel = data.get("channel", None)

    entry = {
        "from": sender,
        "to": channel if channel else "me",
        "msg": msg_text,
        "ts": time.time(),
    }
    with _lock:
        messages.append(entry)

    print("[SampleApp] Received from {}: {}".format(sender, msg_text))
    result = {"status": "ok", "ack": "message received"}
    return json.dumps(result).encode("utf-8")


# Entry point

TRACKER_ROUTE_PATHS = {
    "/login",
    "/hello",
    "/echo",
    "/client-info",
    "/submit-info",
    "/get-list",
}

PEER_ROUTE_PATHS = {
    "/login",
    "/hello",
    "/echo",
    "/local-info",
    "/peer-info",
    "/add-list",
    "/connect-peer",
    "/send-peer",
    "/broadcast-peer",
    "/get-messages",
    "/get-channels",
    "/get-channel-messages",
    "/broadcast-channel",
    "/leave-channel",
    "/receive-message",
}


def configure_routes_for_role(role):
    role = (role or "").lower()

    if role == "tracker":
        allowed_paths = TRACKER_ROUTE_PATHS
    elif role == "peer":
        allowed_paths = PEER_ROUTE_PATHS
    else:
        raise ValueError("Unknown sample app role: {}".format(role))

    app.routes = {
        key: handler for key, handler in app.routes.items() if key[1] in allowed_paths
    }
    print(
        "[SampleApp] Running in {} role with routes: {}".format(
            role,
            sorted("{} {}".format(method, path) for method, path in app.routes.keys()),
        )
    )


def create_sampleapp(ip, port, role):

    configure_routes_for_role(role)
    app.prepare_address(ip, port)
    app.run()
