// ============================================================
// STATE
// ============================================================
let currentView = "direct";
let currentDirectPeer = null;
let lastMsgCount = 0;
let directMessages = [];
let seenDirectMessageKeys = new Set();
let channelMessages = {}; // { channelName: Array<message> }
let lastChannelMsgCounts = {}; // { channelName: count }
let seenChannelMessageKeys = {}; // { channelName: Set<string> }
// knownPeers: { username: {ip, port} }  (converted from server's list format)
let knownPeers = {};
let joinedChannels = ["general"];
let isAuthenticated = false;
let trackerApiBase = "";
let authToken = "";
let selectedPeer = null;
let historyReadyForOwner = "";
let directCryptoKeys = null;
let directPublicKey = "";

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

function currentOwner() {
    return document.getElementById("myName").value.trim();
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
function getLoopbackBackendUrl() {
    return `http://127.0.0.1:${document.getElementById("myPort").value}`;
}
function getTrackerUrl() {
    if (trackerApiBase) return trackerApiBase;

    const trackerIp =
        document.getElementById("trackerIp").value.trim() ||
        window.location.hostname;
    const trackerPort = document.getElementById("trackerPort").value.trim();
    const origin = `http://${trackerIp}:${trackerPort}`;

    // 3001 is the central backend itself. Proxy ports expose it under /api.
    return trackerPort === "3001" ? origin : `${origin}/api`;
}
function authHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    if (authToken) {
        headers["Authorization"] = "Basic " + authToken;
    }
    return headers;
}

function bufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    bytes.forEach((byte) => {
        binary += String.fromCharCode(byte);
    });
    return btoa(binary);
}

function base64ToBuffer(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

function cryptoStorageKey(username) {
    return `chat_crypto_${username}`;
}

async function ensureDirectCryptoKeys(username) {
    if (directCryptoKeys && directPublicKey) {
        return { keyPair: directCryptoKeys, publicKey: directPublicKey };
    }

    const stored = JSON.parse(
        localStorage.getItem(cryptoStorageKey(username)) || "null",
    );
    if (stored && stored.privateKey && stored.publicKey) {
        const privateKey = await crypto.subtle.importKey(
            "jwk",
            stored.privateKey,
            { name: "RSA-OAEP", hash: "SHA-256" },
            true,
            ["unwrapKey", "decrypt"],
        );
        directCryptoKeys = { privateKey };
        directPublicKey = stored.publicKey;
        return { keyPair: directCryptoKeys, publicKey: directPublicKey };
    }

    const keyPair = await crypto.subtle.generateKey(
        {
            name: "RSA-OAEP",
            modulusLength: 2048,
            publicExponent: new Uint8Array([1, 0, 1]),
            hash: "SHA-256",
        },
        true,
        ["wrapKey", "unwrapKey"],
    );
    const publicSpki = await crypto.subtle.exportKey("spki", keyPair.publicKey);
    const privateJwk = await crypto.subtle.exportKey("jwk", keyPair.privateKey);
    const publicKey = bufferToBase64(publicSpki);

    localStorage.setItem(
        cryptoStorageKey(username),
        JSON.stringify({ privateKey: privateJwk, publicKey }),
    );
    directCryptoKeys = { privateKey: keyPair.privateKey };
    directPublicKey = publicKey;
    return { keyPair: directCryptoKeys, publicKey: directPublicKey };
}

async function encryptDirectMessage(plaintext, recipientPublicKey) {
    const publicKey = await crypto.subtle.importKey(
        "spki",
        base64ToBuffer(recipientPublicKey),
        { name: "RSA-OAEP", hash: "SHA-256" },
        true,
        ["wrapKey"],
    );
    const aesKey = await crypto.subtle.generateKey(
        { name: "AES-GCM", length: 256 },
        true,
        ["encrypt", "decrypt"],
    );
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        aesKey,
        new TextEncoder().encode(plaintext),
    );
    const wrappedKey = await crypto.subtle.wrapKey("raw", aesKey, publicKey, {
        name: "RSA-OAEP",
    });

    return JSON.stringify({
        alg: "RSA-OAEP-256+A256GCM",
        wrapped_key: bufferToBase64(wrappedKey),
        iv: bufferToBase64(iv.buffer),
        ciphertext: bufferToBase64(ciphertext),
    });
}

async function decryptDirectMessage(envelopeText) {
    if (!directCryptoKeys || !directCryptoKeys.privateKey) {
        await ensureDirectCryptoKeys(currentOwner());
    }
    const envelope = JSON.parse(envelopeText);
    const aesKey = await crypto.subtle.unwrapKey(
        "raw",
        base64ToBuffer(envelope.wrapped_key),
        directCryptoKeys.privateKey,
        { name: "RSA-OAEP" },
        { name: "AES-GCM", length: 256 },
        false,
        ["decrypt"],
    );
    const plaintext = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: new Uint8Array(base64ToBuffer(envelope.iv)) },
        aesKey,
        base64ToBuffer(envelope.ciphertext),
    );
    return new TextDecoder().decode(plaintext);
}

