#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import tempfile
from enum import Enum
from functools import partial
from os import path
from typing import Callable, List, Optional, Self, Set

from extract_utils.extract import extract_fns_user_type
from extract_utils.file import File, FileArgs, FileList
from extract_utils.fixups import flatten_fixups
from extract_utils.fixups_blob import (
    BlobFixupCtx,
    blob_fixup,
    blob_fixups_user_type,
)
from extract_utils.fixups_lib import lib_fixups_user_type
from extract_utils.makefiles import (
    MakefilesCtx,
    ProductPackagesCtx,
    write_board_info_file,
    write_bp_header,
    write_bp_soong_namespaces,
    write_mk_firmware,
    write_mk_firmware_ab_partitions,
    write_mk_firmware_file,
    write_mk_guard_begin,
    write_mk_guard_end,
    write_mk_header,
    write_mk_local_path,
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
from extract_utils.source import DiskSource, Source
from extract_utils.tools import android_root
from extract_utils.utils import (
    Color,
    color_print,
    file_path_sha1,
    parse_lines,
    remove_dir_contents,
)


class PinnedFileProcessResult(Enum):
    MATCH = 0
    MISMATCH = 1
    BAD_FIXUP = 2


class ProprietaryFileType(Enum):
    BLOBS = 0
    FIRMWARE = 1
    FACTORY = 2


fix_file_list_fn_type = Callable[[FileList], None]
pre_post_makefile_generation_fn_type = Callable[[MakefilesCtx], None]


class ProprietaryFile:
    def __init__(
        self,
        file_list_path: str,
        vendor_rel_sub_path: str = 'proprietary',
        fix_file_list: Optional[fix_file_list_fn_type] = None,
        type=ProprietaryFileType.BLOBS,
    ):
        self.file_list_path = file_list_path
        self.root_path = path.relpath(self.file_list_path, android_root)
        self.vendor_rel_sub_path = vendor_rel_sub_path
        self.file_list = FileList()

        self.__fix_file_list = fix_file_list

        self.pre_makefile_generation_fns: List[
            pre_post_makefile_generation_fn_type
        ] = []
        self.post_makefile_generation_fns: List[
            pre_post_makefile_generation_fn_type
        ] = []

        self.type = type

    def fix_file_list(self, file_list: FileList):
        if self.__fix_file_list is not None:
            self.__fix_file_list(file_list)

    def add_pre_post_makefile_generation_fn(
        self,
        pre_fn: pre_post_makefile_generation_fn_type,
        post_fn: pre_post_makefile_generation_fn_type,
    ) -> Self:
        self.pre_makefile_generation_fns.append(pre_fn)
        self.post_makefile_generation_fns.append(post_fn)
        return self

    def add_copy_files_guard(self, name: str, value: str, invert=False) -> Self:
        def guard_begin_fn(ctx: MakefilesCtx):
            write_mk_guard_begin(name, value, ctx.product_mk_out, invert=invert)

        def guard_end_fn(ctx: MakefilesCtx):
            write_mk_guard_end(ctx.product_mk_out)

        self.add_pre_post_makefile_generation_fn(guard_begin_fn, guard_end_fn)

        return self

    def run_pre_makefile_generation_fns(self, ctx: MakefilesCtx):
        for fn in self.pre_makefile_generation_fns:
            fn(ctx)

    def run_post_makefile_generation_fns(self, ctx: MakefilesCtx):
        for fn in reversed(self.post_makefile_generation_fns):
            fn(ctx)

    def write_makefiles(self, module: ExtractUtilsModule, ctx: MakefilesCtx):
        vendor_path = path.join(
            module.vendor_path,
            self.vendor_rel_sub_path,
        )
        vendor_rel_path = path.join(
            module.vendor_rel_path,
            self.vendor_rel_sub_path,
        )

        packages_ctx = ProductPackagesCtx(
            module.check_elf,
            module.vendor,
            vendor_path,
            self.vendor_rel_sub_path,
            module.lib_fixups,
        )

        self.run_pre_makefile_generation_fns(ctx)

        write_product_copy_files(
            vendor_rel_path,
            self.file_list.copy_files,
            ctx.product_mk_out,
        )

        write_product_packages(
            ctx,
            packages_ctx,
            self.file_list.package_files,
        )

        write_symlink_packages(
            ctx,
            self.file_list.package_symlinks,
        )

        self.run_post_makefile_generation_fns(ctx)

    def write_to_file(self):
        self.file_list.write_to_file(self.file_list_path)

    def init_file_list(
        self,
        module: ExtractUtilsModule,
        section: Optional[str],
    ):
        self.file_list = FileList(
            section=section,
            check_elf=module.check_elf,
        )

    def parse(self):
        self.file_list.add_from_file(self.file_list_path)

    def get_files(self) -> Set[str]:
        files = set()

        for file in self.file_list.files:
            files.add(file.src)
            if file.has_dst:
                files.add(file.dst)

        return files

    def get_partitions(self) -> Set[str]:
        return self.file_list.partitions


class FirmwareProprietaryFile(ProprietaryFile):
    def __init__(
        self,
        file_list_path: str,
        vendor_rel_sub_path: str = 'radio',
        fix_file_list: Optional[fix_file_list_fn_type] = None,
        type=ProprietaryFileType.FIRMWARE,
    ):
        super().__init__(
            file_list_path,
            vendor_rel_sub_path=vendor_rel_sub_path,
            fix_file_list=fix_file_list,
            type=type,
        )

    def write_makefiles(self, module: ExtractUtilsModule, ctx: MakefilesCtx):
        write_mk_firmware_ab_partitions(
            self.file_list.all_files,
            ctx.board_config_mk_out,
        )

        write_mk_guard_begin('TARGET_DEVICE', module.device, ctx.mk_out)

        write_mk_firmware(
            module.vendor_path,
            self.vendor_rel_sub_path,
            self.file_list.all_files,
            ctx.mk_out,
        )

        write_mk_guard_end(ctx.mk_out)

    def get_partitions(self) -> Set[str]:
        files = set()

        for file in self.file_list.files:
            partition, _ = path.splitext(file.dst)
            files.add(partition)

        return files


class FactoryProprietaryFile(ProprietaryFile):
    def __init__(
        self,
        file_list_path: str,
        vendor_rel_sub_path: str = 'factory',
        fix_file_list: Optional[fix_file_list_fn_type] = None,
        type=ProprietaryFileType.FACTORY,
    ):
        super().__init__(
            file_list_path,
            vendor_rel_sub_path=vendor_rel_sub_path,
            fix_file_list=fix_file_list,
            type=type,
        )

    def write_makefiles(self, module: ExtractUtilsModule, ctx: MakefilesCtx):
        write_mk_guard_begin('TARGET_DEVICE', module.device, ctx.mk_out)

        for file in self.file_list.files:
            if file.basename == 'android-info.txt':
                write_board_info_file(
                    module.vendor_rel_path,
                    self.vendor_rel_sub_path,
                    file,
                    ctx.board_config_mk_out,
                )
                continue

            write_mk_firmware_file(
                module.vendor_path,
                self.vendor_rel_sub_path,
                file,
                ctx.mk_out,
            )

        ctx.mk_out.write('\n')

        write_mk_guard_end(ctx.mk_out)


class GeneratedProprietaryFile(ProprietaryFile):
    def __init__(
        self,
        file_list_name: str,
        partition: str,
        rel_path: Optional[str] = None,
        regex: Optional[str] = None,
        skip_file_list_name: Optional[str] = None,
        vendor_rel_sub_path: str = 'proprietary',
        fix_file_list: Optional[fix_file_list_fn_type] = None,
        type=ProprietaryFileType.BLOBS,
    ):
        super().__init__(
            file_list_name,
            vendor_rel_sub_path=vendor_rel_sub_path,
            fix_file_list=fix_file_list,
            type=type,
        )

        self.partition = partition
        self.rel_path = rel_path
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

        partition_rel_path = self.partition
        if self.rel_path is not None:
            partition_rel_path = path.join(partition_rel_path, self.rel_path)

        file_srcs = source.find_sub_dir_files(
            partition_rel_path,
            self.regex,
            skipped_file_rel_paths,
        )

        header_lines = [
            '# All blobs below are extracted from the release '
            'mentioned in proprietary-files.txt\n',
        ]

        self.file_list.add_from_lines(header_lines + file_srcs)
        self.fix_file_list(self.file_list)

    def get_partitions(self) -> Set[str]:
        return {self.partition}


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
        device_rel_path: Optional[str] = None,
        blob_fixups: Optional[blob_fixups_user_type] = None,
        lib_fixups: Optional[lib_fixups_user_type] = None,
        namespace_imports: Optional[List[str]] = None,
        extract_fns: Optional[extract_fns_user_type] = None,
        check_elf=False,
        add_firmware_proprietary_file=False,
        add_factory_proprietary_file=False,
        add_generated_carriersettings=False,
        skip_main_proprietary_file=False,
    ):
        self.device = device
        self.vendor = vendor
        self.proprietary_files: List[ProprietaryFile] = []
        self.rro_packages: List[RuntimeResourceOverlay] = []
        self.postprocess_fns: List[postprocess_fn_type] = []

        self.blob_fixups = flatten_fixups(blob_fixups)
        self.lib_fixups = flatten_fixups(lib_fixups)
        self.extract_fns = flatten_fixups(extract_fns)

        if namespace_imports is None:
            namespace_imports = []
        self.namespace_imports = namespace_imports

        self.check_elf = check_elf

        if device_rel_path is None:
            device_rel_path = path.join('device', vendor, device)
        self.device_rel_path = device_rel_path

        self.device_path = path.join(android_root, self.device_rel_path)
        self.vendor_rel_path = path.join('vendor', vendor, device)
        self.vendor_path = path.join(android_root, self.vendor_rel_path)
        self.vendor_rro_path = path.join(self.vendor_path, 'rro_overlays')

        if add_firmware_proprietary_file:
            self.add_firmware_proprietary_file()

        if add_factory_proprietary_file:
            self.add_factory_proprietary_file()

        if add_generated_carriersettings:
            self.add_generated_carriersettings()

        if not skip_main_proprietary_file:
            self.add_proprietary_file('proprietary-files.txt')

    def get_partitions(self, type: ProprietaryFileType):
        partitions = []

        for proprietary_file in self.proprietary_files:
            if proprietary_file.type is not type:
                continue

            partitions.extend(
                proprietary_file.get_partitions(),
            )

        return partitions

    def get_files(self, type: ProprietaryFileType):
        files = []

        for proprietary_file in self.proprietary_files:
            if proprietary_file.type is not type:
                continue

            files.extend(
                proprietary_file.get_files(),
            )

        return files

    def get_extract_partitions(self):
        return self.get_partitions(ProprietaryFileType.BLOBS)

    def get_firmware_partitions(self):
        return self.get_partitions(ProprietaryFileType.FIRMWARE)

    def get_firmware_files(self):
        return self.get_files(ProprietaryFileType.FIRMWARE)

    def get_factory_files(self):
        return self.get_files(ProprietaryFileType.FACTORY)

    def proprietary_file_vendor_path(self, proprietary_file: ProprietaryFile):
        return path.join(self.vendor_path, proprietary_file.vendor_rel_sub_path)

    def proprietary_file_path(self, file_list_name: str):
        return path.join(self.device_path, file_list_name)

    def add_postprocess_fn(self, fn: postprocess_fn_type) -> Self:
        self.postprocess_fns.append(fn)
        return self

    def add_rro_package(self, *args, **kwargs):
        rro_package = RuntimeResourceOverlay(*args, *kwargs)
        self.rro_packages.append(rro_package)
        return rro_package

    def add_proprietary_file(self, file_list_name: str, *args, **kwargs):
        file_list_path = self.proprietary_file_path(file_list_name)
        proprietary_file = ProprietaryFile(file_list_path, *args, **kwargs)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_generated_proprietary_file(
        self,
        file_list_name: str,
        *args,
        **kwargs,
    ):
        file_list_path = self.proprietary_file_path(file_list_name)
        proprietary_file = GeneratedProprietaryFile(
            file_list_path,
            *args,
            **kwargs,
        )
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_firmware_proprietary_file(self):
        file_list_path = self.proprietary_file_path('proprietary-firmware.txt')
        proprietary_file = FirmwareProprietaryFile(file_list_path)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_factory_proprietary_file(self):
        file_list_path = self.proprietary_file_path(
            'proprietary-firmware-factory.txt'
        )
        proprietary_file = FactoryProprietaryFile(file_list_path)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file

    def add_generated_carriersettings(self):
        pb_partition = 'product'
        pb_dir_rel_path = 'etc/CarrierSettings'
        package_name = 'CarrierConfigOverlay'

        file_list_path = self.proprietary_file_path(
            'proprietary-files-carriersettings.txt'
        )
        proprietary_file = GeneratedProprietaryFile(
            file_list_path,
            pb_partition,
            pb_dir_rel_path,
            r'\.pb$',
        )
        self.proprietary_files.append(proprietary_file)
        self.add_rro_package(
            package_name,
            'com.android.carrierconfig',
            pb_partition,
        )

        vendor_path = self.proprietary_file_vendor_path(proprietary_file)
        pb_dir_path = path.join(vendor_path, pb_partition, pb_dir_rel_path)
        rro_xml_dir_path = path.join(
            self.vendor_rro_path,
            package_name,
            'res/xml',
        )

        postprocess_fn = partial(
            postprocess_carriersettings_fn_impl,
            pb_dir_path,
            rro_xml_dir_path,
        )
        self.add_postprocess_fn(postprocess_fn)
        return proprietary_file

    def write_rro_makefiles(self, ctx: MakefilesCtx):
        for rro_package in self.rro_packages:
            write_rro_package(
                ctx,
                self.vendor_rro_path,
                rro_package.package_name,
                rro_package.target_package_name,
                rro_package.partition,
            )

    def write_makefiles(self, legacy: bool, extract_factory: bool):
        bp_path = path.join(self.vendor_path, 'Android.bp')
        mk_path = path.join(self.vendor_path, 'Android.mk')
        product_mk_path = path.join(
            self.vendor_path, f'{self.device}-vendor.mk'
        )
        board_config_mk_path = path.join(
            self.vendor_path, 'BoardConfigVendor.mk'
        )

        with MakefilesCtx.from_paths(
            legacy,
            bp_path,
            mk_path,
            product_mk_path,
            board_config_mk_path,
        ) as ctx:
            write_bp_header(ctx.bp_out)
            write_bp_soong_namespaces(ctx, self.namespace_imports)

            write_mk_header(ctx.product_mk_out)
            write_mk_soong_namespace(self.vendor_rel_path, ctx.product_mk_out)

            write_mk_header(ctx.board_config_mk_out)
            write_mk_header(ctx.mk_out)
            write_mk_local_path(ctx.mk_out)

            self.write_rro_makefiles(ctx)

            for proprietary_file in self.proprietary_files:
                if (
                    not extract_factory
                    and proprietary_file.type is ProprietaryFileType.FACTORY
                ):
                    continue

                proprietary_file.write_makefiles(self, ctx)

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

        print(f'Updating {proprietary_file.root_path}')

        proprietary_file.write_to_file()

    def write_updated_proprietary_files(self, kang: bool, regenerate: bool):
        for proprietary_file in self.proprietary_files:
            self.write_updated_proprietary_file(
                proprietary_file, kang, regenerate
            )

    def parse(
        self,
        regenerate: bool,
        section: Optional[str],
    ):
        for proprietary_file in self.proprietary_files:
            if regenerate and isinstance(
                proprietary_file,
                GeneratedProprietaryFile,
            ):
                continue

            print(f'Parsing {proprietary_file.root_path}')

            proprietary_file.init_file_list(self, section)
            proprietary_file.parse()

    def regenerate(
        self,
        source: Source,
        regenerate: bool,
    ):
        if not regenerate:
            return

        for proprietary_file in self.proprietary_files:
            if not isinstance(
                proprietary_file,
                GeneratedProprietaryFile,
            ):
                continue

            print(f'Regenerating {proprietary_file.root_path}')

            proprietary_file.init_file_list(self, None)
            proprietary_file.regenerate(self, source)

    def should_fixup_file(self, file: File):
        if FileArgs.FIX_XML in file.args:
            return True

        if FileArgs.FIX_SONAME in file.args:
            return True

        if self.blob_fixups.get(file.dst) is not None:
            return True

        return False

    def fixup_module_file(self, file: File, file_path: str):
        # device path is needed for reading patches
        ctx = BlobFixupCtx(self.device_path)

        if FileArgs.FIX_XML in file.args:
            blob_fixup().fix_xml().run(ctx, file, file_path)

        if FileArgs.FIX_SONAME in file.args:
            blob_fixup().fix_soname().run(ctx, file, file_path)

        # TODO: mark which fixups have been used and print unused ones
        # at the end
        blob_fixup_fn = self.blob_fixups.get(file.dst)
        if blob_fixup_fn is not None:
            blob_fixup_fn.run(ctx, file, file_path)

    # Some duplicate logic between simple copy, kanged copy,
    # and pinned copy, but keep it separate to simplify each function

    def process_simple_file(
        self,
        file: File,
        file_path: str,
    ):
        should_fixup = self.should_fixup_file(file)

        if not should_fixup:
            return

        pre_fixup_hash = file_path_sha1(file_path)
        self.fixup_module_file(file, file_path)
        post_fixup_hash = file_path_sha1(file_path)

        if pre_fixup_hash == post_fixup_hash:
            color_print(
                f'{file.dst}: file expected to be fixed up, '
                f'but pre-fixup hash and post-fixup hash are the same',
                color=Color.YELLOW,
            )
            return

        color_print(f'{file.dst}: fixed up', color=Color.GREEN)

    def process_kanged_file(
        self,
        file: File,
        file_path: str,
    ):
        # Always compute pre-fixup hash for kanged files, since they need to
        # be pinned
        # Only compute post-fixup hash if the file is supposed to be fixed up
        pre_fixup_hash = file_path_sha1(file_path)
        post_fixup_hash = None

        should_fixup = self.should_fixup_file(file)
        if should_fixup:
            self.fixup_module_file(file, file_path)

            post_fixup_hash = file_path_sha1(file_path)

        file.set_hash(pre_fixup_hash)
        file.set_fixup_hash(post_fixup_hash)

        if pre_fixup_hash == post_fixup_hash:
            color_print(
                f'{file.dst}: kanged file pinned with hash {file.hash} '
                f'expected to be fixed up, '
                f'but pre-fixup hash and post-fixup hash are the same',
                color=Color.YELLOW,
            )
            return

        msg = f'{file.dst}: kanged file pinned with hash {file.hash}, '
        if file.fixup_hash is not None:
            msg += f'and fixup hash {file.fixup_hash}'

        color_print(msg, color=Color.GREEN)

    def process_pinned_file_no_fixups(
        self,
        file: File,
        pre_fixup_hash: str,
        action: str,
    ) -> PinnedFileProcessResult:
        if file.hash == pre_fixup_hash:
            # Pinned file has NO fixup hash, and the extracted file hash
            # matches the pre-fixup hash
            color_print(
                f'{file.dst}: {action} pinned file with hash {file.hash} ',
                color=Color.GREEN,
            )
            return PinnedFileProcessResult.MATCH

        # Pinned file has NO fixup hash and the extracted file hash
        # does NOT match the pre-fixup hash
        color_print(
            f'{file.dst}: {action} pinned file with hash {pre_fixup_hash} '
            f'but expected hash {file.hash}',
            color=Color.YELLOW,
        )
        return PinnedFileProcessResult.MISMATCH

    def process_pinned_file(
        self,
        file: File,
        file_path: str,
        restored: bool,
    ) -> PinnedFileProcessResult:
        action = 'restored' if restored else 'found'
        should_fixup = self.should_fixup_file(file)

        if not should_fixup and file.fixup_hash is not None:
            # Pinned file has a fixup hash but NO fixup function
            color_print(
                f'{file.dst}: {action} pinned file with hash {file.hash} '
                f'expected to have fixup hash {file.fixup_hash} '
                f'but has no fixups',
                color=Color.RED,
            )
            return PinnedFileProcessResult.BAD_FIXUP

        pre_fixup_hash = file_path_sha1(file_path)

        if not should_fixup and file.fixup_hash is None:
            # Pinned file has NO fixup hash and NO fixup function
            # Check pre-fixup hash
            return self.process_pinned_file_no_fixups(
                file, pre_fixup_hash, action
            )

        if file.fixup_hash is not None and file.fixup_hash == pre_fixup_hash:
            # Pinned file has a fixup hash, and extracted file hash matches
            # the fixup hash
            color_print(
                f'{file.dst}: {action} pinned file with fixup hash {file.fixup_hash} ',
                color=Color.GREEN,
            )
            return PinnedFileProcessResult.MATCH

        if file.fixup_hash is not None and file.hash != pre_fixup_hash:
            # Pinned file has a fixup hash and the extracted file hash
            # does not match the pre-fixup hash
            color_print(
                f'{file.dst}: {action} pinned file with hash {pre_fixup_hash} '
                f'expected to have hash {file.hash}',
                color=Color.YELLOW,
            )
            return PinnedFileProcessResult.MISMATCH

        self.fixup_module_file(file, file_path)
        post_fixup_hash = file_path_sha1(file_path)

        if file.fixup_hash is None:
            # Pinned file has a fixup function but no fixup hash
            # Print out the fixup hash to let the user update its file
            # TODO: update it automatically?
            color_print(
                f'{file.dst}: {action} pinned file with hash {file.hash} '
                f'has fixup hash {post_fixup_hash}',
                color=Color.RED,
            )
            return PinnedFileProcessResult.MATCH

        if file.fixup_hash != post_fixup_hash:
            # Pinned file has a fixup hash and the extracted file
            # matches the hash, but the fixed-up file does not match the
            # fixup hash
            color_print(
                f'{file.dst}: {action} pinned file with hash {file.hash} '
                f'expected to have fixup hash {file.fixup_hash}'
                f'but instead have fixup hash {post_fixup_hash}',
                color=Color.RED,
            )
            return PinnedFileProcessResult.BAD_FIXUP

        # Pinned file has a fixup hash and the extracted file
        # matches the hash, and fixed-up file matches the fixup hash
        color_print(
            f'{file.dst}: {action} pinned file with hash {file.hash} '
            f'and fixup hash {file.fixup_hash}',
            color=Color.GREEN,
        )

        return PinnedFileProcessResult.MATCH

    def backup_file(
        self,
        file: File,
        backup_source: Source,
        backup_dir: str,
    ):
        if backup_source.copy_file_to_dir(file, backup_dir) is None:
            color_print(f'Failed to back up {file.dst}', color=Color.YELLOW)
            return

        print(f'Backed up {file.dst}')

    def backup_pinned_files(self, backup_dir: str):
        for proprietary_file in self.proprietary_files:
            vendor_path = self.proprietary_file_vendor_path(proprietary_file)
            backup_source = DiskSource(vendor_path)

            printed = False
            for file in proprietary_file.file_list.pinned_files:
                if not printed:
                    print(f'Backing up {proprietary_file.root_path}')
                self.backup_file(file, backup_source, backup_dir)

    def process_file(
        self,
        file: File,
        source: Source,
        backup_source: Source,
        vendor_path: str,
        is_firmware: bool,
        kang: bool,
    ) -> bool:
        file_path = source.get_file_copy_path(file, vendor_path)

        if not kang and file.hash is not None:
            # If we're not kanging and file is pinned, try copying the backup
            # file first
            # If the backup file does not exist or the hashes do not match,
            # try extracting from source
            # It's okay to extract from source if the hashes of the backup
            # file do not match since it can't get any worse than that,
            # even if the source file hashes do not match either
            copied = backup_source.copy_file_to_path(
                file,
                file_path,
                is_firmware,
            )

            if copied:
                process_result = self.process_pinned_file(
                    file,
                    file_path,
                    True,
                )

                if process_result is PinnedFileProcessResult.MATCH:
                    return True

                if process_result is PinnedFileProcessResult.BAD_FIXUP:
                    # Error out at the end if there's a fixup hash but
                    # there's no fixup function or if the pinned hash
                    # matches the file hash but the fixup hash does not match
                    # Both of these cases denote a bad fixup function
                    return False
            else:
                color_print(
                    f'{file.dst}: pinned file not found in backup, trying source',
                    color=Color.RED,
                )

        copied = source.copy_file_to_path(
            file,
            file_path,
            is_firmware,
        )

        if not copied:
            color_print(
                f'{file.dst}: file not found',
                color=Color.RED,
            )
            return False

        if kang:
            self.process_kanged_file(
                file,
                file_path,
            )
        elif file.hash is not None:
            self.process_pinned_file(
                file,
                file_path,
                False,
            )
        else:
            self.process_simple_file(
                file,
                file_path,
            )

        return True

    def process_proprietary_files(
        self,
        source: Source,
        backup_source: Source,
        kang: bool,
        extract_factory: bool,
    ) -> bool:
        all_copied = True

        for proprietary_file in self.proprietary_files:
            if (
                not extract_factory
                and proprietary_file.type is ProprietaryFileType.FACTORY
            ):
                continue

            print(f'Processing {proprietary_file.root_path}')

            is_firmware = proprietary_file.type is ProprietaryFileType.FIRMWARE
            vendor_path = self.proprietary_file_vendor_path(proprietary_file)

            for file in proprietary_file.file_list.files:
                copied = self.process_file(
                    file,
                    source,
                    backup_source,
                    vendor_path,
                    is_firmware,
                    kang,
                )

                if not copied:
                    all_copied = False

        return all_copied

    def cleanup(self):
        remove_dir_contents(self.vendor_path)

        for proprietary_file in self.proprietary_files:
            vendor_path = self.proprietary_file_vendor_path(proprietary_file)
            os.makedirs(vendor_path, exist_ok=True)

        if self.rro_packages:
            os.makedirs(self.vendor_rro_path)

    def process(
        self,
        source: Source,
        kang: bool,
        no_cleanup: bool,
        extract_factory: bool,
        section: Optional[str],
    ):
        with tempfile.TemporaryDirectory() as backup_dir:
            os.makedirs(self.vendor_path, exist_ok=True)

            # Kang is usually combined with section, but allow them separately
            if not kang:
                self.backup_pinned_files(backup_dir)

            if section is None and not no_cleanup:
                self.cleanup()

            backup_source = DiskSource(backup_dir)

            return self.process_proprietary_files(
                source,
                backup_source,
                kang,
                extract_factory,
            )
