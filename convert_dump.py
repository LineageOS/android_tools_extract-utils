#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import argparse
import os
import shutil
from os import path

from extract_utils.extract import move_alternate_partition_paths

parser = argparse.ArgumentParser(
    description='Convert extract dump from bash extract_utils'
    'to python extract_utils structure',
)

parser.add_argument(
    'dump_dir',
    help='dump directory',
    nargs='*',
)


def convert_dump(dump_dir: str):
    dump_output_dir = path.join(dump_dir, 'output')

    if path.isdir(dump_output_dir):
        for file in os.scandir(dump_output_dir):
            shutil.move(file.path, dump_dir)

        shutil.rmtree(dump_output_dir)

    move_alternate_partition_paths(dump_dir)


def convert_dumps(dump_dirs: str):
    for dump_dir in dump_dirs:
        convert_dump(dump_dir)


if __name__ == '__main__':
    parser_args = parser.parse_args()

    convert_dumps(parser_args.dump_dir)
