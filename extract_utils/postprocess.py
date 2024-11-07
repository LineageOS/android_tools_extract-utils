#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
from typing import Callable, Optional

from extract_utils.tools import carriersettings_extractor_path
from extract_utils.utils import run_cmd


class PostprocessCtx:
    def __init__(self):
        pass


postprocess_fn_type = Callable[[PostprocessCtx], None]


def postprocess_carriersettings_fn_impl(
    input_path: str,
    output_path: str,
    ctx: PostprocessCtx,
    apn_output_path: Optional[str] = None,
):
    os.makedirs(output_path, exist_ok=True)

    apn_args = []
    if apn_output_path is not None:
        apn_args = ['-a', apn_output_path]

    run_cmd(
        [
            carriersettings_extractor_path,
            '-i',
            input_path,
            '-v',
            output_path,
        ]
        + apn_args
    )
