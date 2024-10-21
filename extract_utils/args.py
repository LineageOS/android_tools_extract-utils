#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import argparse
from contextlib import suppress
from enum import Enum
from typing import Optional

parser = argparse.ArgumentParser(description='Extract utils')

group = parser.add_mutually_exclusive_group()
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
group.add_argument(
    '--extract-factory',
    action='store_true',
    help='extract factory files',
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
    action='store',
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
    '--keep-dump',
    action='store_true',
    help='keep the dump directory',
)

parser.add_argument(
    'source',
    default='adb',
    help='sources from which to extract',
    nargs='?',
)


class ArgsSource(str, Enum):
    ADB = 'adb'


class Args:
    def __init__(self, args: argparse.Namespace):
        # Wrap to provide type hints
        self.only_common: bool = args.only_common
        self.only_target: bool = args.only_target
        self.extract_factory: bool = args.extract_factory
        self.regenerate_makefiles: bool = args.regenerate_makefiles
        self.regenerate: bool = args.regenerate
        self.legacy: bool = args.legacy
        self.keep_dump: bool = args.keep_dump
        self.no_cleanup: bool = args.no_cleanup
        self.kang: bool = args.kang
        self.section: Optional[str] = args.section

        self.source: ArgsSource | str = args.source
        with suppress(ValueError):
            self.source = ArgsSource(args.source)

        if self.section is not None and self.regenerate:
            raise ValueError('Cannot use --section with --regenerate')

        if self.extract_factory and self.source == ArgsSource.ADB:
            raise ValueError('Cannot use --extract-factory with ADB')


def parse_args():
    parser_args = parser.parse_args()
    return Args(parser_args)
