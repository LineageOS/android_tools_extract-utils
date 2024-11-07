#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import ctypes
from enum import Enum
from io import BufferedReader
from mmap import ACCESS_READ, MAP_PRIVATE, mmap
from typing import Optional

MAG = b'\x7fELF'
E_IDENT_LEN = 16


class EM(int, Enum):
    X86 = 0x3
    ARM = 0x28
    X86_64 = 0x3E
    QDSP6 = 0xA4
    AARCH64 = 0xB7


class EI(int, Enum):
    MAG0 = 0
    MAG3 = 3
    CLASS = 4


class ELFCLASS(int, Enum):
    CLASS_32 = 1
    CLASS_64 = 2


class DT(int, Enum):
    NEEDED = 1
    STRTAB = 5
    STRSZ = 10


class SHT(int, Enum):
    DYNAMIC = 6


class PT(int, Enum):
    LOAD = 1


SO_SUFFIX = '.so'
SO_SUFFIX_LEN = len(SO_SUFFIX)

Elf32_Addr = ctypes.c_uint32
Elf32_Half = ctypes.c_uint16
Elf32_Off = ctypes.c_uint32
Elf32_Sword = ctypes.c_int32
Elf32_Word = ctypes.c_uint32

Elf64_Addr = ctypes.c_uint64
Elf64_Half = ctypes.c_uint16
Elf64_SHalf = ctypes.c_int16
Elf64_Off = ctypes.c_uint64
Elf64_Sword = ctypes.c_int32
Elf64_Word = ctypes.c_uint32
Elf64_Xword = ctypes.c_uint64
Elf64_Sxword = ctypes.c_int64


class Elf_Eident(ctypes.Structure):
    _fields_ = [
        ('ei_mag', (ctypes.c_char) * len(MAG)),
        ('ei_class', ctypes.c_ubyte),
    ]


class Elf32_Ehdr(ctypes.Structure):
    _fields_ = [
        ('e_ident', (ctypes.c_ubyte * E_IDENT_LEN)),
        ('e_type', Elf32_Half),
        ('e_machine', Elf32_Half),
        ('e_version', Elf32_Word),
        ('e_entry', Elf32_Addr),
        ('e_phoff', Elf32_Off),
        ('e_shoff', Elf32_Off),
        ('e_flags', Elf32_Word),
        ('e_ehsize', Elf32_Half),
        ('e_phentsize', Elf32_Half),
        ('e_phnum', Elf32_Half),
        ('e_shentsize', Elf32_Half),
        ('e_shnum', Elf32_Half),
        ('e_shstrndx', Elf32_Half),
    ]


class Elf64_Ehdr(ctypes.Structure):
    _fields_ = [
        ('e_ident', (ctypes.c_ubyte * E_IDENT_LEN)),
        ('e_type', Elf64_Half),
        ('e_machine', Elf64_Half),
        ('e_version', Elf64_Word),
        ('e_entry', Elf64_Addr),
        ('e_phoff', Elf64_Off),
        ('e_shoff', Elf64_Off),
        ('e_flags', Elf64_Word),
        ('e_ehsize', Elf64_Half),
        ('e_phentsize', Elf64_Half),
        ('e_phnum', Elf64_Half),
        ('e_shentsize', Elf64_Half),
        ('e_shnum', Elf64_Half),
        ('e_shstrndx', Elf64_Half),
    ]


class Elf32_Phdr(ctypes.Structure):
    _fields_ = [
        ('p_type', Elf32_Word),
        ('p_offset', Elf32_Off),
        ('p_vaddr', Elf32_Addr),
        ('p_paddr', Elf32_Addr),
        ('p_filesz', Elf32_Word),
        ('p_memsz', Elf32_Word),
        ('p_flags', Elf32_Word),
        ('p_align', Elf32_Word),
    ]


class Elf64_Phdr(ctypes.Structure):
    _fields_ = [
        ('p_type', Elf64_Word),
        ('p_flags', Elf64_Word),
        ('p_offset', Elf64_Off),
        ('p_vaddr', Elf64_Addr),
        ('p_paddr', Elf64_Addr),
        ('p_filesz', Elf64_Xword),
        ('p_memsz', Elf64_Xword),
        ('p_align', Elf64_Xword),
    ]


class Elf32_Shdr(ctypes.Structure):
    _fields_ = [
        ('sh_name', Elf32_Word),
        ('sh_type', Elf32_Word),
        ('sh_flags', Elf32_Word),
        ('sh_addr', Elf32_Addr),
        ('sh_offset', Elf32_Off),
        ('sh_size', Elf32_Word),
        ('sh_link', Elf32_Word),
        ('sh_info', Elf32_Word),
        ('sh_addralign', Elf32_Word),
        ('sh_entsize', Elf32_Word),
    ]


