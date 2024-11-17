#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import sys

from extract_utils.sparse_img import unsparse_images

if __name__ == '__main__':
    unsparse_images(sys.argv[1:-1], sys.argv[-1])
