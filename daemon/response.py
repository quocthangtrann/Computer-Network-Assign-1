#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# AsynApRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#

"""
daemon.response
~~~~~~~~~~~~~~~~~

This module provides a :class:`Response <Response>` object to manage HTTP
responses. It supports:
  - MIME type detection from file extensions
  - Serving static files from www/ and static/ directories
  - Building JSON responses for REST API handlers
  - Building formatted HTTP response headers
  - Injecting authentication headers (WWW-Authenticate, Set-Cookie)

The Response class is used by HttpAdapter to turn the output of route
handlers into raw bytes that are sent back over TCP.
"""
import datetime
import json
import os
import mimetypes
from .dictionary import CaseInsensitiveDict

# Base directory for locating served files (set to project root by default)
BASE_DIR = ""


class Response():
    """The :class:`Response <Response>` object, which contains a
    server's response to an HTTP request.

    Instances are created by HttpAdapter and populated via build_response()
    or build_json_response().

    Attributes:
        status_code (int): HTTP status code (200, 401, 404, …).
        headers (dict): Response header dict.
        url (str): URL of the response.
        encoding (str): Encoding used for the response body.
        history (list): List of prior Response objects (redirect chain).
        reason (str): HTTP reason phrase ("OK", "Not Found", …).
        cookies (CaseInsensitiveDict): Cookies to set in the response.
        elapsed (datetime.timedelta): Time to complete the request.
        request: The originating request object.
        _content (bytes): Response body bytes.
        _header (bytes): Encoded HTTP header bytes.
    """

    __attrs__ = [
        "_content",
        "_header",
        "status_code",
        "method",
        "headers",
        "url",
        "history",
        "encoding",
        "reason",
        "cookies",
        "elapsed",
        "request",
        "body",
        "reason",
    ]

    def __init__(self, request=None):
        """Initialize a new Response object.

        :param request: The originating request (optional).
        """
        self._content = b""
        self._header = b""
        self._content_consumed = False
        self._next = None

        #: Integer HTTP status code, e.g. 200 or 404.
        self.status_code = 200

        #: Case-insensitive dict of response headers.
        self.headers = {}

        #: URL location of the response.
        self.url = None

        #: Encoding to decode with when accessing response text.
        self.encoding = "utf-8"

        #: A list of redirect Response objects.
        self.history = []

        #: Textual reason for the status code (e.g. "OK", "Not Found").
        self.reason = "OK"

        #: A dict of cookies from the response headers.
        self.cookies = CaseInsensitiveDict()

        #: Time elapsed to complete the request.
        self.elapsed = datetime.timedelta(0)

        #: The originating Request object.
        self.request = request

    # ------------------------------------------------------------------
    # MIME helpers
    # ------------------------------------------------------------------

    def get_mime_type(self, path):
        """Determine the MIME type of a resource from its URL path.

        Uses Python's mimetypes stdlib to guess the type from the file
        extension. Falls back to 'application/octet-stream'.

        :param path (str): URL path (e.g. '/index.html', '/static/css/styles.css').
        :returns (str): MIME type string.
        """
        try:
            mime_type, _ = mimetypes.guess_type(path)
        except Exception:
            return 'application/octet-stream'
        return mime_type or 'application/octet-stream'

    def prepare_content_type(self, mime_type='text/html'):
        """Set Content-Type header and return the base directory for the file.

        Routing logic:
          text/html  → www/
          text/css   → static/css/
          text/plain → static/
          image/*    → static/images/
          application/json → apps/  (for REST payloads)
          video/*    → static/video/
          audio/*    → static/audio/
          application/xml, application/zip → static/

        :param mime_type (str): MIME type string.
        :returns (str): Base directory path for the resource.
        :raises ValueError: If the MIME type is unsupported.
        """
        base_dir = ""

        # Ensure headers dict exists
        if not hasattr(self, "headers") or self.headers is None:
            self.headers = {}

        # Split into main/sub type (e.g. "text" / "html")
        main_type, sub_type = mime_type.split('/', 1)
        print("[Response] Processing main_type={} sub_type={}".format(main_type, sub_type))

        if main_type == 'text':
            self.headers['Content-Type'] = 'text/{}; charset=utf-8'.format(sub_type)
            if sub_type == 'css':
                # CSS URL path is /css/styles.css → file at static/css/styles.css
                # Use static/ as base so build_content joins correctly
                base_dir = BASE_DIR + "static/"
            elif sub_type == 'html':
                # HTML pages live under www/
                base_dir = BASE_DIR + "www/"
            elif sub_type == 'plain':
                base_dir = BASE_DIR + "static/"
            elif sub_type == 'csv':
                # TODO: process text/csv
                base_dir = BASE_DIR + "static/"
            elif sub_type == 'xml':
                # TODO: process text/xml
                base_dir = BASE_DIR + "static/"
            else:
                # Fallback for any other text subtype
                base_dir = BASE_DIR + "static/"

        elif main_type == 'image':
            # Image URL path is /images/foo.png → file at static/images/foo.png
            # Use static/ as base so build_content joins correctly
            base_dir = BASE_DIR + "static/"
            self.headers['Content-Type'] = 'image/{}'.format(sub_type)

        elif main_type == 'application':
            if sub_type == 'json':
                # JSON responses are built in-memory (no file read needed)
                base_dir = BASE_DIR + "apps/"
                self.headers['Content-Type'] = 'application/json'
            elif sub_type == 'xml':
                # TODO: process application/xml
                base_dir = BASE_DIR + "static/"
                self.headers['Content-Type'] = 'application/xml'
            elif sub_type == 'zip':
                # TODO: process application/zip
                base_dir = BASE_DIR + "static/"
                self.headers['Content-Type'] = 'application/zip'
            else:
                base_dir = BASE_DIR + "static/"
                self.headers['Content-Type'] = 'application/{}'.format(sub_type)

        elif main_type == 'video':
            # TODO: process video/mp4, video/mpeg, etc.
            base_dir = BASE_DIR + "static/video/"
            self.headers['Content-Type'] = 'video/{}'.format(sub_type)

        elif main_type == 'audio':
            # TODO: process audio types
            base_dir = BASE_DIR + "static/audio/"
            self.headers['Content-Type'] = 'audio/{}'.format(sub_type)

        else:
            raise ValueError("Invalid MIME type: main_type={} sub_type={}".format(
                main_type, sub_type))

        return base_dir

    # Content loading
    def build_content(self, path, base_dir):
        # Load a static file from disk and return its bytes.
        filepath = os.path.join(base_dir, path.lstrip('/'))
        print("[Response] Serving the object at location {}".format(filepath))

        # TODO: implement the step of fetch the object file
        #       store in the return value of content
        try:
            with open(filepath, "rb") as f:
                content = f.read()
        except Exception as e:
            print("[Response] build_content exception: {}".format(e))
            return -1, b""
        return len(content), content

    # Header building
    def build_response_header(self, request, extra_headers=None):
        """Construct full HTTP response header bytes.

        Builds the status line and all headers, then encodes them as UTF-8.
        The formatted header ends with \\r\\n\\r\\n (blank line before body).

        Extra headers (e.g. Set-Cookie, WWW-Authenticate) can be passed in
        via the extra_headers dict.

        :param request: The incoming Request object.
        :param extra_headers (dict | None): Additional headers to inject.
        :returns (bytes): Encoded HTTP header block.
        """
        reqhdr = request.headers if request.headers else {}

        # TODO: prepare the request authentication
        # self.auth = ...

        # Build the header dict from known fields
        headers = {
            "Accept": "{}".format(reqhdr.get("Accept", "application/json")),
            "Accept-Language": "{}".format(reqhdr.get("Accept-Language", "en-US,en;q=0.9")),
            "Cache-Control": "no-cache",
            "Content-Type": "{}".format(self.headers.get('Content-Type', 'text/html')),
            "Content-Length": "{}".format(len(self._content)),
            "Date": "{}".format(datetime.datetime.utcnow().strftime(
                "%a, %d %b %Y %H:%M:%S GMT")),
            "Max-Forward": "10",
            "Pragma": "no-cache",
            "User-Agent": "{}".format(reqhdr.get("User-Agent", "AsynapRous/1.0")),
            "Connection": "close",
        }

        # Inject any extra headers (e.g. Set-Cookie, WWW-Authenticate)
        if extra_headers:
            headers.update(extra_headers)

        # Build the status line (e.g. "HTTP/1.1 200 OK")
        status_reasons = {
            200: "OK",
            201: "Created",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Internal Server Error",
        }
        reason = status_reasons.get(self.status_code, "OK")

        # TODO: implement the header building to create formatted header from the provided headers
        # Assemble all lines
        header_lines = ["HTTP/1.1 {} {}".format(self.status_code, reason)]
        for key, value in headers.items():
            header_lines.append("{}: {}".format(key, value))
        # Blank line signals end of headers
        header_lines.append("")
        header_lines.append("")

        fmt_header = "\r\n".join(header_lines)
        return fmt_header.encode('utf-8')

    # 404 builder
    def build_notfound(self):
        #Construct a standard 404 Not Found HTTP response.

        return (
            "HTTP/1.1 404 Not Found\r\n"
            "Accept-Ranges: bytes\r\n"
            "Content-Type: text/html\r\n"
            "Content-Length: 13\r\n"
            "Cache-Control: max-age=86000\r\n"
            "Connection: close\r\n"
            "\r\n"
            "404 Not Found"
        ).encode('utf-8')

    def build_unauthorized(self, realm="AsynapRous"):
        #Build a 401 Unauthorized response with a WWW-Authenticate header.


        body = b"401 Unauthorized"
        header = (
            "HTTP/1.1 401 Unauthorized\r\n"
            'WWW-Authenticate: Basic realm="{}"\r\n'.format(realm) +
            "Content-Type: text/plain\r\n"
            "Content-Length: {}\r\n".format(len(body)) +
            "Connection: close\r\n"
            "\r\n"
        )
        return header.encode('utf-8') + body

    # JSON response builder (for REST API routes)
    def build_json_response(self, body_bytes, status=200, extra_headers=None):
        #Build a full HTTP response for a JSON REST payload.

        self.status_code = status
        self._content = body_bytes if body_bytes else b""
        self.headers['Content-Type'] = 'application/json'

        # Build status reasons
        status_reasons = {
            200: "OK", 201: "Created", 400: "Bad Request",
            401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 500: "Internal Server Error",
        }
        reason = status_reasons.get(status, "OK")

        # Build headers block
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(self._content)),
            "Date": datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Cache-Control": "no-cache",
            "Connection": "close",
        }
        if extra_headers:
            headers.update(extra_headers)

        header_lines = ["HTTP/1.1 {} {}".format(status, reason)]
        for k, v in headers.items():
            header_lines.append("{}: {}".format(k, v))
        header_lines.append("")
        header_lines.append("")
        header_str = "\r\n".join(header_lines)

        return header_str.encode('utf-8') + self._content

    # Main response builder (for static files)
    def build_response(self, request, envelop_content=None):
        #Build a full HTTP response for a static file request.

        print("[Response] Start build response with req {}".format(request))

        path = request.path

        # Guard: empty / malformed requests (e.g. browser keep-alive pings) have no path
        if not path:
            return self.build_notfound()

        # Root path / → serve index.html
        if path == '/' or path == '':
            path = '/index.html'

        mime_type = self.get_mime_type(path)
        print("[Response] {} path {} mime_type {}".format(
            request.method, path, mime_type))

        base_dir = ""

        # Route to the correct base directory by MIME type
        if path.endswith('.html') or mime_type == 'text/html':
            base_dir = self.prepare_content_type(mime_type='text/html')
        elif mime_type == 'text/css':
            base_dir = self.prepare_content_type(mime_type='text/css')
        elif mime_type and mime_type.startswith('image/'):
            base_dir = self.prepare_content_type(mime_type=mime_type)
        elif mime_type == 'application/json' or mime_type == 'application/octet-stream':
            # JSON API response — body is provided by the route handler
            self.headers['Content-Type'] = 'application/json'
            body = envelop_content if envelop_content else b""
            return self.build_json_response(body)
        # TODO: add support for other object types (video, audio, xml, zip, …)
        else:
            return self.build_notfound()

        # Load file from disk
        content_length, self._content = self.build_content(path, base_dir)
        if content_length == -1:
            return self.build_notfound()

        # Build and store header bytes
        self._header = self.build_response_header(request)

        return self._header + self._content
