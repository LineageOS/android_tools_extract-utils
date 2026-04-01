# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0

"""
Prohibited blob policy

This module blocks extraction of prohibited files including:

  - Megvii / Face++ (face recognition, beautification, etc.)
  - SenseTime license files (e.g. license.lic)

These are disallowed due to licensing, redistribution restrictions,
and more importantly DMCA takedown risk.

To extend this policy:
  - Add tokens to PROHIBITED_PATTERNS for basename substring matches
  - Add entries to PROHIBITED_FILENAMES for exact filename matches
"""

import re
import sys
from typing import List

from extract_utils.utils import Color, color_print

# Substrings that match known prohibited files
PROHIBITED_PATTERNS = [
    'megvii',
    'megface',
    'megskeleton',
    'mgface',
    'mgbeauty',
    'megjpeg',
    'meg',  # broad catch-all for Megvii
]

# Exact filenames to block
PROHIBITED_FILENAMES = [
    'license.lic',  # SenseTime
]


def _build_regex() -> re.Pattern:
    patterns = '|'.join(re.escape(p) for p in PROHIBITED_PATTERNS)
    filenames = '|'.join(re.escape(f) for f in PROHIBITED_FILENAMES)

    pattern = rf"""
        (^|/)
        (
            [^/]*({patterns})[^/]*
            |
            ({filenames})
        )
        $
    """

    return re.compile(pattern, re.IGNORECASE | re.VERBOSE)


PROHIBITED_RE = _build_regex()


def normalize_prohibited_path(path: str) -> str:
    # Normalize paths with appended SHA1SUM or additional flags
    return path.split('|', 1)[0].strip()


def is_prohibited(path: str) -> bool:
    path = normalize_prohibited_path(path)
    return bool(PROHIBITED_RE.search(path))


def fail_prohibited(paths: List[str]):
    if not paths:
        return

    unique_paths = sorted(set(paths))

    color_print(
        'ERROR: Prohibited blobs detected in proprietary file lists:',
        color=Color.RED,
    )

    for p in unique_paths:
        color_print(f'  - {p}', color=Color.RED)

    print()

    color_print('Policy violation:', color=Color.RED)

    print(
        """The following categories of files are not allowed:

   - Megvii / Face++ related libraries and assets:
    (e.g. lib*{M,m}eg*.so, lib*{M,m}g*.so, *{M,m}egvii*)

   - SenseTime license artifacts:
    (e.g. license.lic)

These files are not permitted in LineageOS repositories/builds.

Please look available shims, or develop one to mitigate these dependencies.
"""
    )

    sys.exit(1)
