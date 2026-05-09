#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# AsynapRous release — Local Backend Startup
#
# Usage:
#   python3 start_local.py --port 3001 --central http://192.168.1.4:8080
#

import argparse

from apps.local_app import create_local_app

LOCAL_PORT = 3001

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        prog='LocalBackend',
        description='Start the Local P2P Backend (Data Plane)',
        epilog='Local daemon for HybridChat — handles messaging and P2P'
    )
    parser.add_argument('--ip',
        type=str,
        default='0.0.0.0',
        help='IP address to bind. Default is 0.0.0.0'
    )
    parser.add_argument('--port',
        type=int,
        default=LOCAL_PORT,
        help='Port number. Default is {}'.format(LOCAL_PORT)
    )
    parser.add_argument('--central',
        type=str,
        default='http://192.168.1.4:8080',
        help='URL of the Central Backend (via proxy). Default: http://192.168.1.4:8080'
    )

    args = parser.parse_args()
    create_local_app(args.ip, args.port, args.central)
