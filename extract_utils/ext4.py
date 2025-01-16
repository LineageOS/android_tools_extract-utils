#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

EXT4_SUPERBLOCK_OFFSET = 1024

EXT4_SUPERBLOCK_MAGIC_OFFSET = 56
EXT4_MAGIC_OFFSET = EXT4_SUPERBLOCK_OFFSET + EXT4_SUPERBLOCK_MAGIC_OFFSET
EXT4_MAGIC_LENGTH = 2

EXT4_MAGIC = 0xEF53.to_bytes(2, 'little')
