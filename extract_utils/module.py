#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from functools import partial
from os import path
from typing import Callable, List, Optional, Self

from extract_utils.file import FileList
from extract_utils.fixups import flatten_fixups
from extract_utils.fixups_blob import blob_fixups_user_type
from extract_utils.fixups_lib import lib_fixups_user_type
from extract_utils.extract import extract_fn_type
from extract_utils.makefiles import (
    Makefiles,
    MakefilesCtx,
    write_bp_header,
    write_bp_soong_namespaces,
    write_mk_firmware,
    write_mk_firmware_ab_partitions,
    write_mk_guard_begin,
    write_mk_guard_end,
    write_mk_header,
    write_mk_soong_namespace,
    write_product_copy_files,
    write_product_packages,
    write_rro_package,
    write_symlink_packages,
)
from extract_utils.postprocess import (
    postprocess_carriersettings_fn_impl,
    postprocess_fn_type,
)
from extract_utils.source import Source
from extract_utils.tools import android_root
from extract_utils.utils import parse_lines


fix_file_list_fn_type = Callable[[FileList], None]
pre_post_makefile_generation_fn_type = Callable[[Makefiles], None]


class ProprietaryFile:
    def __init__(
        self,
        file_list_name: str,
        fix_file_list: Optional[fix_file_list_fn_type] = None,
    ):
        self.file_list_name = file_list_name
        self.file_list = FileList()

        if fix_file_list is None:
            fix_file_list = self.__fix_file_list
        self.fix_file_list_fn = fix_file_list

        self.pre_makefile_generation_fns: List[
            pre_post_makefile_generation_fn_type
        ] = []
        self.post_makefile_generation_fns: List[
            pre_post_makefile_generation_fn_type
        ] = []

        self.is_firmware = isinstance(self, FirmwareProprietaryFile)

    def __fix_file_list(self, file_list: FileList):
        pass

    def add_pre_post_makefile_generation_fn(
        self,
        pre_fn: pre_post_makefile_generation_fn_type,
        post_fn: pre_post_makefile_generation_fn_type,
    ) -> Self:
        self.pre_makefile_generation_fns.append(pre_fn)
        self.post_makefile_generation_fns.append(post_fn)
        return self

    def add_copy_files_guard(self, name: str, value: str, invert=False) -> Self:
        def guard_begin_fn(makefiles: Makefiles):
            write_mk_guard_begin(
                name, value, makefiles.product_mk_out, invert=invert
            )

        def guard_end_fn(makefiles: Makefiles):
            write_mk_guard_end(name, makefiles.product_mk_out)

        self.add_pre_post_makefile_generation_fn(guard_begin_fn, guard_end_fn)

        return self

    def run_pre_makefile_generation_fns(self, makefiles: Makefiles):
        for fn in self.pre_makefile_generation_fns:
            fn(makefiles)

    def run_post_makefile_generation_fns(self, makefiles: Makefiles):
        for fn in reversed(self.post_makefile_generation_fns):
            fn(makefiles)

    def write_makefiles(
        self,
        module: ExtractUtilsModule,
        makefiles: Makefiles,
    ):
        ctx = MakefilesCtx(
            module.check_elf,
            module.vendor,
            module.vendor_prop_path,
            module.vendor_prop_rel_sub_path,
            module.lib_fixups,
        )

        self.run_pre_makefile_generation_fns(makefiles)

        write_product_copy_files(
            module.vendor_prop_rel_path,
            self.file_list.copy_files,
            makefiles.product_mk_out,
        )

        write_product_packages(
            ctx,
            self.file_list.package_files,
            makefiles.bp_out,
            makefiles.product_mk_out,
        )

        write_symlink_packages(
            self.file_list.package_symlinks,
            makefiles.bp_out,
            makefiles.product_mk_out,
        )

        self.run_post_makefile_generation_fns(makefiles)

    def write_to_file(self, module: ExtractUtilsModule):
        file_list_path = path.join(
            module.device_path,
            self.file_list_name,
        )

        self.file_list.write_to_file(file_list_path)

    def init_file_list(
        self,
        module: ExtractUtilsModule,
        section: Optional[str],
    ):
        self.file_list = FileList(
            section=section,
            check_elf=module.check_elf,
        )

    def parse(
        self,
        module: ExtractUtilsModule,
    ):
        file_list_path = path.join(
            module.device_path,
            self.file_list_name,
        )

        self.file_list.add_from_file(file_list_path)


class FirmwareProprietaryFile(ProprietaryFile):
    def write_makefiles(
        self,
        module: ExtractUtilsModule,
        makefiles: Makefiles,
    ):
        write_mk_firmware_ab_partitions(
            self.file_list.files,
            makefiles.board_config_mk_out,
        )

        write_mk_firmware(
            module.vendor_path,
            module.vendor_radio_rel_sub_path,
            self.file_list.files,
            makefiles.mk_out,
        )


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

    def regenerate(
        self,
        module: ExtractUtilsModule,
        source: Source,
    ):
        skipped_file_rel_paths: List[str] = []
        if self.skip_file_list_name is not None:
            skip_file_list_path = path.join(
                module.device_path,
                self.skip_file_list_name,
            )
            with open(skip_file_list_path, 'r') as f:
                skipped_file_rel_paths = parse_lines(f)

        file_srcs = source.find_sub_dir_files(
            self.partition,
            self.regex,
            skipped_file_rel_paths,
        )

        header_lines = [
            '# All blobs below are extracted from the release '
            'mentioned in proprietary-files.txt\n',
        ]

        self.file_list.add_from_lines(header_lines + file_srcs)
        self.fix_file_list_fn(self.file_list)