class Elf64_Shdr(ctypes.Structure):
    _fields_ = [
        ('sh_name', Elf64_Word),
        ('sh_type', Elf64_Word),
        ('sh_flags', Elf64_Xword),
        ('sh_addr', Elf64_Addr),
        ('sh_offset', Elf64_Off),
        ('sh_size', Elf64_Xword),
        ('sh_link', Elf64_Word),
        ('sh_info', Elf64_Word),
        ('sh_addralign', Elf64_Xword),
        ('sh_entsize', Elf64_Xword),
    ]


class Elf32_Dyn(ctypes.Structure):
    _fields_ = [
        ('d_tag', ctypes.c_int32),
        ('d_val', ctypes.c_uint32),
    ]


class Elf64_Dyn(ctypes.Structure):
    _fields_ = [
        ('d_tag', ctypes.c_int64),
        ('d_val', ctypes.c_uint64),
    ]


class ELFError(Exception):
    pass


class ELFFile:
    def __init__(self, f: BufferedReader):
        self.mm = mmap(f.fileno(), 0, access=ACCESS_READ | MAP_PRIVATE)

        self.ident = Elf_Eident.from_buffer(self.mm)

        if self.ident.ei_mag != MAG:
            raise ELFError('Invalid file')

        self.ehdr: Elf32_Ehdr | Elf64_Ehdr
        self.shdr_cls: type[Elf32_Shdr | Elf64_Shdr]
        self.phdr_cls: type[Elf32_Phdr | Elf64_Phdr]
        self.dyn_cls: type[Elf32_Dyn | Elf64_Dyn]

        if self.ident.ei_class == ELFCLASS.CLASS_32:
            self.ehdr = Elf32_Ehdr.from_buffer(self.mm)
            self.shdr_cls = Elf32_Shdr
            self.phdr_cls = Elf32_Phdr
            self.dyn_cls = Elf32_Dyn
            self.bits = 32
        elif self.ident.ei_class == ELFCLASS.CLASS_64:
            self.ehdr = Elf64_Ehdr.from_buffer(self.mm)
            self.shdr_cls = Elf64_Shdr
            self.phdr_cls = Elf64_Phdr
            self.dyn_cls = Elf64_Dyn
            self.bits = 64

        self.machine: EM = self.ehdr.e_machine

    def iter_sections(self, kind: Optional[SHT] = None):
        offset = self.ehdr.e_shoff
        for _ in range(self.ehdr.e_shnum):
            shdr = self.shdr_cls.from_buffer(self.mm, offset)
            offset += self.ehdr.e_shentsize

            if kind is None or shdr.sh_type == kind:
                yield shdr

    def iter_section_dyn(self, shdr: Elf32_Shdr | Elf64_Shdr):
        offset = shdr.sh_offset
        end = shdr.sh_offset + shdr.sh_size
        while offset < end:
            dyn = self.dyn_cls.from_buffer(self.mm, offset)
            offset += shdr.sh_entsize
            yield dyn

    def iter_segments(self, kind: Optional[PT] = None):
        offset = self.ehdr.e_phoff
        for _ in range(self.ehdr.e_phnum):
            phdr = self.phdr_cls.from_buffer(self.mm, offset)
            offset += self.ehdr.e_phentsize

            if kind is None or phdr.p_type == kind:
                yield phdr

    def address_to_offset(self, start: int, size: int):
        end = start + size
        for phdr in self.iter_segments(kind=PT.LOAD):
            if start < phdr.p_vaddr:
                continue

            if end > phdr.p_vaddr + phdr.p_filesz:
                continue

            offset = start - phdr.p_vaddr + phdr.p_offset
            end = offset + phdr.p_filesz

            return offset, end

        return None, None

    def dynamic_section_strtab(self, shdr: Elf32_Shdr | Elf64_Shdr):
        strtab_addr = None
        strtab_sz = None
        strtab_offsets = []

        for dyn in self.iter_section_dyn(shdr):
            if dyn.d_tag == DT.STRTAB:
                strtab_addr = dyn.d_val
            elif dyn.d_tag == DT.STRSZ:
                strtab_sz = dyn.d_val
            elif dyn.d_tag == DT.NEEDED:
                strtab_offsets.append(dyn.d_val)

        return strtab_addr, strtab_sz, strtab_offsets

    def get_libs(self):
        shdr_iter = self.iter_sections(SHT.DYNAMIC)
        shdr = next(shdr_iter, None)
        if shdr is None:
            return

        strtab_addr, strtab_sz, offsets = self.dynamic_section_strtab(shdr)

        if strtab_addr is None or strtab_sz is None:
            return

        strtab_offset, strtab_end = self.address_to_offset(
            strtab_addr,
            strtab_sz,
        )

        if strtab_offset is None or strtab_end is None:
            return

        for offset in offsets:
            start = strtab_offset + offset
            end = self.mm.find(b'\0', start, strtab_end)
            if end == -1:
                end = strtab_end
            lib = self.mm[start:end]
            decoded_lib = lib.decode()
            yield decoded_lib
