#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import argparse

from extract_utils.extract import (
    ExtractCtx,
    extract_fns_type,
    extract_image,
    filter_already_extracted_partitions,
    get_dump_dir,
)
from extract_utils.extract_pixel import (
    extract_pixel_factory_image,
    extract_pixel_firmware,
    pixel_factory_image_regex,
    pixel_firmware_regex,
)
from extract_utils.extract_star import (
    extract_star_firmware,
    star_firmware_regex,
)

DEFAULT_EXTRACTED_PARTITIONS = [
    'odm',
    'product',
    'system',
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
    '--extra-partitions',
    nargs='+',
    type=str,
    help='Extra partitions to extract',
)
parser.add_argument(
    '--all',
    action='store_true',
    help='Extract all files from archive',
)
parser.add_argument(
    '--pixel-factory',
    nargs='*',
    type=str,
    help='Files to extract as pixel factory image',
)
parser.add_argument(
    '--pixel-firmware',
    nargs='*',
    type=str,
    help='Files to extract as pixel firmware',
)
parser.add_argument(
    '--star-firmware',
    nargs='*',
    type=str,
    help='Files to extract as star firmware',
)

parser.add_argument(
    'source',
    default='adb',
    help='sources from which to extract',
    nargs='?',
)

if __name__ == '__main__':
    args = parser.parse_args()

    if args.pixel_factory is not None and not args.pixel_factory:
        args.pixel_factory = [pixel_factory_image_regex]

    if args.pixel_firmware is not None and not args.pixel_firmware:
        args.pixel_firmware = [pixel_firmware_regex]

    if args.star_firmware is not None and not args.star_firmware:
        args.star_firmware = [star_firmware_regex]

    extract_fns: extract_fns_type = {}

    if args.pixel_factory:
        for extract_pattern in args.pixel_factory:
            extract_fns.setdefault(extract_pattern, []).append(
                extract_pixel_factory_image,
            )

    if args.pixel_firmware:
        for extract_pattern in args.pixel_firmware:
            extract_fns.setdefault(extract_pattern, []).append(
                extract_pixel_firmware,
            )

    if args.star_firmware:
        for extract_pattern in args.star_firmware:
            extract_fns.setdefault(extract_pattern, []).append(
                extract_star_firmware,
            )

    extract_partitions = args.partitions
    if args.extra_partitions is not None:
        extract_partitions += args.extra_partitions

    ctx = ExtractCtx(
        keep_dump=True,
        extract_partitions=extract_partitions,
        extract_fns=extract_fns,
        extract_all=args.all,
    )

    with get_dump_dir(args.source, ctx) as dump_dir:
        filter_already_extracted_partitions(dump_dir, ctx)
        if ctx.extract_partitions:
            extract_image(args.source, ctx, dump_dir)