async function readJsonResponse(response) {
    const text = await response.text();
    if (!text) return {};
    try {
        return JSON.parse(text);
    } catch (e) {
        return { status: "error", message: text || response.statusText };
    }
}

function isLoopbackIp(ip) {
    return !ip || ip === "127.0.0.1" || ip === "::1" || ip === "localhost";
}

function requireAuthenticated(message = "Please log in first.") {
    if (isAuthenticated) return true;
    showToast(message);
    renderSignedOutState();
    return false;
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

function updateDirectSubtitle() {
    if (currentDirectPeer) {
        document.getElementById("chatSubtitle").textContent =
            `Direct message to ${currentDirectPeer}`;
        return;
    }
    document.getElementById("chatSubtitle").textContent =
        "Direct & Broadcast messages";
}

function resetChannelSidebar() {
    joinedChannels = ["general"];
    document.querySelectorAll("#channelList [data-channel]").forEach((item) => {
        if (item.dataset.channel === "general") {
            item.className = "channel-item";
        } else {
            item.remove();
        }
    });
}

function renderSignedOutState() {
    currentView = "direct";
    currentDirectPeer = null;
    selectedPeer = null;
    knownPeers = {};
    resetHistoryMemory();
    resetChannelSidebar();

    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const directTab = document.querySelector('[data-view="direct"]');
    if (directTab) directTab.classList.add("active");

    document.getElementById("chatTitle").textContent = "All Messages";
    document.getElementById("chatSubtitle").textContent =
        "Authentication required";
    document.getElementById("sendBtn").textContent = "➤ Send Direct";
    document.getElementById("broadcastBtn").style.display = "";
    document.getElementById("connectBtn").style.display = "";
    document.getElementById("messageInput").value = "";
    document.getElementById("chatMessages").innerHTML =
        '<div class="msg-system">Log in to view chat history and message peers.</div>';
    document.getElementById("peerListDisplay").innerHTML =
        '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">Log in to discover peers.</div>';
    document.getElementById("notificationBadge").classList.remove("show");
    document.getElementById("userStatusSidebar").textContent = "Not registered";
    setStatus("Not logged in", "offline");
}

// ============================================================
// VIEW SWITCHING
// ============================================================
function switchView(view) {
    if (!requireAuthenticated()) return;

    currentView = view;
    if (view === "direct") {
        currentDirectPeer = null;
    }
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const target = document.querySelector(`[data-view="${view}"]`);
    if (target) target.classList.add("active");

    document.getElementById("chatTitle").textContent = "All Messages";
    updateDirectSubtitle();
    document.getElementById("sendBtn").textContent = "➤ Send Direct";
    document.getElementById("broadcastBtn").style.display = "";
    document.getElementById("connectBtn").style.display = "";

    renderDirectMessages();
}

function switchDirectConversation(username) {
    if (!requireAuthenticated()) return;

    currentView = "direct";
    currentDirectPeer = username;
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const target = Array.from(
        document.querySelectorAll("[data-direct-peer]"),
    ).find((el) => el.dataset.directPeer === username);
    if (target) target.classList.add("active");

    if (knownPeers[username]) {
        selectedPeer = {
            username,
            ip: knownPeers[username].ip,
            port: knownPeers[username].port,
            online: knownPeers[username].online,
            public_key: knownPeers[username].public_key || "",
        };
    } else if (!selectedPeer || selectedPeer.username !== username) {
        selectedPeer = { username, ip: "", port: "" };
    }

    document.getElementById("chatTitle").textContent = username;
    updateDirectSubtitle();
    document.getElementById("sendBtn").textContent = "➤ Send Direct";
    document.getElementById("broadcastBtn").style.display = "none";
    document.getElementById("connectBtn").style.display = knownPeers[username]
        ? ""
        : "none";
    renderPeerList();
    renderDirectMessages();
}

function switchChannel(channelName) {
    if (!requireAuthenticated()) return;

    currentView = channelName;
    document
        .querySelectorAll(".channel-item")
        .forEach((el) => el.classList.remove("active"));
    const target = Array.from(document.querySelectorAll("[data-channel]")).find(
        (el) => el.dataset.channel === channelName,
    );
    if (target) target.classList.add("active");

    document.getElementById("chatTitle").textContent = "# " + channelName;
    document.getElementById("chatSubtitle").textContent = "Channel messages";
    document.getElementById("sendBtn").textContent = "➤ Send to Channel";
    document.getElementById("broadcastBtn").style.display = "none";
    document.getElementById("connectBtn").style.display = "none";

    renderChannelMessages(channelName);
}

// ============================================================
// PHASE 1: INITIALIZATION — TRACKER
// ============================================================

/**
 * Register this peer with the central tracker through the proxy.
 * API endpoint: POST /api/submit-info  {username, ip, port}
 */
async function registerPeer() {
    if (!requireAuthenticated("Log in before registering with the tracker."))
        return;

    const myName = document.getElementById("myName").value;
    const myPort = document.getElementById("myPort").value;
    const { publicKey } = await ensureDirectCryptoKeys(myName);
    const payload = JSON.stringify({
        username: myName,
        ip: getMyIp(),
        port: myPort,
        public_key: publicKey,
    });
    const headers = authHeaders({ "Content-Type": "application/json" });

    try {
        const res = await fetch(`${getTrackerUrl()}/submit-info`, {
            method: "POST",
            headers,
            body: payload,
            credentials: "include",
        });
        const data = await readJsonResponse(res);
        if (res.ok && data.status === "ok") {
            showToast(`Registered as ${myName}`);
            setStatus("Registered as " + myName, "success");
            document.getElementById("userStatusSidebar").textContent = "Online";
        } else {
            throw new Error(data.message || `HTTP ${res.status}`);
        }
    } catch (e) {
        setStatus("Registration failed", "error");
        showToast("Error: " + e.message);
    }
}

/**
 * Fetch peer list from tracker, update own peer list via /add-list,
 * and render the Peers panel.
 * API: GET /get-list  → {status, peers: [{username, ip, port}], count}
 */
async function getPeerList(showFeedback = true) {
    if (!isAuthenticated) {
        renderSignedOutState();
        if (showFeedback) showToast("Log in before discovering peers.");
        return;
    }

    try {
        const res = await fetch(`${getTrackerUrl()}/get-list`, {
            headers: authHeaders(),
            credentials: "include",
        });
        const data = await readJsonResponse(res);
        if (!res.ok || data.status === "error") {
            throw new Error(data.message || `HTTP ${res.status}`);
        }

        // Convert list [{username, ip, port, online}] → dict
        knownPeers = {};
        (data.peers || []).forEach((p) => {
            knownPeers[p.username] = {
                ip: p.ip,
                port: p.port,
                online: p.online !== false,
                last_seen: p.last_seen,
                public_key: p.public_key || "",
            };
        });

        if (selectedPeer) {
            const currentSelection = knownPeers[selectedPeer.username];
            if (currentSelection) {
                selectedPeer = {
                    username: selectedPeer.username,
                    ip: currentSelection.ip,
                    port: currentSelection.port,
                    online: currentSelection.online,
                    public_key: currentSelection.public_key || "",
                };
            } else {
                selectedPeer = null;
                if (currentView === "direct" && !currentDirectPeer) {
                    updateDirectSubtitle();
                }
            }
        }

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
                            online: info.online,
                            last_seen: info.last_seen,
                            public_key: info.public_key || "",
                        }),
                        credentials: "include",
                    }).catch(() => {}),
                );
            }
        }
        await Promise.all(syncRequests);

        renderPeerList();
        const peers = Object.values(knownPeers);
        const onlineCount = peers.filter((peer) => peer.online).length;
        setStatus(`${onlineCount}/${peers.length} peer(s) online`, "success");
        if (showFeedback) showToast(`Found ${peers.length} peer(s)`);
        await syncChannels();
    } catch (e) {
        setStatus("Discovery failed", "error");
        if (showFeedback) showToast("Error: " + e.message);
    }
}

