#!/usr/bin/env python3
#
# Copyright (C) 2021-2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import re, sys
from functools import cmp_to_key
from locale import LC_ALL, setlocale, strcoll
from pathlib import Path


def get_source_file_name(line: str) -> str:
    # Remove '-' from strings if there,
    # it is used to indicate a build target
    line = re.sub("^-", "", line)

    # Remove the various additional arguments
    line = re.sub(";.*", "", line)

    # Remove the destination path if there
    line = re.sub(":.*", "", line)

    return line


def strcoll_extract_utils(string1: str, string2: str) -> int:
    # Skip logic if one of the string if empty
    if not string1 or not string2:
        return strcoll(string1, string2)

    # Get the source file name
    string1 = get_source_file_name(string1)
    string2 = get_source_file_name(string2)

    # Compare normally
    return strcoll(string1, string2)


if __name__ == "__main__":
    setlocale(LC_ALL, "C")

    for file in sys.argv[1:] or ["proprietary-files.txt"]:
        if not Path(file).is_file():
            print(f"File {file} not found")
            continue

        with open(file, "r") as f:
            sections = f.read().split("\n\n")

        ordered_sections = []
        for section in sections:
            section_list = [line.strip() for line in section.splitlines()]
            section_list.sort(key=cmp_to_key(strcoll_extract_utils))
            ordered_sections.append("\n".join(section_list))

        with open(file, "w") as f:
            f.write("\n\n".join(ordered_sections).strip() + "\n")
