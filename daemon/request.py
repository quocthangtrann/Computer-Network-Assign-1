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
from .dictionary import CaseInsensitiveDict
import base64

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
        self.method = None
        self.url = None
        self.headers = {}
        self.path = None        
        self.cookies = None
        self.body = None
        self._raw_headers = None
        self._raw_body = None
        self.routes = {}
        self.hook = None

    def extract_request_line(self, request):
        try:
            lines = request.splitlines()
            first_line = lines[0]
            method, path, version = first_line.split()

            if path == '/':
                path = '/index.html'
        except Exception:
            return None, None, None

        return method, path, version
             
    def prepare_headers(self, request):
        """Prepares the given HTTP headers."""
        lines = request.split('\r\n')
        headers = {}
        for line in lines[1:]:
            if ': ' in line:
                key, val = line.split(': ', 1)
                headers[key.lower()] = val
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

        if not routes == {}:
            self.routes = routes
            print("[Request] Routing METHOD {} path {}".format(self.method, self.path))
            self.hook = routes.get((self.method, self.path))
            print("[Request] Hook has request {}".format(request))

        _headers, _body = self.fetch_headers_body(request)

        self._raw_headers = _headers 
        self._raw_body = _body
        
        # handle headers
        self.headers = self.prepare_headers(_headers)
        
        # Assign the extracted content to self.body
        self.prepare_body(_body, None)
        
        if self.headers:
            # get cookie, call parse
            cookies_str = self.headers.get('cookie', '')
            self.prepare_cookies(cookies_str)
            
            # get auth, call parse
            auth_str = self.headers.get('authorization', '')
            self.prepare_auth(auth_str)
        else:
            self.headers = {}
            self.cookies = {}
            self.auth = None

        return

    def prepare_body(self, data, files, json=None):
        self.body = data
        self.prepare_content_length(self.body)
        #
        # TODO prepare the request authentication
        #
	# self.auth = ...
        return


    def prepare_content_length(self, body):
        if self.headers is None:
            self.headers = {}
        content_len = len(body) if body else 0
        self.headers["Content-Length"] = str(content_len)
        #
        # TODO prepare the request authentication
        #
	# self.auth = ...
        return


    def prepare_auth(self, auth, url=""):
        """
        Parses and decodes RFC 2617 Basic Authentication header.
        
        Converts "Basic dXNlcjpwYXNz" to {"scheme": "basic", "username": "user", "password": "pass"}
        
        :param auth (str): The raw Authorization header value (e.g., "Basic dXNlcjpwYXNz")
        :param url (str): Optional URL for digest auth
        :return: dict with decoded auth info or None
        """
        if not auth:
            self.auth = None
            return
        
        auth = auth.strip()
        parts = auth.split(' ', 1)
        
        if len(parts) != 2:
            self.auth = None
            return
        
        scheme, credentials = parts
        scheme = scheme.lower()
        
        if scheme == 'basic':
            try:
                # Decode base64 credentials
                decoded = base64.b64decode(credentials).decode('utf-8')
                if ':' in decoded:
                    username, password = decoded.split(':', 1)
                    self.auth = {
                        'scheme': 'basic',
                        'username': username,
                        'password': password
                    }
                else:
                    # Malformed basic auth (no colon separator)
                    self.auth = {
                        'scheme': 'basic',
                        'username': decoded,
                        'password': ''
                    }
            except Exception as e:
                print("[Request] Failed to decode Basic Auth: {}".format(e))
                self.auth = None
        elif scheme == 'bearer':
            # Handle Bearer token
            self.auth = {
                'scheme': 'bearer',
                'token': credentials
            }
        else:
            # Store raw for other auth types
            self.auth = {
                'scheme': scheme,
                'credentials': credentials
            }
        return

    def prepare_cookies(self, cookies_str):
        self.cookies = {}
        if cookies_str:
            for pair in cookies_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    self.cookies[k] = v
