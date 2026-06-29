#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import inspect
from enum import Enum, auto
from functools import cache
from typing import Any, Callable, List, Optional, Tuple, Union

from extract_utils.file import File
from extract_utils.fixups import fixups_type, fixups_user_type


class LibFixupFlag(Enum):
    SHARED = auto()
    EXCLUDE = auto()


lib_fixup_result_type = Union[
    # no changes
    None,
    # rename or remove if ''
    str,
    # rename and exclude
    Tuple[str, LibFixupFlag],
]
lib_fixup_fn_type = Callable[..., lib_fixup_result_type]
lib_fixups_user_type = fixups_user_type[lib_fixup_fn_type]
lib_fixups_type = fixups_type[lib_fixup_fn_type]


libs_clang_rt_ubsan = (
    'libclang_rt.ubsan_standalone-arm-android',
    'libclang_rt.ubsan_standalone-aarch64-android',
)


libs_proto_3_9_1 = ('libprotobuf-cpp-lite-3.9.1', 'libprotobuf-cpp-full-3.9.1')
libs_proto_4_25_8 = (
    'libprotobuf-cpp-lite-4.25.8',
    'libprotobuf-cpp-full-4.25.8',
)
libs_proto_6_33_5 = (
    'libprotobuf-cpp-lite-6.33.5',
    'libprotobuf-cpp-full-6.33.5',
)
libs_proto_21_12 = ('libprotobuf-cpp-lite-21.12', 'libprotobuf-cpp-full-21.12')
libs_proto_unversioned = ('libprotobuf-cpp-lite', 'libprotobuf-cpp-full')


def lib_fixup_remove(
    lib: str,
    *args: Any,
    **kwargs: Any,
):
    return ''


def lib_fixup_exclude(
    lib: str,
    *args: Any,
    **kwargs: Any,
):
    return lib, LibFixupFlag.EXCLUDE


def lib_fixup_remove_arch_suffix(
    lib: str,
    *args: Any,
    **kwargs: Any,
):
    suffixes = ['-arm-android', '-aarch64-android']
    for suffix in suffixes:
        if lib.endswith(suffix):
            return lib[: -len(suffix)]

    assert False


def lib_fixup_vendorcompat(
    lib: str,
    partition: str,
    *args: Any,
    **kwargs: Any,
):
    return f'{lib}-vendorcompat' if partition in ['odm', 'vendor'] else lib


def lib_fixup_remove_proto_version_suffix(
    lib: str,
    *args: Any,
    **kwargs: Any,
):
    return lib.rsplit('-', 1)[0]


lib_fixups: lib_fixups_user_type = {
    libs_clang_rt_ubsan: lib_fixup_remove_arch_suffix,
    libs_proto_3_9_1: lib_fixup_vendorcompat,
    libs_proto_4_25_8: lib_fixup_vendorcompat,
    libs_proto_6_33_5: lib_fixup_remove_proto_version_suffix,
    libs_proto_21_12: lib_fixup_vendorcompat,
    libs_proto_unversioned: lib_fixup_vendorcompat,
}


@cache
def lib_fixup_accepts_file(fixup_fn: lib_fixup_fn_type) -> bool:
    # Only pass the file kwarg to fixups that explicitly declare it
    file_param = inspect.signature(fixup_fn).parameters.get('file')
    return file_param is not None and file_param.kind in (
        file_param.POSITIONAL_OR_KEYWORD,
        file_param.KEYWORD_ONLY,
    )


def run_lib_fixup(
    fixups: Optional[lib_fixups_type],
    lib: str,
    partition: str,
    file: File,
) -> Tuple[str, LibFixupFlag]:
    if fixups is None:
        return lib, LibFixupFlag.SHARED

    lib_fixup_fn = fixups.get(lib)
    if lib_fixup_fn is None:
        return lib, LibFixupFlag.SHARED

    if lib_fixup_accepts_file(lib_fixup_fn):
        result = lib_fixup_fn(lib, partition, file=file)
    else:
        result = lib_fixup_fn(lib, partition)

    if result is None:
        return lib, LibFixupFlag.SHARED

    if isinstance(result, tuple):
        return result

    return result, LibFixupFlag.SHARED


def run_libs_fixup(
    fixups: lib_fixups_type,
    libs: Optional[List[str]],
    partition: str,
    file: File,
) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    if libs is None:
        return None, None

    if not fixups:
        return libs, None

    fixed_libs: List[str] = []
    excluded_libs: List[str] = []
    for lib in libs:
        fixed_lib, flag = run_lib_fixup(fixups, lib, partition, file)
        if fixed_lib == '':
            continue

        if flag == LibFixupFlag.EXCLUDE:
            excluded_libs.append(fixed_lib)
        else:
            fixed_libs.append(fixed_lib)

    return fixed_libs, excluded_libs
