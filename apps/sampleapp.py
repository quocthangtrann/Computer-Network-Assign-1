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
  POST /create-channel
  POST /rename-channel
  DELETE /delete-channel
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
import re
import selectors
import asyncio

from daemon import AsynapRous

# ---------------------------------------------------------------------------
# In-memory stores (peer list, sessions, messages, channels)
# ---------------------------------------------------------------------------

# Registered peers: list of {
#   "username": str, "ip": str, "port": str,
#   "online": bool, "last_seen": float, "online_since": float
# }
peer_list = []

# Active sessions: {token: username}
# Populated when a user logs in (/login) and used by cookie auth.
sessions = {}

# Message log: list of {"from": str, "msg": str, "ts": float}
messages = []

# Sender-side outgoing queue for direct messages that could not be delivered.
outbox_messages = []

# Backup queue for direct messages held on behalf of another sender.
held_messages = []

# Channels: {channel_name: [{"username": str, "ip": str, "port": str}, ...]}
channels = {"general": []}

# Channel metadata catalog. The tracker is the authoritative catalog, while
# peer backends keep a local copy for reconciliation and history sync.
channel_catalog = {
    "general": {
        "name": "general",
        "version": 1,
        "updated_at": time.time(),
        "deleted": False,
        "members": [],
    }
}

# Thread lock for shared state mutations
_lock = threading.RLock()

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


def parse_cookies(headers):
    cookies = {}
    cookie_str = headers.get("cookie", "")
    if cookie_str:
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                cookies[k.strip()] = v.strip()
    return cookies

def generate_session_token():
    """Generate a cryptographically random session token.

    Uses uuid4 (128-bit random) which is sufficiently unpredictable for a
    course project. Production systems would use secrets.token_hex().

    :returns (str): Hex string session token.
    """
    return uuid.uuid4().hex


def validate_session(headers):
    token = get_session_token(headers)
    with _lock:
        return sessions.get(token, None)


def get_session_token(headers):
    return parse_cookies(headers).get("sessionid", "")


def validate_basic_auth(headers):

    auth_header = headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            import base64
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


def find_peer(username):
    """Return the mutable peer registry entry for a username.

    The peer registry is shared by tracker and peer-role processes. Tracker
    uses it as the global directory with presence state, while peer backends
    use it as their local routing cache for direct sends and retry delivery.

    :param username: Peer username to find.
    :returns: The peer dictionary stored in ``peer_list`` or ``None``.
    """
    with _lock:
        for peer in peer_list:
            if peer.get("username") == username:
                return peer
    return None


def upsert_peer(username, peer_ip, peer_port, online=True):
    """Insert or update a peer presence record.

    Registration should not create duplicate users. This helper keeps one
    record per username, updates the latest reachable IP/port, records online
    state, refreshes ``last_seen``, and tracks the current online session start
    time.

    :param username: Authenticated username being registered.
    :param peer_ip: Reachable LAN IP for the user's peer backend.
    :param peer_port: Reachable port for the user's peer backend.
    :param online: Whether the peer should be marked online.
    :returns: ``(peer_copy, was_online)`` for response and broadcast decisions.
    """
    with _lock:
        now = time.time()
        peer = find_peer(username)
        was_online = bool(peer and peer.get("online"))

        if peer is None:
            peer = {
                "username": username,
                "ip": peer_ip,
                "port": peer_port,
                "online": bool(online),
                "last_seen": now,
                "online_since": now if online else 0,
            }
            peer_list.append(peer)
        else:
            peer["ip"] = peer_ip or peer.get("ip", "")
            peer["port"] = peer_port or peer.get("port", "")
            if online and not was_online:
                peer["online_since"] = now
            peer["online"] = bool(online)
            peer["last_seen"] = now

        return dict(peer), was_online


def mark_user_offline(username):
    """Mark a user offline without deleting their peer profile.

    Logout should keep the user visible in the Peers list so other users can
    still select them and queue direct messages. Channel membership is
    removed because channels represent currently joined online participants.

    :param username: User leaving the application.
    :returns: Count of presence/channel records changed.
    """
    if not username:
        return 0

    changed = 0
    with _lock:
        peer = find_peer(username)
        if peer and peer.get("online"):
            peer["online"] = False
            peer["last_seen"] = time.time()
            changed += 1

        for channel_name in list(channels.keys()):
            before_members = len(channels[channel_name])
            channels[channel_name] = [
                p for p in channels[channel_name] if p.get("username") != username
            ]
            changed += before_members - len(channels[channel_name])
            if channel_name != "general" and not channels[channel_name]:
                del channels[channel_name]
            if channel_name in channel_catalog:
                channel_catalog[channel_name]["members"] = list(
                    channels.get(channel_name, [])
                )

    return changed


def parse_peer_response(response):
    """Parse a raw HTTP response from another peer backend.

    Peer-to-peer delivery uses ``send_to_peer`` with manually built HTTP
    payloads. This helper extracts the numeric HTTP status and JSON body so the
    caller can decide whether a delivery, backup-store, or ACK succeeded.

    :param response: Raw HTTP response bytes from ``send_to_peer``.
    :returns: ``(status_code, json_body)`` or ``(0, {})`` on parse failure.
    """
    if not response:
        return 0, {}

    try:
        text = response.decode("utf-8", errors="replace")
        status_line = text.split("\r\n", 1)[0]
        parts = status_line.split()
        status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
        _, body = text.split("\r\n\r\n", 1)
        data = json.loads(body) if body else {}
        return status, data
    except Exception:
        return 0, {}


