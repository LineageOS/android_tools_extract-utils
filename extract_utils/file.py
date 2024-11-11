#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import fnmatch
import re
from enum import Enum
from os import path
from typing import (
    Dict,
    Generator,
    Iterable,
    Iterator,
    List,
    Literal,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

from extract_utils.utils import (
    Color,
    color_print,
    is_valid_line,
    split_lines_into_sections,
    uncomment_line,
)

SEPARATORS = ';:|'
SRC_REGEX = re.compile(rf'^([^{SEPARATORS}]+)')
EXTRA_REGEX = re.compile(rf'([{SEPARATORS}])([^{SEPARATORS}]+)')

LIB_PARTS = ['lib']
LIB_RFSA_PARTS = ['lib', 'rfsa']
LIB64_PARTS = ['lib64']
BIN_PARTS = ['bin']


class FileArgs(str, Enum):
    AB = 'AB'
    MAKE_COPY_RULE = 'MAKE_COPY_RULE'
    MAKE_COPY_RULE_ONLY = 'MAKE_COPY_RULE_ONLY'
    MODULE = 'MODULE'
    MODULE_SUFFIX = 'MODULE_SUFFIX'
    DISABLE_CHECKELF = 'DISABLE_CHECKELF'
    DISABLE_DEPS = 'DISABLE_DEPS'
    FIX_SONAME = 'FIX_SONAME'
    FIX_XML = 'FIX_XML'
    OVERRIDES = 'OVERRIDES'
    PRESIGNED = 'PRESIGNED'
    REQUIRED = 'REQUIRED'
    SKIPAPKCHECKS = 'SKIPAPKCHECKS'
    SYMLINK = 'SYMLINK'
    TRYSRCFIRST = 'TRYSRCFIRST'


FILE_ARGS_TYPE_MAP = {
    FileArgs.AB: True,
    FileArgs.MAKE_COPY_RULE: True,
    FileArgs.MAKE_COPY_RULE_ONLY: True,
    FileArgs.MODULE: str,
    FileArgs.MODULE_SUFFIX: str,
    FileArgs.DISABLE_CHECKELF: True,
    FileArgs.DISABLE_DEPS: True,
    FileArgs.FIX_SONAME: True,
    FileArgs.FIX_XML: True,
    FileArgs.OVERRIDES: list,
    FileArgs.PRESIGNED: True,
    FileArgs.REQUIRED: list,
    FileArgs.SKIPAPKCHECKS: True,
    FileArgs.SYMLINK: list,
    FileArgs.TRYSRCFIRST: True,
}

assert len(FileArgs) == len(FILE_ARGS_TYPE_MAP)


class File:
    def __init__(self, line: str):
        self.fixup_hash: Optional[str] = None
        self.hash: Optional[str] = None
        self.is_package = False

        line = line.strip()

        if line[0] == '-':
            self.is_package = True
            line = line[1:]

        src_regex_result = SRC_REGEX.findall(line)
        if not src_regex_result:
            raise ValueError(f'Failed to find source in {line}')

        self.src: str = src_regex_result[0]
        self.dst: str = self.src
        self.has_dst = False
        self.args: Dict[FileArgs, List[str] | str | bool] = {}

        self.__parse_extras(line)

        self.src_parts = self.parts = self.dst.split('/')
        if self.has_dst:
            self.src_parts = self.src.split('/')

        self.partition = self.parts[0]
        self.src_partition = self.src_parts[0]
        self.basename = self.parts[-1]
        basename_part_len = len(self.basename) + 1
        self.dirname = self.dst[:-basename_part_len]
        self.root, self.ext = path.splitext(self.basename)

    def __parse_extras(self, line: str):
        hashes = []

        extras = EXTRA_REGEX.findall(line)
        for prefix, extra in extras:
            assert isinstance(prefix, str)
            assert isinstance(extra, str)

            if not extra:
                raise ValueError(f'Unexpected empty extra in {line}')

            if prefix == ':':
                self.set_dst(extra)
            elif prefix == ';':
                k_v = extra.split('=', 1)
                k = k_v[0]
                v: Literal[True] | str
                if len(k_v) == 1:
                    v = True
                else:
                    v = k_v[1]

                self.set_arg(k, v)
            elif prefix == '|':
                hashes.append(extra)
            else:
                raise ValueError(f'Unexpected prefix {prefix} in {line}')

        hashes_len = len(hashes)
        if hashes_len > 2:
            raise ValueError(f'Unexpected {hashes_len} hashes in {line}')

        file_hash = None
        file_fixup_hash = None
        if hashes_len >= 1:
            file_hash = hashes[0]
        if hashes_len == 2:
            file_fixup_hash = hashes[1]

        self.set_hash(file_hash)
        self.set_fixup_hash(file_fixup_hash)

    def contains_path_parts(self, path_parts):
        path_parts_len = len(path_parts)
        parts = self.parts
        parts_len = len(parts)

        for i in range(parts_len - path_parts_len + 1):
            extracted_parts = parts[i : i + path_parts_len]
            if extracted_parts == path_parts:
                return True

        return False

    def set_arg(
        self,
        k: FileArgs | str,
        v: Literal[True] | str | List[str],
    ) -> File:
        if isinstance(k, str):
            k = FileArgs[k]

        if k not in FILE_ARGS_TYPE_MAP:
            raise ValueError(f'Unexpected argument {k}')

        k_type = FILE_ARGS_TYPE_MAP[k]
        if (
            (k_type is True and v is not True)
            or (k_type is str and not v)
            or (k_type is list and not v)
        ):
            raise ValueError(
                f'Unexpected value {v} for argument {k}, '
                f'expected type is {k_type}'
            )

        if k_type is True:
            self.args[k] = True
        elif k_type is str:
            self.args[k] = v
        elif k_type is list:
            # Handle comma-delimited entries from proprietary-files
            if isinstance(v, str):
                v = v.split(',')
            assert isinstance(v, list)

            self.args.setdefault(k, [])
            values = self.args[k]
            assert isinstance(values, list)
            values.extend(v)
        else:
            assert False

        return self

    def set_dst(self, dst: str | None):
        if dst is None or dst == self.src:
            self.dst = self.src
            self.has_dst = False
        else:
            self.dst = dst
            self.has_dst = True

        return self

    def set_hash(self, file_hash: str | None):
        self.hash = file_hash
        return self

    def set_fixup_hash(self, file_fixup_hash: str | None):
        self.fixup_hash = file_fixup_hash
        return self

    def __str__(self) -> str:
        line = ''
        if self.is_package:
            line += '-'
        line += self.src
        if self.has_dst:
            line += f':{self.dst}'
        for k, v in self.args.items():
            assert isinstance(k, FileArgs)

            line += f';{k.value}'
            if v is True:
                continue

            line += '='

            if isinstance(v, str):
                line += v
            elif isinstance(v, list):
                line += ','.join(v)
            else:
                assert False
        for file_hash in [self.hash, self.fixup_hash]:
            if file_hash is not None:
                line += f'|{file_hash}'
        return line

    @property
    def symlinks(self):
        return self.args.get(FileArgs.SYMLINK)

    @property
    def overrides(self):
        return self.args.get(FileArgs.OVERRIDES)

    @property
    def required(self):
        return self.args.get(FileArgs.REQUIRED)

    @property
    def presigned(self):
        return FileArgs.PRESIGNED in self.args

    @property
    def privileged(self):
        privileged = self.contains_path_parts(['priv-app'])
        return True if privileged else None

    @property
    def skip_preprocessed_apk_checks(self):
        return self.args.get(FileArgs.SKIPAPKCHECKS)


T = TypeVar('T')

file_tree_dict = Dict[str, Union['file_tree_dict', List[File], None]]


class FileTree:
    def __init__(
        self,
        tree: Optional[file_tree_dict] = None,
        parts: Optional[List[str]] = None,
        common=False,
    ):
        if parts is None:
            parts = []
        # Store a recursive dictionary where each key of every dictionary
        # is a part of the path leading to a file, with the final part
        # pointing to a file or list of files
        self.__tree: file_tree_dict = {}
        self.__common = common

        self.parts = parts
        self.parts_prefix_len = sum(len(p) + 1 for p in parts)

        if tree is not None:
            self.__tree = tree

    def __len__(self):
        return len(self.__tree)

    def __iter__(self) -> Iterator[File]:
        files_list = self._files_list(self.__tree)
        return map(self.__map_files_to_file, files_list)

    def __map_files_to_file(self, files: List[File]):
        assert len(files) == 1
        return files[0]

    @property
    def tree(self):
        return self.__tree

    def add_with_parts(self, file: File, parts: List[str]):
        subtree: file_tree_dict = self.__tree
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

        if not self.__common and len(subtree_value):
            raise ValueError(f'{file.dst}: duplicate entry')

        subtree_value.append(file)

    def add(self, file: File):
        return self.add_with_parts(file, file.parts)

    def _files_list(
        self,
        subtree: file_tree_dict,
    ) -> Generator[List[File], None, None]:
        for v in subtree.values():
            if v is None:
                continue

            if isinstance(v, dict):
                yield from self._files_list(v)
            elif isinstance(v, list):
                yield v
            else:
                assert False

    def __get_prefixed(
        self,
        subtree: file_tree_dict,
        parts: List[str],
    ) -> Optional[file_tree_dict | List[File]]:
        for k, v in subtree.items():
            if parts and k != parts[0]:
                continue

            remaining_parts = parts[1:]
            if not remaining_parts and v is not None:
                subtree[k] = None
                return v

            if isinstance(v, dict):
                found = self.__get_prefixed(v, parts[1:])
                if found:
                    return found

        return None

    def filter_prefixed(self, parts: List[str]) -> FileTree:
        tree = self.__get_prefixed(self.__tree, parts)
        assert tree is None or isinstance(tree, dict)
        file_tree = FileTree(tree, parts)
        return file_tree


class CommonFileTree(FileTree):
    def __init__(self, parts: List[str]):
        super().__init__(parts=parts, common=True)

    def common_files_iter(self) -> Iterator[List[File]]:
        return self._files_list(self.tree)

    @classmethod
    def __common_files(
        cls,
        file_tree: FileTree,
        parts: List[str],
        a: file_tree_dict,
        b: file_tree_dict,
    ):
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
                    remaining_parts = f.parts[len(parts) :]
                    file_tree.add_with_parts(f, remaining_parts)
            else:
                assert False

    @classmethod
    def common_files(cls, a: FileTree, b: FileTree) -> CommonFileTree:
        # These should be equal in length, and we don't really care about
        # the contents since we're only going to use it to remove this number of
        # parts from the file parts to find the subdir, just keep the first one
        assert len(a.parts) == len(b.parts)
        parts = a.parts
        file_tree = CommonFileTree(parts=parts)
        cls.__common_files(file_tree, parts, a.tree, b.tree)
        return file_tree


MANIFEST_PARTS = 'etc/vintf/manifest'.split('/')
DEFAULT_PACKAGES_EXT = ('.apk', '.jar', '.apex')


class SimpleFileList:
    def __init__(self):
        self.__files = {}

    def __bool__(self):
        return bool(self.__files)

    def __iter__(self) -> Iterator[File]:
        return iter(self.__files.values())

    def add(self, file: File):
        self.__files[file.dst] = file

    def get_file(self, file_rel_path: str) -> File:
        return self.__files[file_rel_path]


class FileList:
    def __init__(
        self,
        section: Optional[str] = None,
        check_elf=False,
    ):
        # These are filtered by section
        self.files = SimpleFileList()
        self.pinned_files = SimpleFileList()
        self.partitions: set[str] = set()

        # These are not filtered by section since makefile generation
        # cannot be done per-section
        # packages_files is a FileTree to help with performance while grouping
        # multiple files of the same type together
        self.package_files = FileTree()
        self.package_symlinks = SimpleFileList()
        self.copy_files = SimpleFileList()
        self.all_files = SimpleFileList()

        # Combination of normal lines and files, split into sections,
        # used while updating
        self.__lines_or_files: List[File | str] = []

        self.__section = section
        self.__check_elf = check_elf

    def __is_file_package(self, file: File):
        if file.contains_path_parts(MANIFEST_PARTS):
            return True

        ext = file.ext
        if ext in DEFAULT_PACKAGES_EXT:
            return True

        if not self.__check_elf:
            return False

        if ext == '.so':
            if file.contains_path_parts(LIB_PARTS) or file.contains_path_parts(
                LIB64_PARTS
            ):
                return True

        if file.contains_path_parts(BIN_PARTS) or file.contains_path_parts(
            LIB_RFSA_PARTS
        ):
            return True

        return False

    def __add_file(self, file: File, section: str | None):
        if FileArgs.SYMLINK in file.args:
            self.package_symlinks.add(file)

        if self.__section is None or (
            section is not None and fnmatch.fnmatch(section, self.__section)
        ):
            self.files.add(file)
            self.partitions.add(file.partition)
            if file.has_dst:
                self.partitions.add(file.src_partition)

            if file.hash is not None:
                self.pinned_files.add(file)

        if FileArgs.MAKE_COPY_RULE_ONLY in file.args:
            is_package = False
        else:
            is_package = self.__is_file_package(file)

        if is_package or file.is_package:
            if is_package and file.is_package:
                color_print(
                    f'{file.dst}: already a package, no need for -',
                    color=Color.YELLOW,
                )
            self.package_files.add(file)

        if (
            not is_package
            or FileArgs.MAKE_COPY_RULE in file.args
            or FileArgs.MAKE_COPY_RULE_ONLY in file.args
        ):
            self.copy_files.add(file)

        self.all_files.add(file)

    def __add_line(self, line: str):
        if not is_valid_line(line):
            self.__lines_or_files.append(line)
            return None

        # Postpone adding the files to be able to sort them based on dst
        file = File(line)
        self.__lines_or_files.append(file)

        return file

    def add_from_lines(self, file_lines: Iterable[str]):
        sections_lines = split_lines_into_sections(file_lines)

        files: List[Tuple[str | None, File]] = []

        for lines in sections_lines:
            if not lines:
                continue

            section = uncomment_line(lines[0])

            for line in lines:
                file = self.__add_line(line)
                if file is None:
                    continue

                files.append((section, file))

        files.sort(key=lambda f_s: f_s[1].dst)

        for section, file in files:
            self.__add_file(file, section)

    def add_from_file(self, file_path: str):
        with open(file_path, 'r', encoding='utf-8') as f:
            self.add_from_lines(f)

    def write_to_file(self, file_path: str):
        with open(file_path, 'w', encoding='utf-8') as f:
            for line_or_file in self.__lines_or_files:
                f.write(f'{line_or_file}')
                if isinstance(line_or_file, File):
                    f.write('\n')

    def get_file(self, file_dst: str) -> File:
        return self.files.get_file(file_dst)
