// Browser-local chat history storage.
// This keeps each peer's history in IndexedDB, separated by logged-in owner.
(function () {
    const DB_NAME = "hybrid-chat-history";
    const DB_VERSION = 1;
    const MESSAGE_STORE = "messages";
    const CONVERSATION_STORE = "conversations";

    let dbPromise = null;

    function open() {
        if (dbPromise) return dbPromise;

        dbPromise = new Promise((resolve, reject) => {
            if (!window.indexedDB) {
                reject(new Error("IndexedDB is not available"));
                return;
            }

            const request = indexedDB.open(DB_NAME, DB_VERSION);

            request.onupgradeneeded = () => {
                const db = request.result;

                if (!db.objectStoreNames.contains(MESSAGE_STORE)) {
                    const messages = db.createObjectStore(MESSAGE_STORE, {
                        keyPath: "id",
                    });
                    messages.createIndex("byOwnerTs", ["owner", "ts"]);
                    messages.createIndex("byDirectPeer", [
                        "owner",
                        "peer",
                        "ts",
                    ]);
                    messages.createIndex("byChannel", [
                        "owner",
                        "channel",
                        "ts",
                    ]);
                }

                if (!db.objectStoreNames.contains(CONVERSATION_STORE)) {
                    const conversations = db.createObjectStore(
                        CONVERSATION_STORE,
                        { keyPath: "id" },
                    );
                    conversations.createIndex("byOwnerUpdatedAt", [
                        "owner",
                        "updatedAt",
                    ]);
                }
            };

            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

        return dbPromise;
    }

    function txDone(tx) {
        return new Promise((resolve, reject) => {
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        });
    }

    function conversationForMessage(message) {
        if (message.type === "direct" && message.peer) {
            return {
                id: `${message.owner}:direct:${message.peer}`,
                owner: message.owner,
                type: "direct",
                peer: message.peer,
                channel: null,
                updatedAt: message.ts,
            };
        }

        if (message.type === "channel" && message.channel) {
            return {
                id: `${message.owner}:channel:${message.channel}`,
                owner: message.owner,
                type: "channel",
                peer: null,
                channel: message.channel,
                updatedAt: message.ts,
            };
        }

        return {
            id: `${message.owner}:broadcast`,
            owner: message.owner,
            type: "broadcast",
            peer: null,
            channel: null,
            updatedAt: message.ts,
        };
    }

    async function saveMessage(message) {
        const db = await open();
        const tx = db.transaction(
            [MESSAGE_STORE, CONVERSATION_STORE],
            "readwrite",
        );
        tx.objectStore(MESSAGE_STORE).put(message);
        tx.objectStore(CONVERSATION_STORE).put(conversationForMessage(message));
        await txDone(tx);
    }

    async function getMessagesByOwner(owner) {
        const db = await open();
        const tx = db.transaction(MESSAGE_STORE, "readonly");
        const index = tx.objectStore(MESSAGE_STORE).index("byOwnerTs");
        const range = IDBKeyRange.bound(
            [owner, 0],
            [owner, Number.MAX_SAFE_INTEGER],
        );

        return new Promise((resolve, reject) => {
            const messages = [];
            const request = index.openCursor(range);
            request.onsuccess = () => {
                const cursor = request.result;
                if (!cursor) {
                    resolve(messages);
                    return;
                }
                messages.push(cursor.value);
                cursor.continue();
            };
            request.onerror = () => reject(request.error);
        });
    }

    async function renameChannel(owner, oldChannel, newChannel) {
        const db = await open();
        const tx = db.transaction(
            [MESSAGE_STORE, CONVERSATION_STORE],
            "readwrite",
        );
        const messages = tx.objectStore(MESSAGE_STORE);
        const range = IDBKeyRange.bound(
            [owner, oldChannel, 0],
            [owner, oldChannel, Number.MAX_SAFE_INTEGER],
        );

        await new Promise((resolve, reject) => {
            const request = messages.index("byChannel").openCursor(range);
            request.onsuccess = () => {
                const cursor = request.result;
                if (!cursor) {
                    const conversations = tx.objectStore(CONVERSATION_STORE);
                    conversations.delete(`${owner}:channel:${oldChannel}`);
                    conversations.put({
                        id: `${owner}:channel:${newChannel}`,
                        owner,
                        type: "channel",
                        peer: null,
                        channel: newChannel,
                        updatedAt: Date.now(),
                    });
                    resolve();
                    return;
                }
                const message = cursor.value;
                message.channel = newChannel;
                if (message.to === oldChannel) message.to = newChannel;
                cursor.update(message);
                cursor.continue();
            };
            request.onerror = () => reject(request.error);
        });

        await txDone(tx);
    }

    async function deleteChannel(owner, channel) {
        const db = await open();
        const tx = db.transaction(
            [MESSAGE_STORE, CONVERSATION_STORE],
            "readwrite",
        );
        const messages = tx.objectStore(MESSAGE_STORE);
        const range = IDBKeyRange.bound(
            [owner, channel, 0],
            [owner, channel, Number.MAX_SAFE_INTEGER],
        );

        await new Promise((resolve, reject) => {
            const request = messages.index("byChannel").openCursor(range);
            request.onsuccess = () => {
                const cursor = request.result;
                if (!cursor) {
                    tx.objectStore(CONVERSATION_STORE).delete(
                        `${owner}:channel:${channel}`,
                    );
                    resolve();
                    return;
                }
                cursor.delete();
                cursor.continue();
            };
            request.onerror = () => reject(request.error);
        });

        await txDone(tx);
    }

    window.ChatHistoryDB = {
        open,
        saveMessage,
        getMessagesByOwner,
        renameChannel,
        deleteChannel,
    };
})();
