// ============================================================
// STATE
// ============================================================
let isAuthenticated = false;
let myUsername = "";
let myVirtualIp = "";
let authToken = ""; // Base64-encoded "user:pass" for Authorization header
let knownPeers = {}; // { username: { virtual_ip } }
let pollingInterval = null;
let peerPollingInterval = null;
let currentView = "direct";
let channels = [];
let messageStore = [];
let seenMessageKeys = new Set();

// Returns headers object with Authorization if logged in
function authHeaders(extra) {
    const h = extra ? Object.assign({}, extra) : {};
    if (authToken) h["Authorization"] = "Basic " + authToken;
    return h;
}

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
    const name = isAuthenticated ? myUsername : "Guest";
    document.getElementById("userAvatarSidebar").textContent = name
        .charAt(0)
        .toUpperCase();
    document.getElementById("userNameSidebar").textContent = name;
    document.getElementById("userStatusSidebar").textContent = isAuthenticated
        ? "Online"
        : "Not logged in";
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// VIEW SWITCHING
// ============================================================
function channelStorageKey() {
    return "chat_channels_" + (myUsername || "guest");
}

function normalizeChannelName(name) {
    return name.trim().replace(/\s+/g, "-").toLowerCase();
}

function loadChannels() {
    try {
        channels = JSON.parse(
            localStorage.getItem(channelStorageKey()) || "[]",
        );
    } catch (_) {
        channels = [];
    }
    renderChannels();
}

function saveChannels() {
    localStorage.setItem(channelStorageKey(), JSON.stringify(channels));
}

function renderChannels() {
    const list = document.getElementById("channelList");
    list.innerHTML = "";
    for (const channel of channels) {
        const item = document.createElement("li");
        item.className =
            "channel-item" + (currentView === channel ? " active" : "");
        item.dataset.view = channel;
        item.onclick = () => switchView(channel);
        item.innerHTML = `
            <span class="ch-hash">#</span>
            <span>${escapeHtml(channel)}</span>
        `;
        list.appendChild(item);
    }
}

function createChannel() {
    const rawName = prompt("Channel name");
    if (!rawName) return;

    const channel = normalizeChannelName(rawName);
    if (!channel) return;

    if (!channels.includes(channel)) {
        channels.push(channel);
        saveChannels();
        renderChannels();
    }
    switchView(channel);
}

function switchView(view) {
    currentView = view;
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const target = document.querySelector(`[data-view="${view}"]`);
    if (target) target.classList.add("active");

    if (view === "direct") {
        document.getElementById("chatTitle").textContent = "All Messages";
        document.getElementById("chatSubtitle").textContent =
            "Direct & Broadcast messages";
    } else {
        document.getElementById("chatTitle").textContent = "#" + view;
        document.getElementById("chatSubtitle").textContent =
            "Channel messages";
    }

    renderCurrentView();
}

// ============================================================
// AUTH: Login / Logout
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

