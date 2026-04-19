import sys
import os
import importlib.util
import json
import socket
import threading

from daemon import AsynapRous

app = AsynapRous()

# ==========================================
# GLOBAL DATABASES
# ==========================================

# Global dictionary to act as the Tracker Server's database for active peers.
# Data format: { "username": {"ip": "192.168.1.10", "port": 8001} }
active_peers = {}

# Flat message list for the local peer (all direct + broadcast messages)
chat_messages = []

# Channel storage: { "general": [{"from":"A","message":"hi","channel":"general"}], ... }
channels = {"general": []}

# Joined channels per this peer instance
joined_channels = ["general"]

# Unread message counter (reset when frontend polls)
unread_count = 0

# This peer's own identity (set during registration)
my_username = None



# HELPER: Send HTTP POST to another peer via raw socket


def _send_http_post(host, port, path, payload_dict):
    """
    Send an HTTP POST request to another peer using raw sockets.
    This is used for P2P communication without any external library.

    :param host (str): Target peer IP.
    :param port (int): Target peer port.
    :param path (str): API path (e.g., '/send-peer').
    :param payload_dict (dict): JSON-serializable payload.
    :rtype: str or None: Response body string, or None on failure.
    """
    try:
        body = json.dumps(payload_dict)
        request_line = "POST {} HTTP/1.1\r\n".format(path)
        headers = (
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(host, port, len(body.encode('utf-8')))

        raw_request = request_line + headers + body

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, int(port)))
        sock.sendall(raw_request.encode('utf-8'))

        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        sock.close()

        # Extract body from HTTP response
        resp_str = response.decode('utf-8', errors='replace')
        if "\r\n\r\n" in resp_str:
            return resp_str.split("\r\n\r\n", 1)[1]
        return resp_str
    except Exception as e:
        print("[P2P Helper] Error sending to {}:{}{} - {}".format(host, port, path, e))
        return None


# BASE APIS (AUTH & TESTING)

@app.route('/login', methods=['PUT'])
def login(headers="guest", body="anonymous"):
    """
    Handle user login via PUT request (Theo đúng yêu cầu Figure 2).

    :param headers (str/dict): The request headers or user identifier.
    :param body (str): The request body or login payload.
    :rtype: tuple (bytes, int)
    """
    print("[SampleApp] Logging in {} to {}".format(headers, body))
    data = {"message": "Welcome to the RESTful TCP WebApp"}
    json_str = json.dumps(data)
    return (json_str.encode("utf-8"), 200)

@app.route("/echo", methods=["POST"])
def echo(headers="guest", body="anonymous"):
    """
    Echo the received JSON body back to the client.

    :param headers (str/dict): The request headers.
    :param body (str): The JSON body sent by the client.
    :rtype: tuple (bytes, int)
    """
    print("[SampleApp] received body {}".format(body))
    try:
        message = json.loads(body)
        data = {"received": message}
        json_str = json.dumps(data)
        return (json_str.encode("utf-8"), 200)
    except json.JSONDecodeError:
        data = {"error": "Invalid JSON"}
        json_str = json.dumps(data)
        return (json_str.encode("utf-8"), 400)

@app.route('/hello', methods=['POST'])
def hello(headers, body):
    """
    Handle protected greeting via POST request.
    Requires valid cookie or authorization header to access.

    :param headers (dict): The request headers containing auth details.
    :param body (str): The request body.
    :rtype: tuple (bytes, int)
    """
    # Use hasattr instead of isinstance(dict) because headers may be CaseInsensitiveDict
    cookie_str = headers.get('cookie', '') if hasattr(headers, 'get') else ''
    auth_str = headers.get('authorization', '') if hasattr(headers, 'get') else ''

    if 'sessionid=secure_xyz_789' not in cookie_str and not auth_str:
        data = {"error": "Unauthorized. Please login first."}
        return (json.dumps(data).encode("utf-8"), 401)

    print("[SampleApp] Valid User accessed /hello")
    data = {"id": 1, "name": "A", "message": "Secret Data Accessed!"}
    return (json.dumps(data).encode("utf-8"), 200)


