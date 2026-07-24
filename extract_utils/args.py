#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import argparse
import os
from contextlib import suppress
from enum import Enum
from typing import List, Optional

parser = argparse.ArgumentParser(description='Extract utils')

group = parser.add_mutually_exclusive_group()
group.add_argument(
    '--extract-all',
    action='store_true',
    help='Extract all files from archive',
)
group.add_argument(
    '--only-name',
    help='only extract module with device name',
)
group.add_argument(
    '--only-common',
    action='store_true',
    help='only extract common module',
)
group.add_argument(
    '--only-target',
    action='store_true',
    help='only extract target module',
)
# TODO: --only-firmware

parser.add_argument(
    '-n',
    '--no-cleanup',
    action='store_true',
    help='do not cleanup vendor',
)
parser.add_argument(
    '-k',
    '--kang',
    action='store_true',
    help='kang and modify hashes',
)
parser.add_argument(
    '-s',
    '--section',
    help='only apply to section name matching pattern',
)
parser.add_argument(
    '-m',
    '--regenerate_makefiles',
    action='store_true',
    help='regenerate makefiles',
)
parser.add_argument(
    '-r',
    '--regenerate',
    action='store_true',
    help='regenerate proprietary files',
)
parser.add_argument(
    '-l',
    '--legacy',
    action='store_true',
    help='generate legacy makefiles',
)
parser.add_argument(
    '--extract-factory',
    action='store_true',
    help='extract factory files',
)
parser.add_argument(
    '--keep-dump',
    action='store_true',
    help='keep the dump directory',
)
parser.add_argument(
    '--keep-images',
    action='store_true',
    help='keep the extracted partition images in the dump directory, so it '
    'can be used as the base of a later incremental extraction',
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
    '--allow-prohibited-files',
    action='store_true',
    help='Allow extraction of normally-prohibited files',
)

parser.add_argument(
    'source',
    default='adb',
    help='source to extract from; when multiple are given, the first one is '
    'the dump to apply the following incremental OTAs over, in order',
    nargs='*',
)


DOWNLOAD_DIR_ENV_KEY = 'EXTRACT_UTILS_DOWNLOAD_DIR'


class ArgsSource(str, Enum):
    ADB = 'adb'


class Args:
    def __init__(self, args: argparse.Namespace):
        # Wrap to provide type hints
        self.only_common: bool = args.only_common
        self.only_target: bool = args.only_target
        self.only_name: str = args.only_name
        self.extract_factory: bool = args.extract_factory
        self.regenerate_makefiles: bool = args.regenerate_makefiles
        self.regenerate: bool = args.regenerate
        self.legacy: bool = args.legacy
        self.keep_images: bool = args.keep_images
        # Keeping the images is pointless if the dump they live in is removed
        self.keep_dump: bool = args.keep_dump or args.keep_images
        self.no_cleanup: bool = args.no_cleanup
        self.kang: bool = args.kang
        self.section: Optional[str] = args.section
        self.download_dir: Optional[str] = args.download_dir
        self.download_sha256: Optional[List[str]] = args.download_sha256
        self.allow_prohibited_files: bool = args.allow_prohibited_files

        if self.download_dir is None and DOWNLOAD_DIR_ENV_KEY in os.environ:
            self.download_dir = os.environ[DOWNLOAD_DIR_ENV_KEY]

        self.source: ArgsSource | str | List[str] = args.source
        if isinstance(self.source, list) and len(self.source) == 1:
            # A single source is not a chain of incrementals
            self.source = self.source[0]
        with suppress(ValueError):
            self.source = ArgsSource(self.source)

        if self.section is not None:
            self.regenerate = False

        if self.regenerate_makefiles:
            self.regenerate = False

        if self.extract_factory and self.source == ArgsSource.ADB:
            raise ValueError('Cannot use --extract-factory with ADB')


def parse_args():
    parser_args = parser.parse_args()
    return Args(parser_args)
