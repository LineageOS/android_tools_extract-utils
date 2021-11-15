#!/bin/bash
#
# Copyright (C) 2019-2021 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

_output_file="${2}"

function header() {
    sed -i "1s/^/${1}\n/" "${_output_file}"
}

VENDOR_SKIP_FILES_DEVICE=(
    "lib/libbaz.so"
)

export DEVICE_COMMON=**** FILL IN COMMON NAME ****
export VENDOR=**** FILL IN VENDOR NAME ****

"./../../${VENDOR}/${DEVICE_COMMON}/extract-files.sh"

source "../../${VENDOR}/${DEVICE_COMMON}/regen-vendor.sh" "$@"

BUILD_FINGERPRINT=$(grep BUILD_FINGERPRINT lineage_qux.mk | cut -d\" -f2)

header "# All blobs, unless pinned, are extracted from:\n# $BUILD_FINGERPRINT"
