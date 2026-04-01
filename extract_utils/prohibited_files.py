# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0

import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import List

from extract_utils.utils import Color, color_print

"""
Prohibited blob policy

This module blocks extraction of prohibited files including:

  - Megvii / Face++ (face recognition, beautification, etc.)
  - SenseTime license files (e.g. license.lic)

These are disallowed due to licensing, redistribution restrictions,
and more importantly DMCA takedown risk.

To extend this policy:
  - Add patterns to PROHIBITED_FNMATCH_PATTERNS for basename fnmatch matching
  - Add entries to PROHIBITED_BINARY_SIGNATURES for binary content matching
"""

# fnmatch patterns matched against the lowercase basename of file.dst
PROHIBITED_FNMATCH_PATTERNS = [
    '*megvii*',
    '*megface*',
    '*megskeleton*',
    '*mgface*',
    '*mgbeauty*',
    '*megjpeg*',
    'st_license.lic',
]

# Binary signatures: (human-readable label, byte sequence).
# Scanned against the first 4 MiB of the extracted file.
PROHIBITED_BINARY_SIGNATURES: List[tuple[str, bytes]] = [
    ('Megvii/Face++', b'megvii'),
    ('Megvii/Face++', b'MEGVII'),
    ('Megvii/Face++', b'megface'),
    ('SenseTime', b'com.sensetime'),
    ('SenseTime', b'SenseTime'),
]


def _filename_is_prohibited(dst: str) -> bool:
    basename = Path(dst).name.lower()
    return any(fnmatch(basename, p) for p in PROHIBITED_FNMATCH_PATTERNS)


def _binary_match(file_path: str) -> str | None:
    """Return label of first matching binary signature, or None if clean."""
    try:
        with open(file_path, 'rb') as f:
            data = f.read(4 * 1024 * 1024)
    except OSError:
        return None
    for label, sig in PROHIBITED_BINARY_SIGNATURES:
        if sig in data:
            return label
    return None


def check_prohibited_file(dst: str, file_path: str):
    """
    Called from process_file after the file has been copied to file_path.
    Exits with a policy violation if the file is prohibited by filename
    or binary content.
    """
    if _filename_is_prohibited(dst):
        reason = f'filename matches prohibited pattern ({Path(dst).name})'
    else:
        label = _binary_match(file_path)
        if label is None:
            return
        reason = f'binary signature matched: {label}'

    color_print(
        f'ERROR: Prohibited file detected: {dst}',
        color=Color.RED,
    )
    color_print(f'  Reason: {reason}', color=Color.RED)
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
