#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import tempfile

from os import path
from typing import List, Optional

from extract_utils.args import parse_args
from extract_utils.extract import ExtractCtx
from extract_utils.source import (
    CopyCtx,
    DiskSource,
    Source,
    create_source,
)
from extract_utils.file import File, FileArgs, FileList
from extract_utils.module import (
    ExtractUtilsModule,
    GeneratedProprietaryFile,
    ProprietaryFile,
)
from extract_utils.tools import get_android_root

from extract_utils.fixups_blob import BlobFixupCtx, blob_fixup

from extract_utils.makefiles import (
    MakefilesCtx,
    write_bp_header,
    write_bp_soong_namespaces,
    write_mk_header,
    write_mk_soong_namespaces,
    write_product_copy_files,
    write_product_packages,
    write_symlink_packages,
)

from extract_utils.utils import (
    Color,
    color_print,
    file_path_sha1,
    get_module_attr,
    import_module,
    parse_lines,
    remove_dir_contents,
)


ANDROID_ROOT = get_android_root()


class ExtractUtils:
    def __init__(
        self,
        device_module: ExtractUtilsModule,
        common_module: Optional[ExtractUtilsModule] = None,
    ):
        self.__args = parse_args()

        self.__device_module = device_module
        self.__common_module = common_module

        self.__modules: List[ExtractUtilsModule] = []
        if self.__args.only_target:
            self.__modules.append(self.__device_module)
        elif self.__args.only_common:
            assert self.__common_module is not None
            self.__modules.append(self.__common_module)
        else:
            self.__modules = [self.__device_module]
            if self.__common_module is not None:
                self.__modules.append(self.__common_module)

    @classmethod
    def device_with_common(
        cls,
        device_module: ExtractUtilsModule,
        device_common,
        vendor_common=None,
    ):
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
        module_path = path.join(
            ANDROID_ROOT, 'device', vendor, device, 'extract-files.py'
        )

        module = import_module(module_name, module_path)

        return get_module_attr(module, 'module')

    @classmethod
    def get_module(cls, device: str, vendor: str):
        module = cls.import_module(device, vendor)
        assert module is not None
        return module

    def should_module_fixup_file(self, module: ExtractUtilsModule, file: File):
        if FileArgs.FIX_XML in file.args:
            return True

        if FileArgs.FIX_SONAME in file.args:
            return True

        if module.blob_fixups.get(file.dst) is not None:
            return True

        return False

    def fixup_module_file(
        self, module: ExtractUtilsModule, file: File, file_path: str
    ):
        ctx = BlobFixupCtx(module.dir_path)

        if FileArgs.FIX_XML in file.args:
            blob_fixup().fix_xml().run(ctx, file, file_path)

        if FileArgs.FIX_SONAME in file.args:
            blob_fixup().fix_soname().run(ctx, file, file_path)

        blob_fixup_fn = module.blob_fixups.get(file.dst)
        if blob_fixup_fn is not None:
            blob_fixup_fn.run(ctx, file, file_path)

    # Some duplicate logic between simple copy, kanged copy,
    # and pinned copy, but keep it separate to simplify each function

    def process_module_file(
        self,
        module: ExtractUtilsModule,
        file: File,
        copy_ctx: CopyCtx,
    ) -> bool:
        if not copy_ctx.copy_file(file):
            color_print(
                f'{file.dst}: file not found in source', color=Color.RED
            )
            return False

        should_fixup = self.should_module_fixup_file(module, file)

        if not should_fixup:
            return True

        file_path = path.join(module.vendor_files_path, file.dst)
        pre_fixup_hash = file_path_sha1(file_path)
        self.fixup_module_file(module, file, file_path)
        post_fixup_hash = file_path_sha1(file_path)

        if pre_fixup_hash == post_fixup_hash:
            color_print(
                f'{file.dst}: file expected to be fixed up, '
                f'but pre-fixup hash and post-fixup hash are the same, ',
                color=Color.YELLOW,
            )
            return True

        color_print(f'{file.dst}: fixed up', color=Color.GREEN)

        return True

    def process_module_kanged_file(
        self,
        module: ExtractUtilsModule,
        file: File,
        copy_ctx: CopyCtx,
    ) -> bool:
        if not copy_ctx.copy_file(file):
            color_print(
                f'{file.dst}: kanged file not found in source', color=Color.RED
            )
            return False

        should_fixup = self.should_module_fixup_file(module, file)

        # Always compute pre-fixup hash for kanged files, since they need to
        # be pinned
        # Only compute post-fixup hash if the file is supposed to be fixed up
        file_path = path.join(module.vendor_files_path, file.dst)
        pre_fixup_hash = file_path_sha1(file_path)
        post_fixup_hash = None

        if should_fixup:
            self.fixup_module_file(module, file, file_path)

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

    def process_module_pinned_file(
        self,
        module: ExtractUtilsModule,
        file: File,
        copy_ctx: CopyCtx,
        restore_ctx: CopyCtx,
    ) -> bool:
        restored = True
        # Try to restore first, as restored file should have correct hashes
        if not restore_ctx.copy_file(file):
            restored = False

            if not copy_ctx.copy_file(file):
                color_print(
                    f'{file.dst}: pinned file not found',
                    color=Color.RED,
                )
                return False

        file_path = path.join(module.vendor_files_path, file.dst)
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

        should_fixup = self.should_module_fixup_file(module, file)

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

        self.fixup_module_file(module, file, file_path)
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

    def backup_module_file(self, file: File, backup_ctx: CopyCtx):
        copy_success = backup_ctx.copy_file(file)
        if not copy_success:
            color_print(f'Failed to back up {file.dst}', color=Color.YELLOW)
            return

        print(f'Backed up {file.dst}')

    def backup_module_pinned_files(
        self,
        module: ExtractUtilsModule,
        backup_ctx: CopyCtx,
    ):
        for proprietary_files in module.proprietary_files:
            file_list = proprietary_files.file_list
            assert file_list is not None

            for file in file_list.pinned_files:
                assert isinstance(file, File)
                self.backup_module_file(file, backup_ctx)

    def process_module_files(
        self,
        module: ExtractUtilsModule,
        copy_ctx: CopyCtx,
        restore_ctx: CopyCtx,
    ) -> bool:
        all_copied = True

        for proprietary_file in module.proprietary_files:
            file_list_rel_path = path.join(
                module.device_rel_path,
                proprietary_file.file_list_name,
            )

            print(f'Copying {file_list_rel_path}')

            file_list = proprietary_file.file_list
            assert file_list is not None

            files = file_list.files

            for file in files:
                assert isinstance(file, File)

                if self.__args.kang:
                    copied = self.process_module_kanged_file(
                        module,
                        file,
                        copy_ctx,
                    )
                elif file.hash is not None:
                    copied = self.process_module_pinned_file(
                        module,
                        file,
                        copy_ctx,
                        restore_ctx,
                    )
                else:
                    copied = self.process_module_file(
                        module,
                        file,
                        copy_ctx,
                    )

                if not copied:
                    all_copied = False

        return all_copied

    def cleanup_module(
        self,
        module: ExtractUtilsModule,
    ):
        remove_dir_contents(module.vendor_path)

        os.makedirs(module.vendor_files_path)

    def process_module_with_backup_dir(
        self,
        source: Source,
        module: ExtractUtilsModule,
        backup_dir: str,
    ):
        backup_ctx = CopyCtx(
            DiskSource(module.vendor_files_path),
            backup_dir,
        )

        restore_ctx = CopyCtx(
            DiskSource(backup_dir),
            module.vendor_files_path,
        )

        copy_ctx = CopyCtx(
            source,
            module.vendor_files_path,
        )

        # Kang is usually combined with section, but allow them separately

        if not self.__args.kang:
            self.backup_module_pinned_files(module, backup_ctx)

        if not self.__args.section and not self.__args.no_cleanup:
            self.cleanup_module(module)

        return self.process_module_files(module, copy_ctx, restore_ctx)

    def process_module(
        self,
        source: Source,
        module: ExtractUtilsModule,
    ):
        with tempfile.TemporaryDirectory() as backup_dir:
            return self.process_module_with_backup_dir(
                source, module, backup_dir
            )

    def process_modules(self, source: Source):
        all_copied = True
        for module in self.__modules:
            copied = self.process_module(source, module)
            if not copied:
                all_copied = False
        return all_copied

    def parse_proprietary_file(
        self,
        module: ExtractUtilsModule,
        proprietary_file: ProprietaryFile,
        file_list: FileList,
    ):
        file_list_rel_path = path.join(
            module.device_rel_path,
            proprietary_file.file_list_name,
        )

        print(f'Parsing {file_list_rel_path}')

        file_list_path = path.join(
            module.dir_path,
            proprietary_file.file_list_name,
        )

        file_list.add_from_file(file_list_path)

    def regenerate_proprietary_file(
        self,
        source: Source,
        module: ExtractUtilsModule,
        proprietary_file: GeneratedProprietaryFile,
        file_list: FileList,
    ):
        file_list_rel_path = path.join(
            module.device_rel_path,
            proprietary_file.file_list_name,
        )

        print(f'Regenerating {file_list_rel_path}')

        skipped_file_rel_paths: List[str] = []
        if proprietary_file.skip_file_list_name is not None:
            skip_file_list_path = path.join(
                module.dir_path,
                proprietary_file.skip_file_list_name,
            )
            with open(skip_file_list_path, 'r') as f:
                skipped_file_rel_paths = parse_lines(f)

        file_srcs = source.find_sub_dir_files(
            proprietary_file.partition,
            proprietary_file.regex,
            skipped_file_rel_paths,
        )

        header_lines = [
            '# All blobs below are extracted from the release '
            'mentioned in proprietary-files.txt',
        ]
        file_list.add_from_lines(header_lines + file_srcs)

    def parse_modules(self, source: Source):
        for module in self.__modules:
            for proprietary_file in module.proprietary_files:
                file_list = FileList(
                    section=self.__args.section,
                    check_elf=module.check_elf,
                )

                if self.__args.regenerate and isinstance(
                    proprietary_file, GeneratedProprietaryFile
                ):
                    self.regenerate_proprietary_file(
                        source,
                        module,
                        proprietary_file,
                        file_list,
                    )
                else:
                    self.parse_proprietary_file(
                        module,
                        proprietary_file,
                        file_list,
                    )

                proprietary_file.fix_file_list_fn(file_list)
                proprietary_file.file_list = file_list

    def write_module_makefiles(self, module: ExtractUtilsModule):
        ctx = MakefilesCtx(
            module.device,
            module.vendor,
            module.vendor_files_path,
            module.vendor_files_rel_path,
            module.vendor_files_rel_sub_path,
            module.vendor_imports,
            module.lib_fixups,
            module.check_elf,
        )

        android_bp_path = path.join(module.vendor_path, 'Android.bp')
        product_mk_path = path.join(
            module.vendor_path, f'{module.device}-vendor.mk'
        )
        board_config_mk_path = path.join(
            module.vendor_path, 'BoardConfigVendor.mk'
        )

        with open(android_bp_path, 'w') as bp_out, open(
            product_mk_path, 'w'
        ) as mk_out, open(board_config_mk_path, 'w') as board_mk_out:
            write_bp_header(ctx, bp_out)
            write_bp_soong_namespaces(ctx, bp_out)

            write_mk_header(ctx, mk_out)
            write_mk_soong_namespaces(ctx, mk_out)

            write_mk_header(ctx, board_mk_out)

            for proprietary_file in module.proprietary_files:
                file_list = proprietary_file.file_list
                assert file_list is not None

                write_product_copy_files(ctx, file_list.copy_files, mk_out)

                write_product_packages(
                    ctx,
                    file_list.packages_files,
                    bp_out,
                    mk_out,
                )

                write_symlink_packages(
                    ctx,
                    file_list.packages_files_symlinks,
                    bp_out,
                    mk_out,
                )

    def write_makefiles(self):
        for module in self.__modules:
            self.write_module_makefiles(module)

    def write_updated_proprietary_files(self):
        for module in self.__modules:
            for proprietary_file in module.proprietary_files:
                is_generated = isinstance(
                    proprietary_file, GeneratedProprietaryFile
                )
                update_kanged = self.__args.kang and not is_generated
                update_generated = self.__args.regenerate and is_generated

                if not update_kanged and not update_generated:
                    continue

                file_list_rel_path = path.join(
                    module.device_rel_path,
                    proprietary_file.file_list_name,
                )

                print(f'Updating {file_list_rel_path}')

                file_list_path = path.join(
                    module.dir_path,
                    proprietary_file.file_list_name,
                )

                assert proprietary_file.file_list is not None
                proprietary_file.file_list.write_to_file(file_list_path)

    def run(self):
        extract_ctx = ExtractCtx(
            self.__args.source,
            self.__args.keep_dump,
        )

        with create_source(extract_ctx) as source:
            self.parse_modules(source)
            all_copied = self.process_modules(source)
            if not all_copied:
                color_print(
                    'Some files failed to copy, exiting',
                    color=Color.RED,
                )
                return

        self.write_updated_proprietary_files()
        self.write_makefiles()