# HYBRID CHAT: TRACKER SERVER APIS

@app.route('/submit-info', methods=['POST'])
def submit_info(headers="guest", body="anonymous"):
    """
    Handle peer registration via POST request.

    Parses the incoming JSON body containing the peer's
    username, IP, and port, and stores them in the active_peers list.

    :param headers (dict): The request headers.
    :param body (str): The request body containing peer info in JSON format.
    :rtype: tuple (bytes, int) containing JSON response and HTTP status code.
    """
    global active_peers, my_username
    try:
        peer_info = json.loads(body)
        username = peer_info.get("username")
        ip = peer_info.get("ip")
        port = peer_info.get("port")

        if not username or not ip or not port:
            error_msg = {"error": "Missing username, ip, or port in payload."}
            return (json.dumps(error_msg).encode("utf-8"), 400)

        # Store or update the peer's information
        active_peers[username] = {"ip": ip, "port": port}
        # Remember our own identity
        if my_username is None:
            my_username = username
        print("[Tracker] Registered peer: {} at {}:{}".format(username, ip, port))

        success_msg = {"message": "Peer {} registered successfully.".format(username)}
        return (json.dumps(success_msg).encode("utf-8"), 200)

    except json.JSONDecodeError:
        error_msg = {"error": "Invalid JSON payload."}
        return (json.dumps(error_msg).encode("utf-8"), 400)

@app.route('/get-list', methods=['GET'])
def get_list(headers="guest", body="anonymous"):
    """
    Retrieve the list of active peers via GET request.

    Returns the global active_peers dictionary to the
    requesting client so they can initiate P2P connections.

    :param headers (dict): The request headers.
    :param body (str): The request body (usually empty for GET).
    :rtype: tuple (bytes, int) containing JSON response and HTTP status code.
    """
    global active_peers
    print("[Tracker] Sending active peer list. Total: {}".format(len(active_peers)))

    response_data = {"active_peers": active_peers}
    return (json.dumps(response_data).encode("utf-8"), 200)


# HYBRID CHAT: PEER-TO-PEER (P2P) APIS

@app.route('/connect-peer', methods=['POST'])
def connect_peer(headers="guest", body="anonymous"):
    """
    Handshake API for a peer to check if this peer is online.
    (Connection setup phase)

    :param headers (dict): The request headers.
    :param body (str): JSON body with 'from' field identifying the requester.
    :rtype: tuple (bytes, int)
    """
    try:
        payload = json.loads(body)
        sender = payload.get("from")
        print("[P2P Handshake] Peer '{}' wants to connect.".format(sender))

        # Return 200 OK to confirm "I am online and ready to receive messages"
        success_msg = {"status": "connected", "peer_alive": True}
        return (json.dumps(success_msg).encode("utf-8"), 200)
    except json.JSONDecodeError:
        return (json.dumps({"error": "Invalid JSON"}).encode("utf-8"), 400)


@app.route('/send-peer', methods=['POST'])
def send_peer(headers="guest", body="anonymous"):
    """
    Handle incoming direct messages from another peer.

    Receives a JSON payload containing the sender's name and the message.
    Appends the message to the local chat history.

    :param headers (dict): The request headers.
    :param body (str): The request body containing the message payload.
    :rtype: tuple (bytes, int) containing acknowledgment response.
    """
    global chat_messages, unread_count
    try:
        payload = json.loads(body)
        sender = payload.get("from")
        message = payload.get("message")

        if not sender or not message:
            error_msg = {"error": "Missing 'from' or 'message' in payload."}
            return (json.dumps(error_msg).encode("utf-8"), 400)

        # Store message in history for the frontend to poll and display
        chat_msg = {"type": "direct", "from": sender, "message": message}
        chat_messages.append(chat_msg)
        unread_count += 1
        print("[P2P Receiver] Direct Received: {} says '{}'".format(sender, message))

        success_msg = {"status": "Message received"}
        return (json.dumps(success_msg).encode("utf-8"), 200)

    except json.JSONDecodeError:
        error_msg = {"error": "Invalid JSON payload."}
        return (json.dumps(error_msg).encode("utf-8"), 400)


