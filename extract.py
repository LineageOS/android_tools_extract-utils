#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import argparse

from extract_utils.extract import (
    ExtractCtx,
    extract_image,
    filter_already_extracted_partitions,
    get_dump_dir,
)

DEFAULT_EXTRACTED_PARTITIONS = [
    'system',
    'odm',
    'product',
    'system_ext',
    'vendor',
]

parser = argparse.ArgumentParser(description='Extract')

parser.add_argument(
    '--partitions',
    nargs='+',
    type=str,
    help='Partitions to extract',
    default=DEFAULT_EXTRACTED_PARTITIONS,
)
parser.add_argument(
    '--all',
    action='store_true',
    help='Extract all files from archive',
)

parser.add_argument(
    'source',
    default='adb',
    help='sources from which to extract',
    nargs='?',
)

if __name__ == '__main__':
    args = parser.parse_args()

    ctx = ExtractCtx(
        keep_dump=True,
        extract_partitions=args.partitions,
        extract_all=args.all,
    )

    with get_dump_dir(args.source, ctx) as dump_dir:
        filter_already_extracted_partitions(dump_dir, ctx)
        if ctx.extract_partitions:
            extract_image(args.source, ctx, dump_dir)
