#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from typing import Callable, List, Optional

from extract_utils.fixups import fixups_type, fixups_user_type

lib_fixup_fn_type = Callable[[str, str], Optional[str]]
lib_fixups_user_type = fixups_user_type[lib_fixup_fn_type]
lib_fixups_type = fixups_type[lib_fixup_fn_type]


libs_clang_rt_ubsan = (
    'libclang_rt.ubsan_standalone-arm-android',
    'libclang_rt.ubsan_standalone-aarch64-android',
)


libs_proto_3_9_1 = ('libprotobuf-cpp-lite-3.9.1', 'libprotobuf-cpp-full-3.9.1')
libs_proto_21_12 = ('libprotobuf-cpp-lite-21.12', 'libprotobuf-cpp-full-21.12')
libs_proto_unversioned = ('libprotobuf-cpp-lite', 'libprotobuf-cpp-full')


def lib_fixup_remove(lib: str, *args, **kwargs):
    return ''


def lib_fixup_remove_arch_suffix(lib: str, *args, **kwargs):
    suffixes = ['-arm-android', '-aarch64-android']
    for suffix in suffixes:
        if lib.endswith(suffix):
            return lib[: -len(suffix)]

    assert False


def lib_fixup_vendorcompat(lib: str, partition: str, *args, **kwargs):
    return f'{lib}-vendorcompat' if partition in ['odm', 'vendor'] else lib


def lib_fixup_remove_proto_version_suffix(lib: str, *args, **kwargs):
    return lib.rsplit('-', 1)[0]


lib_fixups: lib_fixups_user_type = {
    libs_clang_rt_ubsan: lib_fixup_remove_arch_suffix,
    libs_proto_3_9_1: lib_fixup_vendorcompat,
    libs_proto_21_12: lib_fixup_remove_proto_version_suffix,
    libs_proto_unversioned: lib_fixup_vendorcompat,
}


def run_lib_fixup(
    fixups: Optional[lib_fixups_type], lib: str, partition: str
) -> str:
    if fixups is None:
        return lib

    lib_fixup_fn = fixups.get(lib)
    if lib_fixup_fn is None:
        return lib

    fixed_up_lib = lib_fixup_fn(lib, partition)
    if fixed_up_lib is None:
        return lib

    return fixed_up_lib


def run_libs_fixup(
    fixups: lib_fixups_type,
    libs: Optional[List[str]],
    partition: str,
):
    if libs is None:
        return None

    if not fixups:
        return libs

    fixed_libs = []
    for lib in libs:
        fixed_lib = run_lib_fixup(fixups, lib, partition)
        if fixed_lib == '':
            continue

        fixed_libs.append(fixed_lib)

    return fixed_libs
