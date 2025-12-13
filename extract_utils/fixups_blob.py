#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
import tempfile
from contextlib import suppress
from functools import partial
from os import path
from typing import Any, List, Optional, Protocol

from extract_utils.elf import file_needs_lib
from extract_utils.file import File
from extract_utils.fixups import fixups_type, fixups_user_type
from extract_utils.tools import (
    DEFAULT_PATCHELF_VERSION,
    apktool_path,
    java_path,
    llvm_strip_path,
    patchelf_version_path_map,
    stripzip_path,
)
from extract_utils.utils import (
    Color,
    TemporaryWorkingDirectory,
    color_print,
    run_cmd,
)

APKTOOL_NO_RES_ARG = '--no-res'
APKTOOL_NO_SRC_ARG = '--no-src'
APKTOOL_SRC_PATH_PREFIX = 'smali'
APKTOOL_RES_PATH = 'res/'
APKTOOL_ANDROID_MANIFEST_NAME = 'AndroidManifest.xml'


class BlobFixupCtx:
    def __init__(self, module_dir: str):
        self.module_dir = module_dir


class blob_fixup_fn_impl_type(Protocol):
    def __call__(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        tmp_dir: Optional[str] = None,
        **kwargs: Any,
    ): ...


class blob_fixup:
    def __init__(self):
        self.__functions: List[
            tuple[
                blob_fixup_fn_impl_type,
                tuple[blob_fixup_fn_impl_type],
                dict[str, blob_fixup_fn_impl_type],
            ]
        ] = []
        self.__create_tmp_dir = False

        self.__patchelf_path = patchelf_version_path_map[
            DEFAULT_PATCHELF_VERSION
        ]

    def call(
        self,
        fn: blob_fixup_fn_impl_type,
        *args: Any,
        need_tmp_dir: bool = True,
        **kwargs: Any,
    ) -> blob_fixup:
        self.__functions.append((fn, args, kwargs))
        if need_tmp_dir:
            self.__create_tmp_dir = True
        return self

    def merge(self, other: blob_fixup):
        self.__functions += other.__functions
        self.__create_tmp_dir = self.__create_tmp_dir or other.__create_tmp_dir

    def patchelf_version(self, version: str) -> blob_fixup:
        self.__patchelf_path = patchelf_version_path_map[version]
        return self

    def replace_needed_impl(
        self,
        from_lib: str,
        to_lib: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        run_cmd(
            [
                self.__patchelf_path,
                '--replace-needed',
                from_lib,
                to_lib,
                file_path,
            ]
        )

    def replace_needed(self, from_lib: str, to_lib: str) -> blob_fixup:
        if len(from_lib) >= len(to_lib):
            to_lib = to_lib.ljust(len(from_lib), '\x00')
            impl = partial(
                self.binary_regex_replace_impl,
                from_lib.encode(),
                to_lib.encode(),
            )
            return self.call(impl)

        impl = partial(self.replace_needed_impl, from_lib, to_lib)
        return self.call(impl)

    def add_needed_impl(
        self,
        lib: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        if file_needs_lib(file_path, lib):
            return

        run_cmd([self.__patchelf_path, '--add-needed', lib, file_path])

    def add_needed(self, lib: str) -> blob_fixup:
        impl = partial(self.add_needed_impl, lib)
        return self.call(impl)

    def remove_needed_impl(
        self,
        lib: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        run_cmd([self.__patchelf_path, '--remove-needed', lib, file_path])

    def remove_needed(self, lib: str) -> blob_fixup:
        impl = partial(self.remove_needed_impl, lib)
        return self.call(impl)

    def clear_symbol_version_impl(
        self,
        symbol: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        run_cmd(
            [self.__patchelf_path, '--clear-symbol-version', symbol, file_path]
        )

    def clear_symbol_version(self, symbol: str) -> blob_fixup:
        impl = partial(self.clear_symbol_version_impl, symbol)
        return self.call(impl)

    def fix_soname_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        run_cmd(
            [self.__patchelf_path, '--set-soname', file.basename, file_path]
        )

    def fix_soname(self) -> blob_fixup:
        return self.call(self.fix_soname_impl)

    def __get_patches(self, ctx: BlobFixupCtx, module_patches_path: str):
        patches_path = path.join(ctx.module_dir, module_patches_path)

        if path.isfile(patches_path):
            return [patches_path]

        assert path.isdir(patches_path)

        patches: List[str] = []
        for f in os.scandir(patches_path):
            if f.name.endswith('.patch'):
                patches.append(f.path)

        patches.sort()

        return patches

    def __get_patch_affected_files(self, patch: str) -> List[str]:
        output = run_cmd(
            ['git', '--work-tree', os.devnull, 'apply', '--numstat', patch]
        )

        files: List[str] = []
        for line in output.strip().splitlines():
            parts = line.split('\t')
            if len(parts) != 3:
                raise ValueError(f'Invalid numstat line {line}')

            _, _, path = parts
            files.append(path)

        return files

    def __get_patches_affected_files(self, patches: List[str]) -> List[str]:
        affected_files: List[str] = []
        for patch in patches:
            affected_files += self.__get_patch_affected_files(patch)
        return affected_files

    def __get_apktool_unpack_args(
        self,
        ctx: BlobFixupCtx,
        patches_path: str,
    ) -> List[str]:
        patches = self.__get_patches(ctx, patches_path)
        affected_files = self.__get_patches_affected_files(patches)

        decode_res = False
        decode_src = False
        decode_manifest = False
        for affected_file in affected_files:
            if affected_file.startswith(APKTOOL_RES_PATH):
                decode_res = True

            if affected_file.startswith(APKTOOL_SRC_PATH_PREFIX):
                decode_src = True

            if affected_file == APKTOOL_ANDROID_MANIFEST_NAME:
                decode_manifest = True

        unpack_args: List[str] = []
        if not decode_res and not decode_manifest:
            unpack_args.append(APKTOOL_NO_RES_ARG)

        if not decode_src:
            unpack_args.append(APKTOOL_NO_SRC_ARG)

        return unpack_args

    def patch_impl(
        self,
        patches_path: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        tmp_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        patches = self.__get_patches(ctx, patches_path)
        assert tmp_dir is not None

        def git_add_files(files: List[str]):
            run_cmd(['git', 'add'] + files)

        # Try to apply the changes in reverse, so that they apply cleanly
        # forward
        with TemporaryWorkingDirectory(tmp_dir):
            run_cmd(['git', 'init'])
            all_files = list(
                filter(
                    os.path.exists, self.__get_patches_affected_files(patches)
                )
            )
            git_add_files(all_files)
            run_cmd(['git', 'commit', '-m', 'Initial commit'])

            for patch in patches[::-1]:
                with suppress(Exception):
                    run_cmd(
                        [
                            'git',
                            'apply',
                            '--verbose',
                            '--reverse',
                            patch,
                        ]
                    )
                    patch_files = self.__get_patch_affected_files(patch)
                    git_add_files(patch_files)
                    run_cmd(['git', 'commit', '-m', f'Revert: "{patch}"'])

            for patch in patches:
                try:
                    run_cmd(['git', 'apply', '--reject', patch])
                    patch_files = self.__get_patch_affected_files(patch)
                    git_add_files(patch_files)
                    run_cmd(['git', 'commit', '-m', f'Apply: "{patch}"'])
                except ValueError as e:
                    color_print(
                        f'Failed to apply patch {patch}',
                        color=Color.RED,
                    )
                    color_print('Git history:', color=Color.RED)
                    output = run_cmd(['git', 'log'])
                    print(output)
                    raise e

    def patch_dir(self, patches_path: str) -> blob_fixup:
        impl = partial(self.patch_impl, patches_path)
        return self.call(impl, need_tmp_dir=True)

    def copy_file_to_tmp_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        tmp_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        assert tmp_dir is not None
        shutil.copy(file_path, tmp_dir)

    def copy_file_to_tmp(self) -> blob_fixup:
        return self.call(self.copy_file_to_tmp_impl, need_tmp_dir=True)

    def copy_file_from_tmp_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        tmp_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        assert tmp_dir is not None
        tmp_file_path = path.join(tmp_dir, file.basename)
        shutil.copy(tmp_file_path, file_path)

    def copy_file_from_tmp(self) -> blob_fixup:
        return self.call(self.copy_file_from_tmp_impl, need_tmp_dir=True)

    def patch_file(self, patches_path: str) -> blob_fixup:
        self.copy_file_to_tmp()
        self.patch_dir(patches_path)
        self.copy_file_from_tmp()
        return self

    def apktool_unpack_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        patches_path: str,
        tmp_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        assert tmp_dir is not None

        unpack_args = self.__get_apktool_unpack_args(ctx, patches_path)

        run_cmd(
            [
                java_path,
                '-jar',
                apktool_path,
                'd',
                file_path,
                '-o',
                tmp_dir,
                '-f',
            ]
            + list(unpack_args)
        )

    def apktool_unpack(
        self,
        patches_path: str,
    ) -> blob_fixup:
        impl = partial(
            self.apktool_unpack_impl,
            patches_path=patches_path,
        )
        return self.call(impl)

    def apktool_pack_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        tmp_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        assert tmp_dir is not None

        run_cmd(
            [
                java_path,
                '-jar',
                apktool_path,
                'b',
                tmp_dir,
                '-o',
                file_path,
            ]
        )

    def apktool_pack(self) -> blob_fixup:
        return self.call(self.apktool_pack_impl, need_tmp_dir=True)

    def stripzip_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        run_cmd(
            [
                stripzip_path,
                file_path,
            ]
        )

    def stripzip(self):
        return self.call(self.stripzip_impl)

    def apktool_patch(
        self,
        patches_path: str,
        *args: Any,
    ) -> blob_fixup:
        if args:
            color_print(
                'apktool_patch() no longer takes custom arguments',
                color=Color.YELLOW,
            )

        self.apktool_unpack(patches_path)
        self.patch_dir(patches_path)
        self.apktool_pack()
        self.stripzip()
        return self

    def strip_debug_sections_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        run_cmd(
            [
                llvm_strip_path,
                '--strip-debug',
                file_path,
            ]
        )

    def strip_debug_sections(self) -> blob_fixup:
        return self.call(self.strip_debug_sections_impl)

    def regex_replace_impl(
        self,
        pattern: str,
        replacement: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        with open(file_path, 'r', newline='', encoding='utf-8') as f:
            data = f.read()

        data = re.sub(pattern, replacement, data)

        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            f.write(data)

    def regex_replace(self, search: str, replace: str) -> blob_fixup:
        impl = partial(self.regex_replace_impl, search, replace)
        return self.call(impl)

    def binary_regex_replace_impl(
        self,
        pattern: bytes,
        replacement: bytes,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        with open(file_path, 'rb') as f:
            data = f.read()

        data = re.sub(pattern, replacement, data)

        with open(file_path, 'wb') as f:
            f.write(data)

    def binary_regex_replace(self, search: bytes, replace: bytes) -> blob_fixup:
        impl = partial(self.binary_regex_replace_impl, search, replace)
        return self.call(impl)

    def sig_replace_impl(
        self,
        pattern: bytes,
        replacement: bytes,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        with open(file_path, 'rb+') as f:
            data = f.read()
            match = re.search(pattern, data)

            if match is not None:
                f.seek(match.start(0))
                f.write(replacement)

    def sig_replace(self, pattern_str: str, replacement_str: str) -> blob_fixup:
        pattern = bytes()
        replacement = bytes.fromhex(replacement_str)

        for byte_str in pattern_str.split():
            if byte_str == '??':
                pattern += b'.'
                continue

            if len(byte_str) != 2:
                raise ValueError(f'Bad byte string length at {byte_str}')

            byte = bytes.fromhex(byte_str)
            pattern += re.escape(byte)

        fn = partial(self.sig_replace_impl, pattern, replacement)
        return self.call(fn)

    def fix_xml_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        lines: list[str] = []
        with open(file_path, 'r', newline='', encoding='utf-8') as f:
            for line in f:
                if line.startswith('<?xml version'):
                    lines.insert(0, line)
                    continue

                lines.append(line)

        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            f.writelines(lines)

    def fix_xml(self) -> blob_fixup:
        return self.call(self.fix_xml_impl)

    def add_line_if_missing_impl(
        self,
        text: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args: Any,
        **kwargs: Any,
    ):
        with open(file_path, 'r+', newline='', encoding='utf-8') as f:
            data = f.read()
            if text not in data.splitlines():
                if data[-1] == '\n':
                    f.write(f'{text}\n')
                else:
                    f.write(f'\n{text}')

    def add_line_if_missing(self, text: str) -> blob_fixup:
        fn = partial(self.add_line_if_missing_impl, text)
        return self.call(fn)

    def run(self, ctx: BlobFixupCtx, file: File, file_path: str) -> bool:
        def run(tmp_dir: Optional[str] = None):
            for function, args, kwargs in self.__functions:
                function(ctx, file, file_path, *args, tmp_dir=tmp_dir, **kwargs)

        if self.__create_tmp_dir:
            with tempfile.TemporaryDirectory() as tmp_dir:
                run(tmp_dir)
        else:
            run()

        return True


blob_fixup_fn_type = blob_fixup
blob_fixups_user_type = fixups_user_type[blob_fixup_fn_type]
blob_fixups_type = fixups_type[blob_fixup_fn_type]
