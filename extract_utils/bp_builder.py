#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import json

from enum import Enum
from typing import List, Optional

from extract_utils.bp_encoder import BpJSONEnconder
from extract_utils.file import File, FileArgs


class Multilib(str, Enum):
    _32 = ('32',)
    _64 = ('64',)
    BOTH = ('both',)

    @classmethod
    def from_int(cls, value: int) -> Multilib:
        if value == 32:
            return Multilib._32
        elif value == 64:
            return Multilib._64

        assert False

    @classmethod
    def from_int_list(cls, value: List[int]) -> Multilib:
        value_len = len(value)

        if value_len == 1:
            return Multilib.from_int(value[0])
        elif value_len == 2:
            return Multilib.BOTH

        assert False


PARTITION_SPECIFIC_MAP = {
    'vendor': 'soc',
    'product': 'product',
    'system_ext': 'system_ext',
    'odm': 'device',
}


class BpBuilder:
    def __init__(self):
        self.__partition = None
        self.__prefix_len = None
        self.__file: Optional[File] = None
        self.__rule_name: Optional[str] = None

        self.o = {}

    def set_file(self, file: File):
        self.__file = file
        return self

    def set_owner(self, owner: str):
        self.__owner = owner
        return self

    def set_rel_sub_path(self, rel_sub_path: str):
        self.__rel_sub_path = rel_sub_path
        return self

    def set_prefix_len(self, prefix_len: int):
        self.__prefix_len = prefix_len
        return self

    def set_partition(self, partition: str):
        self.__partition = partition
        return self

    def get_partition(self):
        assert self.__partition is not None
        return self.__partition

    def set_rule_name(self, rule_name: str):
        self.__rule_name = rule_name
        return self

    def set(self, k, v, optional=False) -> BpBuilder:
        assert v is not None or optional
        if v is not None:
            self.o[k] = v
        return self

    def name(self, package_name: str) -> BpBuilder:
        self.set('name', package_name)
        return self

    def stem(self, stem: Optional[str]) -> BpBuilder:
        return self.set('stem', stem, optional=True)

    def owner(self) -> BpBuilder:
        return self.set('owner', self.__owner)

    def __file_rel_sub_path(self, file_rel_path: str) -> str:
        return f'{self.__rel_sub_path}/{file_rel_path}'

    def __file_dir_without_prefix(self) -> Optional[str]:
        assert self.__file is not None
        assert self.__prefix_len is not None

        remaining = self.__file.dirname[self.__prefix_len :]
        if not remaining:
            return None

        return remaining

    def src(self) -> BpBuilder:
        assert self.__file is not None
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('src', rel_path)

    def apk(self) -> BpBuilder:
        assert self.__file is not None
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('apk', rel_path)

    def jars(self) -> BpBuilder:
        assert self.__file is not None
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('jars', [rel_path])

    def filename(self) -> BpBuilder:
        assert self.__file is not None
        return self.set('filename', self.__file.basename)

    def specific(self) -> BpBuilder:
        if self.__partition is None:
            return self

        specific = PARTITION_SPECIFIC_MAP.get(self.__partition)
        if specific is None:
            return self

        return self.set(f'{specific}_specific', True)

    def target(
        self, f: File, arch: str, deps: Optional[List[str]]
    ) -> BpBuilder:
        target = self.o.setdefault('target', {})

        rel_path = self.__file_rel_sub_path(f.dst)
        target[arch] = {'srcs': [rel_path]}

        if deps is not None:
            target[arch]['shared_libs'] = deps

        return self

    def targets(
        self,
        files: List[File],
        arches: List[str],
        deps: Optional[List[str]],
    ) -> BpBuilder:
        for f, arch in zip(files, arches):
            self.target(f, arch, deps)
        return self

    def __multilib(self, bits: Multilib) -> BpBuilder:
        return self.set('compile_multilib', bits)

    def multilib(self, bits: int) -> BpBuilder:
        value = Multilib.from_int(bits)
        return self.__multilib(value)

    def multilibs(self, bitses: List[int]) -> BpBuilder:
        value = Multilib.from_int_list(bitses)
        return self.__multilib(value)

    def check_elf(self, enable_checkelf: bool) -> BpBuilder:
        if not enable_checkelf:
            self.set('check_elf_files', False)
        return self

    def no_strip(self) -> BpBuilder:
        return self.set(
            'strip',
            {
                'none': True,
            },
        )

    def prefer(self) -> BpBuilder:
        return self.set('prefer', True)

    def relative_install_path(self) -> BpBuilder:
        p = self.__file_dir_without_prefix()
        return self.set('relative_install_path', p, optional=True)

    def sub_dir(self) -> BpBuilder:
        p = self.__file_dir_without_prefix()
        return self.set('sub_dir', p)

    def signature(self) -> BpBuilder:
        assert self.__file is not None
        if self.__file.presigned:
            self.set('preprocessed', True)
            self.set('presigned', True)
        else:
            self.set('certificate', 'platform')
        return self

    def write(self, out):
        assert self.__rule_name is not None

        out.write('\n')
        out.write(self.__rule_name)
        out.write(' ')
        json.dump(self.o, out, cls=BpJSONEnconder)
        out.write('\n')
