#
# SPDX-FileCopyrightText: 2025 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import re

from extract_utils.tools import aapt2_path
from extract_utils.utils import run_cmd


def get_file_uses_libs_optional_uses_libs(
    file_path: str,
) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    libraries = optional_libraries = []

    lines = run_cmd([aapt2_path, 'dump', 'badging', file_path])
    for line in lines.splitlines():
        match = re.match(r"^(.+?):'(.+?)'$", line.strip())
        if match:
            key, value = match.groups()
            if key == 'uses-library':
                libraries.append(value)
            elif key == 'uses-library-not-required':
                optional_libraries.append(value)

    return libraries or None, optional_libraries or None
