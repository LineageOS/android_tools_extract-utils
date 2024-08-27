#!/bin/bash
#
# SPDX-FileCopyrightText: 2019-2024 The LineageOS Project
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

CARRIER_SKIP_FILES=()

# Initialize the helper
setup_vendor_deps "${ANDROID_ROOT}"

generate_prop_list_from_image "${_input_image}" "${_output_file}" CARRIER_SKIP_FILES carriersettings

function header() {
    sed -i "1s/^/${1}\n/" "${_output_file}"
}

header "# All blobs below are extracted from the release mentioned in proprietary-files.txt"
