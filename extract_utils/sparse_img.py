#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
from ctypes import Structure, c_uint16, c_uint32, sizeof
from mmap import ACCESS_READ, MAP_PRIVATE, mmap
from os import SEEK_CUR, SEEK_SET, path
from typing import BinaryIO, List

from extract_utils.utils import read_mmap_chunked, write_zero

SPARSE_HEADER_MAGIC = 0xED26FF3A

SPARSE_HEADER_MAJOR_VER = 1
SPARSE_HEADER_MINOR_VER = 0


class SparseHeader(Structure):
    _fields_ = [
        # 0xed26ff3a
        ('magic', c_uint32),
        # (0x1) - reject images with higher major versions
        ('major_version', c_uint16),
        # (0x0) - allow images with higher minor versions
        ('minor_version', c_uint16),
        # 28 bytes for first revision of the file format
        ('file_hdr_sz', c_uint16),
        # 12 bytes for first revision of the file format
        ('chunk_hdr_sz', c_uint16),
        # block size in bytes, must be a multiple of 4 (4096)
        ('blk_sz', c_uint32),
        # total blocks in the non-sparse output image
        ('total_blks', c_uint32),
        # total chunks in the sparse input image
        ('total_chunks', c_uint32),
        # CRC32 checksum of the original data
        ('image_checksum', c_uint32),
    ]


class SparseChunkHeader(Structure):
    _fields_ = [
        # 0xCAC1 -> raw; 0xCAC2 -> fill; 0xCAC3 -> don't care, 0xCAC4 -> CRC32
        ('chunk_type', c_uint16),
        # Reserved field (unused, typically 0)
        ('reserved', c_uint16),
        # Size of the chunk in blocks in the output image
        ('chunk_sz', c_uint32),
        # Total size of the chunk in bytes, including the header and data
        ('total_sz', c_uint32),
    ]


class UnsparseError(Exception):
    pass


class ChunkType:
    RAW = 0xCAC1
    FILL = 0xCAC2
    DONT_CARE = 0xCAC3
    CRC32 = 0xCAC4


def unsparse_image(f: BinaryIO, o: BinaryIO, first_chunk_dont_care: bool):
    mm = mmap(f.fileno(), 0, access=ACCESS_READ | MAP_PRIVATE)

    header = SparseHeader.from_buffer(mm)

    if header.magic != SPARSE_HEADER_MAGIC:
        raise UnsparseError(
            f'Invalid magic header {hex(header.magic)}, '
            f'expected {hex(SPARSE_HEADER_MAGIC)}'
        )

    if header.major_version != SPARSE_HEADER_MAJOR_VER:
        raise UnsparseError(
            f'Unsupported major version {header.major_version}, '
            f'expected {SPARSE_HEADER_MAJOR_VER}'
        )

    if header.minor_version != SPARSE_HEADER_MINOR_VER:
        raise UnsparseError(
            f'Unsupported minor version {header.minor_version}, '
            f'expected {SPARSE_HEADER_MINOR_VER}'
        )

    if header.file_hdr_sz < sizeof(SparseHeader):
        raise UnsparseError(
            f'Invalid file header size {header.file_hdr_sz}, '
            f'expected {sizeof(SparseHeader)}',
        )

    if header.chunk_hdr_sz < sizeof(SparseChunkHeader):
        raise UnsparseError(
            f'Invalid chunk header size {header.chunk_hdr_sz}, '
            f'expected {sizeof(SparseChunkHeader)}',
        )

    if not header.blk_sz or header.blk_sz % 4 != 0:
        raise UnsparseError(
            f'Invalid block size {header.blk_sz}, expected multiple of 4'
        )

    total_size = header.blk_sz * header.total_blks
    o.truncate(total_size)
    o.seek(0, SEEK_SET)
    offset = header.file_hdr_sz

    chunk_data_size = 0
    for i in range(header.total_chunks):
        offset += chunk_data_size
        chunk_header = SparseChunkHeader.from_buffer(mm, offset)
        offset += header.chunk_hdr_sz

        # First chunk needs to be a don't care chunk if this is not the
        # first super.img sparse chunk because the output file
        # needs to be seeked to the correct offset
        if i == 0 and first_chunk_dont_care:
            assert chunk_header.chunk_type == ChunkType.DONT_CARE

        chunk_data_size = chunk_header.total_sz - header.chunk_hdr_sz
        chunk_size_in_bytes = chunk_header.chunk_sz * header.blk_sz

        if chunk_header.chunk_type == ChunkType.RAW:
            assert chunk_data_size == chunk_size_in_bytes

            for data_chunk in read_mmap_chunked(mm, chunk_data_size, offset):
                o.write(data_chunk)
        elif chunk_header.chunk_type == ChunkType.FILL:
            assert chunk_data_size == 4
            fill_value = mm[offset : offset + chunk_data_size]

            if fill_value == b'\x00\x00\x00\x00':
                write_zero(o, chunk_size_in_bytes)
            else:
                for _ in range(chunk_size_in_bytes // chunk_data_size):
                    o.write(fill_value)
        elif chunk_header.chunk_type == ChunkType.DONT_CARE:
            o.seek(chunk_size_in_bytes, SEEK_CUR)
        elif chunk_header.chunk_type == ChunkType.CRC32:
            pass
        else:
            raise UnsparseError(
                f'Unknown chunk type {hex(chunk_header.chunk_type)}'
            )


def unsparse_images(input_paths: List[str], output_path: str):
    if path.exists(output_path):
        os.remove(output_path)

    with open(output_path, 'wb') as o:
        is_first = True
        for input_path in input_paths:
            with open(input_path, 'rb') as i:
                unsparse_image(i, o, first_chunk_dont_care=not is_first)
            is_first = False
