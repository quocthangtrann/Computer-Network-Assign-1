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
import asyncio
import threading
from .response import *
from .httpadapter import HttpAdapter
from .dictionary import CaseInsensitiveDict

#: Default fallback routing map (used if no proxy.conf is provided).
PROXY_PASS = {
    "192.168.56.103:8080": ("192.168.56.103", 9000),
    "app1.local": ("192.168.56.103", 9001),
    "app2.local": ("192.168.56.103", 9002),
}

round_robin_counter = {}
mode_async = "coroutine"
proxy_lock = threading.Lock()


def receive_http_request(conn):
    raw = b""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        raw += chunk

        if b"\r\n\r\n" not in raw:
            continue

        header_end = raw.find(b"\r\n\r\n")
        header_bytes = raw[:header_end]
        body_received = len(raw) - header_end - 4
        content_length = 0

        for line in header_bytes.decode("utf-8", errors="ignore").split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    content_length = 0
                break

        if body_received >= content_length:
            break

    return raw.decode("utf-8", errors="replace")


def extract_request_path(request):
    first_line = request.splitlines()[0] if request.splitlines() else ""
    parts = first_line.split()
    return parts[1] if len(parts) >= 2 else "/"


def rewrite_request_path(request, new_path):
    lines = request.split("\r\n")
    if not lines:
        return request

    parts = lines[0].split()
    if len(parts) >= 3:
        parts[1] = new_path
        lines[0] = " ".join(parts)
    return "\r\n".join(lines)


def upsert_forwarded_headers(request, client_ip):
    lines = request.split("\r\n")
    if not lines:
        return request

    header_end = None
    for i, line in enumerate(lines):
        if line == "":
            header_end = i
            break

    if header_end is None:
        return request

    filtered = []
    for line in lines[1:header_end]:
        header_name = line.split(":", 1)[0].strip().lower()
        if header_name not in ("x-forwarded-for", "x-real-ip"):
            filtered.append(line)

    new_headers = [
        lines[0],
        "X-Forwarded-For: {}".format(client_ip),
        "X-Real-IP: {}".format(client_ip),
    ] + filtered

    return "\r\n".join(new_headers + lines[header_end:])


def strip_prefix(path, prefix):
    if not path.startswith(prefix):
        return path

    stripped = path[len(prefix) :]
    if not stripped:
        return "/"
    if not stripped.startswith("/"):
        return "/" + stripped
    return stripped


def resolve_path_route(path, routes):
    path_routes = routes.get("__path_routes__", [])
    matches = [route for route in path_routes if path.startswith(route["prefix"])]
    if not matches:
        return None
    return max(matches, key=lambda route: len(route["prefix"]))


def parse_target(target):
    host, port = target.split(":", 1)
    return host, int(port)


def resolve_default_route(routes):
    target = routes.get("__default__")
    if target:
        return parse_target(target), None
    return None, None


def forward_request(host, port, request):
    # Forward a raw HTTP request to a backend server and return the response.
    backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        backend.connect((host, port))
        # Encode to bytes if the caller passed a str
        if isinstance(request, str):
            request = request.encode("utf-8")
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
        ).encode("utf-8")
    finally:
        backend.close()


def resolve_routing_policy(hostname, routes):
    global round_robin_counter
    # Determine the target backend host and port for an incoming hostname.
    print("[Proxy] Resolving hostname: {}".format(hostname))

    # Look up the hostname; fall back to localhost:9000 if unknown
    entry = routes.get(hostname, ("127.0.0.1:9000", "round-robin"))
    proxy_map, policy = entry
    print("[Proxy] proxy_map={} policy={}".format(proxy_map, policy))

    proxy_host = "127.0.0.1"
    proxy_port = "9000"

    if isinstance(proxy_map, list):
        if len(proxy_map) == 0:
            #       The policy is designed by team, but it can be a basic
            #       default host in your self-defined system.
            print("[Proxy] Empty resolved routing of hostname {}".format(hostname))
            # Use dummy host; forward_request will return 502
            proxy_host = "127.0.0.1"
            proxy_port = "9000"
        elif len(proxy_map) == 1:
            # Single backend — use it directly
            proxy_host, proxy_port = proxy_map[0].split(":", 1)
        else:
            # Multiple backends — round-robin (extension point)
            # Implement actual Round-Robin Load Balancing
            with proxy_lock:
                if hostname not in round_robin_counter:
                    round_robin_counter[hostname] = 0

                index = round_robin_counter[hostname] % len(proxy_map)
                proxy_host, proxy_port = proxy_map[index].split(":", 1)

                round_robin_counter[hostname] += 1
    else:
        # Single string "host:port"
        print(
            "[Proxy] Singular route for hostname {} to {}".format(hostname, proxy_map)
        )
        proxy_host, proxy_port = proxy_map.split(":", 1)

    return proxy_host, proxy_port



