// ============================================================
// STATE
// ============================================================
let currentView = "direct";
let lastMsgCount = 0;
let lastChannelMsgCounts = {}; // { channelName: count }
// knownPeers: { username: {ip, port} }  (converted from server's list format)
let knownPeers = {};
let joinedChannels = ["general"];
let isAuthenticated = false;
let trackerApiBase = "";
let authToken = "";

const PEER_COLORS = [
    "#6c5ce7",
    "#00cec9",
    "#fdcb6e",
    "#e17055",
    "#55efc4",
    "#fd79a8",
    "#74b9ff",
    "#a29bfe",
];

function getPeerColor(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i++)
        hash = name.charCodeAt(i) + ((hash << 5) - hash);
    return PEER_COLORS[Math.abs(hash) % PEER_COLORS.length];
}

// ---- URL helpers ----
// Global APIs go through the proxy at /api. Local APIs go to this
// client's own backend, which then contacts other peer backends directly.
function getMyIp() {
    return (
        document.getElementById("myIp").value.trim() ||
        window.location.hostname ||
        "127.0.0.1"
    );
}
function getMyBaseUrl() {
    return `http://${getMyIp()}:${document.getElementById("myPort").value}`;
}
function getTargetUrl() {
    return `http://${document.getElementById("targetIp").value}:${document.getElementById("targetPort").value}`;
}
function getTrackerUrl() {
    if (trackerApiBase) return trackerApiBase;
    return `${window.location.origin}/api`;
}
function authHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    if (authToken) {
        headers["Authorization"] = "Basic " + authToken;
    }
    return headers;
}

