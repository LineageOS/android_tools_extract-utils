# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0

import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, List

from extract_utils.utils import Color, color_print

"""
Prohibited blob policy

This module blocks extraction of prohibited files including:

  - Megvii / Face++ (face recognition, beautification, etc.)
  - SenseTime license files (e.g. license.lic)

These are disallowed due to licensing, redistribution restrictions,
and more importantly DMCA takedown risk.

To extend this policy:
  - Add fnmatch pattern + checker function pairs to PROHIBITED_CHECKS
"""


def _check_sensetime(data: bytes) -> bool:
    return any(x in data for x in [b'com.sensetime', b'SenseTime'])


def _check_megvii(data: bytes) -> bool:
    return any(x in data for x in [b'megface', b'megvii', b'MEGVII'])


# Maps fnmatch pattern (matched against lowercase basename) to a binary
# checker function. The file is only read if the filename matches.
PROHIBITED_CHECKS: List[tuple[str, str, Callable[[bytes], bool]]] = [
    ('*.lic', 'SenseTime', _check_sensetime),
    ('libmegface*', 'Megvii/Face++', _check_megvii),
    ('libmegjpeg*', 'Megvii/Face++', _check_megvii),
    ('libmegskeleton*', 'Megvii/Face++', _check_megvii),
    ('libmegvii*', 'Megvii/Face++', _check_megvii),
    ('libmgbeauty*', 'Megvii/Face++', _check_megvii),
    ('libmgface*', 'Megvii/Face++', _check_megvii),
]


def check_prohibited_file(dst: str, file_path: str):
    basename = Path(dst).name.lower()

    for pattern, label, checker in PROHIBITED_CHECKS:
        if not fnmatch(basename, pattern):
            continue
        try:
            data = open(file_path, 'rb').read(4 * 1024 * 1024)
        except OSError:
            continue
        if not checker(data):
            continue

        color_print(
            f'ERROR: Prohibited file detected: {dst}',
            color=Color.RED,
        )
        color_print(
            f'  Reason: {label} binary signature matched in {Path(dst).name}',
            color=Color.RED,
        )
        print()
        color_print('Policy violation:', color=Color.RED)
        print(
            """The following categories of files are not allowed:

   - Megvii / Face++ related libraries and assets:
    (e.g. lib*{M,m}eg*.so, lib*{M,m}g*.so, *{M,m}egvii*)

   - SenseTime license artifacts:
    (e.g. license.lic)

These files are not permitted in LineageOS repositories/builds.

Please look for available shims, or develop one to mitigate these dependencies.

To extract them anyway for a private/local build, re-run with:

extract-files.py --allow-prohibited-files [...]
"""
        )
        sys.exit(1)
