#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
import tempfile

from functools import partial
from os import path
from typing import List, Protocol, TypeVar

from extract_utils.elf import file_needs_lib
from extract_utils.file import File
from extract_utils.fixups import fixups_user_type, fixups_type
from extract_utils.tools import (
    DEFAULT_PATCHELF_VERSION,
    apktool_path,
    java_path,
    patchelf_version_path_map,
    stripzip_path,
)
from extract_utils.utils import run_cmd


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
        tmp_dir: str | None = None,
        **kwargs,
    ): ...


class blob_fixup:
    def __init__(self):
        self.__functions: List[blob_fixup_fn_impl_type] = []
        self.__create_tmp_dir = False

        self.__patchelf_path = patchelf_version_path_map[
            DEFAULT_PATCHELF_VERSION
        ]

    def call(
        self,
        fn: blob_fixup_fn_impl_type,
        need_tmp_dir=True,
    ) -> blob_fixup:
        self.__functions.append(fn)
        if need_tmp_dir:
            self.__create_tmp_dir = True
        return self

    def patchelf_version(self, version: str):
        self.__patchelf_path = patchelf_version_path_map[version]

    def replace_needed_impl(
        self,
        from_lib: str,
        to_lib: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        **kargs,
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

    def replace_needed(self, from_lib: str, to_lib: str):
        impl = partial(self.replace_needed_impl, from_lib, to_lib)
        return self.call(impl)

    def add_needed_impl(
        self,
        lib: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        **kargs,
    ):
        if file_needs_lib(file_path, lib):
            return

        run_cmd([self.__patchelf_path, '--add-needed', lib, file_path])

    def add_needed(self, lib: str):
        impl = partial(self.add_needed_impl, lib)
        return self.call(impl)

    def remove_needed_impl(
        self,
        lib: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        **kargs,
    ):
        run_cmd([self.__patchelf_path, '--remove-needed', lib, file_path])

    def remove_needed(self, lib: str):
        impl = partial(self.remove_needed_impl, lib)
        return self.call(impl)

    def fix_soname_impl(
        self, ctx: BlobFixupCtx, file: File, file_path: str, *args, **kargs
    ):
        run_cmd(
            [self.__patchelf_path, '--set-soname', file.basename, file_path]
        )

    def fix_soname(self):
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
        **kargs,
    ):
        patches = self.__get_patches(ctx, patches_path)
        assert tmp_dir is not None

        base_cmd = ['git', 'apply', '--unsafe-path', '--directory', tmp_dir]

        # Try to apply the changes in reverse, so that they apply cleanly
        # forward

        try:
            reversed_patches = list(reversed(patches))
            run_cmd(base_cmd + ['--reverse', '--check'] + reversed_patches)
        except Exception:
            return

        run_cmd(base_cmd + patches)

    def patch_dir(self, patches_path: str):
        impl = partial(self.patch_impl, patches_path)
        return self.call(impl, need_tmp_dir=True)

    def copy_file_to_tmp_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kargs,
    ):
        assert tmp_dir is not None
        shutil.copy(file_path, tmp_dir)

    def copy_file_to_tmp(self):
        return self.call(self.copy_file_to_tmp_impl, need_tmp_dir=True)

    def copy_file_from_tmp_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kargs,
    ):
        assert tmp_dir is not None
        tmp_file_path = path.join(tmp_dir, file.basename)
        shutil.copy(tmp_file_path, file_path)

    def copy_file_from_tmp(self):
        return self.call(self.copy_file_from_tmp_impl, need_tmp_dir=True)

    def patch_file(self, patches_path: str):
        self.copy_file_to_tmp()
        self.patch_dir(patches_path)
        self.copy_file_from_tmp()
        return self

    def apktool_unpack_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kargs,
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
        )

    def apktool_unpack(self):
        return self.call(self.apktool_unpack_impl, need_tmp_dir=True)

    def apktool_pack_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        tmp_dir=None,
        **kargs,
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

    def apktool_pack(self):
        return self.call(self.apktool_pack_impl, need_tmp_dir=True)

    def stripzip_impl(
        self, ctx: BlobFixupCtx, file: File, file_path: str, *args, **kargs
    ):
        run_cmd(
            [
                stripzip_path,
                file_path,
            ]
        )

    def stripzip(self):
        return self.call(self.stripzip_impl)

    def apktool_patch(self, patches_path: str):
        self.apktool_unpack()
        self.patch_dir(patches_path)
        self.apktool_pack()
        self.stripzip()
        return self

    def regex_replace_impl(
        self,
        text: str,
        replacement: str,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        **kargs,
    ):
        with open(file_path, 'r') as f:
            data = f.read()

        data = re.sub(text, replacement, data)

        with open(file_path, 'w') as f:
            f.write(data)

    def regex_replace(self, search: str, replace: str):
        impl = partial(self.regex_replace_impl, search, replace)
        return self.call(impl)

    def fix_xml_impl(
        self,
        ctx: BlobFixupCtx,
        file: File,
        file_path: str,
        *args,
        **kargs,
    ):
        lines = []
        with open(file_path, 'r') as f:
            for line in f:
                if line.startswith('<?xml version'):
                    lines.insert(0, line)
                    continue

                lines.append(line)

        with open(file_path, 'w') as f:
            f.writelines(lines)

    def fix_xml(self):
        return self.call(self.fix_xml_impl)

    def run(self, ctx: BlobFixupCtx, file: File, file_path: str) -> bool:
        def run(tmp_dir: str | None = None):
            for function in self.__functions:
                function(ctx, file, file_path, tmp_dir=tmp_dir)

        if self.__create_tmp_dir:
            with tempfile.TemporaryDirectory() as tmp_dir:
                run(tmp_dir)
        else:
            run()

        return True


T = TypeVar('T')

blob_fixup_fn_type = blob_fixup
blob_fixups_user_type = fixups_user_type[blob_fixup_fn_type]
blob_fixups_type = fixups_type[blob_fixup_fn_type]
