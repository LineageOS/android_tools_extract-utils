#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import inspect

from os import path
from typing import Callable, List, Optional

from extract_utils.file import FileList
from extract_utils.tools import get_android_root
from extract_utils.fixups import flatten_fixups
from extract_utils.fixups_blob import blob_fixups_user_type
from extract_utils.fixups_lib import lib_fixups_user_type

ANDROID_ROOT = get_android_root()


fix_file_list_fn_type = Callable[[FileList], None]


class ProprietaryFile:
    def __init__(
        self,
        file_list_name: str,
        fix_file_list: Optional[fix_file_list_fn_type] = None,
    ):
        self.file_list_name = file_list_name
        self.file_list: Optional[FileList] = None

        if fix_file_list is None:
            fix_file_list = self.__fix_file_list
        self.fix_file_list_fn = fix_file_list

    def __fix_file_list(self, file_list: FileList):
        pass


class GeneratedProprietaryFile(ProprietaryFile):
    def __init__(
        self,
        file_list_name: str,
        partition: str,
        regex: str,
        skip_file_list_name: Optional[str] = None,
        fix_file_list_fn: Optional[fix_file_list_fn_type] = None,
    ):
        super().__init__(file_list_name, fix_file_list_fn)

        self.partition = partition
        self.regex = regex
        self.skip_file_list_name = skip_file_list_name


class ExtractUtilsModule:
    def __init__(
        self,
        device,
        vendor,
        blob_fixups: Optional[blob_fixups_user_type] = None,
        lib_fixups: Optional[lib_fixups_user_type] = None,
        vendor_imports: Optional[List[str]] = None,
        check_elf: bool = False,
    ):
        self.device = device
        self.vendor = vendor
        self.proprietary_files: List[ProprietaryFile] = []

        self.blob_fixups = flatten_fixups(blob_fixups)
        self.lib_fixups = flatten_fixups(lib_fixups)
        self.vendor_imports = vendor_imports
        self.check_elf = check_elf

        # Automatically compute module path
        calling_module_stack = inspect.stack()[1]
        calling_module_path = path.normpath(calling_module_stack.filename)
        self.dir_path = path.dirname(calling_module_path)

        self.device_rel_path = path.join('device', vendor, device)
        self.vendor_rel_path = path.join('vendor', vendor, device)
        self.vendor_path = path.join(ANDROID_ROOT, self.vendor_rel_path)
        self.vendor_files_rel_sub_path = 'proprietary'

        self.vendor_files_rel_path = path.join(
            self.vendor_rel_path, self.vendor_files_rel_sub_path
        )
        self.vendor_files_path = path.join(
            self.vendor_path, self.vendor_files_rel_sub_path
        )

        self.add_proprietary_file('proprietary-files.txt')

    def add_generated_carriersettings(self):
        proprietary_file = GeneratedProprietaryFile(
            'proprietary-files-carriersettings.txt',
            'product/etc/CarrierSettings/',
            r'\.pb$',
        )
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_generated_proprietary_file(self, *args, **kwargs):
        proprietary_file = GeneratedProprietaryFile(*args, **kwargs)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_proprietary_file(self, *args, **kwargs):
        proprietary_file = ProprietaryFile(*args, **kwargs)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file
