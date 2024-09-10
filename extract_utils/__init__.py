#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from extract_utils.main import ExtractUtils as ExtractUtils
from extract_utils.module import ExtractUtilsModule as ExtractUtilsModule
from extract_utils.fixups_blob import (
    blob_fixups_user_type as blob_fixups_user_type,
    blob_fixup as blob_fixup,
)

from extract_utils.fixups_lib import (
    lib_fixups_user_type as lib_fixups_user_type,
    lib_fixup_vendorcompat as lib_fixup_vendorcompat,
    libs_proto_3_9_1 as libs_proto_3_9_1,
)
