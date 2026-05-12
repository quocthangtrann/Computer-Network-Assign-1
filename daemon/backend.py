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
daemon.backend
~~~~~~~~~~~~~~~~~

This module implements the backend TCP server with three non-blocking I/O
strategies (Task 2.1):

  1. **Multi-threading** (mode_async = "threading"):
       One OS thread per connection. Each call to handle_client runs in its
       own daemon thread, so the main loop can immediately accept the next
       connection without blocking.

  2. **Event-driven callbacks** (mode_async = "callback"):
       Uses Python's selectors module to watch the server socket for READ
       events. When the socket becomes readable a new connection has arrived;
       handle_client_callback is invoked synchronously. This avoids threads
       but keeps the server non-blocking from the OS perspective.

  3. **Coroutines / asyncio** (mode_async = "coroutine"):
       Uses asyncio.start_server() with async/await. The event loop handles
       all multiplexing; handle_client_coroutine is an async function that
       awaits reader.read() so it never blocks the loop.

Switch between modes by changing the `mode_async` variable below.

Requirements:
--------------
- socket:    TCP socket interface.
- threading: Enables multi-thread mode.
- asyncio:   Enables coroutine mode.
- selectors: Enables callback/event-driven mode.
- inspect:   Detects whether a route handler is a coroutine.
- response, httpadapter, dictionary: framework internals.

Usage Example:
--------------
>>> create_backend("127.0.0.1", 9000, routes={})
"""

import socket
import threading
import argparse
import asyncio
import inspect
import selectors

from .response import *
from .httpadapter import HttpAdapter
from .dictionary import CaseInsensitiveDict

# Selector instance for callback (event-driven) mode
sel = selectors.DefaultSelector()

# Default concurrency mode (can be overridden by start_backend.py)
mode_async = "coroutine"


# Handler: threading mode

def handle_client(ip, port, conn, addr, routes):
# Task 2.1: Handle each client connection using an independent daemon thread

    print("[Backend] Invoke handle_client accepted connection from {}".format(addr))
    # Create adapter and delegate all handling to it
    daemon = HttpAdapter(ip, port, conn, addr, routes)
    daemon.handle_client(conn, addr, routes)


# Handler: callback (event-driven / selectors) mode

def handle_client_callback(server, ip, port, conn, addr, routes):
    # Task 2.1: Handle a client connection in event-driven callback mode without threads.
    print("[Backend] Invoke handle_client_callback accepted connection from {}".format(addr))
    daemon = HttpAdapter(ip, port, conn, addr, routes)
    daemon.handle_client(conn, addr, routes)


# Handler: coroutine (asyncio) mode

async def handle_client_coroutine(reader, writer):

    addr = writer.get_extra_info("peername")
    print("[Backend] Invoke handle_client_coroutine accepted connection from {}".format(addr))

    # Create adapter; conn/addr are None in asyncio mode (streams are used instead)
    daemon = HttpAdapter(None, None, None, None, _coroutine_routes)
    await daemon.handle_client_coroutine(reader, writer)


# Module-level variable to share routes with the coroutine handler
# (asyncio.start_server does not pass extra args to the callback)
_coroutine_routes = {}


async def async_server(ip="0.0.0.0", port=7000, routes={}):

    global _coroutine_routes
    _coroutine_routes = routes

    print("[Backend] async_server **ASYNC** listening on port {}".format(port))
    if routes:
        print("[Backend] route settings")
        for key, value in routes.items():
            is_co_func = "**ASYNC** " if inspect.iscoroutinefunction(value) else ""
            print("   + ('{}', '{}'): {}{}".format(key[0], key[1], is_co_func, str(value)))
    server = await asyncio.start_server(handle_client_coroutine, ip, port)
    async with server:
        await server.serve_forever()


# Main entry point

def run_backend(ip, port, routes):
    # Start the backend server and listen for incoming connections based on mode.
    global mode_async

    print("[Backend] run_backend with routes={} mode={}".format(routes, mode_async))

    # Coroutine mode
    if mode_async == "coroutine":
        asyncio.run(async_server(ip, port, routes))
        return

    # Socket-based modes (threading or callback)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Allow reuse of recently freed ports (avoids "Address already in use")
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((ip, port))
        server.listen(50)

        print("[Backend] Listening on port {}".format(port))
        if routes:
            print("[Backend] route settings")
            for key, value in routes.items():
                is_co_func = "**ASYNC** " if inspect.iscoroutinefunction(value) else ""
                print("   + ('{}', '{}'): {}{}".format(key[0], key[1], is_co_func, str(value)))

        # Callback (event-driven) mode

        if mode_async == "callback":
            sel.register(server, selectors.EVENT_READ, data=("accept", None))
            server.setblocking(False)
            client_buffers = {}
            
            # Create a dedicated event loop for callback mode to run handlers
            # without blocking the selector loop (we'll process them in batches).
            handler_loop = asyncio.new_event_loop()

            while True:
                events = sel.select(timeout=0.01) # Small timeout to allow other processing
                for key, mask in events:
                    action, data = key.data
                    if action == "accept":
                        conn, addr = key.fileobj.accept()
                        conn.setblocking(False)
                        client_buffers[conn] = b""
                        sel.register(conn, selectors.EVENT_READ, data=("read", (ip, port, addr, routes)))
                    elif action == "read":
                        conn = key.fileobj
                        _ip, _port, addr, _routes = data
                        try:
                            chunk = conn.recv(4096)
                            if chunk:
                                client_buffers[conn] += chunk
                                raw = client_buffers[conn]
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
                                        sel.unregister(conn)
                                        daemon = HttpAdapter(_ip, _port, conn, addr, _routes)
                                        msg = raw.decode("utf-8", errors="replace")
                                        daemon.request.prepare(msg, routes=daemon.routes)
                                        response = b""
                                        if daemon.request.hook:
                                            # Execute hook using the dedicated handler loop
                                            # still technically "blocks" this iteration, but 
                                            # it is the standard way to bridge sync/async in callbacks
                                            # without a full async rewrite.
                                            response = handler_loop.run_until_complete(daemon.request.hook(daemon.request.headers, daemon.request.body))
                                        sel.register(conn, selectors.EVENT_WRITE, data=("write", response))
                            else:
                                sel.unregister(conn)
                                conn.close()
                                client_buffers.pop(conn, None)
                        except (BlockingIOError, socket.error):
                            pass
                        except Exception:
                            sel.unregister(conn)
                            conn.close()
                            client_buffers.pop(conn, None)
                    elif action == "write":
                        conn = key.fileobj
                        resp_data = data
                        try:
                            sent = conn.send(resp_data)
                            if sent < len(resp_data):
                                sel.modify(conn, selectors.EVENT_WRITE, data=("write", resp_data[sent:]))
                            else:
                                sel.unregister(conn)
                                conn.close()
                                client_buffers.pop(conn, None)
                        except (BlockingIOError, socket.error):
                            pass
                        except Exception:
                            sel.unregister(conn)
                            conn.close()
                            client_buffers.pop(conn, None)

        # Threading (multi-thread) mode
        else:
            while True:
                # Block here until a client connects
                conn, addr = server.accept()

                # Spawn a daemon thread so the main loop immediately resumes
                client_thread = threading.Thread(
                    target=handle_client,
                    args=(ip, port, conn, addr, routes)
                )
                client_thread.daemon = True   # thread dies with main process
                client_thread.start()

    except socket.error as e:
        print("[Backend] Socket error: {}".format(e))
    finally:
        server.close()


def create_backend(ip, port, routes={}):
    run_backend(ip, port, routes)