function selectPeer(username) {
    const info = knownPeers[username];
    if (!info) return;

    selectedPeer = {
        username,
        ip: info.ip,
        port: info.port,
        online: info.online,
        public_key: info.public_key || "",
    };
    ensureDirectConversation(username);
    renderPeerList();
    switchDirectConversation(username);
    showToast(`Selected ${username} (${info.ip}:${info.port})`);
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
        item.className =
            "peer-item" +
            (info.online === false ? " offline" : "") +
            (selectedPeer && selectedPeer.username === name ? " selected" : "");
        item.addEventListener("click", () => {
            if (!isSelf) {
                selectPeer(name);
            }
        });
        item.innerHTML = `
            <div class="peer-avatar" style="background:${color}">${name.charAt(0).toUpperCase()}</div>
            <span class="peer-name">${name}${isSelf ? " (You)" : ""}</span>
            <span class="peer-port">${info.ip}:${info.port}</span>
            <div class="peer-status-dot ${info.online === false ? "offline" : ""}"></div>
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
    if (!requireAuthenticated("Log in before handshaking with peers.")) return;

    if (!selectedPeer) {
        showToast("Select a peer first.");
        return;
    }

    const targetIp = selectedPeer.ip;
    const targetPort = selectedPeer.port;

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
    if (!requireAuthenticated("Log in before sending messages.")) return;

    if (_sending) return;
    _sending = true;
    setTimeout(() => (_sending = false), 500);

    const msgText = document.getElementById("messageInput").value.trim();
    if (!msgText) return;

    const myName = document.getElementById("myName").value;

    if (currentView === "direct") {
        if (
            currentDirectPeer &&
            (!selectedPeer || selectedPeer.username !== currentDirectPeer)
        ) {
            const info = knownPeers[currentDirectPeer];
            if (info) {
                selectedPeer = {
                    username: currentDirectPeer,
                    ip: info.ip,
                    port: info.port,
                    online: info.online,
                    public_key: info.public_key || "",
                };
            }
        }

        if (!selectedPeer || !selectedPeer.ip || !selectedPeer.port) {
            showToast("Select a peer from Peers first.");
            return;
        }

        try {
            if (!selectedPeer.public_key) {
                showToast("Selected peer has no encryption key yet.");
                return;
            }

            const messageId = crypto.randomUUID();
            const createdAt = Date.now();
            const encryptedText = await encryptDirectMessage(
                msgText,
                selectedPeer.public_key,
            );
            const localRecord = {
                id: messageId,
                owner: myName,
                type: "direct",
                from: myName,
                to: selectedPeer.username,
                channel: null,
                peer: selectedPeer.username,
                msg: msgText,
                ts: createdAt,
                delivery_status:
                    selectedPeer.online === false ? "queued" : "sending",
            };
            seenDirectMessageKeys.add(messageId);
            directMessages.push(localRecord);
            directMessages.sort((a, b) => a.ts - b.ts);
            ensureDirectConversation(selectedPeer.username);
            renderDirectMessages();
            await persistHistoryMessage(localRecord);

            const res = await fetch(`${getMyBaseUrl()}/send-peer`, {
                method: "POST",
                headers: authHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({
                    id: messageId,
                    from: myName,
                    to: selectedPeer.username,
                    msg: encryptedText,
                    encrypted: true,
                    created_at: createdAt,
                    ip: selectedPeer.ip,
                    port: selectedPeer.port,
                    online: selectedPeer.online,
                }),
                credentials: "include",
            });
            if (res.ok) {
                const data = await readJsonResponse(res);
                localRecord.delivery_status = data.delivered
                    ? "delivered"
                    : data.backup_holder
                      ? `queued via ${data.backup_holder}`
                      : "queued";
                document.getElementById("messageInput").value = "";
                renderDirectMessages();
                await persistHistoryMessage(localRecord);
            } else {
                localRecord.delivery_status = "failed";
                renderDirectMessages();
                showToast("Failed: HTTP " + res.status);
            }
        } catch (e) {
            showToast("Connection error: " + e.message);
        }
    } else {
        // Channel message
        try {
            await getPeerList(false);
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
 * Broadcast to other peers via /broadcast-peer {from, msg}.
 * My server fans out to known peers and skips the sender.
 */
async function sendBroadcastMsg() {
    if (!requireAuthenticated("Log in before broadcasting messages.")) return;

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
            await fetchMessages();
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
    const myName = document.getElementById("myName").value.trim();
    const baseSenderName = senderName.replace(" (You)", "");
    const isOwnMessage = msgObj.own || (myName && baseSenderName === myName);
    const displaySender = isOwnMessage ? `${baseSenderName} (You)` : senderName;
    const color = getPeerColor(baseSenderName);
    const time = new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
    });
    const statusText = msgObj.delivery_status
        ? `<span class="msg-time">${escapeHtml(msgObj.delivery_status)}</span>`
        : "";

    if (isOwnMessage) {
        group.classList.add("me");
    }

    group.innerHTML = `
        <div class="msg-sender" style="color:${color}">
            ${escapeHtml(displaySender)}
            <span class="msg-badge ${badgeClass}">${badgeLabel}</span>
            <span class="msg-time">${time}</span>
            ${statusText}
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

function channelMessageKey(channelName, msg) {
    if (msg.id) return msg.id;
    return [
        msg.owner || "",
        msg.type || "channel",
        channelName,
        msg.from || "",
        msg.to || "",
        msg.ts || "",
        msg.msg || "",
    ].join("|");
}

function directMessageKey(msg) {
    if (msg.id) return msg.id;
    return [
        msg.owner || "",
        msg.type || "",
        msg.from || "",
        msg.to || "",
        msg.ts || "",
        msg.msg || "",
    ].join("|");
}

function directConversationPartner(msg, myName) {
    if (!msg || msg.type !== "direct") return null;
    return msg.from === myName ? msg.to : msg.from;
}

function normalizeDirectHistoryMessage(msg, owner) {
    const type = msg.type || (msg.to === "broadcast" ? "broadcast" : "direct");
    const peer =
        type === "direct"
            ? directConversationPartner({ ...msg, type }, owner)
            : null;
    const ts =
        typeof msg.created_at === "number"
            ? msg.created_at
            : typeof msg.ts === "number"
              ? msg.ts
              : msg.ts
                ? Number(msg.ts)
                : Date.now();
    const record = {
        owner,
        type,
        from: msg.from || "",
        to: msg.to || (type === "broadcast" ? "broadcast" : ""),
        channel: null,
        peer,
        msg: msg.msg || msg.message || "",
        ts,
        encrypted: msg.encrypted === true,
        delivery_status: msg.delivery_status || "",
    };
    record.id = msg.id || directMessageKey(record);
    return record;
}

function normalizeChannelHistoryMessage(channelName, msg, owner) {
    const ts =
        typeof msg.ts === "number"
            ? msg.ts
            : msg.ts
              ? Number(msg.ts)
              : Date.now();
    const channel =
        normalizeChannelName(msg.channel || channelName) || channelName;
    const record = {
        owner,
        type: "channel",
        from: msg.from || "",
        to: msg.to || channel,
        channel,
        peer: null,
        msg: msg.msg || msg.message || "",
        ts,
    };
    record.id = msg.id || channelMessageKey(channel, record);
    return record;
}

async function persistHistoryMessage(record) {
    if (!window.ChatHistoryDB || !record.owner) return;
    try {
        await window.ChatHistoryDB.saveMessage(record);
    } catch (e) {
        console.warn("[History] save failed", e);
    }
}

function resetHistoryMemory() {
    directMessages = [];
    seenDirectMessageKeys = new Set();
    channelMessages = {};
    lastChannelMsgCounts = {};
    seenChannelMessageKeys = {};
    lastMsgCount = 0;
    currentDirectPeer = null;
    document
        .querySelectorAll("[data-direct-peer]")
        .forEach((element) => element.remove());
}

async function loadHistoryForOwner(owner) {
    if (!owner || !window.ChatHistoryDB || historyReadyForOwner === owner)
        return;

    resetHistoryMemory();
    try {
        await window.ChatHistoryDB.open();
        const records = await window.ChatHistoryDB.getMessagesByOwner(owner);
        records.forEach((record) => {
            if (record.type === "channel" && record.channel) {
                const channel = normalizeChannelName(record.channel);
                if (!channelMessages[channel]) channelMessages[channel] = [];
                if (!seenChannelMessageKeys[channel]) {
                    seenChannelMessageKeys[channel] = new Set();
                }
                if (!seenChannelMessageKeys[channel].has(record.id)) {
                    channelMessages[channel].push(record);
                    seenChannelMessageKeys[channel].add(record.id);
                    ensureChannelInSidebar(channel);
                }
                return;
            }

            if (!seenDirectMessageKeys.has(record.id)) {
                directMessages.push(record);
                seenDirectMessageKeys.add(record.id);
                if (record.peer) ensureDirectConversation(record.peer);
            }
        });

        Object.keys(channelMessages).forEach((channel) => {
            channelMessages[channel].sort((a, b) => a.ts - b.ts);
            lastChannelMsgCounts[channel] = channelMessages[channel].length;
        });
        directMessages.sort((a, b) => a.ts - b.ts);
        historyReadyForOwner = owner;
        if (currentView === "direct") renderDirectMessages();
        else renderChannelMessages(currentView);
    } catch (e) {
        console.warn("[History] load failed", e);
    }
}

function ensureDirectConversation(username) {
    if (!username || username === "me") return;
    const list = document.getElementById("directMsgList");
    if (!list) return;
    const existing = Array.from(
        list.querySelectorAll("[data-direct-peer]"),
    ).find((item) => item.dataset.directPeer === username);
    if (existing) return;

    const item = document.createElement("li");
    item.className = "channel-item direct-peer-item";
    item.setAttribute("data-direct-peer", username);
    item.addEventListener("click", () => switchDirectConversation(username));

    const avatar = document.createElement("span");
    avatar.className = "direct-peer-avatar";
    avatar.style.background = getPeerColor(username);
    avatar.textContent = username.charAt(0).toUpperCase();

    const label = document.createElement("span");
    label.className = "channel-name";
    label.textContent = username;

    item.append(avatar, label);
    list.appendChild(item);
}

function renderDirectMessages() {
    if (currentView !== "direct") return;

    const box = document.getElementById("chatMessages");
    const myName = document.getElementById("myName").value;
    const visibleMessages = directMessages.filter((msg) => {
        if (!currentDirectPeer) return true;
        return directConversationPartner(msg, myName) === currentDirectPeer;
    });

    box.innerHTML = "";
    if (visibleMessages.length === 0) {
        addSystemMessage(
            currentDirectPeer
                ? `No messages with ${currentDirectPeer} yet.`
                : "No direct or broadcast messages yet.",
        );
        return;
    }

    visibleMessages.forEach((msg) => {
        const from = msg.from === myName ? `${msg.from} (You)` : msg.from;
        addMessage({
            type: msg.type,
            from,
            msg: msg.msg,
            delivery_status: msg.delivery_status,
        });
    });
}

function renderChannelMessages(channelName) {
    if (currentView !== channelName) return;

    const box = document.getElementById("chatMessages");
    const messages = channelMessages[channelName] || [];
    box.innerHTML = `<div class="msg-system">Welcome to #${channelName}</div>`;

    messages.forEach((msg) => {
        addMessage({
            type: "channel",
            from: msg.from,
            msg: msg.msg,
        });
    });
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
    if (!isAuthenticated) return;

    try {
        const res = await fetch(`${getMyBaseUrl()}/get-messages`, {
            headers: authHeaders(),
            credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        const msgs = data.messages || [];
        const myName = document.getElementById("myName").value;
        let newFromOthers = 0;
        let changed = false;

        for (const msg of msgs) {
            const typedMsg = normalizeDirectHistoryMessage(msg, myName);
            const key = typedMsg.id;
            if (seenDirectMessageKeys.has(key)) continue;

            if (typedMsg.encrypted && typedMsg.from !== myName) {
                try {
                    typedMsg.msg = await decryptDirectMessage(typedMsg.msg);
                    typedMsg.encrypted = false;
                } catch (e) {
                    typedMsg.msg = "[Encrypted message could not be decrypted]";
                }
            }

            seenDirectMessageKeys.add(key);
            directMessages.push(typedMsg);
            changed = true;

            if (typedMsg.peer) {
                ensureDirectConversation(typedMsg.peer);
            }
            await persistHistoryMessage(typedMsg);

            if (typedMsg.from !== myName) newFromOthers++;
        }

        lastMsgCount = msgs.length;
        if (changed) {
            directMessages.sort((a, b) => a.ts - b.ts);
            renderDirectMessages();
            if (newFromOthers > 0) showNotification(newFromOthers);
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
        const owner = currentOwner();
        let changed = false;
        if (!seenChannelMessageKeys[channelName]) {
            seenChannelMessageKeys[channelName] = new Set();
        }
        if (!channelMessages[channelName]) {
            channelMessages[channelName] = [];
        }

        for (const msg of msgs) {
            const record = normalizeChannelHistoryMessage(
                channelName,
                msg,
                owner,
            );
            const key = record.id;
            if (seenChannelMessageKeys[channelName].has(key)) continue;
            seenChannelMessageKeys[channelName].add(key);
            channelMessages[channelName].push(record);
            changed = true;
            await persistHistoryMessage(record);
        }

        lastChannelMsgCounts[channelName] = msgs.length;
        if (changed) {
            channelMessages[channelName].sort((a, b) => a.ts - b.ts);
            renderChannelMessages(channelName);
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
function normalizeChannelName(rawName) {
    return String(rawName || "")
        .trim()
        .toLowerCase()
        .replace(/^#+/, "")
        .replace(/\s+/g, "-")
        .replace(/[^a-z0-9_-]/g, "");
}

function openCreateChannelModal() {
    if (!requireAuthenticated("Log in before creating channels.")) return;

    document.getElementById("channelModal").classList.add("show");
    document.getElementById("newChannelName").focus();
}
function closeModal() {
    document.getElementById("channelModal").classList.remove("show");
    document.getElementById("newChannelName").value = "";
}

async function createChannel() {
    if (!requireAuthenticated("Log in before creating channels.")) return;

    const name = normalizeChannelName(
        document.getElementById("newChannelName").value,
    );
    if (!name) return;
    if (name === "direct") {
        showToast("Channel name is reserved.");
        return;
    }

    try {
        await getPeerList(false);

        const myName = document.getElementById("myName").value;
        const peers = Object.entries(knownPeers).map(([username, info]) => ({
            username,
            ip: info.ip,
            port: info.port,
        }));

        const localRes = await fetch(`${getMyBaseUrl()}/create-channel`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                channel: name,
                username: myName,
                ip: getMyIp(),
                port: document.getElementById("myPort").value,
                peers,
            }),
            credentials: "include",
        });
        const data = await readJsonResponse(localRes);
        if (!localRes.ok || data.status === "error") {
            throw new Error(data.message || `HTTP ${localRes.status}`);
        }

        const channelName = normalizeChannelName(data.channel || name);

        fetch(`${getTrackerUrl()}/create-channel`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                channel: channelName,
                username: myName,
                ip: getMyIp(),
                port: document.getElementById("myPort").value,
                announce: false,
            }),
            credentials: "include",
        }).catch(() => {});

        ensureChannelInSidebar(channelName);
        showToast(
            `Channel #${channelName} created. Notified ${data.delivered?.length || 0} peer(s).`,
        );
        closeModal();
        switchChannel(channelName);
        await syncChannels();
    } catch (e) {
        showToast("Create channel failed: " + e.message);
    }
}

function ensureChannelInSidebar(name) {
    if (!name || joinedChannels.includes(name)) return;
    joinedChannels.push(name);
    addChannelToSidebar(name);
}

function removeChannelFromSidebar(name) {
    joinedChannels = joinedChannels.filter((channel) => channel !== name);
    delete lastChannelMsgCounts[name];
    delete seenChannelMessageKeys[name];

    const item = document.querySelector(`[data-channel="${name}"]`);
    if (item) item.remove();

    if (currentView === name) {
        switchView("direct");
        document.getElementById("chatMessages").innerHTML =
            '<div class="msg-system">Channel removed.</div>';
    }
}

function addChannelToSidebar(name) {
    const list = document.getElementById("channelList");
    const li = document.createElement("li");
    li.className = "channel-item";
    li.setAttribute("data-channel", name);
    li.addEventListener("click", () => switchChannel(name));
    const hash = document.createElement("span");
    hash.className = "ch-hash";
    hash.textContent = "#";
    const label = document.createElement("span");
    label.className = "channel-name";
    label.textContent = name;

    li.append(hash, label);
    if (name !== "general") {
        const actions = document.createElement("span");
        actions.className = "channel-actions";

        const renameBtn = document.createElement("button");
        renameBtn.className = "channel-action-btn";
        renameBtn.type = "button";
        renameBtn.title = "Rename channel";
        renameBtn.textContent = "✎";
        renameBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            renameChannel(name);
        });

        const deleteBtn = document.createElement("button");
        deleteBtn.className = "channel-action-btn danger";
        deleteBtn.type = "button";
        deleteBtn.title = "Delete channel";
        deleteBtn.textContent = "×";
        deleteBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteChannel(name);
        });

        actions.append(renameBtn, deleteBtn);
        li.append(actions);
    }
    list.appendChild(li);
}

