#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from extract_utils.extract import ExtractCtx, extract_zip
from extract_utils.tools import fbpacktool_path
from extract_utils.utils import run_cmd

pixel_firmware_regex = r'(bootloader|radio)-.+\.img'
pixel_factory_image_regex = r'image-.+\.zip'


def extract_pixel_factory_image(
    ctx: ExtractCtx,
    file_path: str,
    work_dir: str,
    *args,
    **kwargs,
):
    extract_zip(
        file_path,
        ctx,
        [],
        [],
        work_dir,
    )
    return file_path


def extract_pixel_firmware(
    ctx: ExtractCtx,
    file_path: str,
    work_dir: str,
    *args,
    **kwargs,
):
    run_cmd(['python', fbpacktool_path, 'unpack', '-o', work_dir, file_path])
    return file_path
