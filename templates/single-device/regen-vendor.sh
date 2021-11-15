#!/bin/bash
#
# Copyright (C) 2019-2021 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

set -e

# Load extract_utils and do some sanity checks
MY_DIR="${BASH_SOURCE%/*}"
if [[ ! -d "${MY_DIR}" ]]; then MY_DIR="${PWD}"; fi

ANDROID_ROOT="${MY_DIR}/../../.."

HELPER="${ANDROID_ROOT}/tools/extract-utils/extract_utils.sh"
if [ ! -f "${HELPER}" ]; then
    echo "Unable to find helper script at ${HELPER}"
    exit 1
fi
source "${HELPER}"

_input_image="${1}"
_output_file="${2}"

if [ -z "${_input_image}" ]; then
    echo "No input image supplied"
    exit 1
fi

if [ -z "${_output_file}" ]; then
    echo "No output filename supplied"
    exit 1
fi

VENDOR_SKIP_FILES=(
    "app/Foo/Foo.apk"
    "lib/libbar.so"
)

generate_prop_list_from_image "${_input_image}" "${_output_file}" VENDOR_SKIP_FILES

# Fixups
function presign() {
    sed -i "s|vendor/${1}$|vendor/${1};PRESIGNED|g" "${_output_file}"
}

function as_module() {
    sed -i "s|vendor/${1}$|-vendor/${1}|g" "${_output_file}"
}

function header() {
    sed -i "1s/^/${1}\n/" "${_output_file}"
}

presign "app/Foo/Foo.apk"
as_module "lib64/libbar.so"

BUILD_FINGERPRINT=$(grep BUILD_FINGERPRINT lineage_qux.mk | cut -d\" -f2)

header "# All blobs, unless pinned, are extracted from:\n# $BUILD_FINGERPRINT"