// ============================================================
// UI HELPERS
// ============================================================
function showToast(text) {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = text;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

function setStatus(text, type) {
    document.getElementById("statusText").innerText = text;
    const dot = document.getElementById("statusDot");
    dot.className =
        "status-dot" +
        (type === "error" ? " error" : type === "offline" ? " offline" : "");
}

function updateUserDisplay() {
    const name = isAuthenticated
        ? document.getElementById("myName").value || "User"
        : "Guest";
    document.getElementById("userAvatarSidebar").textContent = name
        .charAt(0)
        .toUpperCase();
    document.getElementById("userNameSidebar").textContent = name;
}
document.getElementById("myName").addEventListener("input", updateUserDisplay);

// ============================================================
// VIEW SWITCHING
// ============================================================
function switchView(view) {
    currentView = view;
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const target = document.querySelector(`[data-view="${view}"]`);
    if (target) target.classList.add("active");

    document.getElementById("chatTitle").textContent = "All Messages";
    document.getElementById("chatSubtitle").textContent =
        "Direct & Broadcast messages";
    document.getElementById("sendBtn").textContent = "➤ Send Direct";
    document.getElementById("broadcastBtn").style.display = "";
    document.getElementById("connectBtn").style.display = "";

    // NOTE: Do NOT reset lastMsgCount or clear chatMessages here.
    // Resetting would cause fetchMessages() to re-render all old messages
    // on top of existing ones → duplicate messages in the UI.
}

function switchChannel(channelName) {
    currentView = channelName;
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const target = document.querySelector(`[data-channel="${channelName}"]`);
    if (target) target.classList.add("active");

    document.getElementById("chatTitle").textContent = "# " + channelName;
    document.getElementById("chatSubtitle").textContent = "Channel messages";
    document.getElementById("sendBtn").textContent = "➤ Send to Channel";
    document.getElementById("broadcastBtn").style.display = "none";
    document.getElementById("connectBtn").style.display = "none";

    document.getElementById("chatMessages").innerHTML =
        `<div class="msg-system">Welcome to #${channelName}</div>`;
    lastChannelMsgCounts[channelName] = 0;
}

// ============================================================
// PHASE 1: INITIALIZATION — TRACKER
// ============================================================

/**
 * Register this peer with the central tracker through the proxy.
 * API endpoint: POST /api/submit-info  {username, ip, port}
 */
async function registerPeer() {
    const myName = document.getElementById("myName").value;
    const myPort = document.getElementById("myPort").value;
    const payload = JSON.stringify({
        username: myName,
        ip: getMyIp(),
        port: myPort,
    });
    const headers = authHeaders({ "Content-Type": "application/json" });

    try {
        const res = await fetch(`${getTrackerUrl()}/submit-info`, {
            method: "POST",
            headers,
            body: payload,
            credentials: "include",
        });
        const data = await res.json();
        if (data.status === "ok") {
            showToast(`Registered as ${myName}`);
            setStatus("Registered as " + myName, "success");
            document.getElementById("userStatusSidebar").textContent = "Online";
        }
    } catch (e) {
        setStatus("Registration failed", "error");
        showToast("Error: " + e.message);
    }
}

/**
 * Fetch peer list from tracker, update own peer list via /add-list,
 * and render the Active Peers panel.
 * API: GET /get-list  → {status, peers: [{username, ip, port}], count}
 */
async function getPeerList() {
    try {
        const res = await fetch(`${getTrackerUrl()}/get-list`, {
            headers: authHeaders(),
            credentials: "include",
        });
        const data = await res.json();

        // Convert list [{username, ip, port}] → dict {username: {ip, port}}
        knownPeers = {};
        (data.peers || []).forEach((p) => {
            knownPeers[p.username] = { ip: p.ip, port: p.port };
        });

        // Sync into own server so /send-peer can route messages
        const myName = document.getElementById("myName").value;
        const syncRequests = [];
        for (const [name, info] of Object.entries(knownPeers)) {
            if (name !== myName) {
                syncRequests.push(
                    fetch(`${getMyBaseUrl()}/add-list`, {
                        method: "POST",
                        headers: authHeaders({
                            "Content-Type": "application/json",
                        }),
                        body: JSON.stringify({
                            username: name,
                            ip: info.ip,
                            port: info.port,
                        }),
                        credentials: "include",
                    }).catch(() => {}),
                );
            }
        }
        await Promise.all(syncRequests);

        renderPeerList();
        const count = Object.keys(knownPeers).length;
        setStatus(`${count} peer(s) online`, "success");
        showToast(`Found ${count} peer(s)`);
    } catch (e) {
        setStatus("Discovery failed", "error");
        showToast("Error: " + e.message);
    }
}

function renderPeerList() {
    const container = document.getElementById("peerListDisplay");
    const myName = document.getElementById("myName").value;

    if (Object.keys(knownPeers).length === 0) {
        container.innerHTML =
            '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">No peers found.<br>Click "Discover".</div>';
        return;
    }

    container.innerHTML = "";
    for (const [name, info] of Object.entries(knownPeers)) {
        const color = getPeerColor(name);
        const isSelf = name === myName;
        const item = document.createElement("div");
        item.className = "peer-item";
        item.addEventListener("click", () => {
            if (!isSelf) {
                document.getElementById("targetIp").value = info.ip;
                document.getElementById("targetPort").value = info.port;
                showToast(`Target set to ${name} (${info.ip}:${info.port})`);
            }
        });
        item.innerHTML = `
            <div class="peer-avatar" style="background:${color}">${name.charAt(0).toUpperCase()}</div>
            <span class="peer-name">${name}${isSelf ? " (You)" : ""}</span>
            <span class="peer-port">${info.ip}:${info.port}</span>
            <div class="peer-status-dot"></div>
        `;
        container.appendChild(item);
    }
}

// ============================================================
// PHASE 2: CHAT — P2P COMMUNICATION
// ============================================================

/**
 * Handshake: check if target peer is alive.
 * Calls MY OWN server's /connect-peer which tries TCP to the target.
 * Returns peer_alive: true/false.
 */
async function connectPeer() {
    const targetIp = document.getElementById("targetIp").value;
    const targetPort = document.getElementById("targetPort").value;

    try {
        const res = await fetch(`${getMyBaseUrl()}/connect-peer`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ ip: targetIp, port: targetPort }),
            credentials: "include",
        });
        const data = await res.json();
        if (data.peer_alive) {
            showToast("Peer is online and ready!");
            addSystemMessage(
                `Handshake successful — peer at ${targetIp}:${targetPort} is online.`,
            );
        } else {
            showToast("Peer not responding");
            addSystemMessage(
                `Handshake failed — ${targetIp}:${targetPort} is offline.`,
            );
        }
    } catch (e) {
        showToast("Peer unreachable: " + e.message);
        addSystemMessage("Handshake failed — connection error.");
    }
}

