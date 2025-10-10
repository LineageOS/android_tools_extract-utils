#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from ctypes import Structure, c_char, c_uint32
from mmap import ACCESS_READ, MAP_PRIVATE, mmap
from os import path

from extract_utils.utils import read_mmap_chunked

MOTO_PIV_MAGIC = b'MOTO\x13W\x9b\x00MOT_PIV_FULL256'


class MotoPivHeader(Structure):
    _fields_ = [
        ('magic', c_char * len(MOTO_PIV_MAGIC)),
        ('offset', c_uint32),
    ]


def extract_moto_piv(file_path: str, output_path: str):
    file_name = path.basename(file_path)
    partition, _ = path.splitext(file_name)
    output_file_name = f'{partition}.raw'
    output_file_path = path.join(output_path, output_file_name)
    with open(file_path, 'rb') as i:
        mm = mmap(i.fileno(), 0, access=ACCESS_READ | MAP_PRIVATE)
        header = MotoPivHeader.from_buffer(mm)
        size = mm.size() - header.offset

        with open(output_file_path, 'wb') as o:
            for data_chunk in read_mmap_chunked(mm, size, header.offset):
                o.write(data_chunk)