async def handle_client_coroutine(reader, writer, routes, client_ip):
    try:
        raw = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            raw += chunk
            if b"\r\n\r\n" in raw:
                header_end = raw.find(b"\r\n\r\n")
                header_bytes = raw[:header_end]
                body_received = len(raw) - header_end - 4
                content_length = 0
                for line in header_bytes.decode("utf-8", errors="ignore").split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            content_length = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                        break
                if body_received >= content_length:
                    break
        request = raw.decode("utf-8", errors="replace")
    except Exception as e:
        print("[Proxy] recv error: {}".format(e))
        writer.close()
        return

    hostname = ""
    for line in request.splitlines():
        if line.lower().startswith("host:"):
            hostname = line.split(":", 1)[1].strip()
            break

    if not hostname:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    request_path = extract_request_path(request)
    path_route = resolve_path_route(request_path, routes)
    request = upsert_forwarded_headers(request, client_ip)

    if path_route:
        resolved_host, resolved_port = parse_target(path_route["target"])
        if path_route.get("strip_prefix"):
            rewritten_path = strip_prefix(request_path, path_route["prefix"])
            request = rewrite_request_path(request, rewritten_path)
    else:
        if hostname in routes:
            resolved_host, resolved_port = resolve_routing_policy(hostname, routes)
        else:
            default_result, _ = resolve_default_route(routes)
            if default_result:
                resolved_host, resolved_port = default_result
            else:
                resolved_host, resolved_port = resolve_routing_policy(hostname, routes)
        try:
            resolved_port = int(resolved_port)
        except ValueError:
            resolved_port = 9000

    if resolved_host:
        try:
            # Forward the request asynchronously
            backend_reader, backend_writer = await asyncio.open_connection(resolved_host, resolved_port)
            backend_writer.write(request.encode("utf-8"))
            await backend_writer.drain()
            
            while True:
                resp_chunk = await backend_reader.read(4096)
                if not resp_chunk:
                    break
                writer.write(resp_chunk)
                await writer.drain()
                
            backend_writer.close()
            await backend_writer.wait_closed()
        except Exception as e:
            print("[Proxy] Connection error to backend {}:{} - {}".format(resolved_host, resolved_port, e))
            err_resp = (
                "HTTP/1.1 502 Bad Gateway\r\n"
                "Content-Type: text/plain\r\n"
                "Content-Length: 15\r\n"
                "Connection: close\r\n\r\n502 Bad Gateway"
            ).encode("utf-8")
            writer.write(err_resp)
            await writer.drain()
    else:
        not_found = (
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 13\r\n"
            "Connection: close\r\n\r\n404 Not Found"
        ).encode("utf-8")
        writer.write(not_found)
        await writer.drain()

    writer.close()
    await writer.wait_closed()

async def async_proxy_server(ip, port, routes):
    async def handle_client_wrapper(reader, writer):
        addr = writer.get_extra_info('peername')
        client_ip = addr[0] if addr else "127.0.0.1"
        await handle_client_coroutine(reader, writer, routes, client_ip)

    server = await asyncio.start_server(handle_client_wrapper, ip, port)
    print("[Proxy] Listening on IP {} port {} (asyncio)".format(ip, port))
    async with server:
        await server.serve_forever()

def run_proxy(ip, port, routes):
    global mode_async
    if mode_async == "threading":
        # Baseline multi-thread implementation
        proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            proxy_socket.bind((ip, port))
            proxy_socket.listen(50)
            print("[Proxy] Listening on IP {} port {} (threading)".format(ip, port))
            while True:
                conn, addr = proxy_socket.accept()
                # Use a wrapper for handle_client to handle it in sync

                def sync_wrapper(c, a):
                    try:
                        raw = b""
                        # Read request with basic buffering
                        while True:
                            chunk = c.recv(4096)
                            if not chunk: break
                            raw += chunk
                            if b"\r\n\r\n" in raw: break
                        
                        request_text = raw.decode("utf-8", errors="replace")
                        # Simple extraction
                        hostname = ""
                        for line in request_text.splitlines():
                            if line.lower().startswith("host:"):
                                hostname = line.split(":", 1)[1].strip()
                                break
                        
                        resolved_host, resolved_port = resolve_routing_policy(hostname, routes)
                        if resolved_host:
                            # Forward synchronously
                            backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            backend.connect((resolved_host, int(resolved_port)))
                            backend.sendall(raw)
                            while True:
                                resp_chunk = backend.recv(4096)
                                if not resp_chunk: break
                                c.sendall(resp_chunk)
                            backend.close()
                    except Exception as e:
                        print("[Proxy] Threading error: {}".format(e))
                    finally:
                        c.close()

                
                t = threading.Thread(target=sync_wrapper, args=(conn, addr))
                t.daemon = True
                t.start()
        except Exception as e:
            print("[Proxy] Socket error: {}".format(e))
        finally:
            proxy_socket.close()
        return

    # Default: coroutine mode
    asyncio.run(async_proxy_server(ip, port, routes))