async function syncChannels() {
    if (!isAuthenticated) return;

    const endpoints = [getTrackerUrl(), getMyBaseUrl()];
    const names = new Set(["general"]);
    let successfulSyncs = 0;

    await Promise.all(
        endpoints.map(async (baseUrl) => {
            try {
                const res = await fetch(`${baseUrl}/get-channels`, {
                    headers: authHeaders(),
                    credentials: "include",
                });
                if (!res.ok) return;
                const data = await readJsonResponse(res);
                successfulSyncs++;
                const channelNames = Array.isArray(data.names)
                    ? data.names
                    : Object.keys(data.channels || {});
                channelNames.forEach((name) => {
                    const normalized = normalizeChannelName(name);
                    if (normalized) names.add(normalized);
                });
            } catch (e) {
                // Channel discovery is best-effort. Chat still works with
                // already-known channels if one source is unavailable.
            }
        }),
    );

    if (successfulSyncs === 0) return;

    joinedChannels
        .filter(
            (name) => !names.has(name) && !(channelMessages[name] || []).length,
        )
        .forEach((name) => removeChannelFromSidebar(name));
    names.forEach((name) => ensureChannelInSidebar(name));
}

function peerPayloadList() {
    return Object.entries(knownPeers).map(([username, info]) => ({
        username,
        ip: info.ip,
        port: info.port,
    }));
}

