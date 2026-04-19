# Computer Network Assignment 1 - Hybrid Chat & Non-blocking HTTP Server

This repository contains the implementation of a non-blocking HTTP server and a hybrid P2P chat application for the CO3093/CO3094 Computer Network course. 

## Project Features

### 2.1 Implement non-blocking mechanisms
* The implementation of non-blocking mechanisms relies on operating system services that allow I/O operations to be halted immediately rather than blocking execution. This logic is located in the backend server daemon at `daemon/backend.py`.
* The system handles incoming connections and delegates clients to the `HttpAdapter` (in `daemon/httpadapter.py`) through three non-blocking strategies. 
* These supported strategies include:
  * **Multi-threading**: Implemented natively in `run_backend()` using the standard `threading.Thread`.
    ```python
    conn, addr = server.accept() # block until a client connects
    client_thread = threading.Thread(
        target=handle_client, 
        args=(ip, port, conn, addr, routes)
    )
    client_thread.daemon = True
    client_thread.start()
    ```
  * **Callback/event-driven**: Implemented in `run_backend()` using Python's `selectors` module to wait for events instead of blocking at `accept()`.
    ```python
    # Register server socket with selector to listen for new connection events
    sel.register(server, selectors.EVENT_READ, data=("accept", ip, port, routes))
    
    events = sel.select(timeout=None)
    for key, mask in events:
        if key.data[0] == "accept":
            # Event: New client connection
            conn, addr = key.fileobj.accept()
            conn.setblocking(False)
            sel.register(conn, selectors.EVENT_READ, data=("read", ...))
    ```
  * **Coroutine-based async/await**: Implemented using `asyncio` via the `async_server()` and `handle_client_coroutine()` functions.
    ```python
    async def handle_client_coroutine_with_routes(reader, writer):
        while True:
            # Pass ip, port, and routes to HttpAdapter
            daemon = HttpAdapter(ip, port, None, addr, routes)
            await daemon.handle_client_coroutine(reader, writer)
    ```

### 2.2 Implement the authentication for HTTP server
* To authenticate a user, the system implements two common approaches including HTTP headers and Cookies. 
* The first approach uses the `WWW-Authenticate` header to request authentication (handled in `daemon/response.py`), and the browser responds with an `Authorization` header carrying login details. This is parsed in `daemon/request.py`:
  ```python
  if scheme == 'basic':
      # Decode base64 credentials (e.g. Basic dXNlcjpwYXNz)
      decoded = base64.b64decode(credentials).decode('utf-8')
      username, password = decoded.split(':', 1)
  ```
* The second approach uses cookies. When a user logs in successfully, the handler assigns a cookie, and the `build_response_header` sends `Set-Cookie`.
  ```python
  # Check webapp request to set new cookie
  if req.path == '/login' and resp.status_code == 200:
      resp.cookies['sessionid'] = 'secure_xyz_789'
  ```
  Protected endpoints then verify this cookie:
  ```python
  cookie_str = headers.get('cookie', '') if isinstance(headers, dict) else ''
  if 'sessionid=secure_xyz_789' not in cookie_str and not auth_str:
      return (json.dumps({"error": "Unauthorized"}).encode("utf-8"), 401)
  ```

### 2.3 Implement hybrid chat application
* This task develops a hybrid network application combining client-server and peer-to-peer (P2P) paradigms. The core APIs are defined in `apps/sampleapp.py`.
* **Centralized Server (Initialization):** Uses a tracker for peer registration (`/submit-info`) and discovery (`/get-list`).
  ```python
  # Storing peers in a global dictionary on the Tracker
  active_peers[username] = {"ip": ip, "port": port}
  ```
* **P2P Chatting:** Peers exchange messages directly replacing the centralized server, using the helper `_send_http_post()` which implements raw socket communication.
  ```python
  def _send_http_post(host, port, path, payload_dict):
      body = json.dumps(payload_dict)
      request_line = f"POST {path} HTTP/1.1\r\n"
      headers = (
          f"Host: {host}:{port}\r\n"
          "Content-Type: application/json\r\n"
          f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
      )
      sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      sock.connect((host, int(port)))
      sock.sendall((request_line + headers + body).encode('utf-8'))
  ```
