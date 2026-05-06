#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
#

"""
start_web
~~~~~~~~~~~~~~~~~

Entry point for the static web layer.

This process serves files from www/ and static/. It does not expose tracker or
peer chat API routes; those are reached through the proxy or peer backends.
"""

import argparse

from daemon import create_backend

PORT = 3000

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Web', description='', epilog='Web daemon')
    parser.add_argument('--server-ip', default='0.0.0.0')
    parser.add_argument('--server-port', type=int, default=PORT)

    args = parser.parse_args()

    create_backend(args.server_ip, args.server_port, routes={})
