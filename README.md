# AsynapRous — README

## Overview

**AsynapRous** is a lightweight Python TCP server framework built from the standard library only
(no third-party packages). It supports three non-blocking I/O strategies, HTTP authentication, and a
hybrid Client-Server / Peer-to-Peer chat application.

---

## Prerequisites

- Python 3.8 or later
- No pip packages required — only the Python standard library

---

## How to Run Each Task

### Task 2.1 — Non-blocking I/O

Three separate processes cover the three components. Start each in its own terminal.

#### Terminal 1 — Backend (port 9000, raw HTTP)

```bash
cd /path/to/CO3094-asynaprous
python3 start_backend.py --server-ip 0.0.0.0 --server-port 9000
```

#### Terminal 2 — Proxy (port 8080)

```bash
python3 start_proxy.py --server-ip 0.0.0.0 --server-port 8080
```

The proxy reads its routing table from `config/proxy.conf`. Edit that file to point virtual
hosts to the correct backends.

#### Terminal 3 — SampleApp / Chat server (port 2026)

```bash
python3 start_sampleapp.py --server-ip 0.0.0.0 --server-port 2026
```


#### Test 2.1 — Load test

```bash
# Install Apache Bench if missing: brew install httpie
ab -n 100 -c 20 http://localhost:2026/get-list
```

If all 100 requests complete without the server freezing, non-blocking I/O is working.

---

### Task 2.2 — HTTP Authentication

To properly test the authentication features, you will need 4 separate terminal windows to simulate the backend, the proxy, a second server, and the client making requests.

#### Terminal 1 — Backend (Port 9000)

```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 9000
```


#### Terminal 2 — Proxy Server (Port 8080)

```bash
python start_proxy.py --server-ip 0.0.0.0 --server-port 8080
```


#### Terminal 3 — Second App Instance (Port 8000)

```bash
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8000
```


#### Terminal 4 — Client (curl Testing)

**1. Login to receive Session Cookie (RFC 6265)**
```bash
curl -X PUT http://127.0.0.1:8000/login -H "Content-Type: application/json" -d '{"username":"admin"}'
```
*Response Output:*
`{"status": "ok", "message": "Welcome, admin!", "username": "admin"}`
*(Note: Check Terminal 3 logs to see the generated `sessionid`, e.g., `Session created for admin → a40a...`)*

**2. Access protected route using the Cookie**
```bash
curl -X POST http://127.0.0.1:8000/hello -H "Cookie: sessionid=<INSERT_SESSIONID_HERE>"
```
*Response Output:*
`{"status": "ok", "message": "Hello, admin! You are authenticated.", "user": "admin"}`

**3. Access protected route using Basic Auth (RFC 7617)**
```bash
curl -X POST http://127.0.0.1:8000/hello -u admin:admin123
```
*Response Output:*
`{"status": "ok", "message": "Hello, admin! You are authenticated.", "user": "admin"}`

Credentials stored in `db/users.json` (format: `{"username": "password"}`).

---

### Task 2.3 — Hybrid P2P Chat

Start two instances on different ports to simulate two peers:

```bash
# Peer A (tracker + peer)
python3 start_sampleapp.py --server-ip 0.0.0.0 --server-port 2026

# Peer B (in separate terminal)
python3 start_sampleapp.py --server-ip 0.0.0.0 --server-port 2027
```

#### Browser UI Testing (Recommended)

