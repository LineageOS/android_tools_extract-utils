#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError

ARCH_BITS_TARGET_MAP = {
    'EM_ARM': 'android_arm',
    'EM_QDSP6': 'android_arm',
    'EM_AARCH64': 'android_arm64',
    'EM_386': 'android_x86',
    'EM_X86_64': 'android_x86_64',
}


def get_elf_libs(elf):
    section = elf.get_section_by_name('.dynamic')
    if not section:
        return None

    libs = []
    for t in section.iter_tags():
        if t.entry.d_tag == 'DT_NEEDED':
            libs.append(t.needed.removesuffix('.so'))

    return libs


def get_file_arch_bits_libs(file, get_libs=False):
    try:
        with open(file.path, 'rb') as f:
            elf = ELFFile(f)
            machine = elf['e_machine']
            arch = ARCH_BITS_TARGET_MAP[machine]
            bits = str(elf.elfclass)
            libs = None

            if get_libs:
                libs = get_elf_libs(elf)

            return arch, bits, libs
    except ELFError:
        return None, None, None


def get_file_arch_bits(file):
    arch, bits, _ = get_file_arch_bits_libs(file)
    return arch, bits
