#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import shutil

from os import path
from typing import List, Optional

from .args import parse_args
from .copy import CopyCtx, FileCopyResult, copy_file, copy_file_with_hashes
from .extract import ExtractCtx, extract_image
from .file import File, FileArgs, FileList
from .module import ExtractUtilsModule, ProprietaryFile
from .tools import get_android_root

from .fixups import \
    BlobFixupCtx, \
    blob_fixup, \
    run_blob_fixup

from .makefiles import \
    MakefilesCtx, \
    write_bp_header, \
    write_bp_soong_namespaces, \
    write_mk_header, \
    write_mk_soong_namespaces, \
    write_product_copy_files, \
    write_product_packages, \
    write_symlink_packages

from .utils import \
    Color, \
    color_print, \
    file_path_sha1, \
    get_module_attr, \
    import_module, \
    remove_dir_contents


ANDROID_ROOT = get_android_root()


class ExtractUtils:
    def __init__(self, device_module: ExtractUtilsModule,
                 common_module: Optional[ExtractUtilsModule] = None):
        self.__device_module = device_module
        self.__common_module = common_module

        self.__enable_check_elf = False

        args = parse_args()

        self.__keep_dump = args.keep_dump
        self.__no_cleanup = args.no_cleanup
        self.__kang = args.kang
        self.__section = args.section
        self.__source = args.src
        self.__extracted_source = None

        self.__modules: List[ExtractUtilsModule] = []
        if args.only_target:
            self.__modules.append(self.__device_module)
        elif args.only_common:
            assert self.__common_module is not None
            self.__modules.append(self.__common_module)
        else:
            self.__modules = [self.__device_module]
            if self.__common_module is not None:
                self.__modules.append(self.__common_module)

    @classmethod
    def device_with_common(cls, device_module: ExtractUtilsModule,
                           device_common, vendor_common=None):
        if vendor_common is None:
            vendor_common = device_module.vendor
        common_module = cls.get_module(device_common, vendor_common)
        return cls(device_module, common_module)

    @classmethod
    def device(cls, device_module: ExtractUtilsModule):
        return cls(device_module)

    @classmethod
    def import_module(cls, device, vendor) -> Optional[ExtractUtilsModule]:
        module_name = f'{vendor}_{device}'
        module_path = path.join(ANDROID_ROOT, 'device',
                                vendor, device, 'extract-files.py')

        module = import_module(module_name, module_path)

        return get_module_attr(module, 'module')

    @classmethod
    def get_module(cls, device: str, vendor: str):
        module = cls.import_module(device, vendor)
        assert module is not None
        return module

    def enable_check_elf(self):
        self.__enable_check_elf = True

    def extract_source(self):
        # TODO: implement ADB
        assert self.__source != 'adb'

        ctx = ExtractCtx(self.__keep_dump)
        self.__extracted_source = extract_image(ctx, self.__source)

    def cleanup_source(self):
        if not self.__keep_dump:
            assert self.__extracted_source is not None
            shutil.rmtree(self.__extracted_source)

    def should_module_fixup_file(self, module: ExtractUtilsModule, file: File):
        if FileArgs.FIX_XML in file.args:
            return True

        if FileArgs.FIX_SONAME in file.args:
            return True

        if run_blob_fixup(module.blob_fixups, None, file, None):
            return True

        return False

    def fixup_module_file(self, module: ExtractUtilsModule, file: File,
                          file_path: str):
        if FileArgs.FIX_XML in file.args:
            # TODO: implement
            assert False

        ctx = BlobFixupCtx(module.dir_path)

        if FileArgs.FIX_SONAME in file.args:
            blob_fixup() \
                .fix_soname() \
                .run(ctx, file, file_path)

        run_blob_fixup(module.blob_fixups, ctx, file, file_path)

    def process_module_file(self, module: ExtractUtilsModule, file: File,
                            copy_ctx: CopyCtx, restore_ctx: CopyCtx):
        file_path = path.join(module.vendor_files_path, file.dst)

        copy_result, pre_fixup_hash = copy_file_with_hashes(
            file, file_path, copy_ctx, restore_ctx)

        # TODO: implement dex2oat

        if copy_result == FileCopyResult.ERROR \
                or copy_result == FileCopyResult.DONE:
            return

        if copy_result == FileCopyResult.FORCE_FIXUP:
            should_fixup = True
        elif copy_result == FileCopyResult.TEST_FIXUP:
            should_fixup = self.should_module_fixup_file(module, file)
        else:
            assert False

        if not should_fixup:
            return

        if pre_fixup_hash is None:
            pre_fixup_hash = file_path_sha1(file_path)

        self.fixup_module_file(module, file, file_path)

        postfixup_hash = file_path_sha1(file_path)

        if pre_fixup_hash == postfixup_hash:
            color_print(
                f'{file.dst}: no fixups applied, file must have been '
                'fixed up already',
                color=Color.YELLOW
            )
            return

        if not self.__kang and file.hash is not None:
            if file.fixup_hash is not None:
                color_print(
                    f'{file.dst}: fixed up pinned file with no fixup hash, '
                    f'fixup hash is {file.fixup_hash}',
                    color=Color.YELLOW
                )
            else:
                color_print(
                    f'{file.dst}: fixed up pinned file with fixup hash, '
                    f'but hash {postfixup_hash} different from fixup hash '
                    f'{file.fixup_hash}',
                    color=Color.YELLOW
                )
            return

        color_print(f'{file.dst}: fixed up', color=Color.GREEN)

    def process_module_proprietary_files(self, module: ExtractUtilsModule):
        if not self.__no_cleanup and path.isdir(module.vendor_files_path):
            # Creating it again is handled automatically by copy_file
            remove_dir_contents(module.vendor_path)

        assert self.__extracted_source is not None
        copy_ctx = CopyCtx(self.__extracted_source, module.vendor_files_path)

        restore_ctx = CopyCtx(
            module.vendor_backup_files_path,
            module.vendor_files_path
        )

        for proprietary_files in module.proprietary_files:
            file_list = proprietary_files.file_list
            assert file_list is not None

            for file in file_list.all_files:
                self.process_module_file(module, file, copy_ctx, restore_ctx)

    def process_proprietary_files(self):
        for module in self.__modules:
            self.process_module_proprietary_files(module)

    def parse_proprietary_file(self, module: ExtractUtilsModule,
                               proprietary_file: ProprietaryFile):
        files_list_name = proprietary_file.name
        files_list_path = path.join(module.dir_path, files_list_name)
        proprietary_file.file_list = FileList(
            files_list_path, section=self.__section,
            target_enable_checkelf=self.__enable_check_elf,
            kang=self.__kang,
        )

    def parse_proprietary_files(self):
        for module in self.__modules:
            for proprietary_file in module.proprietary_files:
                self.parse_proprietary_file(module, proprietary_file)

    def copy_pinned_files(self):
        for module in self.__modules:
            for proprietary_file in module.proprietary_files:
                file_list = proprietary_file.file_list
                assert file_list is not None

                source_path = module.vendor_files_path
                destination_path = module.vendor_backup_files_path
                backup_ctx = CopyCtx(source_path, destination_path)
                for file in file_list.pinned_files:
                    copy_success = copy_file(backup_ctx, file)
                    if copy_success:
                        print(f'Backed up {file.dst}')
                    else:
                        color_print(f'Failed to back up {file.dst}',
                                    color=Color.YELLOW)

    def clean_pinned_files_backup(self):
        for module in self.__modules:
            shutil.rmtree(module.vendor_backup_files_path)

    def write_module_makefiles(self, module: ExtractUtilsModule):
        ctx = MakefilesCtx(
            module.device,
            module.vendor,
            module.vendor_files_path,
            module.vendor_files_rel_path,
            module.vendor_files_rel_sub_path,
            module.vendor_imports,
            module.lib_fixups,
            self.__enable_check_elf,
        )

        android_bp_path = path.join(module.vendor_path, 'Android.bp')
        product_mk_path = path.join(module.vendor_path,
                                    f'{module.device}-vendor.mk')
        board_config_mk_path = path.join(module.vendor_path,
                                         'BoardConfigVendor.mk')

        with open(android_bp_path, 'w') as bp_out, \
                open(product_mk_path, 'w') as mk_out, \
                open(board_config_mk_path, 'w') as board_mk_out:

            write_bp_header(ctx, bp_out)
            write_bp_soong_namespaces(ctx, bp_out)

            write_mk_header(ctx, mk_out)
            write_mk_soong_namespaces(ctx, mk_out)

            write_mk_header(ctx, board_mk_out)

            for proprietary_file in module.proprietary_files:
                file_list = proprietary_file.file_list
                assert file_list is not None

                write_product_copy_files(ctx, file_list.copy_files, mk_out)

                write_product_packages(ctx, file_list.packages_files,
                                       bp_out, mk_out)

                write_symlink_packages(ctx, file_list.packages_files_symlinks,
                                       bp_out, mk_out)

    def write_makefiles(self):
        for module in self.__modules:
            self.write_module_makefiles(module)

    def run(self):
        self.extract_source()
        self.parse_proprietary_files()
        self.copy_pinned_files()
        # TODO: regenerate
        self.process_proprietary_files()
        self.write_makefiles()
        self.clean_pinned_files_backup()
        self.cleanup_source()
