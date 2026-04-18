# Computer Network Assignment 1 - Hybrid Chat & Non-blocking HTTP Server

This repository contains the implementation of a non-blocking HTTP server and a hybrid P2P chat application for the CO3093/CO3094 Computer Network course. 

## Project Features

### 2.1 Implement non-blocking mechanisms
* [cite_start]The implementation of non-blocking mechanisms relies on operating system services that allow I/O operations to be halted immediately rather than blocking execution[cite: 187]. 
* [cite_start]The system handles incoming connections and delegates clients to the HttpAdapter through three non-blocking strategies[cite: 266]. 
* [cite_start]These supported strategies include multi-threading, callback/event-driven using selectors, or coroutine-based async/await[cite: 266].

### 2.2 Implement the authentication for HTTP server
* [cite_start]To authenticate a user, the system implements two common approaches including HTTP headers and Cookies[cite: 272]. 
* [cite_start]The first approach uses the WWW-Authenticate header to request authentication, and the browser responds with an Authorization header carrying login details[cite: 273, 274]. 
* [cite_start]The second approach uses cookies with the Set-Cookie header when a user logs in successfully[cite: 275].

### 2.3 Implement hybrid chat application
* [cite_start]This task develops a hybrid network application that has a chat system combining both client-server and peer-to-peer (P2P) paradigms[cite: 291]. 
* [cite_start]The application supports channel management and synchronization across distributed peers[cite: 292]. 
* [cite_start]The initialization phase uses a centralized server for peer registration and discovery[cite: 345, 346, 348]. 
* [cite_start]The chatting phase allows peers to exchange messages directly without routing through the centralized server[cite: 349, 351].

---

## How to Run & Test

### Part 1: Hybrid Chat Application (Task 2.3)

**Terminal 1 — Admin / Tracker server (port 9000)**
```bash
cd http_daemon
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 9000

**Terminal 2 — User A peer server (port 8000)**
cd http_daemon
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8000

**Terminal 3 — User B peer server (port 8001)**
cd http_daemon
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8001

---

Demo flow:
Open two browser tabs (use incognito for cookie testing):

Tab A → http://127.0.0.1:8000  
→ auto-configures as user "A", trackerPort = 9000.  

Tab B → http://127.0.0.1:8001  
→ auto-configures as user "B", trackerPort = 9000.  

Both users click Register → tracker (port 9000) stores them.  

Both click Discover → see each other in the Active Peers list.  

User A clicks B's name in the list → sets target to B's port.  

Type message → Send Direct → goes peer-to-peer (A:8000 → B:8001), no tracker involved.  

Type message → Broadcast → A sends to own server which forwards to all known peers.  

Click Handshake → confirms peer is online via /connect-peer. 