async function updateTrackerChannel(endpoint, method, body) {
    if (!isAuthenticated) return;

    return fetch(`${getTrackerUrl()}/${endpoint}`, {
        method,
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(Object.assign({}, body, { announce: false })),
        credentials: "include",
    }).catch(() => {});
}

async function renameChannel(oldName) {
    if (!requireAuthenticated("Log in before renaming channels.")) return;

    if (oldName === "general") return;

    const rawName = prompt("Rename channel", oldName);
    if (rawName === null) return;

    const newName = normalizeChannelName(rawName);
    if (!newName || newName === "direct" || newName === oldName) return;

    try {
        await getPeerList(false);
        const myName = document.getElementById("myName").value;
        const wasCurrentChannel = currentView === oldName;
        const payload = {
            old_channel: oldName,
            new_channel: newName,
            username: myName,
            ip: getMyIp(),
            port: document.getElementById("myPort").value,
            peers: peerPayloadList(),
        };

        const res = await fetch(`${getMyBaseUrl()}/rename-channel`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload),
            credentials: "include",
        });
        const data = await readJsonResponse(res);
        if (!res.ok || data.status === "error") {
            throw new Error(data.message || `HTTP ${res.status}`);
        }

        const renamedChannel = data.channel || newName;
        const owner = currentOwner();
        const renamedMessages = (channelMessages[oldName] || []).map((msg) =>
            Object.assign({}, msg, {
                channel: renamedChannel,
                to: msg.to === oldName ? renamedChannel : msg.to,
            }),
        );
        if (window.ChatHistoryDB && owner) {
            await window.ChatHistoryDB.renameChannel(
                owner,
                oldName,
                renamedChannel,
            );
        }
        removeChannelFromSidebar(oldName);
        channelMessages[renamedChannel] = renamedMessages;
        seenChannelMessageKeys[renamedChannel] = new Set(
            renamedMessages.map((msg) => msg.id),
        );
        lastChannelMsgCounts[renamedChannel] = renamedMessages.length;
        ensureChannelInSidebar(renamedChannel);
        await updateTrackerChannel("rename-channel", "POST", payload);
        await syncChannels();
        if (wasCurrentChannel) switchChannel(renamedChannel);
        showToast(
            `Renamed #${oldName} to #${renamedChannel}. Notified ${data.delivered?.length || 0} peer(s).`,
        );
    } catch (e) {
        showToast("Rename failed: " + e.message);
    }
}