let _sending = false;

/**
 * Send a message:
 *  - In 'direct' view: POST to MY server's /send-peer {from, to, msg}
 *    My server looks up the target peer in its registry and delivers it.
 *  - In channel view: POST /broadcast-channel {channel, from, msg}
 */
async function sendMessage() {
    if (_sending) return;
    _sending = true;
    setTimeout(() => (_sending = false), 500);

    const msgText = document.getElementById("messageInput").value.trim();
    if (!msgText) return;

    const myName = document.getElementById("myName").value;

    if (currentView === "direct") {
        // Determine the target peer name from dropdown / peer list
        const targetPort = document.getElementById("targetPort").value;
        let toName = "";
        for (const [name, info] of Object.entries(knownPeers)) {
            if (String(info.port) === String(targetPort)) {
                toName = name;
                break;
            }
        }
        if (!toName) toName = "peer"; // fallback

        try {
            const res = await fetch(`${getMyBaseUrl()}/send-peer`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                // 'msg' matches our backend API (not 'message')
                body: JSON.stringify({
                    from: myName,
                    to: toName,
                    msg: msgText,
                    ip: document.getElementById("targetIp").value,
                    port: targetPort,
                }),
                credentials: "include",
            });
            if (res.ok) {
                // Fix #1 Double-message: do NOT call addMessage() here.
                // Backend records the message in its list immediately.
                // We trigger an instant poll so the sender sees it right away
                // (instead of waiting up to 2 s for the next scheduled poll).
                document.getElementById("messageInput").value = "";
                await fetchMessages(); // instant refresh — appears once via poll
            } else {
                showToast("Failed: HTTP " + res.status);
            }
        } catch (e) {
            showToast("Connection error: " + e.message);
        }
    } else {
        // Channel message
        try {
            const res = await fetch(`${getMyBaseUrl()}/broadcast-channel`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({
                    from: myName,
                    msg: msgText,
                    channel: currentView,
                }),
                credentials: "include",
            });
            if (res.ok) {
                document.getElementById("messageInput").value = "";
                // Trigger instant poll so sender sees their own channel msg right away
                await fetchChannelMessages(currentView);
                showToast("Sent to #" + currentView);
            }
        } catch (e) {
            showToast("Error: " + e.message);
        }
    }
}

/**
 * Broadcast to ALL peers via /broadcast-peer {from, msg}.
 * My server fans-out to every peer in its registry.
 */
async function sendBroadcastMsg() {
    const msgText = document.getElementById("messageInput").value.trim();
    if (!msgText) return;

    const myName = document.getElementById("myName").value;
    try {
        const peers = Object.entries(knownPeers).map(([username, info]) => ({
            username,
            ip: info.ip,
            port: info.port,
        }));
        const res = await fetch(`${getMyBaseUrl()}/broadcast-peer`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ from: myName, msg: msgText, peers }),
            credentials: "include",
        });
        if (res.ok) {
            const data = await res.json();
            document.getElementById("messageInput").value = "";
            showToast(
                `Broadcast sent! Delivered: ${data.delivered?.length || 0} peer(s)`,
            );
        }
    } catch (e) {
        showToast("Error: " + e.message);
    }
}

// ============================================================
// MESSAGE RENDERING
// ============================================================

function addMessage(msgObj) {
    const box = document.getElementById("chatMessages");
    const group = document.createElement("div");
    group.className = "msg-group";

    // Our API uses 'msg' field; example UI used 'message' — handle both
    const text = msgObj.msg || msgObj.message || "";
    const badgeClass = msgObj.type || "direct";
    const badgeLabel = badgeClass.charAt(0).toUpperCase() + badgeClass.slice(1);
    const senderName = msgObj.from || "Unknown";
    const color = getPeerColor(senderName.replace(" (You)", ""));
    const time = new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
    });

    if (senderName.includes("(You)")) {
        group.classList.add("me");
    }

    group.innerHTML = `
        <div class="msg-sender" style="color:${color}">
            ${escapeHtml(senderName)}
            <span class="msg-badge ${badgeClass}">${badgeLabel}</span>
            <span class="msg-time">${time}</span>
        </div>
        <div class="msg-text">${escapeHtml(text)}</div>
    `;
    box.appendChild(group);
    box.scrollTop = box.scrollHeight;
}