@app.route('/broadcast-peer', methods=['POST'])
def broadcast_peer(headers="guest", body="anonymous"):
    """
    Handle incoming broadcast messages from another peer.

    When called with is_origin=true in payload, this peer acts as the
    broadcast originator and forwards the message to all known peers.
    Otherwise, it simply stores the incoming broadcast message locally.

    :param headers (dict): The request headers.
    :param body (str): The request body containing the broadcast message.
    :rtype: tuple (bytes, int) containing acknowledgment response.
    """
    global chat_messages, active_peers, unread_count
    try:
        payload = json.loads(body)
        sender = payload.get("from")
        message = payload.get("message")
        is_origin = payload.get("is_origin", False)

        if not sender or not message:
            error_msg = {"error": "Missing 'from' or 'message' in payload."}
            return (json.dumps(error_msg).encode("utf-8"), 400)

        # Store broadcast message locally
        chat_msg = {"type": "broadcast", "from": sender, "message": message}
        chat_messages.append(chat_msg)
        unread_count += 1
        print("[P2P Receiver] Broadcast Received: {} says '{}'".format(sender, message))

        target_peers = payload.get("peers", active_peers)

        # If this peer is the originator, forward to ALL other peers
        if is_origin:
            forward_payload = {"from": sender, "message": message, "is_origin": False}
            for peer_name, peer_info in target_peers.items():
                if peer_name == sender:
                    continue
                # Forward in a separate thread to avoid blocking
                t = threading.Thread(
                    target=_send_http_post,
                    args=(peer_info["ip"], peer_info["port"], "/broadcast-peer", forward_payload)
                )
                t.daemon = True
                t.start()
            print("[P2P Broadcast] Forwarded to {} peers".format(len(active_peers) - 1))

        success_msg = {"status": "Broadcast message received"}
        return (json.dumps(success_msg).encode("utf-8"), 200)

    except json.JSONDecodeError:
        error_msg = {"error": "Invalid JSON payload."}
        return (json.dumps(error_msg).encode("utf-8"), 400)


@app.route('/get-messages', methods=['GET'])
def get_messages(headers="guest", body="anonymous"):
    """
    Retrieve the local chat history and unread count for the frontend UI.

    Returns all messages received by this peer, plus an unread counter
    that resets after each poll.

    :param headers (dict): The request headers.
    :param body (str): The request body.
    :rtype: tuple (bytes, int) containing JSON list of messages.
    """
    global chat_messages, unread_count
    current_unread = unread_count
    unread_count = 0  # Reset on poll
    response_data = {"messages": chat_messages, "unread_count": current_unread}
    return (json.dumps(response_data).encode("utf-8"), 200)


# ==========================================
# HYBRID CHAT: CHANNEL MANAGEMENT
# ==========================================

@app.route('/add-list', methods=['POST'])
def add_list(headers="guest", body="anonymous"):
    """
    Create a new channel or join an existing channel.
    (Channel Management: Channel listing)

    :param headers (dict): The request headers.
    :param body (str): JSON with 'channel' field.
    :rtype: tuple (bytes, int)
    """
    global channels, joined_channels
    try:
        payload = json.loads(body)
        channel_name = payload.get("channel")

        if not channel_name:
            return (json.dumps({"error": "Missing channel name"}).encode("utf-8"), 400)

        # Create channel if it doesn't exist
        if channel_name not in channels:
            channels[channel_name] = []
            print("[Channel Manager] Created new channel: {}".format(channel_name))

        # Track joined channels
        if channel_name not in joined_channels:
            joined_channels.append(channel_name)

        success_msg = {
            "message": "Joined channel '{}'".format(channel_name),
            "existing_channels": list(channels.keys())
        }
        return (json.dumps(success_msg).encode("utf-8"), 200)
    except json.JSONDecodeError:
        return (json.dumps({"error": "Invalid JSON"}).encode("utf-8"), 400)

