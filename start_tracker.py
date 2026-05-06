#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
#

"""
start_tracker
~~~~~~~~~~~~~~~~~

Entry point for the central tracker/backend process.

The tracker exposes authentication and peer discovery routes only. It should not
be used as Alice or Bob in the P2P chat demo.
"""

import argparse

from apps import create_sampleapp

PORT = 3001

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Tracker', description='', epilog='Tracker daemon')
    parser.add_argument('--server-ip', default='0.0.0.0')
    parser.add_argument('--server-port', type=int, default=PORT)

    args = parser.parse_args()

    create_sampleapp(args.server_ip, args.server_port, role='tracker')
