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

This module implements a simple proxy server using Python's socket and threading libraries.
It routes incoming HTTP requests to backend services based on hostname mappings and returns
the corresponding responses to clients.

Requirement:
-----------------
- socket: provides socket networking interface.
- threading: enables concurrent client handling via threads.
- response: customized :class: `Response <Response>` utilities.
- httpadapter: :class: `HttpAdapter <HttpAdapter >` adapter for HTTP request processing.
- dictionary: :class: `CaseInsensitiveDict <CaseInsensitiveDict>` for managing headers and cookies.

"""
import socket
import threading
from .response import *
from .httpadapter import HttpAdapter
from .dictionary import CaseInsensitiveDict

#: A dictionary mapping hostnames to backend IP and port tuples.
#: Used to determine routing targets for incoming requests.
PROXY_PASS = {
    "127.0.0.1:8080": ('127.0.0.1', 9000),
    "app1.local": ('127.0.0.1', 9001),
    "app2.local": ('127.0.0.1', 9002),
}

#: Round-robin load balancing state - tracks the current index for each hostname
_round_robin_index = {}


def forward_request(host, port, request):
    """
    Forwards an HTTP request to a backend server and retrieves the response.

    :params host (str): IP address of the backend server.
    :params port (int): port number of the backend server.
    :params request (str): incoming HTTP request.

    :rtype bytes: Raw HTTP response from the backend server. If the connection
                  fails, returns a 404 Not Found response.
    """

    backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        backend.connect((host, port))
        backend.sendall(request.encode())
        response = b""
        while True:
            chunk = backend.recv(4096)
            if not chunk:
                break
            response += chunk
        return response
    except socket.error as e:
      print("Socket error: {}".format(e))
      return (
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 13\r\n"
            "Connection: close\r\n"
            "\r\n"
            "404 Not Found"
        ).encode('utf-8')


def read_full_request(conn):
    """
    Read a complete HTTP request from the socket, including headers and body.
    
    Handles large POST bodies by reading until Content-Length bytes are received.
    
    :param conn (socket): The client socket connection.
    :rtype: str - The complete HTTP request message.
    """
    request_data = b''
    
    # Read initial chunk
    chunk = conn.recv(4096)
    if not chunk:
        return ""
    
    request_data += chunk
    
    # Check if we've received the full headers (\r\n\r\n)
    if b'\r\n\r\n' not in request_data:
        # Keep reading until we get headers
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            request_data += chunk
            if b'\r\n\r\n' in request_data:
                break
    
    # Parse headers to check for Content-Length
    header_end = request_data.find(b'\r\n\r\n')
    headers_bytes = request_data[:header_end]
    body_bytes = request_data[header_end + 4:]
    
    # Look for Content-Length header
    content_length = 0
    headers_str = headers_bytes.decode('utf-8', errors='ignore')
    for line in headers_str.split('\r\n'):
        if line.lower().startswith('content-length:'):
            try:
                content_length = int(line.split(':', 1)[1].strip())
            except ValueError:
                content_length = 0
            break
    
    # Read remaining body if needed
    if content_length > 0:
        while len(body_bytes) < content_length:
            chunk = conn.recv(4096)
            if not chunk:
                break
            body_bytes += chunk
    
    # Reconstruct the complete request
    full_request = headers_bytes + b'\r\n\r\n' + body_bytes[:content_length]
    return full_request.decode('utf-8', errors='ignore')


def resolve_routing_policy(hostname, routes):
    """
    Handles an routing policy to return the matching proxy_pass.
    It determines the target backend to forward the request to.

    :params host (str): IP address of the request target server.
    :params port (int): port number of the request target server.
    :params routes (dict): dictionary mapping hostnames and location.
    """

    print(hostname)
    proxy_map, policy = routes.get(hostname,('127.0.0.1:9000','round-robin'))
    print(proxy_map)
    print(policy)

    proxy_host = ''
    proxy_port = '9000'
    if isinstance(proxy_map, list):
        if len(proxy_map) == 0:
            print("[Proxy] Emtpy resolved routing of hostname {}".format(hostname))
            print("Empty proxy_map result")
            # TODO: implement the error handling for non mapped host
            #       the policy is design by team, but it can be 
            #       basic default host in your self-defined system
            # Use a dummy host to raise an invalid connection
            proxy_host = '127.0.0.1'
            proxy_port = '9000'
        elif len(proxy_map) == 1:
            proxy_host, proxy_port = proxy_map[0].split(":", 2)
        elif policy == 'round-robin':
            # Apply round-robin load balancing policy
            if hostname not in _round_robin_index:
                _round_robin_index[hostname] = 0
            
            idx = _round_robin_index[hostname]
            selected_backend = proxy_map[idx % len(proxy_map)]
            proxy_host, proxy_port = selected_backend.split(":", 2)
            
            # Move to next backend for next request
            _round_robin_index[hostname] = (idx + 1) % len(proxy_map)
            print("[Proxy] Round-robin selected backend {} from {} (policy: {})".format(
                selected_backend, proxy_map, policy))
        else:
            # Out-of-handle mapped host or unknown policy
            print("[Proxy] Unknown policy {} - falling back to first backend".format(policy))
            proxy_host, proxy_port = proxy_map[0].split(":", 2)
    else:
        print("[Proxy] resolve route of hostname {} is a singulair to".format(hostname))
        proxy_host, proxy_port = proxy_map.split(":", 2)

    return proxy_host, proxy_port

def handle_client(ip, port, conn, addr, routes):
    """
    Handles an individual client connection by parsing the request,
    determining the target backend, and forwarding the request.

    The handler extracts the Host header from the request to
    matches the hostname against known routes. In the matching
    condition,it forwards the request to the appropriate backend.

    The handler sends the backend response back to the client or
    returns 404 if the hostname is unreachable or is not recognized.

    :params ip (str): IP address of the proxy server.
    :params port (int): port number of the proxy server.
    :params conn (socket.socket): client connection socket.
    :params addr (tuple): client address (IP, port).
    :params routes (dict): dictionary mapping hostnames and location.
    """

    # Read full HTTP request including headers and body
    request = read_full_request(conn)

    # Extract hostname
    for line in request.splitlines():
        if line.lower().startswith('host:'):
            hostname = line.split(':', 1)[1].strip()

    print("[Proxy] {} at Host: {}".format(addr, hostname))

    # Resolve the matching destination in routes and need conver port
    # to integer value
    resolved_host, resolved_port = resolve_routing_policy(hostname, routes)
    try:
        resolved_port = int(resolved_port)
    except ValueError:
        print("Not a valid integer")

    if resolved_host:
        print("[Proxy] Host name {} is forwarded to {}:{}".format(hostname,resolved_host, resolved_port))
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
    """
    Starts the proxy server and listens for incoming connections. 

    The process dinds the proxy server to the specified IP and port.
    In each incomping connection, it accepts the connections and
    spawns a new thread for each client using `handle_client`.
 

    :params ip (str): IP address to bind the proxy server.
    :params port (int): port number to listen on.
    :params routes (dict): dictionary mapping hostnames and location.

    """

    proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        proxy.bind((ip, port))
        proxy.listen(50)
        print("[Proxy] Listening on IP {} port {}".format(ip,port))
        while True:
            conn, addr = proxy.accept()
            # initialize new thread for each client
            client_thread = threading.Thread(
                target=handle_client, 
                args=(ip, port, conn, addr, routes)
            )
            # Set daemon = true to auto close thread when server main stop
            client_thread.daemon = True 
            client_thread.start()
    except socket.error as e:
      print("Socket error: {}".format(e))

def create_proxy(ip, port, routes):
    """
    Entry point for launching the proxy server.

    :params ip (str): IP address to bind the proxy server.
    :params port (int): port number to listen on.
    :params routes (dict): dictionary mapping hostnames and location.
    """

    run_proxy(ip, port, routes)