def build_json_post(path, host, port, payload):
    """Build a raw HTTP POST request containing a JSON payload.

    The project intentionally avoids third-party HTTP clients for backend
    communication. Peer backends therefore exchange raw TCP bytes, and this
    function centralizes the HTTP framing used by direct delivery, backup
    storage, and presence-event notifications.

    :param path: Request path on the remote peer backend.
    :param host: Host header value and target host.
    :param port: Host header port and target port.
    :param payload: JSON-serializable request body.
    :returns: UTF-8 encoded raw HTTP request bytes.
    """
    body = json.dumps(payload)
    return (
        (
            "POST {} HTTP/1.1\r\n"
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{}"
        )
        .format(path, host, port, len(body), body)
        .encode("utf-8")
    )


async def deliver_direct_message(peer, message):
    """Attempt one direct peer-to-peer delivery.

    This is the core live-chat path. It sends a direct message from this local
    peer backend to the recipient's ``/receive-message`` endpoint. The message
    is sent as plaintext JSON.

    :param peer: Target peer record with ``ip`` and ``port``.
    :param message: Message record containing id/from/to/msg metadata.
    :returns: ``True`` only when the remote peer returns a successful JSON ACK.
    """
    payload = {
        "id": message["id"],
        "from": message["from"],
        "to": message["to"],
        "msg": message["msg"],
        "type": "direct",
        "created_at": message.get("created_at", message.get("ts", time.time())),
    }
    response = await send_to_peer(
        peer["ip"],
        peer["port"],
        build_json_post("/receive-message", peer["ip"], peer["port"], payload),
    )
    status, data = parse_peer_response(response)
    return bool(response and status < 400 and data.get("status") == "ok")


def choose_backup_peer(sender, recipient):
    """Choose one online peer to hold a backup direct message.

    Backup peers are best-effort relays. They should not be the sender or final
    recipient, and they must have a reachable IP/port. The first eligible peer
    is enough for this assignment feature; the design intentionally avoids
    complex replica selection or multi-hop routing.

    :param sender: Username that created the queued message.
    :param recipient: Final target username.
    :returns: A copy of the chosen peer record or ``None``.
    """
    with _lock:
        for peer in peer_list:
            if (
                peer.get("online")
                and peer.get("ip")
                and peer.get("port")
                and peer.get("username") not in (sender, recipient)
            ):
                return dict(peer)
    return None


async def broadcast_peer_online_event(peer_entry, exclude_username=None):
    """Notify currently online peers that a user has come online.

    This is the tracker-side trigger for retry-on-presence. When a user's
    registration transitions from offline to online, the tracker sends a
    ``peer-online`` event to all other online peers. Those peers then retry only
    messages addressed to that username instead of wasting resources polling.

    :param peer_entry: Newly online peer record.
    :param exclude_username: Username to skip, normally the user who just joined.
    """
    event = {
        "type": "peer-online",
        "username": peer_entry.get("username"),
        "ip": peer_entry.get("ip"),
        "port": peer_entry.get("port"),
        "last_seen": peer_entry.get("last_seen", time.time()),
    }
    with _lock:
        targets = [
            dict(peer)
            for peer in peer_list
            if peer.get("online")
            and peer.get("username") != exclude_username
            and peer.get("ip")
            and peer.get("port")
        ]

    async def _send_all():
        async def _send(peer):
            try:
                reader, writer = await asyncio.open_connection(peer["ip"], int(peer["port"]))
                raw_request = build_json_post("/presence-event", peer["ip"], peer["port"], event)
                writer.write(raw_request)
                await writer.drain()
                resp = await reader.read()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        
        tasks = [_send(p) for p in targets]
        if tasks:
            await asyncio.gather(*tasks)

    if targets:
        await _send_all()


async def retry_outbox_for(username, peer):
    """Retry local sender-outbox messages for a newly online user.

    The sender-side queue stores direct messages that could not be delivered
    immediately. Presence events call this function with the recipient's latest
    IP/port. Successfully ACKed messages are removed from the outbox and the
    local message log is updated to ``delivery_status=delivered``.

    :param username: Recipient username that just came online.
    :param peer: Latest peer record for that recipient.
    :returns: Number of outbox messages delivered.
    """
    delivered = 0
    with _lock:
        candidates = [m for m in outbox_messages if m.get("to") == username]

    for message in candidates:
        message["attempt_count"] = message.get("attempt_count", 0) + 1
        if await deliver_direct_message(peer, message):
            with _lock:
                outbox_messages[:] = [
                    m for m in outbox_messages if m.get("id") != message.get("id")
                ]
                for local_msg in messages:
                    if local_msg.get("id") == message.get("id"):
                        local_msg["delivery_status"] = "delivered"
            delivered += 1

    return delivered


async def retry_held_for(username, peer):
    """Retry backup-held messages for a newly online recipient.

    A backup peer keeps messages on behalf of another sender.
    When the recipient comes online, the backup peer attempts delivery directly
    to the recipient and deletes each held copy only after a successful ACK.
    Expired backup entries are also removed during this pass.

    :param username: Recipient username that just came online.
    :param peer: Latest peer record for that recipient.
    :returns: Number of held backup messages delivered.
    """
    delivered = 0
    now = time.time()
    with _lock:
        candidates = [
            m
            for m in held_messages
            if m.get("to") == username and m.get("expires_at", 0) > now
        ]

    for message in candidates:
        message["attempt_count"] = message.get("attempt_count", 0) + 1
        if await deliver_direct_message(peer, message):
            with _lock:
                held_messages[:] = [
                    m for m in held_messages if m.get("id") != message.get("id")
                ]
            delivered += 1

    with _lock:
        held_messages[:] = [m for m in held_messages if m.get("expires_at", 0) > now]

    return delivered


# P2P message helper


async def send_to_peer(peer_ip, peer_port, payload_bytes):
    try:
        reader, writer = await asyncio.open_connection(peer_ip, int(peer_port))
        writer.write(payload_bytes)
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
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


def normalize_channel_name(name):
    name = str(name or "").strip().lower()
    name = re.sub(r"^#+", "", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9_-]", "", name)
    return name


