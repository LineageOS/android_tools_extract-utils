#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import re
from os import path

from enum import Enum

from env import ANDROID_ROOT, ENABLE_CHECKELF, OUTDIR

SEPARATORS = ';:|'
SRC_REGEX = re.compile(rf'^([^{SEPARATORS}]+)')
EXTRA_REGEX = re.compile(
    rf'([{SEPARATORS}])([^{SEPARATORS}]+)')


class ModuleClass(str, Enum):
    SHARED_LIBRARIES = 'SHARED_LIBRARIES'
    RFSA = 'RFSA'
    APPS = 'APPS'
    APEX = 'APEX'
    ETC = 'ETC'
    JAVA_LIBRARIES = 'JAVA_LIBRARIES'
    EXECUTABLES = 'EXECUTABLES'
    SYMLINK = 'SYMLINK'


class FileArgs(str, Enum):
    MODULE = 'MODULE'
    MODULE_SUFFIX = 'MODULE_SUFFIX'
    DISABLE_CHECKELF = 'DISABLE_CHECKELF'
    DISABLE_DEPS = 'DISABLE_DEPS'
    PRESIGNED = 'PRESIGNED'
    OVERRIDES = 'OVERRIDES'
    REQUIRED = 'REQUIRED'
    SYMLINK = 'SYMLINK'


def startswith_or_contains_path(s, p):
    return s.startswith(p) or f'/{p}' in s


def is_path_package(p, ext):
    if startswith_or_contains_path(p, 'etc/vintf/manifest/'):
        return True

    if ext in ['.apk', '.jar', '.apex']:
        return True

    if not ENABLE_CHECKELF:
        return False

    if ext == '.so':
        if startswith_or_contains_path(p, 'lib/') or \
                startswith_or_contains_path(p, 'lib64/'):
            return True

    if startswith_or_contains_path(p, 'bin/') or \
            startswith_or_contains_path(p, 'lib/rfsa/'):
        return True

    return False


class File:
    def __init__(self, s):
        self.is_package = False

        if s[0] == '-':
            self.is_package = True
            s = s[1:]

        src_regex_result = SRC_REGEX.findall(s)
        if not src_regex_result:
            raise ValueError(f'Failed to find source in {s}')

        self.src = self.dst = src_regex_result[0]
        self.args = {}

        hashes = []
        extras = EXTRA_REGEX.findall(s)
        for prefix, extra in extras:
            if not len(extra):
                raise ValueError(f'Unexpected empty extra in {s}')

            if prefix == ':':
                self.dst = extra
            elif prefix == ';':
                k_v = extra.split('=', 1)
                k = k_v[0]
                if len(k_v) == 2:
                    v = k_v[1]
                    values = self.args.setdefault(k, [])
                    values.append(v)
                else:
                    self.args[k] = True
            elif prefix == '|':
                hashes.append(extra)
            else:
                raise ValueError(f'Unexpected prefix {prefix} in {s}')

        hashes_len = len(hashes)
        if hashes_len > 2:
            raise ValueError(f'Unexpected {hashes_len} hashes in {s}')

        self.hash = None
        self.fixup_hash = None
        if hashes_len >= 1:
            self.hash = hashes[0]
        if hashes_len == 2:
            self.fixup_hash = hashes[1]

        # TODO: lazy
        self.rel_path = 'proprietary/' + self.dst
        self.root_path = OUTDIR + '/' + self.rel_path
        self.path = ANDROID_ROOT + '/' + self.root_path
        self.dirname, self.basename = path.split(self.dst)
        self.root, self.ext = path.splitext(self.basename)

        if is_path_package(self.dst, self.ext):
            # TODO: remove if deemed too much
            if self.is_package:
                print(f'File {self.dst} is already a package, no need for -')
            self.is_package = True

        self.gen_deps = False
        self.enable_checkelf = False

        if ENABLE_CHECKELF:
            self.gen_deps = True
            self.enable_checkelf = True

        if FileArgs.DISABLE_CHECKELF in self.args:
            self.enable_checkelf = False

        if FileArgs.DISABLE_DEPS in self.args:
            self.gen_deps = False
            self.enable_checkelf = False

    def __hash__(self):
        return hash(self.dst)

    def __eq__(self, other):
        assert isinstance(other, self.__class__)
        return hash(self) == hash(other)

    def remove_prefix(self, prefix):
        without_prefix = self.dst.removeprefix(prefix)
        if len(self.dst) == len(without_prefix):
            return None

        self.dirname_without_prefix = path.dirname(without_prefix)

        return without_prefix

    def set_cls(self, cls):
        self.cls = cls
        self.package_name = self.root
        self.stem = None

        if (self.cls == ModuleClass.EXECUTABLES and self.ext != '.sh') \
                or (self.cls == ModuleClass.ETC and self.ext != '.xml'):
            self.package_name = self.basename

        if self.cls == ModuleClass.EXECUTABLES or \
                self.cls == ModuleClass.SHARED_LIBRARIES:
            if FileArgs.MODULE_SUFFIX in self.args:
                self.stem = self.package_name
                self.package_name += self.args[FileArgs.MODULE_SUFFIX][0]
            elif FileArgs.MODULE in self.args:
                self.stem = self.package_name
                self.package_name = self.args[FileArgs.MODULE][0]

    def set_part(self, part):
        self.part = part

    def find_split_args(self, arg):
        values = self.args.get(arg)
        if not values:
            return None

        flattened_values = []
        for v_unsplit in values:
            v_split = v_unsplit.split(',')
            for v in v_split:
                flattened_values.append(v)

        return flattened_values

    def symlinks(self):
        return self.find_split_args(FileArgs.SYMLINK)

    def overrides(self):
        return self.find_split_args(FileArgs.OVERRIDES)

    def required(self):
        return self.find_split_args(FileArgs.REQUIRED)

    def presigned(self):
        return FileArgs.PRESIGNED in self.args

    def privileged(self):
        return startswith_or_contains_path(self.dst, 'priv-app/')


def parse_line(line):
    line = line.strip()
    if not line:
        return None

    if line.startswith('#'):
        return None

    return File(line)


def parse_file_list(file_list_path, packages_files=None,
                    packages_symlinks=None, copy_files=None):
    with open(file_list_path, 'r') as f:
        lines = f.readlines()

    lines.sort()

    for line in lines:
        file = parse_line(line)
        if file is None:
            continue

        if FileArgs.SYMLINK in file.args:
            packages_symlinks.append(file)

        if file.is_package and packages_files is not None:
            files_list = packages_files
        else:
            files_list = copy_files

        if files_list is None:
            continue

        # TODO: make sure devices get rid of duplicates
        files_list.append(file)
