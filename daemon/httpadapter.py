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

This module provides a http adapter object to manage and persist 
http settings (headers, bodies). The adapter supports both
raw URL paths and RESTful route definitions, and integrates with
Request and Response objects to handle client-server communication.
"""

from .request import Request
from .response import Response
from .dictionary import CaseInsensitiveDict

import asyncio
import inspect

def get_encoding_from_headers(headers):
    """Returns encodings from given HTTP Header Dict.

    :param headers: dictionary to extract encoding from.
    :rtype: str
    """
    if not headers:
        return None

    content_type = headers.get('content-type') or headers.get('Content-Type')
    if not content_type:
        return None

    content_type = content_type.lower()
    if 'charset=' in content_type:
        return content_type.split('charset=')[-1].split(';')[0].strip('"\'')

    if 'text' in content_type:
        return 'ISO-8859-1'
    if 'application/json' in content_type:
        return 'utf-8'

    return None

class HttpAdapter:
    """
    A mutable :class:`HTTP adapter <HTTP adapter>` for managing client connections
    and routing requests.

    The `HttpAdapter` class encapsulates the logic for receiving HTTP requests,
    dispatching them to appropriate route handlers, and constructing responses.
    It supports RESTful routing via hooks and integrates with :class:`Request <Request>` 
    and :class:`Response <Response>` objects for full request lifecycle management.

    Attributes:
        ip (str): IP address of the client.
        port (int): Port number of the client.
        conn (socket): Active socket connection.
        connaddr (tuple): Address of the connected client.
        routes (dict): Mapping of route paths to handler functions.
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
        """
        Initialize a new HttpAdapter instance.

        :param ip (str): IP address of the client.
        :param port (int): Port number of the client.
        :param conn (socket): Active socket connection.
        :param connaddr (tuple): Address of the connected client.
        :param routes (dict): Mapping of route paths to handler functions.
        """

        self.ip = ip
        self.port = port
        self.conn = conn
        self.connaddr = connaddr
        self.routes = routes
        self.request = Request()
        self.response = Response()

    def _read_full_request(self, conn):
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

    async def _read_full_request_async(self, reader):
        """
        Read a complete HTTP request from async stream reader, including headers and body.
        
        :param reader (StreamReader): The async stream reader.
        :rtype: str - The complete HTTP request message.
        """
        request_data = b''
        
        # Read initial chunk
        chunk = await reader.read(4096)
        if not chunk:
            return ""
        
        request_data += chunk
        
        # Check if we've received the full headers (\r\n\r\n)
        if b'\r\n\r\n' not in request_data:
            # Keep reading until we get headers
            while True:
                chunk = await reader.read(4096)
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
                chunk = await reader.read(4096)
                if not chunk:
                    break
                body_bytes += chunk
        
        # Reconstruct the complete request
        full_request = headers_bytes + b'\r\n\r\n' + body_bytes[:content_length]
        return full_request.decode('utf-8', errors='ignore')

    def handle_client(self, conn, addr, routes):
        """
        Handle an incoming client connection.

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param conn (socket): The client socket connection.
        :param addr (tuple): The client's address.
        :param routes (dict): The route mapping for dispatching requests.
        """

        # Connection handler.
        self.conn = conn        
        # Connection address.
        self.connaddr = addr

        # Create fresh request/response objects per connection to avoid stale state
        req = Request()
        resp = Response()

        # Handle the request - read full HTTP request including body
        msg = self._read_full_request(conn)
        req.prepare(msg, routes)
        print("[HttpAdapter] Invoke handle_client connection {}".format(addr))

        if req.method == "OPTIONS":
            resp.status_code = 200
            resp._content = b""
        # Handle request hook (API routing)
        elif req.hook:
            app_result = req.hook(headers=req.headers, body=req.body)
            
            if isinstance(app_result, tuple):
                resp._content = app_result[0]
                resp.status_code = app_result[1]
            else:
                resp._content = app_result
                resp.status_code = 200
            
            # Check webapp request to set new cookie
            if req.path == '/login' and resp.status_code == 200:
                resp.cookies['sessionid'] = 'secure_xyz_789'
                
        response = resp.build_response(req)

        conn.sendall(response)
        conn.close()

    async def handle_client_coroutine(self, reader, writer):
        """
        Handle an incoming client connection using stream reader writer asynchronously.
        """
        # Create fresh request/response objects per connection to avoid stale state
        req = Request()
        resp = Response()

        addr = writer.get_extra_info("peername")
        print("[HttpAdapter] Invoke handle_client_coroutine connection {}".format(addr))

        # Read full HTTP request including body
        msg = await self._read_full_request_async(reader)

        # self.routes
        req.prepare(msg, routes=self.routes)

        if req.method == "OPTIONS":
            resp.status_code = 200
            resp._content = b""
        # Handle request hook
        elif req.hook:
            if inspect.iscoroutinefunction(req.hook):
                app_result = await req.hook(headers=req.headers, body=req.body)
            else:
                app_result = req.hook(headers=req.headers, body=req.body)
            
            if isinstance(app_result, tuple):
                resp._content = app_result[0]
                resp.status_code = app_result[1]
            else:
                resp._content = app_result
                resp.status_code = 200

            # Logic Cookie
            if req.path == '/login' and resp.status_code == 200:
                resp.cookies['sessionid'] = 'secure_xyz_789'
                
        response = resp.build_response(req)

        # Send all the response asynchronously
        writer.write(response)
        await writer.drain()

    def extract_cookies(self, req):
        """
        Build cookies from the :class:`Request <Request>` headers.

        :param req:(Request) The :class:`Request <Request>` object.
        :rtype: cookies - A dictionary of cookie key-value pairs.
        """
        cookies = {}
        if not req or not hasattr(req, 'headers') or not req.headers:
            return cookies
            
        cookie_header = req.headers.get('cookie') or req.headers.get('Cookie')
        if cookie_header:
            for pair in cookie_header.split(";"):
                if "=" in pair:
                    key, value = pair.strip().split("=", 1)
                    cookies[key] = value
        return cookies

    def build_response(self, req, resp):
        """Builds a :class:`Response <Response>` object 

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The  response object.
        :rtype: Response
        """
        response = Response()

        # Set encoding.
        response.encoding = get_encoding_from_headers(response.headers)
        response.raw = resp
        response.reason = response.raw.reason

        if isinstance(req.url, bytes):
            response.url = req.url.decode("utf-8")
        else:
            response.url = req.url

        # Add new cookies from the server.
        response.cookies = self.extract_cookies(req)

        # Give the Response some context.
        response.request = req
        response.connection = self

        return response

    def build_json_response(self, req, resp):
        """Builds a :class:`Response <Response>` object from JSON data

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The  response object.
        :rtype: Response
        """
        response = Response(req)

        # Set encoding.
        response.raw = resp

        if isinstance(req.url, bytes):
            response.url = req.url.decode("utf-8")
        else:
            response.url = req.url

        # Give the Response some context.
        response.request = req
        response.connection = self

        return response


    def add_headers(self, request):
        """
        Add headers to the request.

        This method is intended to be overridden by subclasses to inject
        custom headers. It does nothing by default.

        
        :param request: :class:`Request <Request>` to add headers to.
        """
        pass

    def build_proxy_headers(self, proxy):
        """Returns a dictionary of the headers to add to any request sent
        through a proxy. 

        :class:`HttpAdapter <HttpAdapter>`.

        :param proxy: The url of the proxy being used for this request.
        :rtype: dict
        """
        headers = {}
        #
        # TODO: build your authentication here
        #       username, password =...
        # we provide dummy auth here
        #
        username, password = ("user1", "password")

        if username:
            headers["Proxy-Authorization"] = (username, password)

        return headers