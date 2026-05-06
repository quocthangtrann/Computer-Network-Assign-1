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
daemon.httpadapter
~~~~~~~~~~~~~~~~~

This module provides the HttpAdapter class, which bridges raw TCP socket
data with the route handler system.

For each incoming connection HttpAdapter:
  1. Reads raw bytes from the socket (or asyncio stream).
  2. Passes them to a Request object which parses method, path, headers, body.
  3. Looks up the matching route handler in the routes dict.
  4. Calls the handler with (headers, body) and wraps the return value in HTTP.
  5. Sends the HTTP response bytes back over the socket.

Two variants:
  handle_client         — synchronous (threading mode)
  handle_client_coroutine — async (asyncio/coroutine mode)
"""

from .request import Request
from .response import Response
from .dictionary import CaseInsensitiveDict

import asyncio
import inspect
import json


class HttpAdapter:
    """A mutable HTTP adapter that manages client connections and routes requests.

    The `HttpAdapter` class encapsulates the logic for receiving HTTP requests,
    dispatching them to appropriate route handlers, and constructing responses.
    It supports RESTful routing via hooks registered in the `routes` dict and
    integrates with :class:`Request` and :class:`Response` objects for full
    request lifecycle management.

    Attributes:
        ip (str): IP address of the server.
        port (int): Port number of the server.
        conn (socket): Active TCP socket connection to client.
        connaddr (tuple): (host, port) address of the connected client.
        routes (dict): Mapping of (METHOD, path) to handler functions.
        request (Request): Request object for parsing incoming data.
        response (Response): Response object for building and sending replies.
    """

    __attrs__ = [
        "ip",
        "port",
        "conn",
        "connaddr",
        "routes",
        "request",
        "response",
    ]

    def __init__(self, ip, port, conn, connaddr, routes):
        """Initialize a new HttpAdapter instance.

        :param ip (str): Server IP address.
        :param port (int): Server port number.
        :param conn (socket): Active TCP socket.
        :param connaddr (tuple): Client (ip, port).
        :param routes (dict): Route mapping {(METHOD, path): handler_func}.
        """
        #: IP address
        self.ip = ip
        #: Port
        self.port = port
        #: Connection socket
        self.conn = conn
        #: Connection address (client host, port)
        self.connaddr = connaddr
        #: Route mapping
        self.routes = routes
        #: Request parser
        self.request = Request()
        #: Response builder
        self.response = Response()

    # ------------------------------------------------------------------
    # Synchronous handler (threading mode)
    # ------------------------------------------------------------------

    def handle_client(self, conn, addr, routes):
        """Handle an incoming client connection (synchronous / threaded mode).

        This is the workhorse for task 2.1 multi-thread mode.  Each thread
        calls this method once for a single client connection.

        Flow:
          1. Read raw bytes from socket.
          2. Parse into Request (method, path, headers, body, cookies, auth).
          3. Look up route hook → call handler(headers, body).
          4. Wrap handler result in HTTP response bytes.
          5. Send bytes back; close socket.

        :param conn (socket): The client socket connection.
        :param addr (tuple): The client's address (ip, port).
        :param routes (dict): Route mapping {(METHOD, path): handler}.
        """
        # Store references
        self.conn = conn
        self.connaddr = addr

        req = self.request
        resp = self.response

        # CORS headers injected into every response so browser fetch() to
        # different ports (e.g. 2026 ↔ 2027) works without "blocked by CORS" errors.
        

        # -- Read raw request bytes from the socket -----------------------
        # TODO: handle for App hook here — read request and dispatch
        try:
            raw = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
                # Once we have the full header section, check Content-Length
                if b"\r\n\r\n" in raw:
                    header_end = raw.find(b"\r\n\r\n")
                    header_bytes = raw[:header_end]
                    body_received = len(raw) - header_end - 4
                    # Parse Content-Length
                    content_length = 0
                    for line in header_bytes.decode("utf-8", errors="ignore").split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                content_length = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                            break
                    if body_received >= content_length:
                        break   # have all headers + full body
            msg = raw.decode("utf-8", errors="replace")
        except Exception as e:
            print("[HttpAdapter] recv error: {}".format(e))
            conn.close()
            return

        # -- Parse request -----------------------------------------------
        req.prepare(msg, routes)
        print("[HttpAdapter] Invoke handle_client connection {}".format(addr))

        origin = req.headers.get('origin', '*')
        cors_headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Cookie",
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }

        # -- Handle OPTIONS preflight (CORS pre-flight request from browser) --
        if req.method == "OPTIONS":
            preflight = (
                "HTTP/1.1 200 OK\r\n"
                "Access-Control-Allow-Origin: {}\r\n".format(origin) +
                "Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS\r\n"
                "Access-Control-Allow-Headers: Content-Type, Authorization, Cookie\r\n"
                "Access-Control-Allow-Credentials: true\r\n"
                "Vary: Origin\r\n"
                "Access-Control-Max-Age: 86400\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            conn.sendall(preflight.encode('utf-8'))
            conn.close()
            return

        # -- Dispatch to route handler ------------------------------------
        response_bytes = b""
        extra_headers = dict(cors_headers)   # start with CORS headers in every response

        if req.hook:
            # TODO: handle for App hook here
            # A matching route handler was found: call it with (headers, body)
            print("[HttpAdapter] Dispatching hook {} {}".format(req.method, req.path))
            try:
                if inspect.iscoroutinefunction(req.hook):
                    # Create a fresh event loop so we never depend on the default one.
                    _loop = asyncio.new_event_loop()
                    try:
                        result = _loop.run_until_complete(
                            req.hook(req.headers, req.body)
                        )
                    finally:
                        _loop.close()
                else:
                    result = req.hook(req.headers, req.body)
            except Exception as e:
                print("[HttpAdapter] Handler error: {}".format(e))
                result = json.dumps({"error": str(e)}).encode("utf-8")

            # Normalise result to bytes
            if isinstance(result, str):
                result = result.encode("utf-8")
            elif not isinstance(result, bytes):
                result = json.dumps(result).encode("utf-8")

            # Inspect the JSON payload for sentinel keys injected by handlers:
            #   __status__    → custom HTTP status code (e.g. 401)
            #   __set_cookie__ → value for Set-Cookie header (session auth)
            try:
                payload = json.loads(result.decode("utf-8"))
            except Exception:
                payload = {}

            http_status = payload.pop("__status__", 200)
            set_cookie  = payload.pop("__set_cookie__", None)

            if set_cookie:
                # Session cookie auth (Task 2.2): inject Set-Cookie header
                extra_headers["Set-Cookie"] = set_cookie

            if http_status == 401:
                # Return a proper 401 with WWW-Authenticate challenge (Task 2.2)
                response_bytes = resp.build_unauthorized(extra_headers=extra_headers)
            else:
                # Re-encode cleaned payload (sentinel keys removed)
                clean_result = json.dumps(payload).encode("utf-8") if payload else result
                response_bytes = resp.build_json_response(
                    clean_result, status=http_status, extra_headers=extra_headers
                )
        else:
            # No route hook found → serve static file or 404
            response_bytes = resp.build_response(req)

        # -- Send response and close connection ---------------------------
        conn.sendall(response_bytes)
        conn.close()


    # ------------------------------------------------------------------
    # Asynchronous handler (coroutine / asyncio mode)
    # ------------------------------------------------------------------

    async def handle_client_coroutine(self, reader, writer):
        """Handle an incoming client connection asynchronously (coroutine mode).

        This is the workhorse for task 2.1 asyncio coroutine mode.
        asyncio calls this as a callback for each new TCP connection.

        Flow mirrors handle_client but uses await reader.read() / writer.write().

        :param reader (asyncio.StreamReader): Async stream reader.
        :param writer (asyncio.StreamWriter): Async stream writer.
        """
        req = self.request
        resp = self.response

        addr = writer.get_extra_info("peername")
        print("[HttpAdapter] Invoke handle_client_coroutine connection {})".format(addr))

        # TODO: Handle the request asynchronously
        # Read raw bytes from the async stream
        try:
            msg_bytes = await reader.read(4096)
            msg = msg_bytes.decode("utf-8", errors='replace')
        except Exception as e:
            print("[HttpAdapter] async recv error: {}".format(e))
            writer.close()
            return

        # Re-use the same route dict that was passed during construction
        req.prepare(msg, routes=self.routes)

        response_bytes = b""

        if req.hook:
            # TODO: handle for App hook here (async version)
            print("[HttpAdapter] Async dispatching hook {} {}".format(req.method, req.path))
            try:
                if inspect.iscoroutinefunction(req.hook):
                    result = await req.hook(req.headers, req.body)
                else:
                    result = req.hook(req.headers, req.body)
            except Exception as e:
                print("[HttpAdapter] Async handler error: {}".format(e))
                result = json.dumps({"error": str(e)}).encode("utf-8")

            if isinstance(result, dict) and result.get("__status__") == 401:
                response_bytes = resp.build_unauthorized()
            else:
                if isinstance(result, str):
                    result = result.encode("utf-8")
                elif not isinstance(result, bytes):
                    result = json.dumps(result).encode("utf-8")
                response_bytes = resp.build_json_response(result)
        else:
            # Build static file response
            response_bytes = resp.build_response(req)

        # Send response asynchronously
        writer.write(response_bytes)
        await writer.drain()
        writer.close()

    # ------------------------------------------------------------------
    # Cookie extraction helper
    # ------------------------------------------------------------------

    def extract_cookies(self, req, resp):
        """Extract cookies from the Request headers into a dict.

        Reads the 'Cookie' header value and splits it on ';' to produce
        a {name: value} dictionary.

        :param req (Request): The incoming Request object.
        :param resp (Response): The Response object (unused, for API compat).
        :returns (dict): Parsed cookie name→value pairs.
        """
        cookies = {}
        # Cookie header format: "name1=val1; name2=val2"
        cookie_header = req.headers.get("cookie", "")
        if cookie_header:
            for pair in cookie_header.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    cookies[key.strip()] = value.strip()
        return cookies

    # ------------------------------------------------------------------
    # Header injection hook
    # ------------------------------------------------------------------

    def add_headers(self, request):
        """Inject custom headers into a request.

        This method is an override point for subclasses that need to add
        authentication or other headers before the request is forwarded.
        The base implementation does nothing.

        :param request (Request): The Request object to add headers to.
        """
        # Override in subclass to inject custom headers (e.g. auth tokens)
        pass

    def build_proxy_headers(self, proxy):
        """Return headers to add when proxying a request.

        Constructs Basic Auth credentials for proxy authentication (RFC 7235).
        The username/password are currently set to placeholder values; in a
        real deployment they would come from config or a credential store.

        :param proxy (str): The URL of the proxy being used.
        :returns (dict): Header dict with Proxy-Authorization set.
        """
        headers = {}

        # TODO: build your authentication here
        #       username, password = load_from_config(...)
        # We provide dummy auth here as a placeholder
        username, password = ("user1", "password")

        if username:
            import base64
            credentials = base64.b64encode(
                "{}:{}".format(username, password).encode()
            ).decode()
            headers["Proxy-Authorization"] = "Basic {}".format(credentials)

        return headers

    # ------------------------------------------------------------------
    # JSON response helper (delegates to Response)
    # ------------------------------------------------------------------

    def build_json_response(self, req, body_bytes, status=200, extra_headers=None):
        """Convenience wrapper: build a JSON HTTP response via the Response object.

        :param req (Request): The originating request (for URL context).
        :param body_bytes (bytes): Serialised JSON body.
        :param status (int): HTTP status code.
        :param extra_headers (dict | None): Additional headers.
        :returns (bytes): Complete HTTP response bytes.
        """
        resp = Response(req)
        if isinstance(req.url, bytes):
            resp.url = req.url.decode("utf-8")
        else:
            resp.url = req.url
        resp.request = req
        resp.connection = self
        return resp.build_json_response(body_bytes, status=status,
                                        extra_headers=extra_headers)
