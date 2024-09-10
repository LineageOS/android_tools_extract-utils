#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import json

from enum import Enum
from typing import List, Optional, Self

from extract_utils.bp_encoder import BpJSONEnconder
from extract_utils.file import File

MACHINE_TARGET_MAP = {
    'EM_ARM': 'android_arm',
    'EM_QDSP6': 'android_arm',
    'EM_AARCH64': 'android_arm64',
    'EM_386': 'android_x86',
    'EM_X86_64': 'android_x86_64',
}


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
        self._partition = None
        self._rule_name: Optional[str] = None

        self.o = {}

    def set_owner(self, owner: str):
        self.__owner = owner
        return self

    def set_partition(self, partition: str):
        self._partition = partition
        return self

    def get_partition(self):
        assert self._partition is not None
        return self._partition

    def set_rule_name(self, rule_name: str):
        self._rule_name = rule_name
        return self

    def set(self, k, v, optional=False) -> Self:
        assert v is not None or optional
        if v is not None:
            self.o[k] = v
        return self

    def name(self, package_name: str) -> Self:
        self.set('name', package_name)
        return self

    def stem(self, stem: Optional[str]) -> Self:
        return self.set('stem', stem, optional=True)

    def owner(self) -> Self:
        return self.set('owner', self.__owner)

    def specific(self) -> Self:
        if self._partition is None:
            return self

        specific = PARTITION_SPECIFIC_MAP.get(self._partition)
        if specific is None:
            return self

        return self.set(f'{specific}_specific', True)

    def __multilib(self, bits: Multilib) -> Self:
        return self.set('compile_multilib', bits)

    def multilib(self, bits: int) -> Self:
        value = Multilib.from_int(bits)
        return self.__multilib(value)

    def multilibs(self, bitses: List[int]) -> Self:
        value = Multilib.from_int_list(bitses)
        return self.__multilib(value)

    def check_elf(self, enable_checkelf: bool) -> Self:
        if not enable_checkelf:
            self.set('check_elf_files', False)
        return self

    def no_strip(self) -> Self:
        return self.set(
            'strip',
            {
                'none': True,
            },
        )

    def prefer(self) -> Self:
        return self.set('prefer', True)

    def write(self, out):
        assert self._rule_name is not None

        out.write('\n')
        out.write(self._rule_name)
        out.write(' ')
        json.dump(self.o, out, cls=BpJSONEnconder)
        out.write('\n')


class FileBpBuilder(BpBuilder):
    def __init__(
        self,
        file: File,
        prefix_len: int,
        rel_sub_path: str,
    ):
        super().__init__()

        self.__file = file
        self.__prefix_len = prefix_len
        self.__rel_sub_path = rel_sub_path

        self.set_partition(file.partition)

    def __file_dir_without_prefix(self) -> Optional[str]:
        # Remove the lenght of the file tree prefix from the dirname,
        # including the final slash
        remaining = self.__file.dirname[self.__prefix_len :]
        if not remaining:
            return None

        return remaining

    def relative_install_path(self) -> Self:
        p = self.__file_dir_without_prefix()
        return self.set('relative_install_path', p, optional=True)

    def sub_dir(self) -> Self:
        p = self.__file_dir_without_prefix()
        return self.set('sub_dir', p)

    def __file_rel_sub_path(self, file_rel_path: str) -> str:
        return f'{self.__rel_sub_path}/{file_rel_path}'

    def src(self) -> Self:
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('src', rel_path)

    def apk(self) -> Self:
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('apk', rel_path)

    def jars(self) -> Self:
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('jars', [rel_path])

    def filename(self) -> Self:
        return self.set('filename', self.__file.basename)

    def signature(self) -> Self:
        if self.__file.presigned:
            self.set('preprocessed', True)
            self.set('presigned', True)
        else:
            self.set('certificate', 'platform')
        return self

    def target(self, f: File, machine: str, deps: Optional[List[str]]) -> Self:
        target = self.o.setdefault('target', {})

        rel_path = self.__file_rel_sub_path(f.dst)
        arch = MACHINE_TARGET_MAP[machine]
        target[arch] = {'srcs': [rel_path]}

        if deps is not None:
            target[arch]['shared_libs'] = deps

        return self

    def targets(
        self,
        files: List[File],
        machines: List[str],
        deps: Optional[List[str]],
    ) -> Self:
        for f, machine in zip(files, machines):
            self.target(f, machine, deps)
        return self
