#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import inspect

from os import path
from typing import List, Optional

from .file import FileList
from .tools import get_android_root

from .fixups import \
    flatten_fixups, \
    blob_fixups_user_type, \
    lib_fixups_user_type

ANDROID_ROOT = get_android_root()


class ProprietaryFile:
    def __init__(self, name):
        self.name = name
        self.file_list: Optional[FileList] = None


class GeneratedProprietaryFile(ProprietaryFile):
    def __init__(self, name):
        super().__init__(name)


class ExtractUtilsModule:
    def __init__(self, device, vendor,
                 blob_fixups: Optional[blob_fixups_user_type] = None,
                 lib_fixups: Optional[lib_fixups_user_type] = None,
                 vendor_imports: Optional[List[str]] = None):
        self.device = device
        self.vendor = vendor
        self.vendor_imports = vendor_imports
        self.proprietary_files: List[ProprietaryFile] = []

        self.blob_fixups = flatten_fixups(blob_fixups)
        self.lib_fixups = flatten_fixups(lib_fixups)

        # Automatically compute module path
        calling_module_stack = inspect.stack()[1]
        calling_module_path = path.normpath(calling_module_stack.filename)
        self.dir_path = path.dirname(calling_module_path)

        self.vendor_rel_path = path.join('vendor', vendor, device)
        self.vendor_path = path.join(ANDROID_ROOT, self.vendor_rel_path)
        self.vendor_files_rel_sub_path = 'proprietary'

        self.vendor_files_rel_path = path.join(
            self.vendor_rel_path, self.vendor_files_rel_sub_path)
        self.vendor_files_path = path.join(
            self.vendor_path, self.vendor_files_rel_sub_path)

        self.add_proprietary_file('proprietary-files.txt')

    def add_generated_carriersettings(self):
        name = 'proprietary-files-carriersettings.txt'
        return self.add_generated_proprietary_file(name)

    def add_generated_proprietary_file(self, name):
        proprietary_file = GeneratedProprietaryFile(name)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_proprietary_file(self, name):
        proprietary_file = ProprietaryFile(name)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file
