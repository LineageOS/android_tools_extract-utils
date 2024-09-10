#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import json

from enum import Enum
from typing import List, Optional

from .bp_encoder import BpJSONEnconder
from .file import File, FileArgs


class Multilib(str, Enum):
    _32 = '32',
    _64 = '64',
    BOTH = 'both',

    @classmethod
    def from_int(cls, value: int) -> 'Multilib':
        if value == 32:
            return Multilib._32
        elif value == 64:
            return Multilib._64

        assert False

    @classmethod
    def from_int_list(cls, value: List[int]) -> 'Multilib':
        value_len = len(value)

        if value_len == 1:
            return Multilib.from_int(value[0])
        elif value_len == 2:
            return Multilib.BOTH

        assert False


class ModuleClass(str, Enum):
    SHARED_LIBRARIES = 'SHARED_LIBRARIES'
    RFSA = 'RFSA'
    APPS = 'APPS'
    APEX = 'APEX'
    ETC = 'ETC'
    ETC_XML = 'ETC_XML'
    JAVA_LIBRARIES = 'JAVA_LIBRARIES'
    EXECUTABLES = 'EXECUTABLES'
    SH_BINARIES = 'SH_BINARIES'
    SYMLINK = 'SYMLINK'


MODULE_CLASS_RULE_NAME_MAP = {
    ModuleClass.SHARED_LIBRARIES: 'cc_prebuilt_library_shared',
    ModuleClass.RFSA: 'prebuilt_rfsa',
    ModuleClass.APPS: 'android_app_import',
    ModuleClass.APEX: 'prebuilt_apex',
    ModuleClass.ETC: 'prebuilt_etc',
    ModuleClass.ETC_XML: 'prebuilt_etc_xml',
    ModuleClass.JAVA_LIBRARIES:  'dex_import',
    ModuleClass.EXECUTABLES: 'cc_prebuilt_binary',
    ModuleClass.SH_BINARIES: 'sh_binary',
    ModuleClass.SYMLINK: 'install_symlink',
}