function addSystemMessage(text) {
    const box = document.getElementById("chatMessages");
    const div = document.createElement("div");
    div.className = "msg-system";
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// POLLING — fetch new messages every 2 s
// ============================================================

/**
 * Poll /get-messages on MY server.
 * Response: {messages: [{from, to, msg, ts}]}
 * Derive type from 'to': 'broadcast' → type='broadcast', else 'direct'
 */
async function fetchMessages() {
    if (currentView !== "direct") return;
    if (!isAuthenticated) return;

    try {
        const res = await fetch(`${getMyBaseUrl()}/get-messages`, {
            headers: authHeaders(),
            credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        const msgs = data.messages || [];

        if (msgs.length > lastMsgCount) {
            const myName = document.getElementById("myName").value;
            let newFromOthers = 0;
            for (let i = lastMsgCount; i < msgs.length; i++) {
                const m = msgs[i];
                const type = m.to === "broadcast" ? "broadcast" : "direct";
                // Mark own messages with "(You)" so they're visually distinct
                const displayFrom =
                    m.from === myName ? m.from + " (You)" : m.from;
                addMessage({ type, from: displayFrom, msg: m.msg });
                if (m.from !== myName) newFromOthers++;
            }
            // Only show notification badge for messages from others
            if (newFromOthers > 0) showNotification(newFromOthers);
            lastMsgCount = msgs.length;
        }
    } catch (e) {
        /* silently ignore poll errors */
    }
}

/**
 * Poll /get-channel-messages for the current channel.
 * Response: {messages: [{from, to, msg, ts}]}
 */
async function fetchChannelMessages(channelName) {
    if (currentView !== channelName) return;
    if (!isAuthenticated) return;

    try {
        const res = await fetch(`${getMyBaseUrl()}/get-channel-messages`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ channel: channelName }),
            credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        const msgs = data.messages || [];
        const prevCount = lastChannelMsgCounts[channelName] || 0;

        if (msgs.length > prevCount) {
            for (let i = prevCount; i < msgs.length; i++) {
                addMessage({
                    type: "channel",
                    from: msgs[i].from,
                    msg: msgs[i].msg,
                });
            }
            lastChannelMsgCounts[channelName] = msgs.length;
        }
    } catch (e) {
        /* silently ignore */
    }
}

function showNotification(count) {
    const badge = document.getElementById("notificationBadge");
    badge.textContent = count + " new";
    badge.classList.add("show");
    setTimeout(() => badge.classList.remove("show"), 3000);
}

// ============================================================
// CHANNEL MANAGEMENT
// ============================================================
function openCreateChannelModal() {
    document.getElementById("channelModal").classList.add("show");
    document.getElementById("newChannelName").focus();
}
function closeModal() {
    document.getElementById("channelModal").classList.remove("show");
    document.getElementById("newChannelName").value = "";
}

async function createChannel() {
    const name = document
        .getElementById("newChannelName")
        .value.trim()
        .toLowerCase()
        .replace(/\s+/g, "-");
    if (!name) return;

    // Channels are local: just add to sidebar and start using broadcast-channel
    if (!joinedChannels.includes(name)) {
        joinedChannels.push(name);
        addChannelToSidebar(name);
    }
    showToast("Channel #" + name + " created!");
    closeModal();
    switchChannel(name);
}

function addChannelToSidebar(name) {
    const list = document.getElementById("channelList");
    const li = document.createElement("li");
    li.className = "channel-item";
    li.setAttribute("data-channel", name);
    li.addEventListener("click", () => switchChannel(name));
    li.innerHTML = `<span class="ch-hash">#</span><span>${name}</span>`;
    list.appendChild(li);
}

// ============================================================
// MAIN POLLING LOOP
// ============================================================
function pollLoop() {
    if (currentView === "direct") {
        fetchMessages();
    } else {
        fetchChannelMessages(currentView);
    }
}

// Poll messages every 2 seconds
setInterval(pollLoop, 2000);

// Refresh peer list every 10 seconds if peers are known
setInterval(() => {
    if (Object.keys(knownPeers).length > 0) getPeerList();
}, 10000);

// ============================================================
// AUTO-CONFIGURE ON LOAD
// Detect port from URL and set sensible defaults:
//   port 2026 → alice, port 2027 → bob
// ============================================================
// ============================================================
// TASK 2.2 — AUTHENTICATION: Login / Logout
// ============================================================

function openLoginModal() {
    document.getElementById("loginModal").classList.add("show");
    document.getElementById("loginUsername").focus();
    document.getElementById("loginError").style.display = "none";
}

function closeLoginModal() {
    document.getElementById("loginModal").classList.remove("show");
    document.getElementById("loginError").style.display = "none";
}

function applyAuthenticatedUser(username) {
    isAuthenticated = true;
    document.getElementById("authStatus").textContent =
        "Logged in as " + username;
    document.getElementById("loginBtn").textContent = "Logged In";
    document.getElementById("myName").disabled = false;
    document.getElementById("myName").value = username;
    updateUserDisplay();
}

async function loginUser() {
    const username = document.getElementById("loginUsername").value.trim();
    const password = document.getElementById("loginPassword").value;
    const errDiv = document.getElementById("loginError");
    errDiv.style.display = "none";

    if (!username) {
        errDiv.textContent = "Please enter a username.";
        errDiv.style.display = "block";
        return;
    }

    authToken = btoa(username + ":" + password);

    try {
        const res = await fetch(`${getTrackerUrl()}/login`, {
            method: "PUT",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ username, password }),
            credentials: "include", // accept Set-Cookie from cross-origin server
        });

        let data = {};
        try {
            data = await res.json();
        } catch (_) {}

        if (res.ok && data.status === "ok") {
            try {
                const localRes = await fetch(`${getMyBaseUrl()}/login`, {
                    method: "PUT",
                    headers: authHeaders({
                        "Content-Type": "application/json",
                    }),
                    body: JSON.stringify({
                        username: data.username || username,
                        password,
                    }),
                    credentials: "include",
                });
                if (!localRes.ok) {
                    throw new Error("Local backend rejected login");
                }
            } catch (e) {
                errDiv.textContent = "Local backend unreachable: " + e.message;
                errDiv.style.display = "block";
                return;
            }

            closeLoginModal();
            const loggedInUser = data.username || username;
            localStorage.setItem(
                "chat_auth",
                JSON.stringify({
                    username: loggedInUser,
                    token: authToken,
                }),
            );
            applyAuthenticatedUser(loggedInUser);
            showToast("Welcome, " + loggedInUser + "!");

            // Auto-register peer right after login so the user
            // can immediately start chatting without extra clicks
            await registerPeer();
        } else {
            authToken = "";
            errDiv.textContent = data.message || "Invalid credentials";
            errDiv.style.display = "block";
        }
    } catch (e) {
        authToken = "";
        errDiv.textContent = "Server unreachable: " + e.message;
        errDiv.style.display = "block";
    }
}

