#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import argparse
import os
from typing import List

from extract_utils.args import DOWNLOAD_DIR_ENV_KEY
from extract_utils.extract import ExtractCtx, ExtractFn, extract_fns_type
from extract_utils.extract_misc import ExtractRenameSuperToExtVolumeName
from extract_utils.extract_pixel import (
    copy_pixel_firmware,
    extract_pixel_factory_image,
    extract_pixel_firmware,
    pixel_factory_image_regex,
    pixel_firmware_regex,
)
from extract_utils.extract_star import (
    extract_star_firmware,
    star_firmware_regex,
)
from extract_utils.extract_super_retrofit import ExtractSuperRetrofit
from extract_utils.file import File
from extract_utils.main import create_source
from extract_utils.source import SourceCtx

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
    '--retrofit-super-partitions',
    nargs='*',
    type=str,
    help='Partitions in retrofit super, in order',
)
parser.add_argument(
    '--rename-super-to-volume-name',
    action='store_true',
    help='Rename super_*.img images to their volume name',
)
parser.add_argument(
    '--keep-images',
    action='store_true',
    help='keep the extracted partition images in the dump directory, so it '
    'can be used as the base of a later incremental extraction',
)
parser.add_argument(
    '--firmware',
    help='path to a proprietary-firmware.txt-style file mapping firmware '
    'source files to partition images, so firmware partitions can be updated '
    'by incremental OTAs',
)
parser.add_argument(
    '--download-dir',
    help='path to directory into which to store downloads',
)
parser.add_argument(
    '--download-sha256',
    action='append',
    help='SHA256 of a download, pass once per downloaded source, in order',
)

parser.add_argument(
    'source',
    help='source to extract from; when multiple are given, the first one is '
    'the dump to apply the following incremental OTAs over, in order',
    nargs='+',
)

if __name__ == '__main__':
    args = parser.parse_args()

    if args.pixel_factory is not None and not args.pixel_factory:
        args.pixel_factory = [pixel_factory_image_regex]

    if args.pixel_firmware is not None and not args.pixel_firmware:
        args.pixel_firmware = [pixel_firmware_regex]

    if args.star_firmware is not None and not args.star_firmware:
        args.star_firmware = [star_firmware_regex]

    extract_fns: extract_fns_type = []

    if args.pixel_factory:
        for extract_pattern in args.pixel_factory:
            extract_fns.append(
                ExtractFn(extract_pattern, extract_pixel_factory_image)
            )

    if args.pixel_firmware:
        for extract_pattern in args.pixel_firmware:
            extract_fns.append(
                ExtractFn(
                    extract_pattern,
                    path_fns=[
                        copy_pixel_firmware,
                        extract_pixel_firmware,
                    ],
                )
            )

    if args.star_firmware:
        for extract_pattern in args.star_firmware:
            extract_fns.append(
                ExtractFn(extract_pattern, extract_star_firmware)
            )

    if args.retrofit_super_partitions:
        extract_fns.append(ExtractSuperRetrofit(args.retrofit_super_partitions))

    if args.rename_super_to_volume_name:
        extract_fns.append(ExtractRenameSuperToExtVolumeName())

    download_dir = args.download_dir

    if download_dir is None and DOWNLOAD_DIR_ENV_KEY in os.environ:
        download_dir = os.environ[DOWNLOAD_DIR_ENV_KEY]

    extract_partitions = args.partitions
    if args.extra_partitions is not None:
        extract_partitions += args.extra_partitions

    firmware_files: List[File] = []
    if args.firmware:
        with open(args.firmware, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    firmware_files.append(File(line))

    extract_ctx = ExtractCtx(
        extract_partitions=extract_partitions,
        extract_fns=extract_fns,
        firmware_files=firmware_files,
        keep_images=args.keep_images,
    )

    source_ctx = SourceCtx(
        args.source,
        True,
        download_dir,
        args.download_sha256,
    )

    with create_source(source_ctx, extract_ctx):
        pass
