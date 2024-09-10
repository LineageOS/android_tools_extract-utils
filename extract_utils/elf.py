#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from typing import Generator, List, Optional, Tuple
from elftools.elf.elffile import ELFFile
from elftools.elf.dynamic import DynamicSection
from elftools.common.exceptions import ELFError

ARCH_BITS_TARGET_MAP = {
    'EM_ARM': 'android_arm',
    'EM_QDSP6': 'android_arm',
    'EM_AARCH64': 'android_arm64',
    'EM_386': 'android_x86',
    'EM_X86_64': 'android_x86_64',
}


def __get_elf_libs(elf: ELFFile) -> Generator[str, None, None] | None:
    section = elf.get_section_by_name('.dynamic')
    if not section:
        return None

    assert isinstance(section, DynamicSection)

    for t in section.iter_tags():
        if not t.entry.d_tag == 'DT_NEEDED':
            continue

        # TODO: figure out proper typing for this
        lib = t.needed.removesuffix('.so')  # type: ignore
        yield lib


def file_needs_lib(file_path: str, lib) -> bool:
    try:
        with open(file_path, 'rb') as f:
            elf = ELFFile(f)
            libs = __get_elf_libs(elf)
            if libs is None:
                return False

            for l in libs:
                if l == lib:
                    return True
    except ELFError:
        pass

    return False


def get_file_arch_bits_libs(file_path: str, get_libs: bool) -> \
        Tuple[str, int, Optional[List[str]]] | \
        Tuple[None, None, None]:
    try:
        with open(file_path, 'rb') as f:
            elf = ELFFile(f)
            machine = elf['e_machine']
            arch = ARCH_BITS_TARGET_MAP[machine]
            bits = elf.elfclass
            libs = None

            if get_libs:
                libs = __get_elf_libs(elf)
                if libs is not None:
                    libs = list(libs)

            return arch, bits, libs
    except ELFError:
        return None, None, None


def get_file_arch_bits(file_path):
    arch, bits, _ = get_file_arch_bits_libs(file_path, False)
    return arch, bits
