#!/bin/bash
#
# SPDX-FileCopyrightText: 2016 The CyanogenMod Project
# SPDX-FileCopyrightText: 2017-2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

function blob_fixup() {
    case "${1}" in
        vendor/lib/libsample1.so)
            [ "$2" = "" ] && return 0
            sed -i 's|/data/misc/sample1|/data/misc/sample2|g' "${2}"
            ;;
        vendor/lib64/libsample2.so)
            [ "$2" = "" ] && return 0
            "${PATCHELF}" --remove-needed "libsample3.so" "${2}"
            "${PATCHELF}" --add-needed "libsample4.so" "${2}"
            ;;
        *)
            return 1
    esac

    return 0
}

function blob_fixup_dry() {
    blob_fixup "$1" ""
}

# If we're being sourced by the common script that we called,
# stop right here. No need to go down the rabbit hole.
if [ "${BASH_SOURCE[0]}" != "${0}" ]; then
    return
fi

set -e

export DEVICE=**** FILL IN DEVICE NAME ****
export DEVICE_COMMON=**** FILL IN COMMON NAME ****
export VENDOR=**** FILL IN VENDOR NAME ****
export VENDOR_COMMON=${VENDOR}

"./../../${VENDOR_COMMON}/${DEVICE_COMMON}/extract-files.sh" "$@"
