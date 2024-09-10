#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import re

from os import path
from enum import Enum
from typing import Generator, List, Optional, TypeVar

SEPARATORS = ';:|'
SRC_REGEX = re.compile(rf'^([^{SEPARATORS}]+)')
EXTRA_REGEX = re.compile(
    rf'([{SEPARATORS}])([^{SEPARATORS}]+)')

LIB_PARTS = ['lib']
LIB_RFSA_PARTS = ['lib', 'rfsa']
LIB64_PARTS = ['lib64']
BIN_PARTS = ['bin']


class FileArgs(str, Enum):
    MAKE_COPY_RULE = 'MAKE_COPY_RULE'
    MODULE = 'MODULE'
    MODULE_SUFFIX = 'MODULE_SUFFIX'
    DISABLE_CHECKELF = 'DISABLE_CHECKELF'
    DISABLE_DEPS = 'DISABLE_DEPS'
    FIX_SONAME = 'FIX_SONAME'
    FIX_XML = 'FIX_XML'
    PREFER = 'PREFER'
    PRESIGNED = 'PRESIGNED'
    OVERRIDES = 'OVERRIDES'
    REQUIRED = 'REQUIRED'
    SYMLINK = 'SYMLINK'


class File:
    def __init__(self, line: str):
        self.is_package = False

        if line[0] == '-':
            self.is_package = True
            line = line[1:]

        src_regex_result = SRC_REGEX.findall(line)
        if not src_regex_result:
            raise ValueError(f'Failed to find source in {line}')

        self.src = self.dst = src_regex_result[0]
        self.args: dict[str, List[str] | bool] = {}

        self.__parse_extras(line)

        self.parts = self.dst.split('/')

        self.basename = self.parts[-1]
        basename_part_len = len(self.basename) + 1
        self.dirname = self.dst[:-basename_part_len]
        self.root, self.ext = path.splitext(self.basename)

    def starts_with_path_parts(self, path_parts):
        path_parts_len = len(path_parts)
        parts = self.parts

        if parts[:path_parts_len] == path_parts:
            return parts[path_parts_len:]

    def contains_path_parts(self, path_parts):
        path_parts_len = len(path_parts)
        parts = self.parts
        parts_len = len(parts)

        for i in range(parts_len - path_parts_len + 1):
            extracted_parts = parts[i:i + path_parts_len]
            if extracted_parts == path_parts:
                return True

        return False

    def __parse_extras(self, line: str):
        hashes = []

        extras = EXTRA_REGEX.findall(line)
        for prefix, extra in extras:
            if not len(extra):
                raise ValueError(f'Unexpected empty extra in {line}')

            if prefix == ':':
                self.dst = extra
            elif prefix == ';':
                k_v = extra.split('=', 1)
                k = k_v[0]

                if len(k_v) == 1:
                    self.args[k] = True
                    continue

                self.args.setdefault(k, [])
                values = self.args[k]
                if not isinstance(values, list):
                    raise ValueError(f'Unexpected {k} with value in {line}')

                v = k_v[1]
                values.append(v)
            elif prefix == '|':
                hashes.append(extra)
            else:
                raise ValueError(f'Unexpected prefix {prefix} in {line}')

        hashes_len = len(hashes)
        if hashes_len > 2:
            raise ValueError(f'Unexpected {hashes_len} hashes in {line}')

        self.hash = None
        self.fixup_hash = None
        if hashes_len >= 1:
            self.hash = hashes[0]
        if hashes_len == 2:
            self.fixup_hash = hashes[1]

    def __find_split_args(self, arg):
        values = self.args.get(arg)
        if values is None:
            return None

        assert isinstance(values, list)

        flattened_values = []
        for v_unsplit in values:
            v_split = v_unsplit.split(',')
            for v in v_split:
                flattened_values.append(v)

        return flattened_values

    @property
    def symlinks(self):
        args = self.__find_split_args(FileArgs.SYMLINK)
        assert args is not None
        return args

    @property
    def overrides(self):
        return self.__find_split_args(FileArgs.OVERRIDES)

    @property
    def required(self):
        return self.__find_split_args(FileArgs.REQUIRED)

    @property
    def presigned(self):
        return FileArgs.PRESIGNED in self.args

    @property
    def privileged(self):
        privileged = self.contains_path_parts(['priv-app'])
        return True if privileged else None


T = TypeVar('T')

file_tree_dict = dict[str, 'file_tree_dict' | List[File] | None]