def ensure_channel_record(channel_name):
    """Ensure a channel has both member-list and metadata records.

    Channel state is split on purpose:
    - ``channels`` tracks local peer membership and is used for live P2P fanout.
    - ``channel_catalog`` tracks metadata that the tracker can publish to peers.

    This helper is called before mutating or reading channel metadata so older
    code paths that only created ``channels[name]`` still get a matching
    metadata record.
    """
    channel_name = normalize_channel_name(channel_name) or "general"
    channels.setdefault(channel_name, [])
    if channel_name not in channel_catalog:
        channel_catalog[channel_name] = {
            "name": channel_name,
            "version": 1,
            "updated_at": time.time(),
            "deleted": False,
            "members": list(channels.get(channel_name, [])),
        }
    return channel_catalog[channel_name]


def touch_channel_record(channel_name, deleted=False, extra=None):
    with _lock:
        channel_name = normalize_channel_name(channel_name) or "general"
        if deleted and channel_name not in channels:
            record = channel_catalog.setdefault(
                channel_name,
                {
                    "name": channel_name,
                    "version": 0,
                    "updated_at": time.time(),
                    "deleted": False,
                    "members": [],
                },
            )
        else:
            record = ensure_channel_record(channel_name)
        record["version"] = int(record.get("version", 0)) + 1
        record["updated_at"] = time.time()
        record["deleted"] = bool(deleted)
        record["members"] = list(channels.get(channel_name, []))
        if extra:
            record.update(extra)
        return dict(record)


def channel_metadata_snapshot(include_deleted=True):
    with _lock:
        ensure_channel_record("general")
        snapshot = {}
        for name, record in channel_catalog.items():
            if record.get("deleted") and not include_deleted:
                continue
            copied = dict(record)
            copied["members"] = list(copied.get("members", []))
            snapshot[name] = copied
        return snapshot


def channel_messages_for(channel_name):
    """Return sorted local history for one channel.

    History remains peer-owned. The tracker only stores metadata, so peer
    backends answer history-sync requests from their own ``messages`` list.
    Sorting by ``(ts, id)`` makes cursor-based sync deterministic even when two
    messages share the same timestamp.
    """
    return sorted(
        [
            m
            for m in messages
            if m.get("type") == "channel" and m.get("channel") == channel_name
        ],
        key=lambda m: (m.get("ts") or 0, m.get("id", "")),
    )


def latest_channel_state(channel_name):
    """Summarize local history freshness for peer selection.

    Returning users ask online peers for this lightweight summary before
    downloading any history. The client chooses the peer with the newest
    ``latest_ts`` and largest ``message_count``, then fetches only the missing
    messages from that peer.
    """
    ch_messages = channel_messages_for(channel_name)
    latest = ch_messages[-1] if ch_messages else {}
    return {
        "channel": channel_name,
        "has_channel": channel_name in channels
        and not channel_catalog.get(channel_name, {}).get("deleted", False),
        "latest_ts": latest.get("ts", 0),
        "latest_id": latest.get("id", ""),
        "message_count": len(ch_messages),
        "version": channel_catalog.get(channel_name, {}).get("version", 0),
    }


async def announce_channel_event(
    event_type, channel_name, username, member, peers, extra=None
):
    targets = [
        p
        for p in peers
        if p.get("username") != username and p.get("ip") and p.get("port")
        and p.get("online", True) is not False
    ]
    async def _send_all():
        async def _send(peer):
            try:
                reader, writer = await asyncio.open_connection(peer["ip"], int(peer["port"]))
                
                payload = {
                    "type": event_type,
                    "channel": channel_name,
                    "from": username,
                    "creator": member,
                }
                if extra:
                    payload.update(extra)
                msg_payload = json.dumps(payload)
                raw_request = (
                    "POST /receive-channel HTTP/1.1\r\n"
                    "Host: {}:{}\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: {}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    "{}"
                ).format(peer["ip"], peer["port"], len(msg_payload), msg_payload)
                
                writer.write(raw_request.encode())
                await writer.drain()
                
                # Simple read to ensure ACK
                resp = await reader.read(4096)
                writer.close()
                await writer.wait_closed()
                
                if resp:
                    return peer["username"], True
                return peer["username"], False
            except Exception:
                return peer["username"], False

        tasks = [_send(p) for p in targets]
        if tasks:
            return await asyncio.gather(*tasks)
        return []

    delivered = []
    failed = []
    
    if targets:
        results = await _send_all()
        for peer_username, success in results:
            if success:
                delivered.append(peer_username)
            else:
                failed.append(peer_username)

    return delivered, failed


# AsynapRous application instance

app = AsynapRous()

# Task 2.2 — Login: session cookie auth (RFC 6265)


@app.route("/login", methods=["PUT", "POST"])
def login(headers, body):
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


@app.route("/logout", methods=["POST", "DELETE"])
def logout(headers, body):
    token = get_session_token(headers)
    username = None

    with _lock:
        if token:
            username = sessions.pop(token, None)

        if not username:
            username = validate_basic_auth(req)

        removed_presence = mark_user_offline(username)

    result = {
        "status": "ok",
        "message": "Logged out",
        "username": username,
        "removed_presence": removed_presence,
        "__set_cookie__": "sessionid=; Max-Age=0; Path=/; HttpOnly",
    }
    print(
        "[SampleApp] Logout for {} removed {} presence record(s)".format(
            username or "unknown",
            removed_presence,
        )
    )
    return json.dumps(result).encode("utf-8")


# Task 2.2 — Hello: protected route (cookie OR Basic Auth)


