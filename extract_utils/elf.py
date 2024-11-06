#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from contextlib import suppress
from typing import Iterable, List, Optional, Tuple

from extract_utils.elf_parser import EM, ELFError, ELFFile

SO_SUFFIX = '.so'
SO_SUFFIX_LEN = len(SO_SUFFIX)


def file_needs_lib(file_path: str, needed_lib: str) -> bool:
    with suppress(ELFError):
        with open(file_path, 'rb') as f:
            elf = ELFFile(f)
            libs = elf.get_libs()
            return any(lib == needed_lib for lib in libs)

    return False


def remove_libs_so_ending(libs: None | Iterable[str]) -> None | List[str]:
    if libs is None:
        return None

    so_removed_libs = []
    for lib in libs:
        assert lib.endswith(SO_SUFFIX)
        lib = lib[:-SO_SUFFIX_LEN]
        so_removed_libs.append(lib)

    return so_removed_libs


def get_file_machine_bits_libs(
    file_path: str, gen_deps: bool
) -> Tuple[EM, int, Optional[List[str]]] | Tuple[None, None, None]:
    try:
        with open(file_path, 'rb') as f:
            elf = ELFFile(f)
            machine = elf.machine
            bits = elf.bits
            libs = None

            if gen_deps:
                libs = elf.get_libs()

            if libs is not None:
                libs = list(libs)

            return machine, bits, libs
    except ELFError:
        return None, None, None
