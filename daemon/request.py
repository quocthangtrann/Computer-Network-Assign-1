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
daemon.request
~~~~~~~~~~~~~~~~~

This module provides a Request object to manage and persist 
request settings (cookies, auth, proxies).

The Request class parses raw HTTP bytes that arrive over a TCP socket
and exposes them as structured Python attributes (.method, .path,
.headers, .body, .cookies, .auth) for use by route handlers.
"""
import base64
from .dictionary import CaseInsensitiveDict


class Request():
    """The fully mutable :class:`Request <Request>` object,
    containing the parsed data from a raw HTTP request.

    Instances are populated via :meth:`prepare`, which parses the raw
    incoming TCP message and fills all attributes.

    Attributes:
        method  (str): HTTP method (GET, POST, PUT, DELETE, …).
        url     (str): Requested URL path.
        path    (str): Alias for url.
        version (str): HTTP version string (e.g. "HTTP/1.1").
        headers (CaseInsensitiveDict): Parsed request headers.
        body    (str): Raw request body text.
        cookies (dict): Parsed cookies from Cookie header.
        auth    (tuple | None): (username, password) from Basic Auth, or None.
        routes  (dict): Route mapping passed in from the backend.
        hook    (callable | None): Matched route handler function, or None.

    Usage::

      >>> req = Request()
      >>> req.prepare(raw_bytes_decoded, routes)
      >>> req.method
      'POST'
    """

    __attrs__ = [
        "method",
        "url",
        "headers",
        "body",
        "_raw_headers",
        "_raw_body",
        "reason",
        "cookies",
        "body",
        "routes",
        "hook",
    ]

    def __init__(self):
        #: HTTP verb (GET, POST, PUT, DELETE, …)
        self.method = None
        #: HTTP URL path (e.g. "/login")
        self.url = None
        #: HTTP path (alias for url)
        self.path = None
        #: HTTP version string (e.g. "HTTP/1.1")
        self.version = None
        #: CaseInsensitiveDict of parsed HTTP headers
        self.headers = CaseInsensitiveDict()
        #: Cookie dictionary {name: value}
        self.cookies = {}
        #: Decoded auth tuple (username, password) or None
        self.auth = None
        #: Raw request body string
        self.body = None
        # Raw header section (before \r\n\r\n)
        self._raw_headers = None
        #: Raw body section (after \r\n\r\n)
        self._raw_body = None
        #: Route mapping {(METHOD, path): handler_func}
        self.routes = {}
        #: The matched handler function for the current (method, path)
        self.hook = None

    # Parsing helpers

    def extract_request_line(self, request):

        try:
            lines = request.splitlines()
            first_line = lines[0]
            method, raw_path, version = first_line.split()

            # Strip query string from path for routing and file serving
            if '?' in raw_path:
                path = raw_path.split('?', 1)[0]
            else:
                path = raw_path

        except Exception:
            return None, None, None

        return method, path, version

    def fetch_headers_body(self, request_bytes):
        # Split at the first blank line (using bytes)
        parts = request_bytes.split(b"\r\n\r\n", 1)

        _headers = parts[0].decode("utf-8", errors="replace")
        _body = parts[1] if len(parts) > 1 else b""
        return _headers, _body

    def prepare_headers(self, raw_header_section):

        lines = raw_header_section.split('\r\n')
        headers = CaseInsensitiveDict()
        # Skip line 0 (the request line) and parse "Key: Value" pairs
        for line in lines[1:]:
            if ':' in line:
                key, val = line.split(':', 1)
                headers[key.strip()] = val.strip()
        return headers

    def prepare_cookies(self, raw_cookie_string):

        cookies = {}
        if raw_cookie_string:
            for pair in raw_cookie_string.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    key, val = pair.split('=', 1)
                    cookies[key.strip()] = val.strip()
        self.cookies = cookies

    def prepare_auth(self, auth_header, url=""):

        self.auth = None
        if auth_header and auth_header.lower().startswith("basic "):
            try:
                # Decode base64 credentials
                encoded = auth_header[6:]  # strip "Basic "
                decoded = base64.b64decode(encoded).decode("utf-8")
                username, password = decoded.split(":", 1)
                self.auth = (username, password)
            except Exception:
                self.auth = None

    def prepare_body(self, data, files=None, json=None):
        """Store the request body and update Content-Length.

        :param data: Raw body bytes.
        :param files: (unused stub — for API compatibility).
        :param json: (unused stub — for API compatibility).
        """
        self.body = data
        self.prepare_content_length(data)

    def prepare_content_length(self, body):
        """Set the Content-Length header based on the body length.

        :param body (bytes | None): The request body.
        """
        if body:
            self.headers["Content-Length"] = str(len(body))
        else:
            self.headers["Content-Length"] = "0"

    # Main entry point
    def prepare(self, request_bytes, routes=None):
        # Parse a raw HTTP request bytes and populate all attributes.

        if not isinstance(request_bytes, bytes):
            request_bytes = request_bytes.encode("utf-8", errors="replace")

        # Step 1: Split headers / body (bytes)
        self._raw_headers_text, self._raw_body = self.fetch_headers_body(request_bytes)
        self.body = self._raw_body

        # Step 2: Request line
        print("[Request] prepare request headers section {}".format(self._raw_headers_text))
        self.method, self.path, self.version = self.extract_request_line(self._raw_headers_text)
        self.url = self.path   # keep url as alias so both attributes work
        print("[Request] {} path {} version {}".format(self.method, self.path, self.version))

        # Step 3: Parse headers
        self.headers = self.prepare_headers(self._raw_headers_text)

        # Step 4: Parse cookies
        cookie_str = self.headers.get('cookie', '')
        self.prepare_cookies(cookie_str)

        # Step 5: Parse Basic Auth
        auth_header = self.headers.get('authorization', '')
        self.prepare_auth(auth_header)

        # Step 6: Route hook
        # Preparing the webapp hook with AsynapRous instance
        # The default behaviour with HTTP server is empty routed
        if routes is not None and routes != {}:
            self.routes = routes
            print("[Request] Routing METHOD {} path {}".format(self.method, self.path))
            self.hook = routes.get((self.method, self.path))
            if self.hook:
                print("[SampleApp] hook in route-path METHOD {} PATH [{}]".format(
                    self.path, self.method))

        return
