#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from functools import partial
from os import path
import os
import tempfile
from typing import Callable, List, Optional, Self, Set

from extract_utils.file import File, FileArgs, FileList
from extract_utils.fixups import flatten_fixups
from extract_utils.fixups_blob import (
    BlobFixupCtx,
    blob_fixup,
    blob_fixups_user_type,
)
from extract_utils.fixups_lib import lib_fixups_user_type
from extract_utils.extract import extract_fn_type
from extract_utils.makefiles import (
    MakefilesCtx,
    ProductPackagesCtx,
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
from extract_utils.source import CopyCtx, DiskSource, Source
from extract_utils.tools import android_root
from extract_utils.utils import (
    Color,
    color_print,
    file_path_sha1,
    parse_lines,
    remove_dir_contents,
)


fix_file_list_fn_type = Callable[[FileList], None]
pre_post_makefile_generation_fn_type = Callable[[MakefilesCtx], None]


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
        def guard_begin_fn(makefiles: MakefilesCtx):
            write_mk_guard_begin(
                name, value, makefiles.product_mk_out, invert=invert
            )

        def guard_end_fn(makefiles: MakefilesCtx):
            write_mk_guard_end(name, makefiles.product_mk_out)

        self.add_pre_post_makefile_generation_fn(guard_begin_fn, guard_end_fn)

        return self

    def run_pre_makefile_generation_fns(self, makefiles: MakefilesCtx):
        for fn in self.pre_makefile_generation_fns:
            fn(makefiles)

    def run_post_makefile_generation_fns(self, makefiles: MakefilesCtx):
        for fn in reversed(self.post_makefile_generation_fns):
            fn(makefiles)

    def write_makefiles(
        self,
        module: ExtractUtilsModule,
        makefiles: MakefilesCtx,
    ):
        ctx = ProductPackagesCtx(
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

    def get_partitions(self) -> Set[str]:
        partitions = set()

        for file in self.file_list.files:
            partitions.add(file.partition)
            # dst is different from src, add src partition too
            if file.has_dst:
                src_partition, _ = file.src.split('/', 1)
                partitions.add(src_partition)

        return partitions


class FirmwareProprietaryFile(ProprietaryFile):
    def write_makefiles(
        self,
        module: ExtractUtilsModule,
        makefiles: MakefilesCtx,
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

    def get_partitions(self) -> Set[str]:
        partitions = set()

        for file in self.file_list.files:
            partitions.add(file.dst)
            # dst is different from src, add src partition too
            if file.has_dst:
                partitions.add(file.src)

        return partitions


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

    def get_partitions(self) -> Set[str]:
        return set([self.partition])


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
        extract_fns: Optional[List[extract_fn_type]] = None,
        check_elf=False,
        skip_main_proprietary_file=False,
    ):
        self.device = device
        self.vendor = vendor
        self.proprietary_files: List[ProprietaryFile] = []
        self.rro_packages: List[RuntimeResourceOverlay] = []
        self.postprocess_fns: List[postprocess_fn_type] = []

        self.blob_fixups = flatten_fixups(blob_fixups)
        self.lib_fixups = flatten_fixups(lib_fixups)

        if namespace_imports is None:
            namespace_imports = []
        self.namespace_imports = namespace_imports

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

    def get_partitions(self, for_firmware=False):
        partitions = []

        for proprietary_file in self.proprietary_files:
            if for_firmware != isinstance(
                proprietary_file,
                FirmwareProprietaryFile,
            ):
                continue

            partitions.extend(
                proprietary_file.get_partitions(),
            )

        return partitions

    def get_extract_partitions(self):
        return self.get_partitions(for_firmware=False)

    def get_firmware_partitions(self):
        return self.get_partitions(for_firmware=True)

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

    def write_rro_makefiles(self, makefiles: MakefilesCtx):
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

        with MakefilesCtx.from_paths(
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

            file_list_rel_path = path.join(
                self.device_rel_path,
                proprietary_file.file_list_name,
            )

            print(f'Parsing {file_list_rel_path}')

            proprietary_file.init_file_list(self, section)
            proprietary_file.parse(self)

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

            file_list_rel_path = path.join(
                self.device_rel_path,
                proprietary_file.file_list_name,
            )

            print(f'Regenerating {file_list_rel_path}')

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

        blob_fixup_fn = self.blob_fixups.get(file.dst)
        if blob_fixup_fn is not None:
            blob_fixup_fn.run(ctx, file, file_path)

    # Some duplicate logic between simple copy, kanged copy,
    # and pinned copy, but keep it separate to simplify each function

    def process_file(
        self,
        file: File,
        copy_ctx: CopyCtx,
        is_firmware=False,
    ) -> bool:
        file_path = copy_ctx.copy_file(file, is_firmware)
        if file_path is None:
            color_print(
                f'{file.dst}: file not found in source', color=Color.RED
            )
            return False

        should_fixup = self.should_fixup_file(file)

        if not should_fixup:
            return True

        pre_fixup_hash = file_path_sha1(file_path)
        self.fixup_module_file(file, file_path)
        post_fixup_hash = file_path_sha1(file_path)

        if pre_fixup_hash == post_fixup_hash:
            color_print(
                f'{file.dst}: file expected to be fixed up, '
                f'but pre-fixup hash and post-fixup hash are the same',
                color=Color.YELLOW,
            )
            return True

        color_print(f'{file.dst}: fixed up', color=Color.GREEN)

        return True

    def process_kanged_file(
        self,
        file: File,
        copy_ctx: CopyCtx,
        is_firmware=False,
    ) -> bool:
        file_path = copy_ctx.copy_file(file, is_firmware)
        if file_path is None:
            color_print(
                f'{file.dst}: kanged file not found in source', color=Color.RED
            )
            return False

        should_fixup = self.should_fixup_file(file)

        # Always compute pre-fixup hash for kanged files, since they need to
        # be pinned
        # Only compute post-fixup hash if the file is supposed to be fixed up
        pre_fixup_hash = file_path_sha1(file_path)
        post_fixup_hash = None

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
            return True

        msg = f'{file.dst}: kanged file pinned with hash {file.hash}, '
        if file.fixup_hash is not None:
            msg += f'and fixup hash {file.fixup_hash}'

        color_print(msg, color=Color.GREEN)

        return True

    def process_pinned_file(
        self,
        file: File,
        copy_ctx: CopyCtx,
        restore_ctx: CopyCtx,
        is_firmware=False,
    ) -> bool:
        # Try to restore first, as restored file should have correct hashes
        restored = True
        file_path = restore_ctx.copy_file(file, is_firmware)
        if file_path is None:
            restored = False

            file_path = copy_ctx.copy_file(file, is_firmware)
            if file_path is None:
                color_print(
                    f'{file.dst}: pinned file not found',
                    color=Color.RED,
                )
                return False

        pre_fixup_hash = file_path_sha1(file_path)

        if file.fixup_hash is None:
            action = 'restored' if restored else 'found'

            if file.hash == pre_fixup_hash:
                # If pinned file has NO fixup hash, and the extracted file
                # matches the hash, we found our file
                color_print(
                    f'{file.dst}: {action} pinned file with hash {file.hash} ',
                    color=Color.GREEN,
                )
            else:
                # If pinned file has NO fixup hash and the extracted file
                # does NOT match the hash, we found a bad file
                color_print(
                    f'{file.dst}: {action} pinned file with hash {pre_fixup_hash} '
                    f'but expected hash {file.hash}',
                    color=Color.YELLOW,
                )

            return True

        if file.fixup_hash == pre_fixup_hash:
            # If pinned file has a fixup hash, and extracted file
            # matches the fixup hash, we found our file
            color_print(
                f'{file.dst}: found pinned file with fixup hash {file.fixup_hash} ',
                color=Color.GREEN,
            )
            return True

        should_fixup = self.should_fixup_file(file)

        if not should_fixup:
            # If pinned file has a fixup hash and the extracted file
            # matches the hash, but file has no fixups, we found a bad file
            color_print(
                f'{file.dst}: found pinned file with hash {file.hash} '
                f'expected to have fixup hash {file.fixup_hash}'
                f'but file has no fixups',
                color=Color.YELLOW,
            )
            return True

        self.fixup_module_file(file, file_path)
        post_fixup_hash = file_path_sha1(file_path)

        if file.fixup_hash != post_fixup_hash:
            # If pinned file has a fixup hash and the extracted file
            # matches the hash, but the fixed-up file does not match the
            # fixup hash, we found a bad file
            color_print(
                f'{file.dst}: found pinned file with hash {file.hash} '
                f'expected to have fixup hash {file.fixup_hash}'
                f'but instead have fixup hash {post_fixup_hash}',
                color=Color.YELLOW,
            )
            return True

        # If pinned file has a fixup hash and the extracted file
        # matches the hash, and fixed-up file matches the fixup hash,
        # we found a good file
        color_print(
            f'{file.dst}: found pinned file with hash {file.hash} '
            f'and fixup hash {file.fixup_hash}',
            color=Color.GREEN,
        )

        return False

    def backup_file(self, file: File, backup_ctx: CopyCtx):
        if backup_ctx.copy_file(file) is None:
            color_print(f'Failed to back up {file.dst}', color=Color.YELLOW)
            return

        print(f'Backed up {file.dst}')

    def backup_pinned_files(self, backup_dir: str):
        for proprietary_file in self.proprietary_files:
            file_list_rel_path = path.join(
                self.device_rel_path,
                proprietary_file.file_list_name,
            )

            print(f'Backing up {file_list_rel_path}')

            vendor_path = self.proprietary_file_vendor_path(proprietary_file)
            backup_ctx = CopyCtx(DiskSource(vendor_path), backup_dir)

            file_list = proprietary_file.file_list
            for file in file_list.pinned_files:
                self.backup_file(file, backup_ctx)

    def process_proprietary_file(
        self,
        proprietary_file: ProprietaryFile,
        source: Source,
        kang: bool,
        backup_dir: str,
    ) -> bool:
        vendor_path = self.proprietary_file_vendor_path(proprietary_file)
        restore_ctx = CopyCtx(DiskSource(backup_dir), vendor_path)
        copy_ctx = CopyCtx(source, vendor_path)
        all_copied = True

        file_list = proprietary_file.file_list
        for file in file_list.files:
            if kang:
                copied = self.process_kanged_file(
                    file,
                    copy_ctx,
                    is_firmware=proprietary_file.is_firmware,
                )
            elif file.hash is not None:
                copied = self.process_pinned_file(
                    file,
                    copy_ctx,
                    restore_ctx,
                    is_firmware=proprietary_file.is_firmware,
                )
            else:
                copied = self.process_file(
                    file,
                    copy_ctx,
                    is_firmware=proprietary_file.is_firmware,
                )

            if not copied:
                all_copied = False

        return all_copied

    def process_proprietary_files(
        self,
        source: Source,
        kang: bool,
        backup_dir: str,
    ) -> bool:
        all_copied = True

        for proprietary_file in self.proprietary_files:
            file_list_rel_path = path.join(
                self.device_rel_path,
                proprietary_file.file_list_name,
            )

            print(f'Copying {file_list_rel_path}')

            copied = self.process_proprietary_file(
                proprietary_file,
                source,
                kang,
                backup_dir,
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
        section: Optional[str],
    ):
        with tempfile.TemporaryDirectory() as backup_dir:
            # Kang is usually combined with section, but allow them separately
            if not kang:
                self.backup_pinned_files(backup_dir)

            if section is None and not no_cleanup:
                self.cleanup()

            return self.process_proprietary_files(source, kang, backup_dir)
