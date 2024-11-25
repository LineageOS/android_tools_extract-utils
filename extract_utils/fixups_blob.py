#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
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
from typing import List, Optional, Protocol

from extract_utils.elf import file_needs_lib
from extract_utils.file import File
from extract_utils.fixups import fixups_type, fixups_user_type
from extract_utils.tools import (
    DEFAULT_PATCHELF_VERSION,
    apktool_path,
    java_path,
    patchelf_version_path_map,
    stripzip_path,
)
from extract_utils.utils import TemporaryWorkingDirectory, run_cmd


class BlobFixupCtx:
    def __init__(self, module_dir: str):
        self.module_dir = module_dir


class blob_fixup_fn_impl_type(Protocol):
    def __call__(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir: Optional[str] = None,
        **kwargs,
    ): ...


class blob_fixup:
    def __init__(self):
        self.__functions: List[tuple[blob_fixup_fn_impl_type, tuple, dict]] = []
        self.__create_tmp_dir = False

        self.__patchelf_path = patchelf_version_path_map[
            DEFAULT_PATCHELF_VERSION
        ]

    def call(
        self,
        fn: blob_fixup_fn_impl_type,
        *args,
        need_tmp_dir=True,
        **kwargs,
    ) -> blob_fixup:
        self.__functions.append((fn, args, kwargs))
        if need_tmp_dir:
            self.__create_tmp_dir = True
        return self

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
        *args,
        **kwargs,
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
        *args,
        **kwargs,
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
        *args,
        **kwargs,
    ):
        run_cmd([self.__patchelf_path, '--remove-needed', lib, file_path])

    def remove_needed(self, lib: str) -> blob_fixup:
        impl = partial(self.remove_needed_impl, lib)
        return self.call(impl)

    def fix_soname_impl(
        self, ctx: BlobFixupCtx, file: File, file_path: str, *args, **kwargs
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

        patches = []
        for f in os.scandir(patches_path):
            if f.name.endswith('.patch'):
                patches.append(f.path)

        patches.sort()

        return patches

    def patch_impl(
        self,
        patches_path: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kwargs,
    ):
        patches = self.__get_patches(ctx, patches_path)
        assert tmp_dir is not None

        # Try to apply the changes in reverse, so that they apply cleanly
        # forward
        with TemporaryWorkingDirectory(tmp_dir):
            run_cmd(['git', 'init'])
            run_cmd(['git', 'add', '.'])
            run_cmd(['git', 'commit', '-m', 'Initial commit'])

            with suppress(Exception):
                run_cmd(
                    [
                        'git',
                        'apply',
                        '--reverse',
                        '--check',
                    ]
                    + patches[::-1]
                )
                return

            run_cmd(['git', 'apply'] + patches)

    def patch_dir(self, patches_path: str) -> blob_fixup:
        impl = partial(self.patch_impl, patches_path)
        return self.call(impl, need_tmp_dir=True)

    def copy_file_to_tmp_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kwargs,
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
        *args,
        tmp_dir=None,
        **kwargs,
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
        unpack_args: List[str],
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kwargs,
    ):
        assert tmp_dir is not None

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
            + unpack_args
        )

    def apktool_unpack(self, unpack_args: List[str]) -> blob_fixup:
        impl = partial(self.apktool_unpack_impl, unpack_args)
        return self.call(impl)

    def apktool_pack_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kwargs,
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
        self, ctx: BlobFixupCtx, file: File, file_path: str, *args, **kwargs
    ):
        run_cmd(
            [
                stripzip_path,
                file_path,
            ]
        )

    def stripzip(self):
        return self.call(self.stripzip_impl)

    def apktool_patch(self, patches_path: str, *args) -> blob_fixup:
        self.apktool_unpack(list(args))
        self.patch_dir(patches_path)
        self.apktool_pack()
        self.stripzip()
        return self

    def regex_replace_impl(
        self,
        pattern: str,
        replacement: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        **kwargs,
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
        *args,
        **kwargs,
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
        *args,
        **kwargs,
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
        *args,
        **kwargs,
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
        *args,
        **kwargs,
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
