#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from typing import Generator, Iterable, List, Optional, Tuple
from elftools.common.exceptions import ELFError
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

SO_SUFFIX = '.so'
SO_SUFFIX_LEN = len(SO_SUFFIX)


def __get_elf_libs(elf: ELFFile) -> Generator[str, None, None] | None:
    section = elf.get_section_by_name('.dynamic')
    if not section:
        return None

    assert isinstance(section, DynamicSection)

    for t in section.iter_tags():
        if not t.entry.d_tag == 'DT_NEEDED':
            continue

        # TODO: figure out proper typing for this
        needed = t.needed  # type: ignore
        assert isinstance(needed, str)
        yield needed


def file_needs_lib(file_path: str, needed_lib: str) -> bool:
    try:
        with open(file_path, 'rb') as f:
            elf = ELFFile(f)
            libs = __get_elf_libs(elf)

            if libs is None:
                return False

            return any(lib == needed_lib for lib in libs)
    except ELFError:
        pass

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
    file_path: str, get_libs: bool
) -> Tuple[str, int, Optional[List[str]]] | Tuple[None, None, None]:
    try:
        with open(file_path, 'rb') as f:
            elf = ELFFile(f)
            libs = None

            if get_libs:
                libs = __get_elf_libs(elf)

            if libs is not None:
                libs = list(libs)

            return elf['e_machine'], elf.elfclass, libs
    except ELFError:
        return None, None, None


def get_file_machine_bits(file_path):
    arch, bits, _ = get_file_machine_bits_libs(file_path, False)
    return arch, bits
