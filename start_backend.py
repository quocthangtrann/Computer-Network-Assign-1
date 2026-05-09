#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# AsynapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#




import argparse

from apps.sampleapp import create_sampleapp

# Default port number used if none is specified via command-line arguments.
PORT = 9000

if __name__ == "__main__":


    parser = argparse.ArgumentParser(
        prog='Backend',
        description='Start the backend process (Centralized Hub)',
        epilog='Backend daemon for HybridChat application'
    )
    parser.add_argument('--server-ip',
        type=str,
        default='0.0.0.0',
        help='IP address to bind the server. Default is 0.0.0.0'
    )
    parser.add_argument(
        '--server-port',
        type=int,
        default=PORT,
        help='Port number to bind the server. Default is {}.'.format(PORT)
    )

    args = parser.parse_args()
    ip = args.server_ip
    port = args.server_port

    create_sampleapp(ip, port)
