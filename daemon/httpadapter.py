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
from .utils import get_auth_from_url

import asyncio
import inspect
import base64

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

        #: IP address.
        self.ip = ip
        #: Port.
        self.port = port
        #: Connection
        self.conn = conn
        #: Conndection address
        self.connaddr = connaddr
        #: Routes
        self.routes = routes
        #: Request
        self.request = Request()
        #: Response
        self.response = Response()

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
        # Request handler
        req = self.request
        # Response handler
        resp = self.response

        # Read the FULL HTTP request (headers + body based on Content-Length)
        def _read_full(conn):
            data = b''
            chunk = conn.recv(4096)
            if not chunk:
                return ''
            data += chunk
            while b'\r\n\r\n' not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            header_end = data.find(b'\r\n\r\n')
            body_bytes = data[header_end + 4:]
            content_length = 0
            for line in data[:header_end].decode('utf-8', errors='ignore').split('\r\n'):
                if line.lower().startswith('content-length:'):
                    try:
                        content_length = int(line.split(':', 1)[1].strip())
                    except ValueError:
                        pass
                    break
            while len(body_bytes) < content_length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                body_bytes += chunk
            return (data[:header_end] + b'\r\n\r\n' + body_bytes[:content_length]).decode('utf-8', errors='ignore')

        msg = _read_full(conn)
        req.prepare(msg, routes)
        print("[HttpAdapter] Invoke handle_client connection {}".format(addr))

        # Handle request hook
        response_data = None
        if req.hook:
            result = req.hook(headers=req.headers, body=req.body)  # pass dict, not raw string

            if inspect.iscoroutine(result):
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(result)
                loop.close()

            # Unwrap (content, status_code) tuple returned by route handlers
            if isinstance(result, tuple) and len(result) == 2:
                response_data, status_code = result
                resp.status_code = status_code
                resp.reason = "OK" if status_code == 200 else \
                              "Unauthorized" if status_code == 401 else \
                              "Bad Request" if status_code == 400 else "Error"
            else:
                response_data = result

            # Set session cookie on successful login
            if req.path == '/login' and resp.status_code == 200:
                resp.cookies['sessionid'] = 'secure_xyz_789'
        
        response = resp.build_response(req, envelop_content=response_data)

        #print("[HttpAdapter] Response content {}".format(response))
        conn.sendall(response)
        conn.close()

    async def handle_client_coroutine(self, reader, writer):
        """
        Handle an incoming client connection using stream reader writer asynchronously.

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param conn (socket): The client socket connection.
        :param addr (tuple): The client's address.
        :param routes (dict): The route mapping for dispatching requests.
        """
        # Request handler
        req = self.request
        # Response handler
        resp = self.response

        print("[HttpAdapter] Invoke handle_client_coroutine connection {})".format(
            writer.get_extra_info('peername')))
        addr = writer.get_extra_info("peername")

        # Read the FULL HTTP request asynchronously
        data = b''
        chunk = await reader.read(4096)
        if not chunk:
            writer.close()
            return
        data += chunk
        while b'\r\n\r\n' not in data:
            chunk = await reader.read(4096)
            if not chunk:
                break
            data += chunk
        header_end = data.find(b'\r\n\r\n')
        body_bytes = data[header_end + 4:]
        content_length = 0
        for line in data[:header_end].decode('utf-8', errors='ignore').split('\r\n'):
            if line.lower().startswith('content-length:'):
                try:
                    content_length = int(line.split(':', 1)[1].strip())
                except ValueError:
                    pass
                break
        while len(body_bytes) < content_length:
            chunk = await reader.read(4096)
            if not chunk:
                break
            body_bytes += chunk
        msg = (data[:header_end] + b'\r\n\r\n' + body_bytes[:content_length]).decode('utf-8', errors='ignore')

        req.prepare(msg, routes=self.routes)

        # Handle request hook
        response_data = None
        if req.hook:
            result = req.hook(headers=req.headers, body=req.body)  # pass dict, not raw string

            if inspect.iscoroutine(result):
                result = await result

            # Unwrap (content, status_code) tuple returned by route handlers
            if isinstance(result, tuple) and len(result) == 2:
                response_data, status_code = result
                resp.status_code = status_code
                resp.reason = "OK" if status_code == 200 else \
                              "Unauthorized" if status_code == 401 else \
                              "Bad Request" if status_code == 400 else "Error"
            else:
                response_data = result

            # Set session cookie on successful login
            if req.path == '/login' and resp.status_code == 200:
                resp.cookies['sessionid'] = 'secure_xyz_789'

        # Build response
        #print("[HttpAdapter] Start **ASYNC** build_response with type {}".format(type(req)))
        response = resp.build_response(req, envelop_content=response_data)

        # Send all the response asynchronously
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    #@property
    def extract_cookies(self, req, resp):
        """
        Build cookies from the :class:`Request <Request>` headers.

        :param req:(Request) The :class:`Request <Request>` object.
        :param resp: (Response) The res:class:`Response <Response>` object.
        :rtype: cookies - A dictionary of cookie key-value pairs.
        """
        req = self.request
        cookies = {}
        if req and req.headers:
            cookies_str = req.headers.get("cookie", "")
            print(f"[DEBUG Raw cookie string from header: '{ cookies_str }'")
            if cookies_str:
                for pair in cookies_str.splite(";"):
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
        response = Response(request=req)

        # Set encoding.
        response.encoding = 'utf-8'

        if hasattr(resp, 'reason'):
            response.reason = resp.reason

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
        response = Response(request=req)

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


    # def get_connection(self, url, proxies=None):
        # """Returns a url connection for the given URL. 

        # :param url: The URL to connect to.
        # :param proxies: (optional) A Requests-style dictionary of proxies used on this request.
        # :rtype: int
        # """

        # proxy = select_proxy(url, proxies)

        # if proxy:
            # proxy = prepend_scheme_if_needed(proxy, "http")
            # proxy_url = parse_url(proxy)
            # if not proxy_url.host:
                # raise InvalidProxyURL(
                    # "Please check proxy URL. It is malformed "
                    # "and could be missing the host."
                # )
            # proxy_manager = self.proxy_manager_for(proxy)
            # conn = proxy_manager.connection_from_url(url)
        # else:
            # # Only scheme should be lower case
            # parsed = urlparse(url)
            # url = parsed.geturl()
            # conn = self.poolmanager.connection_from_url(url)

        # return conn


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

        if proxy:
            u, p = get_auth_from_url(proxy)
            if u:
                username, password = u, p

        if username:
            auth_str = "{}:{}".forma(username,password)
            encoded = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
            headers["Proxy-Authorization"] = "Basic {}".format(encoded)

        return headers