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
from extract_utils.postprocess import PostprocessCtx
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
from extract_utils.tools import android_root

from extract_utils.fixups_blob import BlobFixupCtx, blob_fixup


from extract_utils.utils import (
    Color,
    color_print,
    file_path_sha1,
    get_module_attr,
    import_module,
    parse_lines,
    remove_dir_contents,
)


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
            android_root, 'device', vendor, device, 'extract-files.py'
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
        ctx = BlobFixupCtx(module.device_path)

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
        is_firmware=False,
    ) -> bool:
        file_path = copy_ctx.copy_file(file, is_firmware)
        if file_path is None:
            color_print(
                f'{file.dst}: file not found in source', color=Color.RED
            )
            return False

        should_fixup = self.should_module_fixup_file(module, file)

        if not should_fixup:
            return True

        pre_fixup_hash = file_path_sha1(file_path)
        self.fixup_module_file(module, file, file_path)
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

    def process_module_kanged_file(
        self,
        module: ExtractUtilsModule,
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

        should_fixup = self.should_module_fixup_file(module, file)

        # Always compute pre-fixup hash for kanged files, since they need to
        # be pinned
        # Only compute post-fixup hash if the file is supposed to be fixed up
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
        if backup_ctx.copy_file(file) is None:
            color_print(f'Failed to back up {file.dst}', color=Color.YELLOW)
            return

        print(f'Backed up {file.dst}')

    def backup_module_pinned_files(
        self,
        module: ExtractUtilsModule,
        backup_dir: str,
    ):
        for proprietary_file in module.proprietary_files:
            file_list_rel_path = path.join(
                module.device_rel_path,
                proprietary_file.file_list_name,
            )

            print(f'Backing up {file_list_rel_path}')

            file_list = proprietary_file.file_list
            assert file_list is not None

            vendor_path = module.proprietary_file_vendor_path(proprietary_file)
            backup_ctx = CopyCtx(DiskSource(vendor_path), backup_dir)

            for file in file_list.pinned_files:
                self.backup_module_file(file, backup_ctx)

    def process_module_files(
        self,
        module: ExtractUtilsModule,
        source: Source,
        backup_dir: str,
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

            vendor_path = module.proprietary_file_vendor_path(proprietary_file)
            restore_ctx = CopyCtx(DiskSource(backup_dir), vendor_path)
            copy_ctx = CopyCtx(source, vendor_path)

            for file in file_list.files:
                if self.__args.kang:
                    copied = self.process_module_kanged_file(
                        module,
                        file,
                        copy_ctx,
                        is_firmware=proprietary_file.is_firmware,
                    )
                elif file.hash is not None:
                    copied = self.process_module_pinned_file(
                        module,
                        file,
                        copy_ctx,
                        restore_ctx,
                        is_firmware=proprietary_file.is_firmware,
                    )
                else:
                    copied = self.process_module_file(
                        module,
                        file,
                        copy_ctx,
                        is_firmware=proprietary_file.is_firmware,
                    )

                if not copied:
                    all_copied = False

        return all_copied

    def cleanup_module(
        self,
        module: ExtractUtilsModule,
    ):
        remove_dir_contents(module.vendor_path)

        for proprietary_file in module.proprietary_files:
            vendor_path = module.proprietary_file_vendor_path(proprietary_file)
            os.makedirs(vendor_path)

        if module.rro_packages:
            os.makedirs(module.vendor_rro_path)

    def process_module_with_backup_dir(
        self,
        source: Source,
        module: ExtractUtilsModule,
        backup_dir: str,
    ):
        # Kang is usually combined with section, but allow them separately

        if not self.__args.kang:
            self.backup_module_pinned_files(module, backup_dir)

        if not self.__args.section and not self.__args.no_cleanup:
            self.cleanup_module(module)

        return self.process_module_files(module, source, backup_dir)

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
            module.device_path,
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
                module.device_path,
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
            'mentioned in proprietary-files.txt\n',
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

    def write_updated_proprietary_files(self):
        for module in self.__modules:
            module.write_updated_proprietary_files(
                self.__args.kang,
                self.__args.regenerate,
            )

    def postprocess_modules(self):
        ctx = PostprocessCtx()

        for module in self.__modules:
            for postprocess_fn in module.postprocess_fns:
                postprocess_fn(ctx)

    def write_makefiles(self):
        for module in self.__modules:
            module.write_makefiles()

    def run(self):
        extract_fns = []
        extract_partitions = []

        for module in self.__modules:
            extract_fns.extend(module.extract_fns)
            extract_partitions.extend(module.extract_partitions)

        extract_ctx = ExtractCtx(
            self.__args.source,
            self.__args.keep_dump,
            extract_fns,
            extract_partitions,
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

        self.postprocess_modules()
        self.write_updated_proprietary_files()
        self.write_makefiles()
