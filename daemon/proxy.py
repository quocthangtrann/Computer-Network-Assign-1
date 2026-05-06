#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# AsynapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#

"""
daemon.proxy
~~~~~~~~~~~~~~~~~

This module implements the proxy server (reverse proxy).

Architecture role:
  Client Browser → Proxy (port 8080) → Backend (port 9000) or SampleApp (port 2026)

The proxy reads the HTTP `Host` header from each incoming request, looks up the
matching backend from the routes config (built from proxy.conf), and forwards the
raw HTTP bytes to that backend. The backend's response is stream-copied back to the
client.

The proxy uses multi-threading: one daemon thread per client connection, so multiple
browsers can be served simultaneously without blocking.

Requirements:
-----------------
- socket: TCP networking.
- threading: One thread per client for non-blocking proxy.
- response: 404 builder.
- httpadapter: HttpAdapter for HTTP request processing.
- dictionary: CaseInsensitiveDict for headers.
"""

import socket
import threading
from .response import *
from .httpadapter import HttpAdapter
from .dictionary import CaseInsensitiveDict

#: Default fallback routing map (used if no proxy.conf is provided).
PROXY_PASS = {
    "192.168.56.103:8080": ('192.168.56.103', 9000),
    "app1.local":          ('192.168.56.103', 9001),
    "app2.local":          ('192.168.56.103', 9002),
}

round_robin_counter = {}


def forward_request(host, port, request):
    # Forward a raw HTTP request to a backend server and return the response.
    backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        backend.connect((host, port))
        # Encode to bytes if the caller passed a str
        if isinstance(request, str):
            request = request.encode('utf-8')
        backend.sendall(request)

        # Stream-read the full response in 4 KB chunks
        response = b""
        while True:
            chunk = backend.recv(4096)
            if not chunk:
                break
            response += chunk
        return response

    except socket.error as e:
        print("[Proxy] Socket error forwarding to {}:{} — {}".format(host, port, e))
        # Return a 502 Bad Gateway instead of an empty response
        return (
            "HTTP/1.1 502 Bad Gateway\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 15\r\n"
            "Connection: close\r\n"
            "\r\n"
            "502 Bad Gateway"
        ).encode('utf-8')
    finally:
        backend.close()


def resolve_routing_policy(hostname, routes):
    global round_robin_counter
    # Determine the target backend host and port for an incoming hostname.
    print("[Proxy] Resolving hostname: {}".format(hostname))

    # Look up the hostname; fall back to localhost:9000 if unknown
    entry = routes.get(hostname, ('127.0.0.1:9000', 'round-robin'))
    proxy_map, policy = entry
    print("[Proxy] proxy_map={} policy={}".format(proxy_map, policy))

    proxy_host = '127.0.0.1'
    proxy_port = '9000'

    if isinstance(proxy_map, list):
        if len(proxy_map) == 0:
            # TODO: implement error handling for non-mapped host.
            #       The policy is designed by team, but it can be a basic
            #       default host in your self-defined system.
            print("[Proxy] Empty resolved routing of hostname {}".format(hostname))
            # Use dummy host; forward_request will return 502
            proxy_host = '127.0.0.1'
            proxy_port = '9000'
        elif len(proxy_map) == 1:
            # Single backend — use it directly
            proxy_host, proxy_port = proxy_map[0].split(":", 1)
        else:
            # Multiple backends — round-robin (extension point)
            # Implement actual Round-Robin Load Balancing
            if hostname not in round_robin_counter:
                round_robin_counter[hostname] = 0
            
            index = round_robin_counter[hostname] % len(proxy_map)
            proxy_host, proxy_port = proxy_map[index].split(":", 1)
            
            round_robin_counter[hostname] += 1
    else:
        # Single string "host:port"
        print("[Proxy] Singular route for hostname {} to {}".format(hostname, proxy_map))
        proxy_host, proxy_port = proxy_map.split(":", 1)

    return proxy_host, proxy_port


def handle_client(ip, port, conn, addr, routes):
    # Handle a single proxied client connection.
    try:
        # Read the full request (use a generous buffer — headers can be large)
        request = conn.recv(4096).decode('utf-8', errors='replace')
    except Exception as e:
        print("[Proxy] recv error: {}".format(e))
        conn.close()
        return

    # Extract Host header (required by HTTP/1.1, RFC 7230 §5.4)
    hostname = ""
    for line in request.splitlines():
        if line.lower().startswith('host:'):
            hostname = line.split(':', 1)[1].strip()
            break

    if not hostname:
        print("[Proxy] No Host header found from {}".format(addr))
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        conn.close()
        return

    print("[Proxy] {} at Host: {}".format(addr, hostname))

    # Resolve backend destination
    resolved_host, resolved_port = resolve_routing_policy(hostname, routes)
    try:
        resolved_port = int(resolved_port)
    except ValueError:
        print("[Proxy] Invalid port value '{}'".format(resolved_port))
        resolved_port = 9000

    if resolved_host:
        print("[Proxy] Forwarding {} → {}:{}".format(hostname, resolved_host, resolved_port))
        response = forward_request(resolved_host, resolved_port, request)
    else:
        response = (
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 13\r\n"
            "Connection: close\r\n"
            "\r\n"
            "404 Not Found"
        ).encode('utf-8')

    conn.sendall(response)
    conn.close()


def run_proxy(ip, port, routes):
    # Start the proxy server and accept connections with one thread per client.
    proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Allow rapid restart without "Address already in use" errors
    proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        proxy.bind((ip, port))
        proxy.listen(50)
        print("[Proxy] Listening on IP {} port {}".format(ip, port))

        while True:
            conn, addr = proxy.accept()

            # TODO: implement the step of the client incoming connection
            #       using multi-thread programming with the provided handle_client routine
            # Spawn a daemon thread so the accept() loop is never blocked
            t = threading.Thread(
                target=handle_client,
                args=(ip, port, conn, addr, routes)
            )
            t.daemon = True   # thread exits when the main process exits
            t.start()

    except socket.error as e:
        print("[Proxy] Socket error: {}".format(e))
    finally:
        proxy.close()


def create_proxy(ip, port, routes):

    run_proxy(ip, port, routes)
