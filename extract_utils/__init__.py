#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

try:
    import elftools
except ImportError:
    sys.exit(
        f"""
pyelftools is not found, please install it by using one of the following commands:
* apk add py3-elftools
* apt install python3-pyelftools
* dnf install python3-pyelftools
* emerge dev-python/pyelftools
* pacman -S python-pyelftools
* zypper install python3-pyelftools
* pip install pyelftools
""".strip()
    )

import sys

sys.dont_write_bytecode = True