assert len(ModuleClass) == len(MODULE_CLASS_RULE_NAME_MAP)

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

        self.__cls: Optional[ModuleClass] = None
        self.__rule_name: Optional[str] = None
        self.__files: Optional[List[File]] = None
        self.__stem = None
        self.__package_name = None

        self.o = {}

    def prefix_len(self, prefix_len):
        self.__prefix_len = prefix_len
        return self

    def partition(self, partition):
        self.__partition = partition
        return self

    def get_package_name(self):
        assert self.__package_name is not None
        return self.__package_name

    def get_file(self):
        assert self.__file is not None
        return self.__file

    def get_partition(self):
        assert self.__partition is not None
        return self.__partition

    def get_files(self):
        assert self.__files is not None
        return self.__files

    def stem_package_name(self):
        assert self.__cls is not None
        assert self.__file is not None

        cls = self.__cls
        file = self.__file

        package_name = file.root
        stem = None

        if cls == ModuleClass.EXECUTABLES or cls == ModuleClass.ETC:
            package_name = file.basename

        if cls == ModuleClass.EXECUTABLES or \
                cls == ModuleClass.SHARED_LIBRARIES:
            if FileArgs.MODULE_SUFFIX in file.args:
                stem = package_name
                args = file.args[FileArgs.MODULE_SUFFIX]
                assert isinstance(args, list) and len(args) == 1
                package_name += args[0]
            elif FileArgs.MODULE in file.args:
                stem = package_name
                args = file.args[FileArgs.MODULE]
                assert isinstance(args, list) and len(args) == 1
                package_name = args[0]

        return stem, package_name

    def __set_stem_package_name(self):
        if self.__cls is not None and self.__file is not None:
            self.__stem, self.__package_name = self.stem_package_name()
        return self

    def cls(self, cls: ModuleClass):
        self.__cls = cls
        self.__rule_name = MODULE_CLASS_RULE_NAME_MAP.get(self.__cls)
        return self.__set_stem_package_name()

    def rule_name(self, rule_name: str):
        self.__rule_name = rule_name
        return self

    def files(self, files: List[File]):
        self.__file = files[0]
        self.__files = files
        return self.__set_stem_package_name()

    def file(self, file: File):
        self.__file = file
        self.__files = [file]
        return self.__set_stem_package_name()

    def set(self, k, v, optional=False) -> 'BpBuilder':
        assert v is not None or optional
        if v is not None:
            self.o[k] = v
        return self

    def name(self) -> 'BpBuilder':
        self.set('name', self.__package_name)
        return self

    def raw_name(self, name) -> 'BpBuilder':
        self.__package_name = name
        return self.name()

    def stem(self) -> 'BpBuilder':
        return self.set('stem', self.__stem, optional=True)

    def owner(self, owner: str) -> 'BpBuilder':
        return self.set('owner', owner)

    def __rel_path_file(self, rel_path: str, file: File) -> str:
        return f'{rel_path}/{file.dst}'

    def src(self, rel_path) -> 'BpBuilder':
        assert self.__file is not None

        prop = 'src'
        rel_path = self.__rel_path_file(rel_path, self.__file)
        if self.__cls == ModuleClass.APPS:
            prop = 'apk'
        elif self.__cls == ModuleClass.JAVA_LIBRARIES:
            prop = 'jars'
            rel_path = [rel_path]

        return self.set(prop, rel_path)

    def filename(self) -> 'BpBuilder':
        assert self.__file is not None
        return self.set('filename', self.__file.basename)

    def specific(self) -> 'BpBuilder':
        if self.__partition is None:
            return self

        specific = PARTITION_SPECIFIC_MAP.get(self.__partition)
        if specific is None:
            return self

        return self.set(f'{specific}_specific', True)

    def target(self, rel_path: str, f: File,
               arch: str, deps: Optional[List[str]]) -> 'BpBuilder':
        target = self.o.setdefault('target', {})

        rel_path = self.__rel_path_file(rel_path, f)
        target[arch] = {
            'srcs': [rel_path]
        }

        if deps is not None:
            target[arch]['shared_libs'] = deps

        return self

    def targets(self, rel_path: str, files: List[File],
                arches: List[str], deps: Optional[List[str]]) -> 'BpBuilder':
        for f, arch in zip(files, arches):
            self.target(rel_path, f, arch, deps)
        return self

    def __multilib(self, bits: Multilib) -> 'BpBuilder':
        return self.set('compile_multilib', bits)

    def multilib(self, bits: int) -> 'BpBuilder':
        value = Multilib.from_int(bits)
        return self.__multilib(value)

    def multilibs(self, bitses: List[int]) -> 'BpBuilder':
        value = Multilib.from_int_list(bitses)
        return self.__multilib(value)

    def check_elf(self, enable_checkelf: bool) -> 'BpBuilder':
        if not enable_checkelf:
            self.set('check_elf_files', False)
        return self

    def no_strip(self) -> 'BpBuilder':
        return self.set('strip', {
            'none': True,
        })

    def prefer(self) -> 'BpBuilder':
        return self.set('prefer', True)

    def __file_dir_without_prefix(self) -> Optional[List[str]]:
        assert self.__file is not None
        assert self.__prefix_len is not None

        remaining = self.__file.dirname[self.__prefix_len:]
        if not remaining:
            return None

        return remaining

    def relative_install_path(self) -> 'BpBuilder':
        p = self.__file_dir_without_prefix()
        return self.set('relative_install_path', p, optional=True)

    def sub_dir(self) -> 'BpBuilder':
        p = self.__file_dir_without_prefix()
        return self.set('sub_dir', p)

    def signature(self) -> 'BpBuilder':
        assert self.__file is not None
        if self.__file.presigned:
            self.set('preprocessed', True)
            self.set('presigned', True)
        else:
            self.set('certificate', 'platform')
        return self

    def write(self, out):
        assert self.__rule_name is not None

        out.write(self.__rule_name)
        out.write(' ')
        json.dump(self.o, out, cls=BpJSONEnconder)
        out.write('\n')
        out.write('\n')
