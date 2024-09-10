#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from os import path
from typing import Callable, List, Optional

from extract_utils.file import FileList
from extract_utils.fixups import flatten_fixups
from extract_utils.fixups_blob import blob_fixups_user_type
from extract_utils.fixups_lib import lib_fixups_user_type
from extract_utils.extract import extract_fn_type
from extract_utils.tools import android_root


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

        self.is_firmware = isinstance(self, FirmwareProprietaryFile)

    def __fix_file_list(self, file_list: FileList):
        pass


class FirmwareProprietaryFile(ProprietaryFile):
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
        namespace_imports: Optional[List[str]] = None,
        extract_partitions: Optional[List[str]] = None,
        extract_fns: Optional[List[extract_fn_type]] = None,
        check_elf: bool = False,
    ):
        self.device = device
        self.vendor = vendor
        self.proprietary_files: List[ProprietaryFile] = []
        self.extract_partitions: List[str] = []

        self.blob_fixups = flatten_fixups(blob_fixups)
        self.lib_fixups = flatten_fixups(lib_fixups)

        if namespace_imports is None:
            namespace_imports = []
        self.namespace_imports = namespace_imports

        if extract_partitions is None:
            extract_partitions = []
        self.extract_partitions = extract_partitions

        if extract_fns is None:
            extract_fns = []
        self.extract_fns = extract_fns

        self.check_elf = check_elf

        self.device_rel_path = path.join('device', vendor, device)
        self.device_path = path.join(android_root, self.device_rel_path)

        self.vendor_rel_path = path.join('vendor', vendor, device)
        self.vendor_path = path.join(android_root, self.vendor_rel_path)

        self.vendor_prop_rel_sub_path = 'proprietary'
        self.vendor_prop_rel_path = path.join(
            self.vendor_rel_path, self.vendor_prop_rel_sub_path
        )
        self.vendor_prop_path = path.join(
            self.vendor_path, self.vendor_prop_rel_sub_path
        )

        self.vendor_radio_rel_sub_path = 'radio'
        self.vendor_radio_rel_path = path.join(
            self.vendor_rel_path, self.vendor_radio_rel_sub_path
        )
        self.vendor_radio_path = path.join(
            self.vendor_path, self.vendor_radio_rel_sub_path
        )

        self.add_proprietary_file('proprietary-files.txt')

    def add_proprietary_file(self, *args, **kwargs):
        proprietary_file = ProprietaryFile(*args, **kwargs)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_generated_proprietary_file(self, *args, **kwargs):
        proprietary_file = GeneratedProprietaryFile(*args, **kwargs)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_firmware_proprietary_file(self):
        proprietary_file = FirmwareProprietaryFile('proprietary-firmware.txt')
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_generated_carriersettings(self):
        proprietary_file = GeneratedProprietaryFile(
            'proprietary-files-carriersettings.txt',
            'product/etc/CarrierSettings',
            r'\.pb$',
        )
        self.proprietary_files.append(proprietary_file)
        return proprietary_file