async function restoreSavedSession() {
    async function validateSession(baseUrl) {
        const res = await fetch(`${baseUrl}/hello`, {
            method: "GET",
            headers: authHeaders(),
            credentials: "include",
        });
        if (!res.ok) return null;

        const data = await res.json();
        return data.status === "ok" && data.user ? data.user : null;
    }

    try {
        const saved = JSON.parse(localStorage.getItem("chat_auth") || "null");
        if (saved && saved.token) {
            authToken = saved.token;
        }

        let restoredUser = await validateSession(getMyBaseUrl());
        if (!restoredUser && getMyBaseUrl() !== getTrackerUrl()) {
            restoredUser = await validateSession(getTrackerUrl());
        }

        if (restoredUser) {
            applyAuthenticatedUser(restoredUser);
            await registerPeer();
            showToast("Session restored for " + restoredUser);
        }
    } catch (e) {
        // No saved session, expired in-memory session, or tracker unreachable.
    }
}

function logoutUser() {
    isAuthenticated = false;
    authToken = "";
    localStorage.removeItem("chat_auth");
    document.getElementById("authStatus").textContent = "Not logged in";
    document.getElementById("loginBtn").textContent = "Login";
    document.getElementById("myName").disabled = true;
    document.getElementById("myName").value = "";
    updateUserDisplay();
    showToast("Logged out.");
}

