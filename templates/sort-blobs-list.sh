#!/bin/bash
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

set -e

# List of files to sort
PROPRIETARY_FILES_TXT=(
    "proprietary-files.txt"
)

# Load sort-blobs-list.py and do some sanity checks
MY_DIR="${BASH_SOURCE%/*}"
if [[ ! -d "${MY_DIR}" ]]; then MY_DIR="${PWD}"; fi

ANDROID_ROOT="${MY_DIR}/../../.."

HELPER="${ANDROID_ROOT}/tools/extract-utils/sort-blobs-list.py"
if [ ! -f "${HELPER}" ]; then
    echo "Unable to find helper script at ${HELPER}"
    exit 1
fi

# Call the helper to sort the list
# Add --dir-first to give priority to directories and subdirectories
"${HELPER}" "${PROPRIETARY_FILES_TXT[@]}"
