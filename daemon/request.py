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
"""

import base64
from .dictionary import CaseInsensitiveDict
from urllib.parse import urlparse

class Request():
    """The fully mutable "class" `Request <Request>` object,
    containing the exact bytes that will be sent to the server.

    Instances are generated from a "class" `Request <Request>` object, and
    should not be instantiated manually; doing so may produce undesirable
    effects.

    Usage::

      >>> import deamon.request
      >>> req = request.Request()
      ## Incoming message obtain aka. incoming_msg
      >>> r = req.prepare(incoming_msg)
      >>> r
      <Request>
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
        #: HTTP verb to send to the server.
        self.method = None
        #: HTTP URL to send the request to.
        self.url = None
        #: dictionary of HTTP headers.
        self.headers = CaseInsensitiveDict()
        #: HTTP path
        self.path = None        
        # The cookies set used to create Cookie header
        self.cookies = None
        #: request body to send to the server.
        self.body = None
        # The raw header
        self._raw_headers = None
        #: The raw body
        self._raw_body = None
        #: Routes
        self.routes = {}
        #: Hook point for routed mapped-path
        self.hook = None

    def extract_request_line(self, request):
        try:
            lines = request.splitlines()
            first_line = lines[0]
            method, path, version = first_line.split()

            if path.startswith('http://') or path.startswith('http://'):
                path = urlparse(path).path

            if path == '/':
                path = '/index.html'
        except Exception:
            return None, None, None

        return method, path, version
    
    def prepare_headers(self, request):
        """Prepares the given HTTP headers."""
        lines = request.split('\r\n')
        headers = CaseInsensitiveDict()
        for line in lines[1:]:
            if ': ' in line:
                key, val = line.split(': ', 1)
                headers[key] = val # auto lowercase because of CaseInsensitiveDict
        return headers

    def fetch_headers_body(self, request):
        """Prepares the given HTTP headers."""
        # Split request into header section and body section
        parts = request.split("\r\n\r\n", 1)  # split once at blank line

        _headers = parts[0]
        _body = parts[1] if len(parts) > 1 else ""
        return _headers, _body

    def prepare(self, request, routes=None):
        """Prepares the entire request with the given parameters."""

        # Prepare the request line from the request header
        print("[Request] prepare request missg {}".format(request))
        self.method, self.path, self.version = self.extract_request_line(request)
        print("[Request] {} path {} version {}".format(self.method, self.path, self.version))

        #
        # @bksysnet Preapring the webapp hook with AsynapRous instance
        # The default behaviour with HTTP server is empty routed
        #
        # TODO manage the webapp hook in this mounting point
        #

        self._raw_headers, self._raw_body = self.fetch_headers_body(request)

        self.headers = self.prepare_headers(self._raw_headers)

        if not routes == {}:
            self.routes = routes
            print("[Request] Routing METHOD {} path {}".format(self.method, self.path))
            self.hook = routes.get((self.method, self.path))
            print("[Request] Hook has request {}".format(request))
            #
            # self.hook manipulation goes here
            # ...
            #
            if self.hook:
                pass

        cookies = self.headers.get('cookie', '') if self.headers else ''
            #
            #  TODO: implement the cookie function here
            #        by parsing the header            
        
        self.cookies = CaseInsensitiveDict()
        if cookies:
            parts = cookies.split(';')
            for part in parts:
                if '=' in part:
                    key, val = part.strip().split('=', 1)
                    self.cookies[key] = val
        
        auth_header = self.headers.get('authorization', '') if self.headers else ''
        self.prepare_auth(auth_header, url = self.path)
        self.prepare_body(self._raw_body, files=None)

        return

    def prepare_body(self, data, files, json=None):
        """Prepares the request body and calculates content-length."""
        self.prepare_content_length(data)
        self.body = data
        return

    def prepare_content_length(self, body):
        """Sets the Content-Length header based on body size."""
        if body:
            self.headers["Content-Length"] = str(len(body.encode('utf-8')))
        else:
            self.headers["Content-Length"] = "0"
        return


    def prepare_auth(self, auth = "", url=""):
        #
        # TODO prepare the request authentication
        #
        self.auth = ("", "")
        if auth and auth.lower().startswith('basic '):
            encoded_cred = auth[6:].strip()
            try:
                decoded_cred = base64.b64decode(encoded_cred).decode('utf-8')
                if ':' in decoded_cred:
                    self.auth = tuple(decoded_cred.split(':', 1))
            except Exception:
                pass
        return

    def prepare_cookies(self, cookies = None):
        self.headers["Cookie"] = cookies
        return