async function deleteChannel(name) {
    if (!requireAuthenticated("Log in before deleting channels.")) return;

    if (name === "general") return;
    if (
        !confirm(`Delete #${name}? Messages in this channel will be removed.`)
    ) {
        return;
    }

    try {
        await getPeerList(false);
        const payload = {
            channel: name,
            username: document.getElementById("myName").value,
            ip: getMyIp(),
            port: document.getElementById("myPort").value,
            peers: peerPayloadList(),
        };

        const res = await fetch(`${getMyBaseUrl()}/delete-channel`, {
            method: "DELETE",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload),
            credentials: "include",
        });
        const data = await readJsonResponse(res);
        if (!res.ok || data.status === "error") {
            throw new Error(data.message || `HTTP ${res.status}`);
        }

        removeChannelFromSidebar(name);
        if (window.ChatHistoryDB && currentOwner()) {
            await window.ChatHistoryDB.deleteChannel(currentOwner(), name);
        }
        await updateTrackerChannel("delete-channel", "DELETE", payload);
        await syncChannels();
        showToast(
            `Deleted #${name}. Notified ${data.delivered?.length || 0} peer(s).`,
        );
    } catch (e) {
        showToast("Delete failed: " + e.message);
    }
}

// ============================================================
// MAIN POLLING LOOP
// ============================================================
function pollLoop() {
    fetchMessages();
    if (currentView !== "direct") {
        fetchChannelMessages(currentView);
    }
}