* **Channel Synchronization:** Broadcasts are managed by spawning threads to forward messages avoiding blocking the main thread execution.
  ```python
  if is_origin:
      for peer_name, peer_info in target_peers.items():
          if peer_name != sender:
              # Forward in a separate thread to avoid blocking
              t = threading.Thread(
                  target=_send_http_post,
                  args=(peer_info["ip"], peer_info["port"], "/broadcast-peer", forward_payload)
              )
              t.start()
  ```

---

## How to Run & Test

> **Run all commands from the project root directory:**
> ```bash
> cd "/Users/leuyentran/Downloads/CO3094-asynaprous copy"
> ```

---

### Part 1: Hybrid Chat Application (Task 2.3)

**Terminal 1 — Admin / Tracker server (port 9000)**
```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 9000
```

**Terminal 2 — User A peer server (port 8000)**
```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8000
```

**Terminal 3 — User B peer server (port 8001)**
```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8001
```

**Demo flow:**
Open two browser tabs (use incognito for cookie isolation):

- Tab A → http://127.0.0.1:8000 → auto-configures as user **"A"**, trackerPort = 9000
- Tab B → http://127.0.0.1:8001 → auto-configures as user **"B"**, trackerPort = 9000

1. Both users click **Register** → tracker (port 9000) stores them.
2. Both click **Discover** → see each other in the Active Peers list.
3. User A clicks B's name in the list → sets target to B's port.
4. Type message → **Send Direct** → goes peer-to-peer (A:8000 → B:8001), no tracker involved.
5. Type message → **Broadcast** → A sends to own server which forwards to all known peers.
6. Click **Handshake** → confirms peer is online via `/connect-peer`.

---

### Part 2: Proxy Server (Task 2.1)

The proxy reads virtual host routes from `config/proxy.conf` and forwards requests to backend servers.

**Step 1 — Start the backend app server (the upstream target)**
```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 9000
```

**Step 2 — Start the proxy server (port 8080)**
```bash
python start_proxy.py --server-ip 0.0.0.0 --server-port 8080
```

**Step 3 — Configure `config/proxy.conf`** to map your host to the backend:
```nginx
host "127.0.0.1:8080" {
    proxy_pass http://127.0.0.1:9000;
}
```

**Step 4 — Test with curl** (the `Host` header must match the config):
```bash
# Forward a GET request through the proxy to the backend
curl -v http://127.0.0.1:8080/get-list -H "Host: 127.0.0.1:8080"

# Forward a POST request through the proxy
curl -v -X POST http://127.0.0.1:8080/submit-info \
  -H "Host: 127.0.0.1:8080" \
  -H "Content-Type: application/json" \
  -d '{"username": "test", "ip": "127.0.0.1", "port": 8000}'
```

> The proxy reads the `Host` header from the request, looks it up in `proxy.conf`, and forwards to the matched `proxy_pass` backend. Round-robin load balancing is supported when multiple `proxy_pass` entries exist.

---

### Part 3: Authentication (Task 2.2)

The server supports two authentication methods. Start any app server first:
```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8000
```

#### Method 1 — Cookie-based (Login then access protected route)

**Step 1 — Login to get a session cookie:**
```bash
curl -v -X PUT http://127.0.0.1:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'
```
The response will include: `Set-Cookie: sessionid=secure_xyz_789; Path=/; HttpOnly`

**Step 2 — Access the protected `/hello` endpoint with the cookie:**
```bash
curl -v -X POST http://127.0.0.1:8000/hello \
  -H "Cookie: sessionid=secure_xyz_789" \
  -H "Content-Type: application/json"
```

**Step 3 — Access `/hello` WITHOUT cookie → expect `401 Unauthorized` + `WWW-Authenticate` header:**
```bash
curl -v -X POST http://127.0.0.1:8000/hello
# Response: HTTP/1.1 401 Unauthorized
#           WWW-Authenticate: Basic realm="Restricted Area"
```

#### Method 2 — HTTP Basic Auth (Authorization header)

The `Authorization` header carries base64-encoded `username:password`. The server decodes it in `daemon/request.py` → `prepare_auth()`.

```bash
# base64("admin:admin123") = "YWRtaW46YWRtaW4xMjM="
curl -v -X POST http://127.0.0.1:8000/hello \
  -H "Authorization: Basic YWRtaW46YWRtaW4xMjM="
```

Or encode on the fly:
```bash
curl -v -X POST http://127.0.0.1:8000/hello \
  -u admin:admin123
``` 