function autoConfigure() {
    const params = new URLSearchParams(window.location.search);
    const port = params.get("peerPort") || params.get("myPort") || "8000";
    const host = window.location.hostname || "127.0.0.1";
    const trackerIp = params.get("trackerIp") || params.get("tracker") || host;
    const myIp = params.get("peerIp") || params.get("myIp") || "127.0.0.1";
    const targetIp = params.get("targetIp") || trackerIp;
    const apiBase = params.get("apiBase") || params.get("trackerBase");
    const configuredUsername = params.get("username") || params.get("user");
    trackerApiBase = apiBase
        ? new URL(apiBase, window.location.origin).href.replace(/\/$/, "")
        : `${window.location.origin}/api`;

    document.getElementById("myIp").value = myIp;
    document.getElementById("myPort").value = port;
    document.getElementById("trackerPort").value =
        params.get("trackerPort") || window.location.port || "8080";
    document.getElementById("trackerIp").value = trackerIp;
    document.getElementById("targetIp").value = targetIp;
    document.getElementById("targetPort").value =
        params.get("targetPort") || "8000";

    if (configuredUsername) {
        document.getElementById("loginUsername").value = configuredUsername;
    } else if (port === "2026") {
        document.getElementById("loginUsername").value = "alice";
    } else if (port === "2027") {
        document.getElementById("loginUsername").value = "bob";
    } else {
        document.getElementById("loginUsername").value = "Peer_" + port;
    }

    updateUserDisplay();
}

async function loadClientInfo() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("peerIp") || params.get("myIp")) return;

    try {
        const res = await fetch(`${getTrackerUrl()}/client-info`, {
            credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.client_ip) {
            document.getElementById("myIp").value = data.client_ip;
        }
    } catch (e) {
        // Keep the manual/default IP when proxy client-info is unavailable.
    }
}

function addClickHandler(id, handler) {
    const element = document.getElementById(id);
    if (element) {
        element.addEventListener("click", handler);
    }
}

function bindDomEvents() {
    document.querySelectorAll('[data-view="direct"]').forEach((element) => {
        element.addEventListener("click", () => switchView("direct"));
    });

    document.querySelectorAll("[data-channel]").forEach((element) => {
        element.addEventListener("click", () =>
            switchChannel(element.dataset.channel),
        );
    });

    addClickHandler("openChannelModalBtn", openCreateChannelModal);
    addClickHandler("sendBtn", sendMessage);
    addClickHandler("broadcastBtn", sendBroadcastMsg);
    addClickHandler("connectBtn", connectPeer);
    addClickHandler("registerBtn", registerPeer);
    addClickHandler("discoverBtn", getPeerList);
    addClickHandler("loginBtn", openLoginModal);
    addClickHandler("logoutBtn", logoutUser);
    addClickHandler("cancelChannelBtn", closeModal);
    addClickHandler("createChannelBtn", createChannel);
    addClickHandler("cancelLoginBtn", closeLoginModal);
    addClickHandler("submitLoginBtn", loginUser);

    document
        .getElementById("messageInput")
        .addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        });

    document
        .getElementById("newChannelName")
        .addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                createChannel();
            }
        });

    document
        .getElementById("loginUsername")
        .addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                document.getElementById("loginPassword").focus();
            }
        });

    document
        .getElementById("loginPassword")
        .addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                loginUser();
            }
        });
}

async function bootstrap() {
    bindDomEvents();
    autoConfigure();
    await loadClientInfo();
    await restoreSavedSession();
}

bootstrap();
