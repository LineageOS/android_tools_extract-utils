#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from os import path, environ


ENABLE_CHECKELF = environ['TARGET_ENABLE_CHECKELF']
ANDROID_ROOT = path.normpath(environ['ANDROID_ROOT'])
OUTDIR = environ['OUTDIR']


VENDOR = environ['VENDOR']
ANDROIDBP = environ['ANDROIDBP']
PRODUCTMK = environ['PRODUCTMK']