class RuntimeResourceOverlay:
    def __init__(
        self,
        package_name: str,
        target_package_name: str,
        partition: str,
    ):
        self.package_name = package_name
        self.target_package_name = target_package_name
        self.partition = partition


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
        check_elf=False,
        skip_main_proprietary_file=False,
    ):
        self.device = device
        self.vendor = vendor
        self.proprietary_files: List[ProprietaryFile] = []
        self.extract_partitions: List[str] = []
        self.rro_packages: List[RuntimeResourceOverlay] = []
        self.postprocess_fns: List[postprocess_fn_type] = []

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

        self.vendor_rro_rel_sub_path = 'rro_overlays'
        self.vendor_rro_rel_path = path.join(
            self.vendor_rel_path, self.vendor_rro_rel_sub_path
        )
        self.vendor_rro_path = path.join(
            self.vendor_path, self.vendor_rro_rel_sub_path
        )

        if not skip_main_proprietary_file:
            self.add_proprietary_file('proprietary-files.txt')

    def proprietary_file_vendor_path(self, proprietary_file: ProprietaryFile):
        vendor_path = self.vendor_prop_path
        if proprietary_file.is_firmware:
            vendor_path = self.vendor_radio_path
        return vendor_path

    def add_postprocess_fn(self, fn: postprocess_fn_type) -> Self:
        self.postprocess_fns.append(fn)
        return self

    def add_rro_package(self, *args, **kwargs):
        rro_package = RuntimeResourceOverlay(*args, *kwargs)
        self.rro_packages.append(rro_package)
        return rro_package

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
        pb_partition = 'product'
        pb_dir_rel_path = f'{pb_partition}/etc/CarrierSettings'
        package_name = 'CarrierConfigOverlay'

        proprietary_file = GeneratedProprietaryFile(
            'proprietary-files-carriersettings.txt',
            pb_dir_rel_path,
            r'\.pb$',
        )
        self.proprietary_files.append(proprietary_file)
        self.add_rro_package(
            package_name,
            'com.android.carrierconfig',
            pb_partition,
        )

        pb_dir_path = f'{self.vendor_prop_path}/{pb_dir_rel_path}'
        rro_xml_dir_path = f'{self.vendor_rro_path}/{package_name}/res/xml'

        postprocess_fn = partial(
            postprocess_carriersettings_fn_impl,
            pb_dir_path,
            rro_xml_dir_path,
        )
        self.add_postprocess_fn(postprocess_fn)
        return proprietary_file

    def write_rro_makefiles(self, makefiles: Makefiles):
        for rro_package in self.rro_packages:
            write_rro_package(
                self.vendor_rro_path,
                rro_package.package_name,
                rro_package.target_package_name,
                rro_package.partition,
                makefiles.product_mk_out,
            )

    def write_makefiles(self):
        bp_path = path.join(self.vendor_path, 'Android.bp')
        mk_path = path.join(self.vendor_path, 'Android.mk')
        product_mk_path = path.join(
            self.vendor_path, f'{self.device}-vendor.mk'
        )
        board_config_mk_path = path.join(
            self.vendor_path, 'BoardConfigVendor.mk'
        )

        with Makefiles.from_paths(
            bp_path,
            mk_path,
            product_mk_path,
            board_config_mk_path,
        ) as makefiles:
            write_bp_header(makefiles.bp_out)
            write_bp_soong_namespaces(self.namespace_imports, makefiles.bp_out)

            write_mk_header(makefiles.product_mk_out)
            write_mk_soong_namespace(
                self.vendor_rel_path, makefiles.product_mk_out
            )

            write_mk_header(makefiles.board_config_mk_out)
            write_mk_header(makefiles.mk_out)

            self.write_rro_makefiles(makefiles)

            for proprietary_file in self.proprietary_files:
                proprietary_file.write_makefiles(self, makefiles)

    def write_updated_proprietary_file(
        self,
        proprietary_file: ProprietaryFile,
        kang: bool,
        regenerate: bool,
    ):
        is_generated = isinstance(proprietary_file, GeneratedProprietaryFile)
        kanged = kang and not is_generated
        generated = regenerate and is_generated

        if not kanged and not generated:
            return

        file_list_rel_path = path.join(
            self.device_rel_path,
            proprietary_file.file_list_name,
        )

        print(f'Updating {file_list_rel_path}')

        proprietary_file.write_to_file(self)

    def write_updated_proprietary_files(self, kang: bool, regenerate: bool):
        for proprietary_file in self.proprietary_files:
            self.write_updated_proprietary_file(
                proprietary_file, kang, regenerate
            )

    def parse_proprietary_files(
        self,
        source: Source,
        regenerate: bool,
        section: Optional[str],
    ):
        for proprietary_file in self.proprietary_files:
            file_list_rel_path = path.join(
                self.device_rel_path,
                proprietary_file.file_list_name,
            )

            proprietary_file.init_file_list(self, section)

            if regenerate and isinstance(
                proprietary_file, GeneratedProprietaryFile
            ):
                print(f'Regenerating {file_list_rel_path}')
                proprietary_file.regenerate(self, source)
            else:
                proprietary_file.parse(self)
