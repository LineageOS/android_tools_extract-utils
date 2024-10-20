#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import argparse

from extract_utils.extract import convert_dump

parser = argparse.ArgumentParser(
    description='Convert extract dump from bash extract_utils'
    'to python extract_utils structure',
)

parser.add_argument(
    'dump_dir',
    help='dump directory',
    nargs='*',
)


def convert_dumps(dump_dirs: str):
    for dump_dir in dump_dirs:
        convert_dump(dump_dir)


if __name__ == '__main__':
    parser_args = parser.parse_args()

    convert_dumps(parser_args.dump_dir)
