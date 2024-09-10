#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import os
import re
import shutil
import tempfile

from os import path
from typing import Callable, Dict, List, Optional, Protocol, Tuple, TypeVar
from subprocess import run

from extract_utils.elf import file_needs_lib

from .file import File
from .tools import \
    get_apktool_path, \
    get_java_path, \
    get_patchelf_path, \
    get_stripzip_path


class BlobFixupCtx:
    def __init__(self, module_dir: str):
        self.module_dir = module_dir


class blob_fixup_fn_impl_type(Protocol):
    def __call__(self, ctx: BlobFixupCtx, file: File,
                 file_path: str,  *args,
                 tmp_dir: str | None = None, ** kwargs):
        ...


class blob_fixup:
    def __init__(self):
        self.__patchelf_path = get_patchelf_path()
        self.__functions: List[blob_fixup_fn_impl_type] = []
        self.__create_tmp_dir = False

    def run_cmd(self, parts: List[str]):
        run(parts, check=True)

    def call(self, fn: blob_fixup_fn_impl_type, need_tmp_dir=True):
        self.__functions.append(fn)
        if need_tmp_dir:
            self.__create_tmp_dir = True
        return self

    def patchelf_version(self, version: str):
        self.__patchelf_path = get_patchelf_path(version)

    def replace_needed_impl(self, from_lib: str, to_lib: str,
                            ctx: BlobFixupCtx, file: File,
                            file_path: str, *args, **kargs):
        self.run_cmd([
            self.__patchelf_path,
            '--replace-needed',
            from_lib, to_lib,
            file_path])

    def replace_needed(self, from_lib: str, to_lib: str):
        def impl(*args, **kwargs):
            self.replace_needed_impl(from_lib, to_lib, *args, *kwargs)

        return self.call(impl)

    def add_needed_impl(self, lib: str,
                        ctx: BlobFixupCtx, file: File,
                        file_path: str, *args, **kargs):
        if file_needs_lib(file_path, lib):
            return

        self.run_cmd([
            self.__patchelf_path,
            '--add-needed',
            lib,
            file_path])

    def add_needed(self, lib: str):
        def impl(*args, **kwargs):
            self.add_needed_impl(lib, *args, **kwargs)

        return self.call(impl)

    def remove_needed_impl(self, lib: str,
                           ctx: BlobFixupCtx, file: File,
                           file_path: str, *args, **kargs):
        self.run_cmd([
            self.__patchelf_path,
            '--remove-needed',
            lib,
            file_path])

    def remove_needed(self, lib: str):
        def impl(*args, **kwargs):
            self.remove_needed_impl(lib, *args, **kwargs)

        return self.call(impl)

    def fix_soname_impl(self,
                        ctx: BlobFixupCtx, file: File,
                        file_path: str, *args, **kargs):
        self.run_cmd([
            self.__patchelf_path,
            '--set-soname',
            file.basename,
            file_path])

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

    def patch_impl(self, patches_path: str,
                   ctx: BlobFixupCtx, file: File,
                   file_path: str, *args,
                   tmp_dir=None, **kargs):
        patches = self.__get_patches(ctx, patches_path)
        assert tmp_dir is not None
        self.run_cmd([
            'git',
            'apply',
            '--unsafe-path',
            '--directory', tmp_dir,
            *patches
        ])

    def patch_dir(self, patches_path: str):
        def impl(*args, **kwargs):
            self.patch_impl(patches_path, *args, **kwargs)

        return self.call(impl)

    def copy_file_to_tmp_impl(self, ctx: BlobFixupCtx, file: File,
                              file_path: str, *args,
                              tmp_dir=None, **kargs):
        assert tmp_dir is not None
        shutil.copy(file_path, tmp_dir)

    def copy_file_to_tmp(self):
        return self.call(self.copy_file_to_tmp_impl)

    def copy_file_from_tmp_impl(self, ctx: BlobFixupCtx, file: File,
                                file_path: str, *args,
                                tmp_dir=None, **kargs):
        assert tmp_dir is not None
        tmp_file_path = path.join(tmp_dir, file.basename)
        shutil.copy(tmp_file_path, file_path)

    def copy_file_from_tmp(self):
        return self.call(self.copy_file_from_tmp_impl)

    def patch_file(self, patches_path: str):
        self.copy_file_to_tmp()
        self.patch_dir(patches_path)
        self.copy_file_from_tmp()
        return self

    def apktool_unpack_impl(self, ctx: BlobFixupCtx, file: File,
                            file_path: str, *args,
                            tmp_dir=None, **kargs):
        java_path = get_java_path()
        apktool_path = get_apktool_path()

        assert tmp_dir is not None

        self.run_cmd([
            java_path,
            '-jar', apktool_path,
            'd', file_path,
            '-o', tmp_dir,
            '-f',
        ])

    def apktool_unpack(self):
        return self.call(self.apktool_unpack_impl, need_tmp_dir=True)

    def apktool_pack_impl(self, ctx: BlobFixupCtx, file: File,
                          file_path: str, *args,
                          tmp_dir=None, **kargs):
        java_path = get_java_path()
        apktool_path = get_apktool_path()
        assert tmp_dir is not None

        self.run_cmd([
            java_path,
            '-jar', apktool_path,
            'b', tmp_dir,
            '-o', file_path,
        ])

    def apktool_pack(self):
        return self.call(self.apktool_pack_impl, need_tmp_dir=True)

    def stripzip_impl(self, ctx: BlobFixupCtx, file: File,
                      file_path: str, *args, **kargs):
        stripzip_path = get_stripzip_path()
        self.run_cmd([
            stripzip_path,
            file_path,
        ])

    def stripzip(self):
        return self.call(self.stripzip_impl)

    def apktool_patch(self, patches_path: str):
        self.apktool_unpack()
        self.patch_dir(patches_path)
        self.apktool_pack()
        self.stripzip()
        return self

    def regex_replace_impl(self, text: str, replacement: str,
                           ctx: BlobFixupCtx, file: File,
                           file_path: str, *args, **kargs):
        with open(file_path, 'r') as f:
            data = f.read()

        data = re.sub(text, replacement, data)

        with open(file_path, 'w') as f:
            f.write(data)

    def regex_replace(self, search: str, replace: str):
        def impl(*args, **kwargs):
            self.regex_replace_impl(search, replace, *args, **kwargs)

        return self.call(impl)

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

