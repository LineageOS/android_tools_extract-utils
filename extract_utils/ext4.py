#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

EXT4_SUPERBLOCK_OFFSET = 1024

EXT4_SUPERBLOCK_MAGIC_OFFSET = 56
EXT4_MAGIC_OFFSET = EXT4_SUPERBLOCK_OFFSET + EXT4_SUPERBLOCK_MAGIC_OFFSET
EXT4_MAGIC_LENGTH = 2

EXT4_SUPERBLOCK_VOLUME_NAME_OFFSET = 120
EXT4_VOLUME_NAME_OFFSET = (
    EXT4_SUPERBLOCK_OFFSET + EXT4_SUPERBLOCK_VOLUME_NAME_OFFSET
)
EXT4_VOLUME_NAME_LENGTH = 16

EXT4_MAGIC = 0xEF53.to_bytes(2, 'little')


def ext4_get_volume_name(file_path: str):
    with open(file_path, 'rb') as f:
        f.seek(EXT4_MAGIC_OFFSET)
        magic = f.read(EXT4_MAGIC_LENGTH)
        if magic != EXT4_MAGIC:
            return None

        f.seek(EXT4_VOLUME_NAME_OFFSET)
        volume_name_data = f.read(EXT4_VOLUME_NAME_LENGTH)
        volume_name = volume_name_data.decode('utf-8').strip('\x00')
        return volume_name
