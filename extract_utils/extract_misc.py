#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
from os import path

from extract_utils.ext4 import ext4_get_volume_name
from extract_utils.extract import ExtractCtx, ExtractFn


def extract_rename_ext_image(
    ctx: ExtractCtx,
    file_path: str,
    work_dir: str,
    *args,
    **kwargs,
):
    volume_name = ext4_get_volume_name(file_path)
    if not volume_name:
        return file_path

    if volume_name == '/':
        volume_name = 'system'

    output_file_name = f'{volume_name}.img'
    output_file_path = path.join(work_dir, output_file_name)
    os.rename(file_path, output_file_path)

    return None


class ExtractRenameToExtVolumeName(ExtractFn):
    def __init__(self, key: str):
        super().__init__(key, path_fn=extract_rename_ext_image)


class ExtractRenameSuperToExtVolumeName(ExtractFn):
    def __init__(self):
        super().__init__(r'^super_\d+.img', path_fn=extract_rename_ext_image)
