async function sendEcho() {
    const message = document.getElementById("msg").value;

    // Example: POST request to /echo endpoint
    try {
        const response = await fetch("/echo", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ text: message }),
        });

        if (!response.ok) {
            throw new Error("HTTP error " + response.status);
        }

        const result = await response.text();
        document.getElementById("response").textContent =
            "Server replied: " + result;
    } catch (err) {
        document.getElementById("response").textContent =
            "Request failed: " + err.message;
    }
}

async function sendLogin() {
    const message = document.getElementById("msg").value;

    // Example: POST request to /login endpoint
    try {
        const response = await fetch("/login", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ text: message }),
        });

        if (!response.ok) {
            throw new Error("HTTP error " + response.status);
        }

        const result = await response.text();
        document.getElementById("response").textContent =
            "Server replied: " + result;
    } catch (err) {
        document.getElementById("response").textContent =
            "Request failed: " + err.message;
    }
}

async function sendHello() {
    const message = document.getElementById("msg").value;

    // Example: PUT request to /hello endpoint
    try {
        const response = await fetch("/hello", {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ text: message }),
        });

        if (!response.ok) {
            throw new Error("HTTP error " + response.status);
        }

        const result = await response.text();
        document.getElementById("response").textContent =
            "Server replied: " + result;
    } catch (err) {
        document.getElementById("response").textContent =
            "Request failed: " + err.message;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("sendEchoBtn").addEventListener("click", sendEcho);
    document
        .getElementById("sendLoginBtn")
        .addEventListener("click", sendLogin);
    document
        .getElementById("sendHelloBtn")
        .addEventListener("click", sendHello);
});
