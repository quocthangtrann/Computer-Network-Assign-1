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

# Fixed: Python 2 used `from urlparse import urlparse`; Python 3 moved it to urllib.parse
from urllib.parse import urlparse, unquote

def get_auth_from_url(url):

    # Parse the URL to extract its components
    parsed = urlparse(url)

    try:
        # unquote handles percent-encoded characters in username/password
        auth = (unquote(parsed.username), unquote(parsed.password))
    except (AttributeError, TypeError):
        auth = ("", "")

    return auth