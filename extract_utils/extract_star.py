#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import struct
from os import path
from typing import BinaryIO

from extract_utils.extract import ExtractCtx

star_firmware_regex = r'(bootloader|radio)\.img'


def get_string(f: BinaryIO, length: int):
    data = f.read(length)
    data = data.strip(b'\0')
    return data.decode()


def get_long(f: BinaryIO):
    data = f.read(8)
    return struct.unpack('Q', data)[0]


def seek_pad(f: BinaryIO, size: int):
    pad = 0
    if size % 4096 != 0:
        pad = 4096 - (size % 4096)
        f.seek(pad, os.SEEK_CUR)


def extract_file(
    input_file: BinaryIO,
    file_name: str,
    length: int,
    output_dir: str,
):
    file_path = path.join(output_dir, file_name)
    data = input_file.read(length)
    with open(file_path, 'wb') as of:
        of.write(data)


def extract_star_firmware(
    ctx: ExtractCtx,
    file_path: str,
    work_dir: str,
    *args,
    **kwargs,
) -> str:
    with open(file_path, 'rb') as f:
        magic = get_string(f, 256)
        if magic != 'SINGLE_N_LONELY':
            raise ValueError(f'{file_path} is not a STAR archive')

        while True:
            name = get_string(f, 248)
            if name == 'LONELY_N_SINGLE':
                break

            size = get_long(f)

            extract_file(f, name, size, work_dir)
            seek_pad(f, size)

    return file_path
