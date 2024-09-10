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


def get_file_arch(file):
    try:
        with open(file.path, 'rb') as f:
            elf = ELFFile(f)
            return elf['e_machine'], elf.elfclass
    except ELFError:
        return None, None


def get_file_arch_bits(file):
    arch, bits = get_file_arch(file)
    if arch is None:
        return None, None

    mapping = ARCH_BITS_TARGET_MAP[arch]

    return mapping, bits


def get_file_deps(file):
    libs = []

    with open(file.path, 'rb') as f:
        elf = ELFFile(f)

        for segment in elf.iter_segments():
            if segment.header.p_type == 'PT_DYNAMIC':
                for t in segment.iter_tags():
                    if t.entry.d_tag == 'DT_NEEDED':
                        libs.append(t.needed.removesuffix('.so'))
                break

    return libs