fixups_user_type = Dict[str | Tuple[str, ...], T]
fixups_type = Dict[str, T]

blob_fixup_fn_type = blob_fixup
blob_fixups_user_type = fixups_user_type[blob_fixup_fn_type]
blob_fixups_type = fixups_type[blob_fixup_fn_type]

lib_fixup_fn_type = Callable[[str, str], Optional[str]]
lib_fixups_user_type = fixups_user_type[lib_fixup_fn_type]
lib_fixups_type = fixups_type[lib_fixup_fn_type]


def flatten_fixups(fixups: Optional[fixups_user_type[T]]) -> Optional[fixups_type[T]]:
    if fixups is None:
        return None

    fixups_final: fixups_type = {}

    for entries, value in fixups.items():
        if isinstance(entries, str):
            fixups_final[entries] = value
        elif isinstance(entries, tuple):
            for entry in entries:
                fixups_final[entry] = value
        else:
            assert False

    return fixups_final


libs_clang_rt_ubsan = (
    'libclang_rt.ubsan_standalone-arm-android',
    'libclang_rt.ubsan_standalone-aarch64-android'
)


libs_proto_3_9_1 = (
    'libprotobuf-cpp-lite-3.9.1',
    'libprotobuf-cpp-full-3.9.1'
)


def lib_fixup_remove_arch_suffix(lib: str, *args, **kwargs):
    suffixes = ['-arm-android', '-aarch64-android']
    for suffix in suffixes:
        removed = lib.removesuffix(suffix)
        if removed != lib:
            return removed

    assert False


def lib_fixup_vendorcompat(lib: str, *args, **kwargs):
    return f'{lib}-vendorcompat'


lib_fixups = {
    libs_clang_rt_ubsan: lib_fixup_remove_arch_suffix,
    libs_proto_3_9_1: lib_fixup_vendorcompat,
}


def run_lib_fixup(fixups: lib_fixups_type | None,
                  lib: str, partition: str) -> str:
    if fixups is None:
        return lib

    lib_fixup_fn = fixups.get(lib)
    if lib_fixup_fn is None:
        return lib

    fixed_up_lib = lib_fixup_fn(lib, partition)
    if fixed_up_lib is None:
        return lib

    return fixed_up_lib


def run_libs_fixup(fixups: lib_fixups_type | None,
                   libs: List[str] | None, partition: str):
    if libs is None:
        return None

    if fixups is None:
        return libs

    return [run_lib_fixup(fixups, lib, partition) for lib in libs]


def run_blob_fixup(fixups: Optional[blob_fixups_type],
                   ctx: Optional[BlobFixupCtx], file: File,
                   file_path: Optional[str]) -> bool:
    if fixups is None:
        return False

    blob_fixup_fn = fixups.get(file.dst)
    if blob_fixup_fn is None:
        return False

    if file_path is None:
        return True

    assert isinstance(blob_fixup_fn, blob_fixup)
    assert ctx is not None

    blob_fixup_fn.run(ctx, file, file_path)

    return True
