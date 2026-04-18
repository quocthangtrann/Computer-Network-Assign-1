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

This module provides a backend object to manage and persist backend daemon. 
It implements a basic backend server using Python's socket and threading libraries.
It supports handling multiple client connections concurrently and routing requests using a
custom HTTP adapter.

Requirements:
--------------
- socket: provide socket networking interface.
- threading: Enables concurrent client handling via threads.
- response: response utilities.
- httpadapter: the class for handling HTTP requests.
- CaseInsensitiveDict: provides dictionary for managing headers or routes.


Notes:
------
- The server create daemon threads for client handling.
- The current implementation error handling is minimal, socket errors are printed to the console.
- The actual request processing is delegated to the HttpAdapter class.

Usage Example:
--------------
>>> create_backend("127.0.0.1", 9000, routes={})

"""

import socket
import threading
import argparse

import asyncio
import inspect

from .response import *
from .httpadapter import HttpAdapter
from .dictionary import CaseInsensitiveDict

import selectors
sel = selectors.DefaultSelector()

mode_async = "callback"
#mode_async = "coroutine"
mode_async = "threading"

def handle_client(ip, port, conn, addr, routes):
    """
    Initializes an HttpAdapter instance and delegates the client handling logic to it.

    :param ip (str): IP address of the server.
    :param port (int): Port number the server is listening on.
    :param conn (socket.socket): Client connection socket.
    :param addr (tuple): client address (IP, port).
    :param routes (dict): Dictionary of route handlers.
    """
    print("[Backend] Invoke handle_client accepted connection from {}".format(addr))
    daemon = HttpAdapter(ip, port, conn, addr, routes)

    # Handle client
    daemon.handle_client(conn, addr, routes)


# Callback for handling new client (itself run in sync mode)
def handle_client_callback(server, ip, port,conn, addr, routes):
    """
    Initialize connection instance and delegates the client handling logic to it.

    :param ip (str): IP address of the server.
    :param port (int): Port number the server is listening on.
    :param routes (dict): Dictionary of route handlers.
    """
    print("[Backend] Invoke handle_client_callback accepted connection from {}".format(addr))

    daemon = HttpAdapter(ip, port, conn, addr, routes)

    # Handle client
    daemon.handle_client(conn, addr, routes)


# Coroutine async/await for handling new client
async def handle_client_coroutine(reader, writer):
    """
    Coroutine in async communication to initialize connection instance
    then delegates the client handling logic to it.

    :param reader (StreamReader): Stream reader wrapper.
    :param write (Stream write): Stream write wrapper.
    """
    addr = writer.get_extra_info("peername")
    print("[Backend] Invoke handle_client_coroutine accepted connection from {}".format(addr))

    # Handle client in asynchronous mode
    while True:
        daemon = HttpAdapter(None, None, None, None, None)
        await daemon.handle_client_coroutine(reader, writer)

def create_async_handler(ip, port, routes):
    """
    Creates an async handler closure with IP, port, and routes captured.
    
    :param ip (str): IP address of the server.
    :param port (int): Port number of the server.
    :param routes (dict): Dictionary of route handlers.
    :rtype: async function - Handler for asyncio.start_server
    """
    async def handle_client_coroutine_with_routes(reader, writer):
        """
        Coroutine async/await handler with routes passed in.
        
        :param reader (StreamReader): Stream reader wrapper.
        :param writer (StreamWriter): Stream writer wrapper.
        """
        addr = writer.get_extra_info("peername")
        print("[Backend] Invoke handle_client_coroutine accepted connection from {}".format(addr))

        # Handle client in asynchronous mode
        while True:
            # Pass ip, port, and routes to HttpAdapter
            daemon = HttpAdapter(ip, port, None, addr, routes)
            await daemon.handle_client_coroutine(reader, writer)

    return handle_client_coroutine_with_routes

async def async_server(ip="0.0.0.0", port=7000, routes={}):
    print("[Backend] async_server **ASYNC** listening on port {}".format(port))
    if routes != {}:
        print("[Backend] route settings")
        for key, value in routes.items():
            isCoFunc = ""
            if inspect.iscoroutinefunction(value):
               isCoFunc += "**ASYNC** "
            print("   + ('{}', '{}'): {}{}".format(key[0], key[1], isCoFunc, str(value)))

    # Create handler with routes captured
    handler = create_async_handler(ip, port, routes)
    
    async_server = await asyncio.start_server(handler, ip, port)
    async with async_server:
        await async_server.serve_forever()
    return


def run_backend(ip, port, routes):
    """
    Starts the backend server, binds to the specified IP and port, and listens for incoming
    connections. Each connection is handled in a separate thread. The backend accepts incoming
    connections and spawns a thread for each client.


    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict): Dictionary of route handlers.
    """
    global mode_async

    print("[Backend] run_backend with routes={}".format(routes))
    # Process async stream for registering the service and terminate
    if mode_async == "coroutine":

       asyncio.run(async_server(ip, port, routes))
       return

    # Process socket object
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        server.bind((ip, port))
        server.listen(50)

        print("[Backend] Listening on port {}".format(port))
        if routes != {}:
            print("[Backend] route settings")
            for key, value in routes.items():
               isCoFunc = ""
               if inspect.iscoroutinefunction(value):
                  isCoFunc += "**ASYNC** "
               print("   + ('{}', '{}'): {}{}".format(key[0], key[1], isCoFunc, str(value)))

        if mode_async == "callback":
            server.setblocking(False)
            # Register server socket with selector to listen for new connection events
            sel.register(server, selectors.EVENT_READ, data=("accept", ip, port, routes))

        while True:
            if mode_async == "callback":
                # Wait for events instead of blocking at accept()
                events = sel.select(timeout=None)
                for key, mask in events:
                    if key.data[0] == "accept":
                        # Event: New client connection
                        _, ip_pass, port_pass, routes_pass = key.data
                        conn, addr = key.fileobj.accept()
                        conn.setblocking(False)
                        # Register new client socket with selector to wait for data
                        sel.register(conn, selectors.EVENT_READ, data=("read", ip_pass, port_pass, addr, routes_pass))
                    
                    elif key.data[0] == "read":
                        # Event: Client sends data
                        _, ip_pass, port_pass, addr_pass, routes_pass = key.data
                        conn = key.fileobj
                        # Unregister to avoid infinite loop before processing
                        sel.unregister(conn)
                        # Handle client
                        conn.setblocking(True)
                        handle_client_callback(server, ip_pass, port_pass, conn, addr_pass, routes_pass)
                        # Re-register connection for persistent HTTP keep-alive support
                        # Check if connection is still open before re-registering
                        try:
                            conn.setblocking(False)
                            # Re-register the connection for potential next request
                            sel.register(conn, selectors.EVENT_READ, data=("read", ip_pass, port_pass, addr_pass, routes_pass))
                        except (OSError, ValueError) as e:
                            # Connection closed by client or peer
                            print("[Backend] Connection closed by peer: {}".format(e))
            else:
                # Baseline multi-thread implementation
                conn, addr = server.accept() #block until a client connects
                client_thread = threading.Thread(
                    target=handle_client, 
                    args=(ip, port, conn, addr, routes)
                )
                client_thread.daemon = True
                client_thread.start()


    except socket.error as e:
      print("Socket error: {}".format(e))

def create_backend(ip, port, routes={}):
    """
    Entry point for creating and running the backend server.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict, optional): Dictionary of route handlers. Defaults to empty dict.
    """

    run_backend(ip, port, routes)