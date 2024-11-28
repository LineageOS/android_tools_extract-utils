#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from enum import Enum
from json import JSONEncoder
from typing import List, Optional

from extract_utils.elf_parser import EM
from extract_utils.file import File

MACHINE_TARGET_MAP = {
    EM.ARM: 'android_arm',
    EM.QDSP6: 'android_arm',
    EM.AARCH64: 'android_arm64',
    EM.X86: 'android_x86',
    EM.X86_64: 'android_x86_64',
}


class Multilib(str, Enum):
    _64 = ('64',)
    _32 = ('32',)
    BOTH = ('both',)

    @classmethod
    def from_int(cls, value: int) -> Multilib:
        if value == 64:
            return Multilib._64

        if value == 32:
            return Multilib._32

        assert False

    @classmethod
    def from_int_list(cls, value: List[int]) -> Multilib:
        value_len = len(value)

        if value_len == 1:
            return Multilib.from_int(value[0])

        if value_len == 2:
            return Multilib.BOTH

        assert False


PARTITION_SPECIFIC_MAP = {
    'vendor': 'soc',
    'product': 'product',
    'system_ext': 'system_ext',
    'odm': 'device',
}


class BpBuilder:
    def __init__(self, encoder: JSONEncoder):
        self.__owner: Optional[str] = None
        self.__partition: Optional[str] = None
        self.__rule_name: Optional[str] = None
        self.__encoder = encoder

        self.o: dict = {}

    def set_owner(self, owner: str):
        self.__owner = owner
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

    def specific(self) -> BpBuilder:
        if self.__partition is None:
            return self

        specific = PARTITION_SPECIFIC_MAP.get(self.__partition)
        if specific is None:
            return self

        return self.set(f'{specific}_specific', True)

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

    def write(self, out):
        assert self.__rule_name is not None

        out.write('\n')
        out.write(self.__rule_name)
        out.write(' ')
        output_str = self.__encoder.encode(self.o)
        out.write(output_str)
        out.write('\n')


class FileBpBuilder(BpBuilder):
    def __init__(
        self,
        file: File,
        prefix_len: int,
        rel_sub_path: str,
        encoder: JSONEncoder,
    ):
        super().__init__(encoder)

        self.__file = file
        self.__prefix_len = prefix_len
        self.__rel_sub_path = rel_sub_path

        self.set_partition(file.partition)

    def __file_dir_without_prefix(self) -> Optional[str]:
        # Remove the length of the file tree prefix from the dirname,
        # including the final slash
        remaining = self.__file.dirname[self.__prefix_len :]
        if not remaining:
            return None

        return remaining

    def relative_install_path(self) -> BpBuilder:
        p = self.__file_dir_without_prefix()
        return self.set('relative_install_path', p, optional=True)

    def sub_dir(self) -> BpBuilder:
        p = self.__file_dir_without_prefix()
        return self.set('sub_dir', p, optional=True)

    def __file_rel_sub_path(self, file_rel_path: str) -> str:
        return f'{self.__rel_sub_path}/{file_rel_path}'

    def src(self) -> BpBuilder:
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('src', rel_path)

    def apk(self) -> BpBuilder:
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('apk', rel_path)

    def jars(self) -> BpBuilder:
        rel_path = self.__file_rel_sub_path(self.__file.dst)
        return self.set('jars', [rel_path])

    def filename(self) -> BpBuilder:
        return self.set('filename', self.__file.basename)

    def signature(self) -> BpBuilder:
        if self.__file.presigned:
            self.set('preprocessed', True)
            self.set('presigned', True)
        elif self.__file.certificate:
            self.set('certificate', self.__file.certificate)
        else:
            self.set('certificate', 'platform')
        return self

    def skip_preprocessed_apk_checks(self) -> BpBuilder:
        return self.set(
            'skip_preprocessed_apk_checks',
            self.__file.skip_preprocessed_apk_checks,
            optional=True,
        )

    def target(
        self, f: File, machine: EM, deps: Optional[List[str]]
    ) -> BpBuilder:
        target = self.o.setdefault('target', {})

        rel_path = self.__file_rel_sub_path(f.dst)
        arch = MACHINE_TARGET_MAP[machine]
        target[arch] = {'srcs': [rel_path]}

        if deps:
            target[arch]['shared_libs'] = deps

        return self

    def targets(
        self,
        files: List[File],
        machines: List[EM],
        depses: List[Optional[List[str]]],
    ) -> BpBuilder:
        for f, machine, deps in zip(files, machines, depses):
            self.target(f, machine, deps)
        return self