1. Open two browser tabs (you can use Incognito to isolate sessions):
   - Tab A: [http://127.0.0.1:2026](http://127.0.0.1:2026) (auto-configured as `alice`)
   - Tab B: [http://127.0.0.1:2027](http://127.0.0.1:2027) (auto-configured as `bob`)
2. **Login & Register**: Click **🔐 Login** on the right sidebar and enter credentials (e.g., `admin:admin123` or `alice:password1`). Upon successful login, you will automatically be registered as a peer.
3. **Discover**: Click **Discover** on the right sidebar to find other online peers.
4. **Direct P2P Message**: In the Active Peers list, click a peer's name to target them. Type a message and click **Send Direct**.
5. **Broadcast Message**: Type a message and click **Broadcast** to send to all discovered peers.
6. **Channels**: Use the left sidebar to switch between direct messages and channels. Click the `+` icon to create a new channel and start broadcasting messages within that channel.

#### Command-line Testing (curl)

If you prefer testing via `curl`, you can use the commands below.

**Initialization Phase:**

```bash
# Peer A registers itself with Peer B's tracker
curl -X POST http://localhost:2027/submit-info \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","ip":"127.0.0.1","port":"2026"}'

# Peer B registers itself with Peer A's tracker
curl -X POST http://localhost:2026/submit-info \
  -H "Content-Type: application/json" \
  -d '{"username":"bob","ip":"127.0.0.1","port":"2027"}'

# Get peer list from tracker
curl http://localhost:2026/get-list
```

**Chat Phase (direct P2P):**

```bash
# Send direct message from alice to bob
curl -X POST http://localhost:2026/send-peer \
  -H "Content-Type: application/json" \
  -d '{"from":"alice","to":"bob","msg":"Hello Bob!"}'

# Broadcast to all peers
curl -X POST http://localhost:2026/broadcast-peer \
  -H "Content-Type: application/json" \
  -d '{"from":"alice","msg":"Hello everyone!"}'

# Get message log
curl http://localhost:2026/get-messages
```

**Channel Management:**

```bash
# List channels
curl http://localhost:2026/get-channels

# Get messages in a channel
curl -X POST http://localhost:2026/get-channel-messages \
  -H "Content-Type: application/json" \
  -d '{"channel":"general"}'

# Broadcast to a channel
curl -X POST http://localhost:2026/broadcast-channel \
  -H "Content-Type: application/json" \
  -d '{"channel":"general","from":"alice","msg":"Hello channel!"}'

# Leave a channel
curl -X DELETE http://localhost:2026/leave-channel \
  -H "Content-Type: application/json" \
  -d '{"channel":"general","username":"alice"}'
```

---

## REST API Reference

| Method   | Endpoint               | Description                               |
|----------|------------------------|-------------------------------------------|
| PUT/POST | `/login`               | Login; returns session cookie             |
| POST/GET | `/hello`               | Protected route (cookie or Basic Auth)    |
| POST     | `/echo`                | Echo request body                         |
| POST     | `/submit-info`         | Register peer (ip, port, username)        |
| GET      | `/get-list`            | List all registered peers                 |
| POST     | `/add-list`            | Add a peer entry                          |
| POST     | `/connect-peer`        | Fetch peer list from remote tracker       |
| POST     | `/send-peer`           | Direct message to one peer                |
| POST     | `/broadcast-peer`      | Broadcast to all peers                    |
| GET      | `/get-messages`        | Get local message log                     |
| GET      | `/get-channels`        | List channels and members                 |
| POST     | `/get-channel-messages`| Messages in a specific channel            |
| POST     | `/broadcast-channel`   | Broadcast to a channel                    |
| DELETE   | `/leave-channel`       | Remove peer from channel                  |
| POST     | `/receive-message`     | Receive an incoming P2P message           |

---

## Project Structure

```
CO3094-asynaprous/
├── start_proxy.py        # Entry point: proxy server
├── start_backend.py      # Entry point: raw backend
├── start_sampleapp.py    # Entry point: chat app
├── config/
│   └── proxy.conf        # Virtual host routing config
├── daemon/
│   ├── asynaprous.py     # App framework + route decorator
│   ├── backend.py        # TCP backend (3 non-blocking modes)
│   ├── proxy.py          # Reverse proxy
│   ├── httpadapter.py    # HTTP request/response bridge
│   ├── request.py        # HTTP request parser
│   ├── response.py       # HTTP response builder
│   ├── dictionary.py     # CaseInsensitiveDict
│   └── utils.py          # URL auth extraction
├── apps/
│   └── sampleapp.py      # P2P chat app (Task 2.2 + 2.3)
├── db/
│   ├── users.json        # Credential store
│   └── sessions.json     # Session token store
├── www/                  # Static HTML pages
└── static/               # CSS, JS, images
```


# AsynapRous — How We Built It

## Project Goals

This project implements three tasks from the CO3094 course:

- **Task 2.1** — Non-blocking I/O: multi-threading, event callbacks, and asyncio coroutines.
- **Task 2.2** — HTTP authentication: session cookies (RFC 6265) and HTTP Basic Auth (RFC 7617).
- **Task 2.3** — Hybrid chat app: tracker-based peer discovery + direct P2P messaging.

---

## Step-by-Step Build Process

### Step 1 — Fix foundational bugs

Before adding any new features we had to fix several bugs that prevented the code from running:

| File | Bug | Fix |
|------|-----|-----|
| `daemon/dictionary.py` | `from collections import MutableMapping` removed in Python 3.10 | Changed to `from collections.abc import MutableMapping` |
| `daemon/utils.py` | Python 2 `from urlparse import urlparse` | Changed to `from urllib.parse import urlparse, unquote` |
| `daemon/backend.py` | `await` inside a non-async function (indentation error on line 110) | Fixed indentation; moved `await` inside the correct `async` function |
| `daemon/request.py` | `self.headers.get(...)` called before `self.headers` was initialised (crash) | Initialised `self.headers = CaseInsensitiveDict()` in `__init__` and parsed headers in `prepare()` before using them |
| `daemon/response.py` | `fmt_header` variable referenced but never defined | Implemented the header-building logic inside `build_response_header()` |
| `daemon/response.py` | `self._header` and `self._content` set but never returned | Added `return self._header + self._content` at the end of `build_response()` |
| `daemon/httpadapter.py` | `response = ""` after calling the hook — empty string sent back | Implemented proper hook dispatch and JSON response wrapping |
| `daemon/proxy.py` | `len(value)` — `value` is undefined (should be `proxy_map`) | Fixed to `len(proxy_map)` |
| `daemon/httpadapter.py` | Single `conn.recv(4096)` truncated large broadcast requests | Implemented a robust `while True:` loop checking `Content-Length` |
| `daemon/httpadapter.py` | DeprecationWarning on `asyncio.get_event_loop()` | Updated to use `asyncio.new_event_loop()` and `.close()` gracefully |
| `daemon/backend.py` | `Resource temporarily unavailable` (EAGAIN) when using callback mode | Added `conn.setblocking(True)` on the accepted socket before handing off to `HttpAdapter` |
| `www/index.html` | Initial static UI lacked proper cookie auth flow and duplicate rendering | Designed a dynamic reactive UI matching the strict routing constraints |

---

### Step 2 — Implement `daemon/request.py`

The `Request` class needed to:

1. **Parse the request line** — extract method, path, HTTP version from the first line.
2. **Split headers / body** at the `\r\n\r\n` blank line.
3. **Parse headers** into a `CaseInsensitiveDict` (case-insensitive per RFC 7230).
4. **Parse cookies** from the `Cookie: name=value; name2=value2` header.
5. **Decode Basic Auth** from `Authorization: Basic <base64>`.
6. **Resolve the route hook** — look up `(method, path)` in the routes dict.

**Key decision:** We initialize `self.headers = CaseInsensitiveDict()` in `__init__` so that
it can never be `None`, avoiding the crash when cookies are parsed before headers.

---

### Step 3 — Implement `daemon/response.py`

The `Response` class is responsible for turning Python data into raw HTTP bytes.

Three response builders were implemented:

- **`build_json_response(body_bytes, status, extra_headers)`** — used for all REST API
  endpoints. Builds the status line, injects `Content-Type: application/json`, and appends
  the body bytes.

- **`build_unauthorized(realm)`** — builds a `401 Unauthorized` response with the
  `WWW-Authenticate: Basic realm="..."` header. Browsers react to this header by showing
  a native credential dialog (Task 2.2 Basic Auth).

- **`build_response(request)`** — serves static files. Detects MIME type from the URL
  extension, picks the correct base directory (`www/` for HTML, `static/css/` for CSS,
  `static/images/` for images), reads the file, and assembles the HTTP envelope.

**Key decision on MIME types:** We added handling for video/*, audio/*, application/xml,
application/zip, text/csv, and text/xml to satisfy the TODO items in the original code.

---

### Step 4 — Implement `daemon/httpadapter.py`

The `HttpAdapter` is the bridge between raw TCP bytes and route handlers.

```
recv(4096) → request.prepare() → routes.get(method, path) → handler(headers, body)
                                                                      │
                                                              decode JSON bytes
                                                              check __status__ key
                                                              check __set_cookie__ key
                                                                      │
                                                    build_json_response() or build_unauthorized()
                                                                      │
                                                                conn.sendall()
```

**Sentinel pattern for auth:** Instead of passing extra arguments, handlers return JSON where
special keys communicate auth needs back to the adapter:

- `__status__: 401` → adapter sends a 401 + `WWW-Authenticate` header
- `__set_cookie__: "sessionid=<token>; HttpOnly; Path=/"` → adapter injects `Set-Cookie`

This keeps handler functions clean and testable in isolation.

---

### Step 5 — Implement `daemon/backend.py` (Task 2.1)

Three non-blocking I/O strategies:

#### Multi-threading (default)

```python
client_thread = threading.Thread(target=handle_client, args=(ip, port, conn, addr, routes))
client_thread.daemon = True
client_thread.start()
```

The main loop calls `accept()` again immediately. Each client gets its own OS thread.
`daemon=True` ensures threads are killed when the main process exits.

#### Event-driven callbacks (selectors)

```python
sel.register(server, selectors.EVENT_READ, (handle_client_callback, ip, port, routes))
server.setblocking(False)
while True:
    events = sel.select(timeout=None)
    for key, mask in events:
        callback, ... = key.data
        conn, addr = key.fileobj.accept()
        callback(key.fileobj, ..., conn, addr, ...)
```

The `selectors` module uses the OS `epoll`/`kqueue` under the hood. The single main thread
handles all connections by reacting to readiness events.

#### Asyncio coroutines

```python
server = await asyncio.start_server(handle_client_coroutine, ip, port)
async with server:
    await server.serve_forever()
```

`asyncio` manages the event loop. `handle_client_coroutine` uses `await reader.read()` so
it yields control while waiting for data — the event loop can serve other connections in
the meantime.

---

### Step 6 — Implement `daemon/proxy.py`

The proxy:

1. Binds to port 8080 and loops on `accept()`.
2. For each connection, spawns a daemon thread that calls `handle_client`.
3. `handle_client` reads the raw request, extracts the `Host` header, calls
   `resolve_routing_policy()` to find the backend from `proxy.conf`, then calls
   `forward_request()` which opens a new TCP socket to the backend and streams the response.

`SO_REUSEADDR` is set on both proxy and backend sockets so they can be restarted quickly
without "Address already in use" errors.

---

### Step 7 — Implement `apps/sampleapp.py` (Tasks 2.2 + 2.3)

#### Authentication (Task 2.2)

**Session cookie flow:**
1. Client POSTs `{"username": "x", "password": "y"}` to `/login`.
2. Handler validates against `db/users.json`.
3. On success: generates `token = uuid4().hex`, stores `sessions[token] = username`.
4. Returns `__set_cookie__: "sessionid=<token>; HttpOnly; Path=/"` sentinel.
5. `HttpAdapter` injects the `Set-Cookie` header into the HTTP response.
6. Browser stores the cookie and sends it on every subsequent request.

**Basic Auth flow:**
1. Client sends `Authorization: Basic <base64(user:pass)>` header.
2. `validate_basic_auth(headers)` decodes the base64 and checks `db/users.json`.
3. Returns the username if valid.

Both methods are checked by `require_auth(headers)` — session cookie first, Basic Auth second.

#### P2P Chat (Task 2.3)

**Initialization phase:** Peers call `/submit-info` to register with the tracker.
The tracker stores `peer_list = [{"username": ..., "ip": ..., "port": ...}, ...]`.
Peers call `/get-list` to discover each other.

**Chat phase:** Peers call `/send-peer` or `/broadcast-peer`. These directly open TCP
connections to the target peer's port and POST to `/receive-message` — no tracker relay.

**Broadcast:** Uses `threading.Thread` per peer for parallel fan-out. Each thread calls
`send_to_peer()` with a raw HTTP POST, waits up to 3 seconds, and records success/failure.

**Thread safety:** The in-memory stores (`peer_list`, `messages`, `sessions`, `channels`)
are protected by a single `threading.Lock` (`_lock`) to avoid race conditions when multiple
threads read/write simultaneously.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Sentinel keys in JSON (`__status__`, `__set_cookie__`) | Keeps handler functions pure and testable without coupling them to HTTP |
| `CaseInsensitiveDict` for headers | HTTP spec (RFC 7230) says header names are case-insensitive |
| `SO_REUSEADDR` on all sockets | Prevents "Address already in use" on rapid restart during development |
| `daemon=True` threads | Ensures no zombie threads linger after Ctrl+C |
| uuid4 session tokens | Cryptographically random, no collision risk; sufficient for course project |
| In-memory stores (not SQLite) | Stays within standard library; state is reset on restart which is acceptable for demo |
| `timeout=3` in `send_to_peer` | Prevents broadcast from hanging forever if a peer is offline |

---

## Files Created / Modified

| File | Action | Description |
|------|--------|-------------|
| `daemon/dictionary.py` | Modified | Fixed Python 3.10+ `MutableMapping` import |
| `daemon/utils.py` | Modified | Fixed Python 2 `urlparse` import |
| `daemon/request.py` | Rewritten | Full HTTP request parser |
| `daemon/response.py` | Rewritten | Full HTTP response builder with JSON + static file support |
| `daemon/httpadapter.py` | Rewritten | Complete hook dispatch and auth header injection |
| `daemon/backend.py` | Rewritten | All three non-blocking modes implemented |
| `daemon/proxy.py` | Rewritten | Multi-thread proxy with routing |
| `apps/sampleapp.py` | Rewritten | Full Task 2.2 + 2.3 implementation |
| `db/users.json` | Created | User credential store |
| `db/sessions.json` | Created | Session token store |



# AsynapRous — Architecture

## System Overview

The AsynapRous system consists of three independently runnable processes that communicate
over raw TCP sockets. A browser (or `curl`) connects to the proxy, which routes to the
backend or chat app.

---

## Component Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENT SIDE                                  │
│                                                                      │
│   Browser / curl               Peer B                               │
│       │                           │                                  │
└───────┼───────────────────────────┼──────────────────────────────────┘
        │ HTTP :8080                │ Direct TCP :2027
        ▼                           │
┌───────────────────┐               │
│   PROXY SERVER    │◄──────────────┘ (pass-through for API calls)
│   proxy.py        │
│   port 8080       │
│                   │
│  ┌─────────────┐  │
│  │ handle_     │  │   For each connection: spawn daemon thread
│  │ client      │  │   → extract Host header
│  │ (Thread)    │  │   → resolve_routing_policy(hostname, routes)
│  └──────┬──────┘  │   → forward_request(backend_ip, backend_port, raw_bytes)
│         │         │   ← stream response back to client
└─────────┼─────────┘
          │ config/proxy.conf
          │ maps host "192.168.56.114:8080" → "192.168.56.114:9000"
          │
     ┌────┴────────────────────────────────────────────┐
     │                                                 │
     ▼                                                 ▼
┌──────────────────┐                        ┌──────────────────────────┐
│  BACKEND SERVER  │                        │  SAMPLEAPP (Chat Tracker)│
│  backend.py      │                        │  sampleapp.py            │
│  port 9000       │                        │  port 2026               │
│                  │                        │                          │
│  Static files:   │                        │  In-memory stores:       │
│  • www/*.html    │                        │  • peer_list[]           │
│  • static/css/   │                        │  • sessions{}            │
│  • static/images │                        │  • messages[]            │
│                  │                        │  • channels{}            │
│  Non-blocking:   │                        │                          │
│  • threading     │                        │  REST routes:            │
│  • selectors     │                        │  /login /submit-info     │
│  • asyncio       │                        │  /get-list /add-list     │
└──────────────────┘                        │  /send-peer              │
                                            │  /broadcast-peer         │
                                            │  /get-channels etc.      │
                                            └────────────┬─────────────┘
                                                         │
                                           ┌─────────────┼──────────────┐
                                           │       P2P Chat Phase       │
                                           │  Direct TCP (no tracker)   │
                                           │                            │
                                           │  Peer A ◄────────────► Peer B
                                           │  :2026           :2027     │
                                           │  /receive-message          │
                                           └────────────────────────────┘
```

---

## Class Diagram

```
┌─────────────┐     uses      ┌──────────────┐
│ AsynapRous  │ ─────────────►│ create_backend│
│             │               └──────┬───────┘
│ .routes{}   │                      │ runs
│ .route()    │                      ▼
│ .run()      │               ┌──────────────┐
└─────────────┘               │ run_backend  │
                              │              │
                              │  mode:       │
                              │  threading──►│ threading.Thread(handle_client)
                              │  callback──► │ selectors.select() → callback
                              │  coroutine─► │ asyncio.start_server()
                              └──────┬───────┘
                                     │ creates per-connection
                                     ▼
                              ┌──────────────┐
                              │  HttpAdapter │
                              │              │
                              │ handle_client│ ◄── reads socket bytes
                              │    │         │
                              │    ├─► Request.prepare()
                              │    │     ├── extract_request_line()
                              │    │     ├── fetch_headers_body()
                              │    │     ├── prepare_headers()
                              │    │     ├── prepare_cookies()
                              │    │     └── prepare_auth()
                              │    │
                              │    ├─► routes.get((METHOD, path)) → hook
                              │    │     └── hook(headers, body) → bytes
                              │    │
                              │    └─► Response.build_json_response()
                              │          or Response.build_response()
                              │          or Response.build_unauthorized()
                              └──────────────┘
```

---

## Request Lifecycle (Callback Mode)

```
Client TCP connect
       │
       ▼
backend.run_backend() -> Selectors Event Loop
  ├── events = sel.select(timeout=None)
  └── for key, mask in events:
        │
        ├── Accept new connection: conn, addr = key.fileobj.accept()
        ├── conn.setblocking(True)       # Prevent EAGAIN during sync HTTP parsing
        └── handle_client_callback(conn)
                 │
                 ▼
         HttpAdapter.handle_client()
           ├── Request.prepare(raw)
           ├── while True: chunk = recv(4096)  # Reads Headers then Body sequentially 
           ├── routes.get((M, path)) = hook
           ├── asyncio.new_event_loop() (if hook is coroutine)
           ├── hook(headers, body) → bytes
           └── Response.build_json_response()
                 │
                 ▼
           conn.sendall(response_bytes)
```

---

## Authentication Flow (Task 2.2)

```
         SESSION COOKIE FLOW                    BASIC AUTH FLOW
         (RFC 6265)                             (RFC 7617)

Client ──PUT /login──────────────► App        Client ──POST /hello──────────────► App
       {"username":"x","pass":"y"}                    (no credentials)
                                                                                    │
App validates against db/users.json             App checks cookies + auth headers   │
       │                                               │                            │
       ▼                                               ▼                            │
App generates token = uuid4().hex            No valid session/auth found             │
sessions[token] = username                                                           │
       │                                               ▼                            │
       ▼                                        Response: 401 Unauthorized          │
Response: 200 OK                                        WWW-Authenticate: Basic     │
Set-Cookie: sessionid=<token>; HttpOnly                                             │
                                               Browser shows dialog ──────────────► │
Client stores cookie in browser               Client sends Authorization: Basic ... │
       │                                                                             │
       ▼                                        App decodes base64, validates        │
Client ──POST /hello──────────────► App         against db/users.json               │
Cookie: sessionid=<token>                                                            │
                                               Response: 200 OK  ◄──────────────────┘
App finds token in sessions{}
Response: 200 OK {"user": "x"}
```

---

## P2P Chat Phase (Task 2.3)

```
INITIALIZATION PHASE                    CHAT PHASE (direct P2P)

Peer A          Tracker          Peer B         Peer A              Peer B
  │                │                │              │                   │
  │──POST /submit-info──────────────┤              │                   │
  │  {"ip":"…","port":"2026"}       │              │                   │
  │                │                │              │                   │
  │                │◄──POST /submit-info ──────────┤              
  │                │  {"ip":"…","port":"2027"}      │              
  │                │                │              │                   │
  │──GET /get-list─►                │              │──POST /send-peer──►
  │◄──[{alice},{bob}]───────────────│              │  (TCP to :2027)   │
  │                │                │              │   /receive-message │
  │◄──────────────────GET /get-list─┤              │◄──200 OK ─────────┤
  │                │                │
  │                │   (done — chat goes direct, tracker not involved)
```
