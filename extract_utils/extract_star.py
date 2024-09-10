#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import struct

from os import path
from typing import BinaryIO, List

from extract_utils.extract import ExtractCtx, print_file_paths


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


def extract_star(file_path: str, output_dir: str):
    with open(file_path, 'rb') as f:
        magic = get_string(f, 256)
        if magic != 'SINGLE_N_LONELY':
            # TODO: use custom error class
            raise ValueError(f'{file_path} is not a STAR archive')

        while True:
            name = get_string(f, 248)
            if name == 'LONELY_N_SINGLE':
                break

            size = get_long(f)

            extract_file(f, name, size, output_dir)
            seek_pad(f, size)


def extract_star_file_names(
    ctx: ExtractCtx,
    work_dir: str,
    output_dir: str,
    file_names: List[str],
) -> List[str]:
    file_paths = []

    for file_name in file_names:
        file_path = path.join(work_dir, file_name)
        if not path.exists(file_path):
            continue

        file_paths.append(file_path)

    print_file_paths(file_paths, 'STAR')

    for file_path in file_paths:
        extract_star(file_path, output_dir)

    return file_paths
