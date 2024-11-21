#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import sys

from extract_utils.lp import lpunpack

if __name__ == '__main__':
    lpunpack(sys.argv[1], sys.argv[-1])