class FileTree:
    def __init__(
            self,
            tree: file_tree_dict | None = None,
            parts: Optional[List[str]] = None,
    ):
        if parts is None:
            parts = []
        # Store a recursive dictionary where each key of every dictionary
        # is a part of the path leading to a file, with the final part
        # pointing to a file or list of files
        self._tree: file_tree_dict = {}

        self.parts = parts
        self.parts_prefix_len = sum([len(p) + 1 for p in parts])

        if tree is not None:
            self._tree = tree

    def __len__(self):
        return len(self._tree)

    def add_with_parts(self, file: File, parts: List[str]):
        subtree: file_tree_dict = self._tree
        dir_parts = parts[:-1]
        file_part = parts[-1]

        for part in dir_parts:
            subtree.setdefault(part, {})
            new_subtree = subtree[part]
            assert isinstance(new_subtree, dict)
            subtree = new_subtree

        subtree.setdefault(file_part, [])
        subtree_value = subtree[file_part]
        assert isinstance(subtree_value, list)
        subtree_value.append(file)

    def add(self, file: File):
        return self.add_with_parts(file, file.parts)

    def tree(self):
        return self._tree

    def __files(self, subtree: file_tree_dict) -> Generator[File, None, None]:
        for v in subtree.values():
            if v is None:
                continue

            if isinstance(v, dict):
                yield from self.__files(v)
            elif isinstance(v, list):
                yield v[0]
            else:
                assert False

    def __iter__(self):
        return self.__files(self._tree)

    def __get_prefixed_subtree(self, d: dict, parts: List[str]) -> Optional[dict]:
        for k, v in d.items():
            if parts and k != parts[0]:
                continue

            remaining_parts = parts[1:]
            if not remaining_parts and v is not None:
                d[k] = None
                return v

            if isinstance(v, dict):
                found = self.__get_prefixed_subtree(v, parts[1:])
                if found:
                    return found

        return None

    def filter_prefixed(self, parts: List[str]):
        tree = self.__get_prefixed_subtree(self._tree, parts)
        file_tree = FileTree(tree, parts)
        return file_tree


class CommonFileTree(FileTree):
    # Duplicate these to get a list
    def __files(self, subtree: file_tree_dict) -> Generator[List[File], None, None]:
        for v in subtree.values():
            if v is None:
                continue

            if isinstance(v, dict):
                yield from self.__files(v)
            elif isinstance(v, list):
                yield v
            else:
                assert False

    def __iter__(self):
        return self.__files(self._tree)

    @classmethod
    def __common_files(cls, file_tree: FileTree, parts: List[str],
                       a: file_tree_dict, b: file_tree_dict):
        for k in a:
            if b.get(k) is None:
                continue

            v_a = a[k]
            v_b = b[k]

            if isinstance(v_a, dict) and isinstance(v_b, dict):
                cls.__common_files(file_tree, parts, v_a, v_b)
            elif isinstance(v_a, list) and isinstance(v_b, list):
                a[k] = None
                b[k] = None

                for f in v_a + v_b:
                    remaining_parts = f.parts[len(parts):]
                    file_tree.add_with_parts(f, remaining_parts)

    @classmethod
    def common_files(cls, a: FileTree, b: FileTree) -> 'CommonFileTree':
        # These should be equal in length, and we don't really care about
        # the contents since we're only going to use it to remove this number of
        # parts from the file parts to find the subdir, just keep the first one
        assert len(a.parts) == len(b.parts)
        parts = a.parts
        file_tree = CommonFileTree(parts=parts)
        cls.__common_files(file_tree, parts, a.tree(), b.tree())
        return file_tree


MANIFEST_PARTS = 'etc/vintf/manifest'.split('/')
DEFAULT_PACKAGES_EXT = {
    '.apk': True,
    '.jar': True,
    '.apex': True,
}


class FileList:
    def __init__(
        self,
        file_list_path: str,
        section: Optional[str] = None,
        target_enable_checkelf: bool = False,
        kang: bool = False,
    ):
        self.path = file_list_path

        self.all_files = FileTree()
        self.packages_files = FileTree()
        self.packages_files_symlinks = FileTree()
        self.copy_files = FileTree()

        self.pinned_files = FileTree()

        self.__target_enable_checkelf = target_enable_checkelf
        self.__section = section
        self.__kang = kang

        self.__parse_file_list(file_list_path)

    def __is_file_package(self, file: File):
        if file.contains_path_parts(MANIFEST_PARTS):
            return True

        ext = file.ext
        if DEFAULT_PACKAGES_EXT.get(ext):
            return True

        if not self.__target_enable_checkelf:
            return False

        if ext == '.so':
            if file.contains_path_parts(LIB_PARTS) or \
                    file.contains_path_parts(LIB64_PARTS):
                return True

        if file.contains_path_parts(BIN_PARTS) or \
                file.contains_path_parts(LIB_RFSA_PARTS):
            return True

        return False

    def __parse_file_list(self, file_list_path: str) -> None:
        lines = []

        with open(file_list_path, 'r') as f:
            section = None
            for line in f.readlines():
                line = line.strip()
                if not line:
                    section = None
                    continue

                if line.startswith('#'):
                    section = line.strip('# ').lower()
                    if not line:
                        section = None
                    continue

                if self.__section is None or section == self.__section:
                    lines.append(line)

        lines.sort()

        for line in lines:
            file = File(line)

            if self.__kang:
                file.hash = None
                file.fixup_hash = None

            if FileArgs.SYMLINK in file.args:
                self.packages_files_symlinks.add(file)

            self.all_files.add(file)

            if file.hash is not None:
                self.pinned_files.add(file)

            is_package = self.__is_file_package(file)
            if is_package:
                files_list = self.packages_files

            if not is_package or FileArgs.MAKE_COPY_RULE in file.args:
                files_list = self.copy_files

            # TODO: make sure devices get rid of duplicates
            files_list.add(file)
