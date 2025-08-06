#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2021-2025 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import re
from argparse import ArgumentParser
from functools import cmp_to_key
from itertools import groupby
from locale import LC_ALL, setlocale, strcoll
from pathlib import Path


def is_blob(line: str) -> bool:
    line = line.strip()
    return line and not line.startswith('#')


def get_source_file_name(line: str) -> str:
    # Remove '-' from strings if there,
    # it is used to indicate a build target
    line = re.sub('^-', '', line)

    # Remove the various additional arguments
    line = re.sub(';.*', '', line)

    # Remove the destination path if there
    line = re.sub(':.*', '', line)

    return line


def strcoll_extract_utils(
    string1: str, string2: str, dir_first: bool = False
) -> int:
    # Skip logic if one of the string if empty
    if not string1 or not string2:
        return strcoll(string1, string2)

    # Get the source file name
    string1 = get_source_file_name(string1)
    string2 = get_source_file_name(string2)

    if dir_first:
        # If no directories, compare normally
        if '/' not in string1 and '/' not in string2:
            return strcoll(string1, string2)

        string1_dir = string1.rsplit('/', 1)[0] + '/'
        string2_dir = string2.rsplit('/', 1)[0] + '/'
        if string1_dir == string2_dir:
            # Same directory, compare normally
            return strcoll(string1, string2)

        if string1_dir.startswith(string2_dir):
            # First string dir is a subdirectory of the second one,
            # return string1 > string2
            return -1

        if string2_dir.startswith(string1_dir):
            # Second string dir is a subdirectory of the first one,
            # return string2 > string1
            return 1

    # Compare normally
    return strcoll(string1, string2)


if __name__ == '__main__':
    setlocale(LC_ALL, 'C')

    parser = ArgumentParser(description='Sort blobs list')
    parser.add_argument(
        'files',
        nargs='*',
        default=['proprietary-files.txt'],
        help='Files to sort',
    )
    parser.add_argument(
        '--dir-first',
        action='store_true',
        help='Sort directories first',
    )
    args = parser.parse_args()

    sort_key = cmp_to_key(
        lambda x, y: strcoll_extract_utils(x, y, args.dir_first)
    )

    for file in args.files:
        if not Path(file).is_file():
            print(f'File {file} not found')
            continue

        with open(file, 'r', encoding='utf-8') as f:
            sections = groupby(f.readlines(), is_blob)

        ordered_sections = []
        for sort, section in sections:
            if sort:
                ordered_sections.append(''.join(sorted(section, key=sort_key)))
            else:
                ordered_sections.append(''.join(section))

        with open(file, 'w', encoding='utf-8') as f:
            f.write(''.join(ordered_sections))