@app.route("/hello", methods=["POST", "PUT", "GET"])
def hello(headers, body):
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
def echo(req):
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
def client_info(headers, body):
    forwarded_for = headers.get("x-forwarded-for", "")
    real_ip = headers.get("x-real-ip", "")
    client_ip = forwarded_for.split(",", 1)[0].strip() or real_ip.strip()
    result = {
        "status": "ok",
        "client_ip": client_ip,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/local-info", methods=["POST", "GET"])
def local_info(req):
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
async def submit_info(headers, body):
    # Register a peer's network information with the tracker (Task 2.3).
    auth_user = require_auth(headers)
    if not auth_user:
        return unauthorized_result()

    print("[SampleApp] submit_info body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    username = auth_user
    peer_ip = data.get("ip", "")
    peer_port = data.get("port", "")

    if not peer_ip or not peer_port:
        result = {"status": "error", "message": "ip and port are required"}
        return json.dumps(result).encode("utf-8")

    with _lock:
        peer_entry, was_online = upsert_peer(
            username,
            peer_ip,
            peer_port,
            online=True,
        )

    if not was_online:
        await broadcast_peer_online_event(peer_entry, exclude_username=username)

    print("[SampleApp] Registered peer: {}".format(peer_entry))
    result = {
        "status": "ok",
        "message": "Peer '{}' registered at {}:{}".format(username, peer_ip, peer_port),
        "peer": peer_entry,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/get-list", methods=["GET"])
def get_list(headers, body):
    # Return the list of all registered peers (Task 2.3 — Initialization Phase).
    if not require_auth(headers):
        return unauthorized_result()

    with _lock:
        total_peers = len(peer_list)
    print("[SampleApp] get_list called, {} peers".format(total_peers))
    with _lock:
        peers = [dict(peer) for peer in peer_list]
    result = {
        "status": "ok",
        "peers": peers,
        "count": len(peers),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/add-list", methods=["POST"])
def add_list(headers, body):
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

    peer_entry = {
        "username": username,
        "ip": peer_ip,
        "port": peer_port,
        "online": bool(data.get("online", True)),
        "last_seen": data.get("last_seen", time.time()),
        "online_since": data.get("online_since", 0),
    }
    with _lock:
        current = find_peer(username)
        if current:
            current.update(peer_entry)
        else:
            peer_list.append(peer_entry)
        total_peers = len(peer_list)

    result = {"status": "ok", "added": peer_entry, "total": total_peers}
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Chat Phase: direct peer-to-peer messaging


@app.route("/connect-peer", methods=["POST"])
async def connect_peer(headers, body):
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

    response = await send_to_peer(target_ip, target_port, raw_request.encode())

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
def peer_info(req):
    result = {
        "status": "ok",
        "peer": {
            "message": "local peer backend is reachable",
        },
    }
    return json.dumps(result).encode("utf-8")


@app.route("/send-peer", methods=["POST"])
async def send_peer(headers, body):
    """Send, queue, or back up one direct peer-to-peer message.

    The browser sends plaintext direct messages to its own local peer backend.
    This route first records the message locally so the sender can see it in
    history, then attempts direct delivery to the recipient peer. If the
    recipient is offline or unreachable, the message is stored in this peer's
    in-memory sender outbox and one online backup peer is asked to hold the
    message as a best-effort relay.

    :param headers: HTTP request headers; must authenticate the sender.
    :param body: JSON message payload with id, to, msg, and peer info.
    :returns: JSON result describing delivered/queued state and backup holder.
    """
    auth_user = require_auth(headers)
    if not auth_user:
        return unauthorized_result()

    print("[SampleApp] send_peer body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    to_user = data.get("to", "")
    msg_text = data.get("msg", "")
    sender = auth_user
    message_id = data.get("id") or uuid.uuid4().hex
    created_at = data.get("created_at") or time.time()

    # Find target peer in registry (or use explicit ip/port)
    target_ip = data.get("ip", "")
    target_port = data.get("port", "")
    target_online = data.get("online", True)
    target_peer = None

    if not target_ip or not target_port:
        # Look up peer by username
        with _lock:
            for p in peer_list:
                if p.get("username") == to_user:
                    target_peer = dict(p)
                    target_ip = p["ip"]
                    target_port = p["port"]
                    target_online = p.get("online", True)
                    break
    else:
        target_peer = {
            "username": to_user,
            "ip": target_ip,
            "port": target_port,
            "online": bool(target_online),
        }

    if not target_ip:
        result = {"status": "error", "message": "Peer '{}' not found".format(to_user)}
        return json.dumps(result).encode("utf-8")

    # Record message locally so poller can show it immediately (no optimistic render needed)
    entry = {
        "id": message_id,
        "from": sender,
        "to": to_user,
        "msg": msg_text,
        "type": "direct",
        "created_at": created_at,
        "ts": created_at,
        "delivery_status": "sending" if target_online else "queued",
    }
    with _lock:
        messages.append(entry)

    delivered = False
    if target_online and target_peer:
        delivered = await deliver_direct_message(target_peer, entry)

    backup_holder = None
    if delivered:
        entry["delivery_status"] = "delivered"
    else:
        entry["delivery_status"] = "queued"
        outbox_entry = dict(entry)
        outbox_entry.update(
            {
                "target_ip": target_ip,
                "target_port": target_port,
                "status": "queued",
                "attempt_count": 0,
            }
        )

        with _lock:
            outbox_messages[:] = [
                m for m in outbox_messages if m.get("id") != message_id
            ]
            outbox_messages.append(outbox_entry)

        backup_peer = choose_backup_peer(sender, to_user)
        if backup_peer:
            backup_payload = dict(outbox_entry)
            backup_payload["expires_at"] = time.time() + 3600
            response = await send_to_peer(
                backup_peer["ip"],
                backup_peer["port"],
                build_json_post(
                    "/store-backup-message",
                    backup_peer["ip"],
                    backup_peer["port"],
                    backup_payload,
                ),
            )
            status, data = parse_peer_response(response)
            if response and status < 400 and data.get("status") == "ok":
                backup_holder = backup_peer.get("username")
                outbox_entry["backup_holder"] = backup_holder
                entry["backup_holder"] = backup_holder

    # Return immediately — browser shows the message via polling within 2 s
    result = {
        "status": "ok",
        "message": "Message {} for {}".format(
            "delivered" if delivered else "queued",
            to_user,
        ),
        "id": message_id,
        "queued": not delivered,
        "delivered": delivered,
        "backup_holder": backup_holder,
    }
    return json.dumps(result).encode("utf-8")


# ---------------------------------------------------------------------------


@app.route("/broadcast-peer", methods=["POST"])
async def broadcast_peer(headers, body):
    """Broadcast a message to ALL known peers (Task 2.3 — Chat Phase).

    Fan-out: iterates the peer_list and sends to all peers concurrently
    using non-blocking asyncio.gather.
    
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

    with _lock:
        request_peers = data.get("peers")
        if isinstance(request_peers, list):
            source_peers = [dict(p) for p in request_peers]
        else:
            source_peers = [dict(p) for p in peer_list]

    # Keep a local copy so the sender sees what they sent, but do not
    # network-deliver it back to the sender.
    entry = {
        "from": sender,
        "to": "broadcast",
        "msg": msg_text,
        "type": "broadcast",
        "ts": time.time(),
    }
    with _lock:
        messages.append(entry)

    # Never connect back to the sender's own peer entry. In callback mode
    # that would deadlock until timeout because this request is still being handled.
    targets = [
        p
        for p in source_peers
        if p.get("username") != sender and p.get("ip") and p.get("port")
        and p.get("online", True) is not False
    ]

    async def _send_all():
        async def _send(peer):
            try:
                reader, writer = await asyncio.open_connection(peer["ip"], int(peer["port"]))
                
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
                
                writer.write(raw_request.encode())
                await writer.drain()
                
                resp = await reader.read(4096)
                writer.close()
                await writer.wait_closed()
                
                if resp:
                    return peer["username"], True
                return peer["username"], False
            except Exception:
                return peer["username"], False

        tasks = [_send(p) for p in targets]
        if tasks:
            return await asyncio.gather(*tasks)
        return []

    delivered = []
    failed = []
    
    if targets:
        results = await _send_all()
        for peer_username, success in results:
            if success:
                delivered.append(peer_username)
            else:
                failed.append(peer_username)

    result = {
        "status": "ok",
        "message": "Broadcast sent to other peers",
        "delivered": delivered,
        "failed": failed,
        "total_peers": len(targets),
    }
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Message log


@app.route("/get-messages", methods=["GET"])
def get_messages(headers, body):

    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] get_messages called")
    with _lock:
        msg_snapshot = [
            dict(m) for m in messages if m.get("type") != "channel" and not m.get("channel")
        ]
    msg_snapshot.sort(
        key=lambda m: (m.get("created_at") or m.get("ts") or 0, m.get("id", ""))
    )
    result = {
        "status": "ok",
        "messages": msg_snapshot,
        "count": len(msg_snapshot),
    }
    return json.dumps(result).encode("utf-8")


# Task 2.3 — Channel management


@app.route("/get-channels", methods=["GET"])
def get_channels(headers, body):
    """Return tracker channel metadata used by browser reconciliation.

    ``channels`` contains only active, visible channels for compatibility with
    older frontend callers. ``metadata`` also includes deleted/renamed channel
    tombstones so offline clients can correct stale local state after login.
    """
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] get_channels called")
    with _lock:
        metadata = {name: dict(record) for name, record in channel_metadata_snapshot(include_deleted=True).items()}
        active_metadata = {
            name: record for name, record in metadata.items() if not record.get("deleted")
        }
    result = {
        "status": "ok",
        "channels": active_metadata,
        "metadata": metadata,
        "names": sorted(active_metadata.keys()),
        "count": len(active_metadata),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/create-channel", methods=["POST"])
async def create_channel(headers, body):
    """Create or re-activate a channel and optionally announce it to peers.

    The local peer uses this route to update its own backend and send a P2P
    channel-created event. The browser then calls the tracker with
    ``announce=False`` so the tracker becomes the metadata source of truth
    without routing live messages.
    """
    auth_user = require_auth(headers)
    if not auth_user:
        return unauthorized_result()

    print("[SampleApp] create_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = normalize_channel_name(data.get("channel") or data.get("name"))
    if not channel_name or channel_name == "direct":
        result = {"status": "error", "message": "Invalid channel name"}
        return json.dumps(result).encode("utf-8")

    username = data.get("username") or auth_user
    member = {
        "username": username,
        "ip": data.get("ip", ""),
        "port": data.get("port", ""),
    }

    request_peers = data.get("peers")
    with _lock:
        # Keep exactly one membership entry per username. This avoids duplicate
        # fanout targets when a user registers again with a new LAN IP/port.
        members = channels.setdefault(channel_name, [])
        if username:
            members[:] = [m for m in members if m.get("username") != username]
            members.append(member)
        # Bump metadata after membership changes so the tracker/client can see
        # that this channel changed while someone may have been offline.
        metadata = touch_channel_record(channel_name, deleted=False)
        source_peers = (
            request_peers if isinstance(request_peers, list) else list(peer_list)
        )

    delivered = []
    failed = []
    if data.get("announce", True) is not False:
        delivered, failed = await announce_channel_event(
            "channel-created",
            channel_name,
            username,
            member,
            source_peers,
        )

    result = {
        "status": "ok",
        "channel": channel_name,
        "metadata": metadata,
        "message": "Channel #{} is available".format(channel_name),
        "delivered": delivered,
        "failed": failed,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/rename-channel", methods=["POST"])
async def rename_channel(headers, body):
    """Rename a channel while preserving history and offline reconciliation.

    The old channel is not simply forgotten. It becomes a deleted metadata
    tombstone with ``renamed_to`` so offline users can map their old IndexedDB
    history to the new channel name when they come back.
    """
    auth_user = require_auth(headers)
    if not auth_user:
        return unauthorized_result()

    print("[SampleApp] rename_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    old_name = normalize_channel_name(data.get("old_channel") or data.get("channel"))
    new_name = normalize_channel_name(data.get("new_channel") or data.get("name"))
    if not old_name or not new_name or old_name == "general" or new_name == "direct":
        result = {"status": "error", "message": "Invalid channel rename"}
        return json.dumps(result).encode("utf-8")

    username = data.get("username") or auth_user
    member = {
        "username": username,
        "ip": data.get("ip", ""),
        "port": data.get("port", ""),
    }
    request_peers = data.get("peers")

    with _lock:
        old_members = channels.pop(old_name, [])
        # Store a tombstone for the old name before creating the new name. This
        # is what lets clients distinguish "deleted" from "renamed".
        touch_channel_record(
            old_name,
            deleted=True,
            extra={"renamed_to": new_name, "members": list(old_members)},
        )
        # Merge old members into the new channel, preserving any existing
        # members and avoiding duplicates by username.
        merged_members = channels.setdefault(new_name, [])
        seen_users = {m.get("username") for m in merged_members}
        for old_member in old_members:
            if old_member.get("username") not in seen_users:
                merged_members.append(old_member)
                seen_users.add(old_member.get("username"))
        if username:
            merged_members[:] = [
                m for m in merged_members if m.get("username") != username
            ]
            merged_members.append(member)

        # Local peer history is peer-owned, so rename any messages this backend
        # already has. Browser IndexedDB is renamed separately in index.js.
        for msg in messages:
            if msg.get("type") == "channel" and msg.get("channel") == old_name:
                msg["channel"] = new_name
                if msg.get("to") == old_name:
                    msg["to"] = new_name

        metadata = touch_channel_record(
            new_name,
            deleted=False,
            extra={"previous_name": old_name},
        )

        source_peers = (
            request_peers if isinstance(request_peers, list) else list(peer_list)
        )

    delivered = []
    failed = []
    if data.get("announce", True) is not False:
        delivered, failed = await announce_channel_event(
            "channel-renamed",
            old_name,
            username,
            member,
            source_peers,
            {"new_channel": new_name},
        )

    result = {
        "status": "ok",
        "old_channel": old_name,
        "channel": new_name,
        "metadata": metadata,
        "delivered": delivered,
        "failed": failed,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/delete-channel", methods=["DELETE", "POST"])
async def delete_channel(headers, body):
    """Delete a channel locally and publish a metadata tombstone.

    Deleting removes active membership and local channel messages, but the
    metadata record remains with ``deleted=True``. That tombstone is required so
    users who were offline during deletion can hide the channel on next sync.
    """
    auth_user = require_auth(headers)
    if not auth_user:
        return unauthorized_result()

    print("[SampleApp] delete_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = normalize_channel_name(data.get("channel", ""))
    if not channel_name or channel_name == "general":
        result = {"status": "error", "message": "Cannot delete this channel"}
        return json.dumps(result).encode("utf-8")

    username = data.get("username") or auth_user
    member = {
        "username": username,
        "ip": data.get("ip", ""),
        "port": data.get("port", ""),
    }
    request_peers = data.get("peers")

    with _lock:
        old_members = list(channels.get(channel_name, []))
        channels.pop(channel_name, None)
        metadata = touch_channel_record(
            channel_name,
            deleted=True,
            extra={"members": old_members},
        )
        messages[:] = [
            m
            for m in messages
            if not (m.get("type") == "channel" and m.get("channel") == channel_name)
        ]
        source_peers = (
            request_peers if isinstance(request_peers, list) else list(peer_list)
        )

    delivered = []
    failed = []
    if data.get("announce", True) is not False:
        delivered, failed = await announce_channel_event(
            "channel-deleted",
            channel_name,
            username,
            member,
            source_peers,
        )

    result = {
        "status": "ok",
        "channel": channel_name,
        "metadata": metadata,
        "delivered": delivered,
        "failed": failed,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/receive-channel", methods=["POST"])
def receive_channel(headers, body):
    """Apply a P2P channel metadata event from another peer.

    This route keeps peer backends roughly in sync for live operation. The
    tracker catalog still remains the authoritative repair source when a peer
    missed events while offline.
    """
    print("[SampleApp] receive_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    event_type = data.get("type", "channel-created")
    channel_name = normalize_channel_name(data.get("channel", ""))
    if not channel_name or channel_name == "direct":
        result = {"status": "error", "message": "Invalid channel name"}
        return json.dumps(result).encode("utf-8")

    if event_type == "channel-renamed":
        new_name = normalize_channel_name(data.get("new_channel", ""))
        if not new_name or channel_name == "general" or new_name == "direct":
            result = {"status": "error", "message": "Invalid channel rename"}
            return json.dumps(result).encode("utf-8")
        with _lock:
            old_members = channels.pop(channel_name, [])
            touch_channel_record(
                channel_name,
                deleted=True,
                extra={"renamed_to": new_name, "members": list(old_members)},
            )
            merged_members = channels.setdefault(new_name, [])
            seen_users = {m.get("username") for m in merged_members}
            for member in old_members:
                if member.get("username") not in seen_users:
                    merged_members.append(member)
                    seen_users.add(member.get("username"))
            for msg in messages:
                if msg.get("type") == "channel" and msg.get("channel") == channel_name:
                    msg["channel"] = new_name
                    if msg.get("to") == channel_name:
                        msg["to"] = new_name
            metadata = touch_channel_record(
                new_name,
                deleted=False,
                extra={"previous_name": channel_name},
            )
        result = {
            "status": "ok",
            "old_channel": channel_name,
            "channel": new_name,
            "metadata": metadata,
            "message": "Channel #{} renamed to #{}".format(channel_name, new_name),
        }
        return json.dumps(result).encode("utf-8")

    if event_type == "channel-deleted":
        if channel_name == "general":
            result = {"status": "error", "message": "Cannot delete this channel"}
            return json.dumps(result).encode("utf-8")
        with _lock:
            old_members = list(channels.get(channel_name, []))
            channels.pop(channel_name, None)
            metadata = touch_channel_record(
                channel_name,
                deleted=True,
                extra={"members": old_members},
            )
            messages[:] = [
                m
                for m in messages
                if not (m.get("type") == "channel" and m.get("channel") == channel_name)
            ]
        result = {
            "status": "ok",
            "channel": channel_name,
            "metadata": metadata,
            "message": "Channel #{} deleted".format(channel_name),
        }
        return json.dumps(result).encode("utf-8")

    creator = data.get("creator") or {}
    username = creator.get("username") or data.get("from", "")
    member = {
        "username": username,
        "ip": creator.get("ip", ""),
        "port": creator.get("port", ""),
    }

    with _lock:
        members = channels.setdefault(channel_name, [])
        if username:
            members[:] = [m for m in members if m.get("username") != username]
            members.append(member)
        metadata = touch_channel_record(channel_name, deleted=False)

    result = {
        "status": "ok",
        "channel": channel_name,
        "metadata": metadata,
        "message": "Channel #{} received".format(channel_name),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/get-channel-messages", methods=["POST"])
def get_channel_messages(headers, body):
    # Return messages belonging to a specific channel (Task 2.3).
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] get_channel_messages body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = normalize_channel_name(data.get("channel", "general")) or "general"

    # Filter message log for messages explicitly sent to this channel.
    # A direct message can have "to" equal to a channel name, so do not
    # use the destination field alone here.
    with _lock:
        ch_messages = channel_messages_for(channel_name)

    result = {
        "status": "ok",
        "channel": channel_name,
        "messages": ch_messages,
        "count": len(ch_messages),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/channel-sync-state", methods=["POST"])
async def channel_sync_state(headers, body):
    """Return local channel history freshness for reconciliation.

    A returning peer calls this on online peers before downloading history. The
    response is intentionally small: for each requested channel it reports
    whether this peer has the channel, the latest message cursor, and total
    message count. The browser uses that to choose the best history source.
    """
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] channel_sync_state body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    requested = data.get("channels")
    with _lock:
        if isinstance(requested, list) and requested:
            names = [normalize_channel_name(name) for name in requested]
        else:
            names = list(channel_catalog.keys())
        states = {
            name: latest_channel_state(name)
            for name in names
            if name and not channel_catalog.get(name, {}).get("deleted", False)
        }

    result = {"status": "ok", "channels": states}
    return json.dumps(result).encode("utf-8")


@app.route("/channel-history", methods=["POST"])
async def channel_history(headers, body):
    """Return channel messages newer than the caller's local sync cursor.

    The cursor is ``(after_ts, after_id)`` instead of timestamp alone. This
    avoids losing messages when two messages have the same timestamp and gives
    the browser a stable ordering for deduplication.
    """
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] channel_history body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = normalize_channel_name(data.get("channel", ""))
    try:
        after_ts = float(data.get("after_ts", 0) or 0)
    except (TypeError, ValueError):
        after_ts = 0
    after_id = data.get("after_id", "") or ""
    if not channel_name:
        result = {"status": "error", "message": "channel is required"}
        return json.dumps(result).encode("utf-8")

    with _lock:
        ch_messages = [
            m
            for m in channel_messages_for(channel_name)
            if (m.get("ts") or 0, m.get("id", "")) > (after_ts, after_id)
        ]

    result = {
        "status": "ok",
        "channel": channel_name,
        "messages": ch_messages,
        "count": len(ch_messages),
    }
    return json.dumps(result).encode("utf-8")


@app.route("/broadcast-channel", methods=["POST"])
async def broadcast_channel(headers, body):
    """Broadcast a channel message directly to peer backends.

    Live channel messages remain P2P. This route stores the sender's local copy,
    bumps channel metadata for freshness, then sends the message only to online
    peer backends. The tracker is not used to route message content.
    """
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] broadcast_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = normalize_channel_name(data.get("channel", "general")) or "general"
    msg_text = data.get("msg", "")
    sender = data.get("from", "anonymous")

    # Record message locally with to=channel_name so /get-channel-messages returns it.
    # The id lets browser polling deduplicate overlapping immediate/interval fetches.
    entry = {
        "id": uuid.uuid4().hex,
        "from": sender,
        "to": channel_name,
        "msg": msg_text,
        "type": "channel",
        "channel": channel_name,
        "ts": int(time.time() * 1000),
    }
    with _lock:
        ensure_channel_record(channel_name)
        messages.append(entry)
        touch_channel_record(channel_name, deleted=False)
        targets = [
            dict(p)
            for p in peer_list
            if p.get("username") != sender
            and p.get("online", True) is not False
            and p.get("ip")
            and p.get("port")
        ]

    async def _send_all():
        async def _send(peer):
            try:
                reader, writer = await asyncio.open_connection(peer["ip"], int(peer["port"]))
                
                msg_payload = json.dumps(
                    {
                        "id": entry["id"],
                        "from": sender,
                        "channel": channel_name,
                        "msg": msg_text,
                        "type": "channel",
                    }
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
                
                writer.write(raw_request.encode())
                await writer.drain()
                
                resp = await reader.read(4096)
                writer.close()
                await writer.wait_closed()
                
                if resp:
                    return peer["username"], True
                return peer["username"], False
            except Exception:
                return peer["username"], False

        tasks = [_send(p) for p in targets]
        if tasks:
            return await asyncio.gather(*tasks)
        return []

    delivered = []
    failed = []
    
    if targets:
        results = await _send_all()
        for peer_username, success in results:
            if success:
                delivered.append(peer_username)
            else:
                failed.append(peer_username)

    result = {
        "status": "ok",
        "channel": channel_name,
        "delivered": delivered,
        "failed": failed,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/leave-channel", methods=["DELETE"])
def leave_channel(headers, body):
    if not require_auth(headers):
        return unauthorized_result()

    print("[SampleApp] leave_channel body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    channel_name = normalize_channel_name(data.get("channel", "general")) or "general"
    username = data.get("username", "")

    with _lock:
        if channel_name in channels:
            channels[channel_name] = [
                p for p in channels[channel_name] if p.get("username") != username
            ]
            if not channels[channel_name]:
                del channels[channel_name]
            touch_channel_record(channel_name, deleted=channel_name not in channels)

    result = {
        "status": "ok",
        "message": "{} left channel {}".format(username, channel_name),
    }
    return json.dumps(result).encode("utf-8")


# Receive incoming P2P message (called by other peers)


@app.route("/receive-message", methods=["POST"])
def receive_message(headers, body):
    """Receive a direct or channel message from another peer backend.

    Direct messages are plaintext JSON. Message IDs are used for deduplication
    because both a sender outbox and a backup peer can retry the same message
    when the recipient comes online.

    :param headers: HTTP request headers from the sending peer.
    :param body: JSON message payload from direct, channel, or backup delivery.
    :returns: JSON ACK; duplicate IDs return a successful duplicate ACK.
    """

    print("[SampleApp] receive_message body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    sender = data.get("from", "unknown")
    msg_text = data.get("msg", "")
    channel = normalize_channel_name(data.get("channel", "")) or None
    message_id = data.get("id", uuid.uuid4().hex)
    created_at = data.get("created_at") or time.time()

    entry = {
        "id": message_id,
        "from": sender,
        "to": channel if channel else data.get("to", "me"),
        "msg": msg_text,
        "type": "channel" if channel else "direct",
        "channel": channel,
        "created_at": created_at,
        "delivery_status": "delivered",
        "ts": created_at,
    }
    with _lock:
        if any(m.get("id") == message_id for m in messages):
            result = {"status": "ok", "ack": "duplicate", "duplicate": True}
            return json.dumps(result).encode("utf-8")
        if channel:
            channels.setdefault(channel, [])
        messages.append(entry)

    print("[SampleApp] Received from {}: {}".format(sender, msg_text))
    result = {"status": "ok", "ack": "message received"}
    return json.dumps(result).encode("utf-8")


@app.route("/presence-event", methods=["POST"])
async def presence_event(headers, body):
    """Handle a tracker broadcast that a peer has come online.

    Peer backends receive this event from the tracker after another user
    registers. The handler updates the local routing cache, then retries two
    targeted queues for that username:

    1. Sender outbox messages created by this peer.
    2. Backup-held messages stored for other senders.

    :param headers: HTTP request headers from the peer/tracker POST.
    :param body: JSON event payload with username, ip, and port.
    :returns: JSON result with retry counts for outbox and held messages.
    """
    print("[SampleApp] presence_event body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    if data.get("type") != "peer-online":
        result = {"status": "error", "message": "Unsupported presence event"}
        return json.dumps(result).encode("utf-8")

    username = data.get("username", "")
    peer = {
        "username": username,
        "ip": data.get("ip", ""),
        "port": data.get("port", ""),
        "online": True,
        "last_seen": data.get("last_seen", time.time()),
    }

    with _lock:
        current = find_peer(username)
        if current:
            current.update(peer)
        elif username:
            peer_list.append(peer)

    delivered_outbox = await retry_outbox_for(username, peer)
    delivered_held = await retry_held_for(username, peer)
    result = {
        "status": "ok",
        "event": "peer-online",
        "username": username,
        "delivered_outbox": delivered_outbox,
        "delivered_held": delivered_held,
    }
    return json.dumps(result).encode("utf-8")


@app.route("/store-backup-message", methods=["POST"])
async def store_backup_message(headers, body):
    """Store one direct message as a best-effort backup.

    Senders call this route on one online backup peer when direct delivery to
    the final recipient fails. The backup peer stores the message plus delivery
    metadata and waits for a future ``peer-online`` presence event for the
    recipient.

    :param headers: HTTP request headers from the sender peer backend.
    :param body: JSON message envelope and metadata.
    :returns: JSON ACK containing the stored message id.
    """
    print("[SampleApp] store_backup_message body={}".format(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    message_id = data.get("id") or uuid.uuid4().hex
    if not data.get("to") or not data.get("msg"):
        result = {"status": "error", "message": "to and msg are required"}
        return json.dumps(result).encode("utf-8")

    held_entry = {
        "id": message_id,
        "from": data.get("from", ""),
        "to": data.get("to", ""),
        "msg": data.get("msg", ""),
        "type": "direct",
        "created_at": data.get("created_at") or time.time(),
        "ts": data.get("created_at") or time.time(),
        "attempt_count": data.get("attempt_count", 0),
        "expires_at": data.get("expires_at") or (time.time() + 3600),
    }

    with _lock:
        held_messages[:] = [m for m in held_messages if m.get("id") != message_id]
        held_messages.append(held_entry)

    result = {
        "status": "ok",
        "message": "Backup message stored",
        "id": message_id,
    }
    return json.dumps(result).encode("utf-8")


# Entry point

TRACKER_ROUTE_PATHS = {
    "/login",
    "/logout",
    "/hello",
    "/echo",
    "/client-info",
    "/submit-info",
    "/get-list",
    "/get-channels",
    "/create-channel",
    "/rename-channel",
    "/delete-channel",
}

PEER_ROUTE_PATHS = {
    "/login",
    "/logout",
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
    "/create-channel",
    "/rename-channel",
    "/delete-channel",
    "/receive-channel",
    "/get-channel-messages",
    "/channel-sync-state",
    "/channel-history",
    "/broadcast-channel",
    "/leave-channel",
    "/receive-message",
    "/presence-event",
    "/store-backup-message",
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