// Poll messages every 2 seconds
setInterval(pollLoop, 2000);

// Refresh peer list every 10 seconds if peers are known
setInterval(() => {
    if (Object.keys(knownPeers).length > 0) getPeerList(false);
}, 10000);

// Refresh shared channel names from the tracker/local backend.
// This is how peer-created channels show up in other browsers.
setInterval(syncChannels, 2000);

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
    directCryptoKeys = null;
    directPublicKey = "";
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

        const data = await readJsonResponse(res);

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
                const localData = await readJsonResponse(localRes);
                if (!localRes.ok || localData.status === "error") {
                    throw new Error(
                        localData.message || "Local backend rejected login",
                    );
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
            await loadHistoryForOwner(loggedInUser);
            showToast("Welcome, " + loggedInUser + "!");

            // Auto-register peer right after login so the user
            // can immediately start chatting without extra clicks
            await registerPeer();
            await getPeerList(false);
            await syncChannels();
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

        const data = await readJsonResponse(res);
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
            await loadHistoryForOwner(restoredUser);
            await registerPeer();
            await getPeerList(false);
            await syncChannels();
            showToast("Session restored for " + restoredUser);
        }
    } catch (e) {
        // No saved session, expired in-memory session, or tracker unreachable.
    }
}

function postLogoutRequest(baseUrl, headers) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1500);

    return fetch(`${baseUrl}/logout`, {
        method: "POST",
        headers,
        credentials: "include",
        signal: controller.signal,
    })
        .catch(() => {})
        .finally(() => clearTimeout(timeoutId));
}

