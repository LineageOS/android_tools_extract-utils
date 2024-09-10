#!/usr/bin/env python3
#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import tempfile
from extract_utils.args import parse_args
from extract_utils.extract import ExtractCtx, extract_image, get_dump_dir

args = parse_args()

ctx = ExtractCtx(
    args.source,
    args.keep_dump,
)

with get_dump_dir(ctx) as (dump_dir, extract):
    if extract:
        with tempfile.TemporaryDirectory() as work_dir:
            extract_image(ctx, dump_dir, work_dir)