function applyAuthenticatedUser(username, virtualIp) {
    isAuthenticated = true;
    myUsername = username;
    myVirtualIp = virtualIp || "—";
    document.getElementById("authStatus").textContent =
        "Logged in as " + username;
    document.getElementById("loginBtn").textContent = "Logged In";
    document.getElementById("myName").value = username;
    document.getElementById("myVirtualIp").value = myVirtualIp;
    updateUserDisplay();
    setStatus("Connected as " + username, "success");
    loadChannels();
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

    // Store Basic Auth token
    authToken = btoa(username + ":" + password);

    try {
        // Step 1: Login — get session
        const res = await fetch("/login", {
            method: "PUT",
            headers: authHeaders({
                "Content-Type": "application/json",
            }),
            body: JSON.stringify({ username, password }),
            credentials: "include",
        });

        let data = {};
        try {
            data = await res.json();
        } catch (_) {}

        if (res.ok && data.status === "ok") {
            closeLoginModal();
            const loggedInUser = data.username || username;

            // Save credentials to localStorage for session restoration
            localStorage.setItem(
                "chat_auth",
                JSON.stringify({
                    username: loggedInUser,
                    token: authToken,
                }),
            );

            // Step 2: Register peer — get virtual IP
            let virtualIp = "—";
            try {
                const regRes = await fetch("/submit-info", {
                    method: "POST",
                    headers: authHeaders({
                        "Content-Type": "application/json",
                    }),
                    body: JSON.stringify({
                        username: loggedInUser,
                    }),
                    credentials: "include",
                });
                const regData = await regRes.json();
                if (regData.peer && regData.peer.virtual_ip) {
                    virtualIp = regData.peer.virtual_ip;
                }
            } catch (_) {}

            applyAuthenticatedUser(loggedInUser, virtualIp);
            await loadMessageHistory();
            addSystemMessage(
                "Logged in as " +
                    loggedInUser +
                    " (Virtual IP: " +
                    virtualIp +
                    ")",
            );
            startPolling();
            showToast("Welcome, " + loggedInUser + "!");

            // Immediately fetch peer list
            await getPeerList();
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

async function logoutUser() {
    // Notify the server to remove this peer
    if (isAuthenticated) {
        try {
            await fetch("/logout", {
                method: "POST",
                headers: authHeaders(),
                credentials: "include",
            });
        } catch (_) {}
    }
    localStorage.removeItem("chat_auth");
    authToken = "";
    forceLogoutUI("Logged out.");
}

// Force-reset UI to logged-out state (no server call)
function forceLogoutUI(reason) {
    isAuthenticated = false;
    myUsername = "";
    myVirtualIp = "";
    authToken = "";
    localStorage.removeItem("chat_auth");
    stopPolling();

    document.getElementById("authStatus").textContent = "Not logged in";
    document.getElementById("loginBtn").textContent = "Login";
    document.getElementById("myName").value = "";
    document.getElementById("myVirtualIp").value = "—";
    currentView = "direct";
    channels = [];
    messageStore = [];
    seenMessageKeys.clear();
    renderChannels();
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const allMessages = document.querySelector('[data-view="direct"]');
    if (allMessages) allMessages.classList.add("active");
    document.getElementById("chatMessages").innerHTML =
        '<div class="msg-system">Welcome to HybridChat! Login to start chatting.</div>';
    document.getElementById("peerListDisplay").innerHTML =
        '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">No peers online yet.<br>Login to see active users.</div>';
    document.getElementById("targetPeerSelect").innerHTML =
        '<option value="">Select peer...</option>';
    updateUserDisplay();
    setStatus("Not connected", "offline");
    if (reason) showToast(reason);
}

// ============================================================
// PEER LIST
// ============================================================

async function getPeerList() {
    if (!isAuthenticated) return;

    try {
        const res = await fetch("/get-list", {
            headers: authHeaders(),
            credentials: "include",
        });
        if (!res.ok) {
            // 401 = session expired or cookie deleted
            if (res.status === 401) {
                forceLogoutUI("Session expired. Please login again.");
                return;
            }
            return;
        }
        const data = await res.json();

        // Also check JSON-level auth error
        if (data.__status__ === 401 || data.status === "error") {
            forceLogoutUI("Session expired. Please login again.");
            return;
        }

        knownPeers = {};
        (data.peers || []).forEach((p) => {
            knownPeers[p.username] = { virtual_ip: p.virtual_ip };
        });

        renderPeerList();
        updatePeerSelector();

        const count = Object.keys(knownPeers).length;
        setStatus(count + " peer(s) online", "success");
    } catch (e) {
        /* silently ignore */
    }
}

function renderPeerList() {
    const container = document.getElementById("peerListDisplay");

    if (Object.keys(knownPeers).length === 0) {
        container.innerHTML =
            '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">No peers found.</div>';
        return;
    }

    container.innerHTML = "";
    for (const [name, info] of Object.entries(knownPeers)) {
        const color = getPeerColor(name);
        const isSelf = name === myUsername;
        const item = document.createElement("div");
        item.className = "peer-item";
        if (!isSelf) {
            item.onclick = () => {
                document.getElementById("targetPeerSelect").value = name;
                showToast("Target set to " + name);
            };
        }
        item.innerHTML = `
        <div class="peer-avatar" style="background:${color}">${name.charAt(0).toUpperCase()}</div>
        <span class="peer-name">${escapeHtml(name)}${isSelf ? " (You)" : ""}</span>
        <span class="peer-port" style="font-family:monospace;">${escapeHtml(info.virtual_ip || "—")}</span>
        <div class="peer-status-dot"></div>
    `;
        container.appendChild(item);
    }
}

function updatePeerSelector() {
    const select = document.getElementById("targetPeerSelect");
    const currentValue = select.value;
    select.innerHTML = '<option value="">Select peer...</option>';
    for (const name of Object.keys(knownPeers)) {
        if (name !== myUsername) {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        }
    }
    // Restore previous selection if still available
    if (
        currentValue &&
        select.querySelector(`option[value="${currentValue}"]`)
    ) {
        select.value = currentValue;
    }
}

// ============================================================
// SEND MESSAGES
// ============================================================

let _sending = false;

async function sendMessage() {
    if (_sending) return;
    _sending = true;
    setTimeout(() => (_sending = false), 500);

    const msgText = document.getElementById("messageInput").value.trim();
    if (!msgText) return;

    const toUser = document.getElementById("targetPeerSelect").value;
    if (!toUser) {
        showToast("Please select a peer to send to!");
        return;
    }

    try {
        const res = await fetch("/send-peer", {
            method: "POST",
            headers: authHeaders({
                "Content-Type": "application/json",
            }),
            body: JSON.stringify({
                from: myUsername,
                to: toUser,
                msg: msgText,
            }),
            credentials: "include",
        });

        if (res.ok) {
            document.getElementById("messageInput").value = "";
            // Message will appear via the next poll cycle (within 1s)
        } else {
            const data = await res.json().catch(() => ({}));
            showToast("Failed: " + (data.message || "HTTP " + res.status));
        }
    } catch (e) {
        showToast("Connection error: " + e.message);
    }
}

async function sendBroadcastMsg() {
    const msgText = document.getElementById("messageInput").value.trim();
    if (!msgText) return;

    try {
        const res = await fetch("/broadcast-peer", {
            method: "POST",
            headers: authHeaders({
                "Content-Type": "application/json",
            }),
            body: JSON.stringify({
                from: myUsername,
                msg: msgText,
                channel: currentView === "direct" ? "" : currentView,
            }),
            credentials: "include",
        });

        if (res.ok) {
            document.getElementById("messageInput").value = "";
            showToast(
                currentView === "direct"
                    ? "Broadcast sent!"
                    : "Channel message sent!",
            );
            // Message will appear via the next poll cycle (within 1s)
        }
    } catch (e) {
        showToast("Error: " + e.message);
    }
}

// ============================================================
// MESSAGE RENDERING
// ============================================================

function messageKey(msgObj) {
    if (msgObj.id) return msgObj.id;
    return [
        msgObj.from || "",
        msgObj.to || "",
        msgObj.channel || "",
        msgObj.type || "",
        msgObj.ts || "",
        msgObj.msg || "",
    ].join("|");
}

function messageBelongsToCurrentView(msgObj) {
    if (currentView === "direct") {
        return !msgObj.channel;
    }
    return msgObj.channel === currentView;
}

function emptyMessageText() {
    return currentView === "direct"
        ? "No messages yet."
        : "No messages in this channel yet.";
}

function resetMessageView(emptyText) {
    seenMessageKeys.clear();
    messageStore = [];
    document.getElementById("chatMessages").innerHTML = "";
    if (emptyText) addSystemMessage(emptyText);
}

function rememberMessage(msgObj) {
    const key = messageKey(msgObj);
    if (seenMessageKeys.has(key)) return false;
    seenMessageKeys.add(key);
    if (msgObj.channel && !channels.includes(msgObj.channel)) {
        channels.push(msgObj.channel);
        saveChannels();
        renderChannels();
    }
    messageStore.push(msgObj);
    return true;
}

function renderMessageElement(msgObj) {
    const box = document.getElementById("chatMessages");
    const group = document.createElement("div");
    group.className = "msg-group";

    const text = msgObj.msg || "";
    const type = msgObj.type || "direct";
    const badgeLabel = type.charAt(0).toUpperCase() + type.slice(1);
    const senderName = msgObj.from || "Unknown";
    const color = getPeerColor(senderName);

    // Format timestamp
    let time;
    if (msgObj.ts) {
        time = new Date(msgObj.ts * 1000).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    } else {
        time = new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    const isMe = senderName === myUsername;
    const displayName = isMe ? senderName + " (You)" : senderName;

    if (isMe) {
        group.classList.add("me");
    }

    group.innerHTML = `
    <div class="msg-sender" style="color:${color}">
        ${escapeHtml(displayName)}
        <span class="msg-badge ${type}">${badgeLabel}</span>
        <span class="msg-time">${time}</span>
    </div>
    <div class="msg-text">${escapeHtml(text)}</div>
`;
    box.appendChild(group);
    box.scrollTop = box.scrollHeight;
}

function renderCurrentView() {
    const visibleMessages = messageStore.filter(messageBelongsToCurrentView);
    document.getElementById("chatMessages").innerHTML = "";

    if (visibleMessages.length === 0) {
        addSystemMessage(emptyMessageText());
        return;
    }

    for (const msg of visibleMessages) {
        renderMessageElement(msg);
    }
}

function addMessage(msgObj) {
    const added = rememberMessage(msgObj);
    if (!added) return false;

    if (messageBelongsToCurrentView(msgObj)) {
        const onlyEmptyMessage =
            document.querySelectorAll("#chatMessages .msg-group").length === 0;
        if (onlyEmptyMessage) {
            document.getElementById("chatMessages").innerHTML = "";
        }
        renderMessageElement(msgObj);
    }

    return true;
}

function addSystemMessage(text) {
    const box = document.getElementById("chatMessages");
    const div = document.createElement("div");
    div.className = "msg-system";
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

// ============================================================
// POLLING — fetch new messages every 1 second
// ============================================================

async function loadMessageHistory() {
    if (!isAuthenticated) return;

    try {
        const res = await fetch("/message-history", {
            headers: authHeaders(),
            credentials: "include",
        });

        if (!res.ok) {
            if (res.status === 401) {
                forceLogoutUI("Session expired. Please login again.");
            }
            return;
        }

        const data = await res.json();
        if (data.__status__ === 401 || data.status === "error") {
            forceLogoutUI("Session expired. Please login again.");
            return;
        }

        const msgs = data.messages || [];
        resetMessageView("");
        for (const m of msgs) {
            rememberMessage(m);
        }
        renderCurrentView();
    } catch (e) {
        /* keep restored login usable even if history fails */
    }
}

async function fetchMessages() {
    if (!isAuthenticated) return;

    try {
        const res = await fetch("/fetch-messages", {
            headers: authHeaders(),
            credentials: "include",
        });

        // Handle auth failure (cookie deleted, session expired)
        if (!res.ok) {
            if (res.status === 401) {
                forceLogoutUI("Session expired. Please login again.");
            }
            return;
        }

        const data = await res.json();

        // Also check JSON-level auth error
        if (data.__status__ === 401 || data.status === "error") {
            forceLogoutUI("Session expired. Please login again.");
            return;
        }

        const msgs = data.messages || [];

        let newFromOthers = 0;
        for (const m of msgs) {
            if (addMessage(m) && m.from !== myUsername) newFromOthers++;
        }

        if (newFromOthers > 0) {
            showNotification(newFromOthers);
        }
    } catch (e) {
        /* silently ignore poll errors */
    }
}

function showNotification(count) {
    const badge = document.getElementById("notificationBadge");
    badge.textContent = count + " new";
    badge.classList.add("show");
    setTimeout(() => badge.classList.remove("show"), 3000);
}

function startPolling() {
    stopPolling();
    // Fetch messages every 1 second
    pollingInterval = setInterval(fetchMessages, 1000);
    // Refresh peer list every 5 seconds
    peerPollingInterval = setInterval(getPeerList, 5000);
}

function stopPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    if (peerPollingInterval) {
        clearInterval(peerPollingInterval);
        peerPollingInterval = null;
    }
}

// ============================================================
// INITIALIZATION — restore session from localStorage
// ============================================================
(async function init() {
    // Use saved Basic auth when available, but the HttpOnly session
    // cookie is enough for restore after a normal browser refresh.
    const saved = localStorage.getItem("chat_auth");
    let token = "";

    try {
        if (saved) {
            const parsed = JSON.parse(saved);
            token = parsed.token || "";
        }
        authToken = token;

        // Validate credentials with the server
        const res = await fetch("/hello", {
            method: "GET",
            headers: authHeaders(),
            credentials: "include",
        });

        if (res.ok) {
            const data = await res.json();
            if (data.status === "ok" && data.user) {
                // Valid session — restore it and re-register
                let virtualIp = "—";
                try {
                    const regRes = await fetch("/submit-info", {
                        method: "POST",
                        headers: authHeaders({
                            "Content-Type": "application/json",
                        }),
                        body: JSON.stringify({
                            username: data.user,
                        }),
                        credentials: "include",
                    });
                    const regData = await regRes.json();
                    if (regData.peer && regData.peer.virtual_ip) {
                        virtualIp = regData.peer.virtual_ip;
                    }
                } catch (_) {}

                applyAuthenticatedUser(data.user, virtualIp);
                await loadMessageHistory();
                startPolling();
                showToast("Session restored for " + data.user);
                await getPeerList();
                return;
            }
        }
    } catch (_) {}

    // Invalid saved credentials
    forceLogoutUI();
})();