function logoutUser() {
    const logoutUrls = Array.from(new Set([getTrackerUrl(), getMyBaseUrl()]));
    const logoutHeaders = authHeaders();

    isAuthenticated = false;
    authToken = "";
    directCryptoKeys = null;
    directPublicKey = "";
    historyReadyForOwner = "";
    localStorage.removeItem("chat_auth");
    document.getElementById("authStatus").textContent = "Not logged in";
    document.getElementById("loginBtn").textContent = "Login";
    document.getElementById("myName").disabled = true;
    document.getElementById("myName").value = "";
    closeLoginModal();
    closeModal();
    renderSignedOutState();
    updateUserDisplay();
    showToast("Logged out.");

    Promise.all(
        logoutUrls.map((baseUrl) => postLogoutRequest(baseUrl, logoutHeaders)),
    ).catch(() => {});
}

function autoConfigure() {
    const params = new URLSearchParams(window.location.search);
    const port = params.get("peerPort") || params.get("myPort") || "8000";
    const host = window.location.hostname || "127.0.0.1";
    const trackerIp = params.get("trackerIp") || params.get("tracker") || host;
    const myIp = params.get("peerIp") || params.get("myIp") || "127.0.0.1";
    const apiBase = params.get("apiBase") || params.get("trackerBase");
    const configuredUsername = params.get("username") || params.get("user");
    const pagePort = window.location.port || "";
    const trackerPort =
        params.get("trackerPort") ||
        params.get("proxyPort") ||
        (pagePort === "3000" ? "3001" : pagePort || "8080");
    trackerApiBase = apiBase
        ? new URL(apiBase, window.location.origin).href.replace(/\/$/, "")
        : "";

    document.getElementById("myIp").value = myIp;
    document.getElementById("myPort").value = port;
    document.getElementById("trackerPort").value = trackerPort;
    document.getElementById("trackerIp").value = trackerIp;

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

    const trackerHost = document.getElementById("trackerIp").value.trim();
    const trackerPort = document.getElementById("trackerPort").value.trim();

    try {
        const res = await fetch(`${getLoopbackBackendUrl()}/local-info`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_host: trackerHost,
                target_port: trackerPort,
            }),
        });
        if (res.ok) {
            const data = await res.json();
            if (data.local_ip && !isLoopbackIp(data.local_ip)) {
                document.getElementById("myIp").value = data.local_ip;
                return;
            }
        }
    } catch (e) {
        // Fall back to the proxy-observed client address below.
    }

    try {
        const res = await fetch(`${getTrackerUrl()}/client-info`, {
            credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.client_ip && !isLoopbackIp(data.client_ip)) {
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
    addClickHandler("discoverBtn", () => getPeerList(true));
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
    if (isAuthenticated) {
        await syncChannels();
    } else {
        renderSignedOutState();
    }
}

bootstrap();