@app.route('/leave-channel', methods=['DELETE'])
def leave_channel(headers="guest", body="anonymous"):
    """
    API để rời khỏi channel.
    Hỗ trợ phương thức DELETE để chứng minh chuẩn RESTful đầy đủ.
    """
    global joined_channels
    try:
        payload = json.loads(body)
        channel = payload.get("channel")
        
        if not channel:
            return (json.dumps({"error": "Missing channel name"}).encode("utf-8"), 400)

        if channel in joined_channels:
            joined_channels.remove(channel)
            print("[Channel Manager] Left channel: {}".format(channel))

        success_msg = {"message": "Left channel '{}'".format(channel)}
        return (json.dumps(success_msg).encode("utf-8"), 200)
    except json.JSONDecodeError:
        return (json.dumps({"error": "Invalid JSON"}).encode("utf-8"), 400)


@app.route('/get-channels', methods=['GET'])
def get_channels(headers="guest", body="anonymous"):
    """
    Retrieve the list of available channels and joined channels.

    :param headers (dict): The request headers.
    :param body (str): The request body.
    :rtype: tuple (bytes, int) containing channel listings.
    """
    global channels, joined_channels
    response_data = {
        "all_channels": list(channels.keys()),
        "joined_channels": joined_channels
    }
    return (json.dumps(response_data).encode("utf-8"), 200)


@app.route('/broadcast-channel', methods=['POST'])
def broadcast_channel(headers="guest", body="anonymous"):
    """
    Receive a message for a specific channel.
    When is_origin=true, forward to all peers.

    :param headers (dict): The request headers.
    :param body (str): JSON with 'from', 'message', 'channel' fields.
    :rtype: tuple (bytes, int)
    """
    global channels, active_peers, unread_count
    try:
        payload = json.loads(body)
        sender = payload.get("from")
        message = payload.get("message")
        channel = payload.get("channel", "general")
        is_origin = payload.get("is_origin", False)

        if not sender or not message:
            return (json.dumps({"error": "Missing data"}).encode("utf-8"), 400)

        if channel not in channels:
            channels[channel] = []

        chat_msg = {"from": sender, "message": message, "channel": channel}
        channels[channel].append(chat_msg)
        unread_count += 1
        print("[P2P Receiver] Channel '{}' msg from {}: {}".format(channel, sender, message))

        target_peers = payload.get("peers", active_peers)

        # If originator, forward to all other peers
        if is_origin:
            forward_payload = {"from": sender, "message": message, "channel": channel, "is_origin": False}
            for peer_name, peer_info in target_peers.items(): 
                if peer_name == sender:
                    continue
                t = threading.Thread(
                    target=_send_http_post,
                    args=(peer_info["ip"], peer_info["port"], "/broadcast-channel", forward_payload)
                )
                t.daemon = True
                t.start()

        return (json.dumps({"status": "Message logged to channel"}).encode("utf-8"), 200)
    except json.JSONDecodeError:
        return (json.dumps({"error": "Invalid JSON"}).encode("utf-8"), 400)


@app.route('/get-channel-messages', methods=['POST'])
def get_channel_messages(headers="guest", body="anonymous"):
    """
    Retrieve messages for a specific channel.

    :param headers (dict): The request headers.
    :param body (str): JSON with 'channel' field.
    :rtype: tuple (bytes, int)
    """
    global channels
    try:
        payload = json.loads(body)
        channel = payload.get("channel", "general")

        msgs = channels.get(channel, [])
        response_data = {"channel": channel, "messages": msgs}
        return (json.dumps(response_data).encode("utf-8"), 200)
    except json.JSONDecodeError:
        return (json.dumps({"error": "Invalid JSON"}).encode("utf-8"), 400)


def create_sampleapp(ip, port):
    app.prepare_address(ip, port)
    app.run()