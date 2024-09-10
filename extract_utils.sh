#!/bin/bash
#
# Copyright (C) 2016 The CyanogenMod Project
# Copyright (C) 2017-2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

PRODUCT_COPY_FILES_HASHES=()
PRODUCT_COPY_FILES_FIXUP_HASHES=()
PRODUCT_COPY_FILES_SRC=()
PRODUCT_COPY_FILES_DEST=()
PRODUCT_COPY_FILES_ARGS=()
PRODUCT_COPY_FILES_PACKAGE=()
EXTRACT_SRC=
EXTRACT_STATE=-1
EXTRACT_RADIO_STATE=-1
VENDOR_STATE=-1
VENDOR_RADIO_STATE=-1
COMMON=-1
ARCHES=
FULLY_DEODEXED=-1

KEEP_DUMP=${KEEP_DUMP:-0}
SKIP_CLEANUP=${SKIP_CLEANUP:-0}
EXTRACT_TMP_DIR=$(mktemp -d)
HOST=$(uname | tr '[:upper:]' '[:lower:]')

#
# cleanup
#
# kill our tmpfiles with fire on exit
#
function cleanup() {
    if [ "$SKIP_CLEANUP" == "true" ] || [ "$SKIP_CLEANUP" == "1" ]; then
        echo "Skipping cleanup of $EXTRACT_TMP_DIR"
    else
        rm -rf "${EXTRACT_TMP_DIR:?}"
    fi
}

trap cleanup 0

#
# setup_vendor_deps
#
# $1: Android root directory
# Sets up common dependencies for extraction
#
function setup_vendor_deps() {
    export ANDROID_ROOT="$1"
    if [ ! -d "$ANDROID_ROOT" ]; then
        echo "\$ANDROID_ROOT must be set and valid before including this script!"
        exit 1
    fi

    export BINARIES_LOCATION="$ANDROID_ROOT"/prebuilts/extract-tools/${HOST}-x86/bin
    export JDK_BINARIES_LOCATION="$ANDROID_ROOT"/prebuilts/jdk/jdk21/${HOST}-x86/bin
    export COMMON_BINARIES_LOCATION="$ANDROID_ROOT"/prebuilts/extract-tools/common

    export SIMG2IMG="$BINARIES_LOCATION"/simg2img
    export LPUNPACK="$BINARIES_LOCATION"/lpunpack
    export OTA_EXTRACTOR="$BINARIES_LOCATION"/ota_extractor
    export SIGSCAN="$BINARIES_LOCATION"/SigScan
    export STRIPZIP="$BINARIES_LOCATION"/stripzip
    export JAVA="$JDK_BINARIES_LOCATION"/java
    export APKTOOL="$COMMON_BINARIES_LOCATION"/apktool/apktool.jar

    for VERSION in 0_8 0_9 0_17_2; do
        export PATCHELF_${VERSION}="$BINARIES_LOCATION"/patchelf-"${VERSION}"
    done

    if [ -z "$PATCHELF_VERSION" ]; then
        export PATCHELF_VERSION=0_9
    fi

    if [ -z "$PATCHELF" ]; then
        local PATCHELF_VARIABLE="PATCHELF_${PATCHELF_VERSION}"
        export PATCHELF=${!PATCHELF_VARIABLE}
    fi
}

#
# setup_vendor
#
# $1: device name
# $2: vendor name
# $3: Android root directory
# $4: is common device - optional, default to false
# $5: cleanup - optional, default to true
# $6: custom vendor makefile name - optional, default to false
#
# Must be called before any other functions can be used. This
# sets up the internal state for a new vendor configuration.
#
function setup_vendor() {
    local DEVICE="$1"
    if [ -z "$DEVICE" ]; then
        echo "\$DEVICE must be set before including this script!"
        exit 1
    fi

    local VENDOR="$2"
    if [ -z "$VENDOR" ]; then
        echo "\$VENDOR must be set before including this script!"
        exit 1
    fi

    export ANDROID_ROOT="$3"
    if [ ! -d "$ANDROID_ROOT" ]; then
        echo "\$ANDROID_ROOT must be set and valid before including this script!"
        exit 1
    fi

    export OUTDIR=vendor/"$VENDOR"/"$DEVICE"
    if [ ! -d "$ANDROID_ROOT/$OUTDIR" ]; then
        mkdir -p "$ANDROID_ROOT/$OUTDIR"
    fi

    VNDNAME="$6"
    if [ -z "$VNDNAME" ]; then
        VNDNAME="$DEVICE"
    fi

    export PRODUCTMK="$ANDROID_ROOT"/"$OUTDIR"/"$VNDNAME"-vendor.mk
    export ANDROIDBP="$ANDROID_ROOT"/"$OUTDIR"/Android.bp
    export ANDROIDMK="$ANDROID_ROOT"/"$OUTDIR"/Android.mk
    export BOARDMK="$ANDROID_ROOT"/"$OUTDIR"/BoardConfigVendor.mk

    if [ "$4" == "true" ] || [ "$4" == "1" ]; then
        COMMON=1
    else
        COMMON=0
    fi

    if [ "$5" == "false" ] || [ "$5" == "0" ]; then
        VENDOR_STATE=1
        VENDOR_RADIO_STATE=1
    else
        VENDOR_STATE=0
        VENDOR_RADIO_STATE=0
    fi

    setup_vendor_deps "$ANDROID_ROOT"
}

# Helper functions for parsing a spec.
# notes: an optional "|SHA1" that may appear in the format is stripped
#        early from the spec in the parse_file_list function, and
#        should not be present inside the input parameter passed
#        to these functions.

#
# input: spec in the form of "src[:dst][;args]"
# output: "src[:dst]"
#
function spec() {
    # Remove the args by removing the longest trailing substring starting with ;
    echo "${1%%;*}"
}

#
# input: spec in the form of "src[:dst]"
# output: "src"
#
function spec_src_file() {
    # Remove the shortest trailing substring starting with :
    # If there's no : to match against, src will be kept,
    # otherwise, :dst will be removed
    echo "${1%%:*}"
}

function spec_target_file() {
    # Remove the shortest beginning substring ending in :
    # If there's no : to match against, src will be kept,
    # otherwise, src: will be removed
    echo "${1##*:}"
}

#
# input: spec in the form of "src[:dst][;args]"
# output: "dst" if present, "src" otherwise.
#
function target_file() {
    local SPEC=$(spec "$1")
    spec_target_file "$SPEC"
}

function spec_target_args() {
    # Remove the shortest beginning substring ending in ;
    # If there isn't one, the entire string will be kept, so check
    # against that
    local ARGS="${2#*;}"
    if [ "$1" = "$ARGS" ]; then
        echo ""
    else
        echo "$ARGS"
    fi
}

#
# input: spec in the form of "src[:dst][;args]"
# output: "args" if present, "" otherwise.
#
function target_args() {
    local SPEC=$(spec "$1")
    spec_target_args "$SPEC" "$1"
}

#
# prefix_match:
#
# input:
#   - $1: prefix
#   - (global variable) PRODUCT_PACKAGES_DEST: array of dst
#   - (global variable) PRODUCT_PACKAGES_ARGS: array of args
# output:
#   - new array consisting of dst[;args] entries where $1 is a prefix of ${dst}.
#
function prefix_match() {
    local PREFIX="$1"
    local NEW_ARRAY=()
    local DEST_LIST=("${PRODUCT_PACKAGES_DEST[@]}")
    local ARGS_LIST=("${PRODUCT_PACKAGES_ARGS[@]}")
    local COUNT=${#DEST_LIST[@]}

    for ((i = 1; i < COUNT + 1; i++)); do
        local FILE="${DEST_LIST[$i - 1]}"
        if [[ "$FILE" =~ ^"$PREFIX" ]]; then
            local ARGS="${ARGS_LIST[$i - 1]}"
            if [[ -z "${ARGS}" || "${ARGS}" =~ 'SYMLINK' ]]; then
                NEW_ARRAY+=("${FILE#"$PREFIX"}")
            else
                NEW_ARRAY+=("${FILE#"$PREFIX"};${ARGS}")
            fi
        fi
    done
    printf '%s\n' "${NEW_ARRAY[@]}" | LC_ALL=C sort
}

#
# prefix_match_file:
#
# $1: the prefix to match on
# $2: the file to match the prefix for
#
# Internal function which returns true if a filename contains the
# specified prefix.
#
function prefix_match_file() {
    local PREFIX="$1"
    local FILE="$2"
    if [[ "$FILE" =~ ^"$PREFIX" ]]; then
        return 0
    else
        return 1
    fi
}

#
# suffix_match_file:
#
# $1: the suffix to match on
# $2: the file to match the suffix for
#
# Internal function which returns true if a filename contains the
# specified suffix.
#
function suffix_match_file() {
    local SUFFIX="$1"
    local FILE="$2"
    if [[ "$FILE" = *"$SUFFIX" ]]; then
        return 0
    else
        return 1
    fi
}

#
# truncate_file
#
# $1: the filename to truncate
#
# Internal function which truncates a filename by removing the first dir
# in the path. ex. vendor/lib/libsdmextension.so -> lib/libsdmextension.so
#
function truncate_file() {
    local FILE="$1"
    local FIND="${FILE%%/*}"
    local LOCATION="${#FIND}+1"
    echo "${FILE:$LOCATION}"
}

function lib_to_package_fixup_clang_rt_ubsan_standalone() {
    case "$1" in
        libclang_rt.ubsan_standalone-arm-android | \
            libclang_rt.ubsan_standalone-aarch64-android)
            echo "libclang_rt.ubsan_standalone"
            ;;
        *)
            return 1
            ;;
    esac
}

function lib_to_package_fixup_proto_3_9_1() {
    case "$1" in
        libprotobuf-cpp-lite-3.9.1 | \
            libprotobuf-cpp-full-3.9.1)
            echo "$1-vendorcompat"
            ;;
        *)
            return 1
            ;;
    esac
}

#
# lib_to_package_fixup
#
# $1: library name without the .so suffix
# $2: partition of the file for which we are generating shared libs
# $3: name of the file for which we are generating shared libs
#
#
# Can be overridden by device-level extract-files.sh
#
function lib_to_package_fixup() {
    lib_to_package_fixup_clang_rt_ubsan_standalone "$1" ||
        lib_to_package_fixup_proto_3_9_1 "$1"
}

#
# write_single_product_copy_files:
#
# $1: the file to be copied
#
# Creates a PRODUCT_COPY_FILES section in the product makefile for the
# item provided in $1.
#
function write_single_product_copy_files() {
    local FILE="$1"
    if [ -z "$FILE" ]; then
        echo "A file must be provided to write_single_product_copy_files()!"
        exit 1
    fi

    local TARGET=$(target_file "$FILE")
    local OUTTARGET=$(truncate_file "$TARGET")

    printf '%s\n' "PRODUCT_COPY_FILES += \\" >>"$PRODUCTMK"
    printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_PRODUCT)/%s\n' \
        "$OUTDIR" "$TARGET" "$OUTTARGET" >>"$PRODUCTMK"
}

#
# write_single_product_packages:
#
# $1: the package to be built
#
# Creates a PRODUCT_PACKAGES section in the product makefile for the
# item provided in $1.
#
function write_single_product_packages() {
    local PACKAGE="$1"
    if [ -z "$PACKAGE" ]; then
        echo "A package must be provided to write_single_product_packages()!"
        exit 1
    fi

    printf '\n%s\n' "PRODUCT_PACKAGES += \\" >>"$PRODUCTMK"
    printf '    %s\n' "$PACKAGE" >>"$PRODUCTMK"
}

#
# write_rro_androidmanifest:
#
# $2: target package for the RRO overlay
#
# Creates an AndroidManifest.xml for an RRO overlay.
#
function write_rro_androidmanifest() {
    local TARGET_PACKAGE="$1"

    cat <<EOF
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="$TARGET_PACKAGE.vendor"
    android:versionCode="1"
    android:versionName="1.0">
    <application android:hasCode="false" />
    <overlay
        android:targetPackage="$TARGET_PACKAGE"
        android:isStatic="true"
        android:priority="0"/>
</manifest>
EOF
}

#
# write_rro_blueprint:
#
# $1: package name for the RRO overlay
# $2: target partition for the RRO overlay
#
# Creates an Android.bp for an RRO overlay.
#
function write_rro_blueprint() {
    local PKGNAME="$1"
    local PARTITION="$2"

    printf 'runtime_resource_overlay {\n'
    printf '\tname: "%s",\n' "$PKGNAME"
    printf '\ttheme: "%s",\n' "$PKGNAME"
    printf '\tsdk_version: "%s",\n' "current"
    printf '\taaptflags: ["%s"],\n' "--keep-raw-values"

    if [ "$PARTITION" = "vendor" ]; then
        printf '\tsoc_specific: true,\n'
    elif [ "$PARTITION" = "product" ]; then
        printf '\tproduct_specific: true,\n'
    elif [ "$PARTITION" = "system_ext" ]; then
        printf '\tsystem_ext_specific: true,\n'
    elif [ "$PARTITION" = "odm" ]; then
        printf '\tdevice_specific: true,\n'
    fi
    printf '}\n'
}

#
# write_blueprint_header:
#
# $1: file which will be written to
#
# writes out the warning message regarding manual file modifications.
# note that this is not an append operation, and should
# be executed first!
#
function write_blueprint_header() {
    if [ -f "$1" ]; then
        rm "$1"
    fi

    [ "$COMMON" -eq 1 ] && local DEVICE="$DEVICE_COMMON"
    [ "$COMMON" -eq 1 ] && local VENDOR="${VENDOR_COMMON:-$VENDOR}"

    cat <<EOF >>"$1"
// Automatically generated file. DO NOT MODIFY
//
// This file is generated by device/$VENDOR/$DEVICE/setup-makefiles.sh

EOF
}

#
# write_makefile_header:
#
# $1: file which will be written to
#
# writes out the warning message regarding manual file modifications.
# note that this is not an append operation, and should
# be executed first!
#
function write_makefile_header() {
    if [ -f "$1" ]; then
        rm "$1"
    fi

    [ "$COMMON" -eq 1 ] && local DEVICE="$DEVICE_COMMON"
    [ "$COMMON" -eq 1 ] && local VENDOR="${VENDOR_COMMON:-$VENDOR}"

    cat <<EOF >>"$1"
# Automatically generated file. DO NOT MODIFY
#
# This file is generated by device/$VENDOR/$DEVICE/setup-makefiles.sh

EOF
}

#
# write_xml_header:
#
# $1: file which will be written to
#
# writes out the warning message regarding manual file modifications.
# note that this is not an append operation, and should
# be executed first!
#
function write_xml_header() {
    if [ -f "$1" ]; then
        rm "$1"
    fi

    [ "$COMMON" -eq 1 ] && local DEVICE="$DEVICE_COMMON"
    [ "$COMMON" -eq 1 ] && local VENDOR="${VENDOR_COMMON:-$VENDOR}"

    cat <<EOF >>"$1"
<?xml version="1.0" encoding="utf-8"?>
<!--
    Automatically generated file. DO NOT MODIFY

    This file is generated by device/$VENDOR/$DEVICE/setup-makefiles.sh
-->
EOF
}

#
# write_rro_package:
#
# $1: the RRO package name
# $2: the RRO target package
# $3: the partition for the RRO overlay
#
# Generates the file structure for an RRO overlay.
#
function write_rro_package() {
    local PKGNAME="$1"
    if [ -z "$PKGNAME" ]; then
        echo "A package name must be provided to write_rro_package()!"
        exit 1
    fi

    local TARGET_PACKAGE="$2"
    if [ -z "$TARGET_PACKAGE" ]; then
        echo "A target package must be provided to write_rro_package()!"
        exit 1
    fi

    local PARTITION="$3"
    if [ -z "$PARTITION" ]; then
        PARTITION="vendor"
    fi

    local RROBP="$ANDROID_ROOT"/"$OUTDIR"/rro_overlays/"$PKGNAME"/Android.bp
    local RROMANIFEST="$ANDROID_ROOT"/"$OUTDIR"/rro_overlays/"$PKGNAME"/AndroidManifest.xml

    write_blueprint_header "$RROBP"
    write_xml_header "$RROMANIFEST"

    write_rro_blueprint "$PKGNAME" "$PARTITION" >>"$RROBP"
    write_rro_androidmanifest "$TARGET_PACKAGE" >>"$RROMANIFEST"
}

#
# write_headers:
#
# $1: devices falling under common to be added to guard - optional
# $2: custom guard - optional
#
# Calls write_makefile_header for each of the makefiles and
# write_blueprint_header for Android.bp and creates the initial
# path declaration and device guard for the Android.mk
#
function write_headers() {
    write_makefile_header "$ANDROIDMK"

    GUARD="$2"
    if [ -z "$GUARD" ]; then
        GUARD="TARGET_DEVICE"
    fi

    cat <<EOF >>"$ANDROIDMK"
LOCAL_PATH := \$(call my-dir)

EOF
    if [ "$COMMON" -ne 1 ]; then
        cat <<EOF >>"$ANDROIDMK"
ifeq (\$($GUARD),$DEVICE)

EOF
    else
        if [ -z "$1" ]; then
            echo "Argument with devices to be added to guard must be set!"
            exit 1
        fi
        cat <<EOF >>"$ANDROIDMK"
ifneq (\$(filter $1,\$($GUARD)),)

EOF
    fi

    write_makefile_header "$BOARDMK"
    write_makefile_header "$PRODUCTMK"
    write_blueprint_header "$ANDROIDBP"

    cat <<EOF >>"$ANDROIDBP"
soong_namespace {
	imports: [
EOF

    if [ -n "$DEVICE_COMMON" ] && [ "$COMMON" -ne 1 ]; then
        cat <<EOF >>"$ANDROIDBP"
		"vendor/${VENDOR_COMMON:-$VENDOR}/$DEVICE_COMMON",
EOF
    fi
    vendor_imports "$ANDROIDBP"

    cat <<EOF >>"$ANDROIDBP"
	],
}

EOF

    [ "$COMMON" -eq 1 ] && local DEVICE="$DEVICE_COMMON"
    [ "$COMMON" -eq 1 ] && local VENDOR="${VENDOR_COMMON:-$VENDOR}"
    cat <<EOF >>"$PRODUCTMK"
PRODUCT_SOONG_NAMESPACES += \\
    vendor/$VENDOR/$DEVICE

EOF
}

#
# write_footers:
#
# Closes the inital guard and any other finalization tasks. Must
# be called as the final step.
#
function write_footers() {
    cat <<EOF >>"$ANDROIDMK"
endif
EOF
}

# Return success if adb is up and not in recovery
function _adb_connected {
    {
        if [[ "$(adb get-state)" == device ]]; then
            return 0
        fi
    } 2>/dev/null

    return 1
}

#
# parse_file_list:
#
# $1: input file
# $2: blob section in file - optional
#
# Sets PRODUCT_PACKAGES and PRODUCT_COPY_FILES while parsing the input file
#
function parse_file_list() {
    if [ -z "$1" ]; then
        echo "An input file is expected!"
        exit 1
    elif [ ! -f "$1" ]; then
        echo "Input file $1 does not exist!"
        exit 1
    fi

    if [ -n "$2" ]; then
        echo "Using section \"$2\""
        LIST=$EXTRACT_TMP_DIR/files.txt
        # Match all lines starting with first line found to start* with '#'
        # comment and contain** $2, and ending with first line to be empty*.
        # *whitespaces (tabs, spaces) at the beginning of lines are discarded
        # **the $2 match is case-insensitive
        cat "$1" | sed -n '/^[[:space:]]*#.*'"$2"'/I,/^[[:space:]]*$/ p' >$LIST
    else
        LIST=$1
    fi

    PRODUCT_COPY_FILES_HASHES=()
    PRODUCT_COPY_FILES_FIXUP_HASHES=()
    PRODUCT_COPY_FILES_SRC=()
    PRODUCT_COPY_FILES_DEST=()
    PRODUCT_COPY_FILES_ARGS=()

    while read -r line; do
        if [ -z "$line" ]; then continue; fi

        # If the line has a pipe delimiter, a sha1 hash should follow.
        # This indicates the file should be pinned and not overwritten
        # when extracting files.
        local SPLIT=(${line//\|/ })
        local COUNT=${#SPLIT[@]}
        local SPEC=${SPLIT[0]}
        local HASH=
        local FIXUP_HASH=
        if [ "$COUNT" -gt "1" ]; then
            HASH="${SPLIT[1],,}"
        fi
        if [ "$COUNT" -gt "2" ]; then
            FIXUP_HASH="${SPLIT[2],,}"
        fi

        local IS_PRODUCT_PACKAGE=
        # if line starts with a dash, it needs to be packaged
        if [[ "$SPEC" =~ ^- ]]; then
            IS_PRODUCT_PACKAGE=true
            SPEC="${SPEC#-}"
        fi

        local STRIPPED_SPEC=$(spec "$SPEC")
        local SRC_FILE=$(spec_src_file "$STRIPPED_SPEC")
        local TARGET_FILE="$SRC_FILE"
        local ARGS=
        if [ "$SRC_FILE" != "$SPEC" ]; then
            TARGET_FILE=$(spec_target_file "$STRIPPED_SPEC")
            ARGS=$(spec_target_args "$STRIPPED_SPEC" "$SPEC")
        fi

        PRODUCT_COPY_FILES_HASHES+=("$HASH")
        PRODUCT_COPY_FILES_FIXUP_HASHES+=("$FIXUP_HASH")
        PRODUCT_COPY_FILES_SRC+=("$SRC_FILE")
        PRODUCT_COPY_FILES_DEST+=("$TARGET_FILE")
        PRODUCT_COPY_FILES_ARGS+=("$ARGS")
        PRODUCT_COPY_FILES_PACKAGE+=("$IS_PRODUCT_PACKAGE")

    done < <(grep -v -E '(^#|^[[:space:]]*$)' "$LIST" | LC_ALL=C sort | uniq)
}

#
# write_makefiles:
#
# $1: file containing the list of items to extract
#
function write_makefiles() {
    GENERATE_BP_PY="$ANDROID_ROOT/tools/extract-utils/write_makefiles.py"
    python "$GENERATE_BP_PY" "$1"
}

#
# append_firmware_calls_to_makefiles:
#
# $1: file containing the list of items to extract
#
# Appends the calls to all images present in radio folder to Android.mk
# and radio AB_OTA_PARTITIONS to BoardConfigVendor.mk
#
function append_firmware_calls_to_makefiles() {
    parse_file_list "$1"

    local DEST_LIST=("${PRODUCT_COPY_FILES_DEST[@]}")
    local ARGS_LIST=("${PRODUCT_COPY_FILES_ARGS[@]}")
    local COUNT=${#DEST_LIST[@]}

    if [[ ${ARGS_LIST[*]} =~ "AB" ]]; then
        printf '%s\n' "AB_OTA_PARTITIONS += \\" >>"$BOARDMK"
    fi

    for ((i = 1; i < COUNT + 1; i++)); do
        local DST_FILE="${DEST_LIST[$i - 1]}"
        local SPEC_ARGS="${ARGS_LIST[$i - 1]}"
        local SHA1=$(get_hash "$ANDROID_ROOT"/"$OUTDIR"/radio/"$DST_FILE")
        local DST_FILE_NAME="${DST_FILE%.img}"
        local ARGS=(${SPEC_ARGS//;/ })
        LINEEND=" \\"
        if [ "$i" -eq "$COUNT" ]; then
            LINEEND=""
        fi

        for ARG in "${ARGS[@]}"; do
            if [ "$ARG" = "AB" ]; then
                printf '    %s%s\n' "$DST_FILE_NAME" "$LINEEND" >>"$BOARDMK"
            fi
        done
        printf '%s\n' "\$(call add-radio-file-sha1-checked,radio/$DST_FILE,$SHA1)" >>"$ANDROIDMK"
    done
    printf '\n' >>"$ANDROIDMK"
}

#
# get_file_helper:
#
# $1: input file/folder (exact path)
# $2: target file/folder
# $3: source of the file (must be local folder)
#
# Silently extracts the input file to defined target if normal file, or calls get_file if symlink.
# Returns success if file exists
#
function get_file_helper() {
    local SRC="$3"
    if [[ -L "$1" ]]; then
        # Always resolve symlink path to be able to handle /system/odm etc in relative and absolute symlinks
        get_file "$(readlink -nm "$1")" "$2" "$SRC"
    else
        cp -r "$1" "$2"
    fi
}

#
# get_file:
#
# $1: input file/folder
# $2: target file/folder
# $3: source of the file (can be "adb" or a local folder)
#
# Silently extracts the input file to defined target
# Returns success if file can be pulled from the device or found locally
#
function get_file() {
    local SRC="$3"
    local SOURCES=("$1" "${1#/system}" "system/$1")

    if [ "$SRC" = "adb" ]; then
        for SOURCE in "${SOURCES[@]}"; do
            adb pull "$SOURCE" "$2" >/dev/null 2>&1 && return 0
        done

        return 1
    else
        for SOURCE in "${SOURCES[@]}"; do
            if [ -f "$SRC/$SOURCE" ] || [ -d "$SRC/$SOURCE" ]; then
                get_file_helper "$SRC/$SOURCE" "$2" 2>/dev/null && return 0
            fi
        done

        # try /vendor/odm for devices without /odm partition
        [[ "$1" == /system/odm/* ]] && get_file_helper "$SRC/vendor/${1#/system}" "$2" 2>/dev/null && return 0

        return 1
    fi
}

#
# oat2dex:
#
# $1: extracted apk|jar (to check if deodex is required)
# $2: odexed apk|jar to deodex
# $3: source of the odexed apk|jar
#
# Convert apk|jar .odex in the corresposing classes.dex
#
function oat2dex() {
    local CUSTOM_TARGET="$1"
    local OEM_TARGET="$2"
    local SRC="$3"
    local TARGET=
    local OAT=

    if [ -z "$BAKSMALIJAR" ] || [ -z "$SMALIJAR" ]; then
        export BAKSMALIJAR="$COMMON_BINARIES_LOCATION/smali/baksmali.jar"
        export SMALIJAR="$COMMON_BINARIES_LOCATION/smali/smali.jar"
    fi

    if [ -z "$VDEXEXTRACTOR" ]; then
        export VDEXEXTRACTOR="$BINARIES_LOCATION/vdexExtractor"
    fi

    if [ -z "$CDEXCONVERTER" ]; then
        export CDEXCONVERTER="$BINARIES_LOCATION/compact_dex_converter"
    fi

    # Extract existing boot.oats to the temp folder
    if [ -z "$ARCHES" ]; then
        echo "Checking if system is odexed and locating boot.oats..."
        for ARCH in "arm64" "arm" "x86_64" "x86"; do
            mkdir -p "$EXTRACT_TMP_DIR/system/framework/$ARCH"
            if get_file "/system/framework/$ARCH" "$EXTRACT_TMP_DIR/system/framework/" "$SRC"; then
                ARCHES+="$ARCH "
            else
                rmdir "$EXTRACT_TMP_DIR/system/framework/$ARCH"
            fi
        done
    fi

    if [ -z "$ARCHES" ]; then
        FULLY_DEODEXED=1 && return 0 # system is fully deodexed, return
    fi

    if [ ! -f "$CUSTOM_TARGET" ]; then
        return
    fi

    if grep "classes.dex" "$CUSTOM_TARGET" >/dev/null; then
        return 0 # target apk|jar is already odexed, return
    fi

    for ARCH in $ARCHES; do
        BOOTOAT="$EXTRACT_TMP_DIR/system/framework/$ARCH/boot.oat"

        local DIRNAME=$(dirname "$OEM_TARGET")
        local EXTENSION="${OEM_TARGET##*.}"
        local NAME_WITHOUT_EXT=$(basename "$OEM_TARGET" ".$EXTENSION")
        local OAT_VDEX_PATH="$DIRNAME/oat/$ARCH/$NAME_WITHOUT_EXT"
        local OAT="$OAT_VDEX_PATH.odex"
        local VDEX="$OAT_VDEX_PATH.vdex"

        if get_file "$OAT" "$EXTRACT_TMP_DIR" "$SRC"; then
            if get_file "$VDEX" "$EXTRACT_TMP_DIR" "$SRC"; then
                "$VDEXEXTRACTOR" -o "$EXTRACT_TMP_DIR/" -i "$EXTRACT_TMP_DIR/$(basename "$VDEX")" >/dev/null
                CLASSES=$(ls "$EXTRACT_TMP_DIR/$(basename "${OEM_TARGET%.*}")_classes"*)
                for CLASS in $CLASSES; do
                    NEWCLASS=$(echo "$CLASS" | sed 's/.*_//;s/cdex/dex/')
                    # Check if we have to deal with CompactDex
                    if [[ "$CLASS" == *.cdex ]]; then
                        "$CDEXCONVERTER" "$CLASS" &>/dev/null
                        mv "$CLASS.new" "$EXTRACT_TMP_DIR/$NEWCLASS"
                    else
                        mv "$CLASS" "$EXTRACT_TMP_DIR/$NEWCLASS"
                    fi
                done
            else
                "$JAVA" -jar "$BAKSMALIJAR" deodex -o "$EXTRACT_TMP_DIR/dexout" -b "$BOOTOAT" -d "$EXTRACT_TMP_DIR" "$EXTRACT_TMP_DIR/$(basename "$OAT")"
                "$JAVA" -jar "$SMALIJAR" assemble "$EXTRACT_TMP_DIR/dexout" -o "$EXTRACT_TMP_DIR/classes.dex"
            fi
        elif [[ "$CUSTOM_TARGET" =~ .jar$ ]]; then
            JARNAME=$(basename "${OEM_TARGET%.*}")
            JAROAT="$EXTRACT_TMP_DIR/system/framework/$ARCH/boot-$JARNAME.oat"
            JARVDEX="$EXTRACT_TMP_DIR/system/framework/boot-$JARNAME.vdex"
            if [ ! -f "$JAROAT" ]; then
                JAROAT=$BOOTOAT
            fi
            if [ ! -f "$JARVDEX" ]; then
                JARVDEX="$EXTRACT_TMP_DIR/system/framework/$ARCH/boot-$JARNAME.vdex"
            fi
            # try to extract classes.dex from boot.vdex for frameworks jars
            # fallback to boot.oat if vdex is not available
            if get_file "$JARVDEX" "$EXTRACT_TMP_DIR" "$SRC"; then
                "$VDEXEXTRACTOR" -o "$EXTRACT_TMP_DIR/" -i "$EXTRACT_TMP_DIR/$(basename "$JARVDEX")" >/dev/null
                CLASSES=$(ls "$EXTRACT_TMP_DIR/$(basename "${JARVDEX%.*}")_classes"* 2>/dev/null)
                for CLASS in $CLASSES; do
                    NEWCLASS=$(echo "$CLASS" | sed 's/.*_//;s/cdex/dex/')
                    # Check if we have to deal with CompactDex
                    if [[ "$CLASS" == *.cdex ]]; then
                        "$CDEXCONVERTER" "$CLASS" &>/dev/null
                        mv "$CLASS.new" "$EXTRACT_TMP_DIR/$NEWCLASS"
                    else
                        mv "$CLASS" "$EXTRACT_TMP_DIR/$NEWCLASS"
                    fi
                done
            else
                "$JAVA" -jar "$BAKSMALIJAR" deodex -o "$EXTRACT_TMP_DIR/dexout" -b "$BOOTOAT" -d "$EXTRACT_TMP_DIR" "$JAROAT/$OEM_TARGET"
                "$JAVA" -jar "$SMALIJAR" assemble "$EXTRACT_TMP_DIR/dexout" -o "$EXTRACT_TMP_DIR/classes.dex"
            fi
        else
            continue
        fi

    done

    rm -rf "$EXTRACT_TMP_DIR/dexout"
}

#
# init_adb_connection:
#
# Starts adb server and waits for the device
#
function init_adb_connection() {
    adb start-server # Prevent unexpected starting server message from adb get-state in the next line
    if ! _adb_connected; then
        echo "No device is online. Waiting for one..."
        echo "Please connect USB and/or enable USB debugging"
        until _adb_connected; do
            sleep 1
        done
        echo "Device Found."
    fi

    # Retrieve IP and PORT info if we're using a TCP connection
    TCPIPPORT=$(adb devices | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+[^0-9]+' |
        head -1 | awk '{print $1}')
    adb root &>/dev/null
    sleep 0.3
    if [ -n "$TCPIPPORT" ]; then
        # adb root just killed our connection
        # so reconnect...
        adb connect "$TCPIPPORT"
    fi
    adb wait-for-device &>/dev/null
    sleep 0.3
}

#
# fix_soname:
#
# $1: so file to fix
#
function fix_soname() {
    local SO="$1"

    "${PATCHELF}" --set-soname $(basename "$SO") "$SO"
}

#
# fix_xml:
#
# $1: xml file to fix
#
function fix_xml() {
    local XML="$1"
    local TEMP_XML="$EXTRACT_TMP_DIR/$(basename "$XML").temp"

    grep -a '^<?xml version' "$XML" >"$TEMP_XML"
    grep -av '^<?xml version' "$XML" >>"$TEMP_XML"

    mv "$TEMP_XML" "$XML"
}

function get_hash() {
    local FILE="$1"
    sha1sum "${FILE}" | awk '{print $1}'
}

function print_spec() {
    local SPEC_PRODUCT_PACKAGE="$1"
    local SPEC_SRC_FILE="$2"
    local SPEC_DST_FILE="$3"
    local SPEC_ARGS="$4"
    local SPEC_HASH="$5"
    local SPEC_FIXUP_HASH="$6"

    local PRODUCT_PACKAGE=""
    if [ "$SPEC_PRODUCT_PACKAGE" = true ]; then
        PRODUCT_PACKAGE="-"
    fi
    local SRC=""
    if [ -n "${SPEC_SRC_FILE}" ] && [ "${SPEC_SRC_FILE}" != "${SPEC_DST_FILE}" ]; then
        SRC="${SPEC_SRC_FILE}:"
    fi
    local DST=""
    if [ -n "${SPEC_DST_FILE}" ]; then
        DST="${SPEC_DST_FILE}"
    fi
    local ARGS=""
    if [ -n "${SPEC_ARGS}" ]; then
        ARGS=";${SPEC_ARGS}"
    fi
    local HASH=""
    if [ -n "${SPEC_HASH}" ]; then
        HASH="|${SPEC_HASH}"
    fi
    local FIXUP_HASH=""
    if [ -n "${SPEC_FIXUP_HASH}" ] && [ "${SPEC_FIXUP_HASH}" != "${SPEC_HASH}" ]; then
        FIXUP_HASH="|${SPEC_FIXUP_HASH}"
    fi
    printf '%s%s%s%s%s%s\n' "${PRODUCT_PACKAGE}" "${SRC}" "${DST}" "${ARGS}" "${HASH}" "${FIXUP_HASH}"
}

# Helper function to be used by device-level extract-files.sh
# to patch a jar
#   $1: path to blob file.
#   $2: path to patch file or directory with patches.
#   ...: arguments to be passed to apktool
#
function apktool_patch() {
    local APK_PATH="$1"
    shift

    local PATCHES_PATH="$1"
    shift

    local PATCHES_PATHS=$(find "$PATCHES_PATH" -name "*.patch" | sort)

    local TEMP_DIR=$(mktemp -dp "$EXTRACT_TMP_DIR")
    "$JAVA" -jar "$APKTOOL" d "$APK_PATH" -o "$TEMP_DIR" -f "$@"

    while IFS= read -r PATCH_PATH; do
        echo "Applying patch $PATCH_PATH"
        # unsafe-paths is required since the directory is outside of the current working directory
        git apply --unsafe-paths --directory="$TEMP_DIR" "$PATCH_PATH"
    done <<<"$PATCHES_PATHS"

    # apktool modifies timestamps, we cannot use its output.
    # To get reproductible builds, use stripzip to strip the timestamps.
    "$JAVA" -jar "$APKTOOL" b "$TEMP_DIR" -o "$APK_PATH"

    "$STRIPZIP" "$APK_PATH"
}

# To be overridden by device-level extract-files.sh
# Parameters:
#   $1: spec name of a blob. Can be used for filtering.
#       If the spec is "src:dest", then $1 is "dest".
#       If the spec is "src", then $1 is "src".
#   $2: path to blob file. Can be used for fixups.
#
function blob_fixup() {
    :
}

# To be overridden by device-level extract-files.sh
# Parameters:
#   $1: spec name of a blob. Can be used for filtering.
#       If the spec is "src:dest", then $1 is "dest".
#       If the spec is "src", then $1 is "src".
#
function blob_fixup_dry() {
    return 0
}

# To be overridden by device-level extract-files.sh
# Parameters:
#   $1: Path to vendor Android.bp
#
function vendor_imports() {
    :
}

#
# prepare_images:
#
# Positional parameters:
# $1: path to extracted system folder or an ota zip file
#
function prepare_images() {
    # Consume positional parameters
    local SRC="$1"
    shift
    KEEP_DUMP_DIR="$SRC"

    if [ -d "$SRC"/output ]; then
        EXTRACT_SRC="$SRC"/output
        EXTRACT_STATE=1
        return 0
    fi

    if [ -f "$SRC" ] && [ "${SRC##*.}" == "zip" ]; then
        local BASENAME=$(basename "$SRC")
        local DIRNAME=$(dirname "$SRC")
        DUMPDIR="$EXTRACT_TMP_DIR"/system_dump
        KEEP_DUMP_DIR="$DIRNAME"/"${BASENAME%.zip}"
        if [ "$KEEP_DUMP" == "true" ] || [ "$KEEP_DUMP" == "1" ]; then
            rm -rf "$KEEP_DUMP_DIR"
            mkdir "$KEEP_DUMP_DIR"
        fi

        # Check if we're working with the same zip that was passed last time.
        # If so, let's just use what's already extracted.
        MD5=$(md5sum "$SRC" | awk '{print $1}')
        OLDMD5=""
        if [ -f "$DUMPDIR/zipmd5.txt" ]; then
            OLDMD5=$(cat "$DUMPDIR/zipmd5.txt")
        fi

        if [ "$MD5" != "$OLDMD5" ]; then
            rm -rf "$DUMPDIR"
            mkdir "$DUMPDIR"
            unzip "$SRC" -d "$DUMPDIR"
            echo "$MD5" >"$DUMPDIR"/zipmd5.txt

            # Extract A/B OTA
            if [ -a "$DUMPDIR"/payload.bin ]; then
                for PARTITION in "system" "odm" "product" "system_ext" "vendor"; do
                    "$OTA_EXTRACTOR" --payload "$DUMPDIR"/payload.bin --output_dir "$DUMPDIR" --partitions "$PARTITION" &
                    2>&1
                done
                wait
            fi

            for PARTITION in "system" "odm" "product" "system_ext" "vendor"; do
                # If OTA is block based, extract it.
                if [ -a "$DUMPDIR"/"$PARTITION".new.dat.br ]; then
                    echo "Converting $PARTITION.new.dat.br to $PARTITION.new.dat"
                    brotli -d "$DUMPDIR"/"$PARTITION".new.dat.br
                    rm "$DUMPDIR"/"$PARTITION".new.dat.br
                fi
                if [ -a "$DUMPDIR"/"$PARTITION".new.dat ]; then
                    echo "Converting $PARTITION.new.dat to $PARTITION.img"
                    python "$ANDROID_ROOT"/tools/extract-utils/sdat2img.py "$DUMPDIR"/"$PARTITION".transfer.list "$DUMPDIR"/"$PARTITION".new.dat "$DUMPDIR"/"$PARTITION".img 2>&1
                    rm -rf "$DUMPDIR"/"$PARTITION".new.dat "$DUMPDIR"/"$PARTITION"
                    mkdir "$DUMPDIR"/"$PARTITION" "$DUMPDIR"/tmp
                    extract_img_data "$DUMPDIR"/"$PARTITION".img "$DUMPDIR"/"$PARTITION"/
                    rm "$DUMPDIR"/"$PARTITION".img
                fi
                if [ -a "$DUMPDIR"/"$PARTITION".img ]; then
                    extract_img_data "$DUMPDIR"/"$PARTITION".img "$DUMPDIR"/"$PARTITION"/
                fi
            done
        fi

        SRC="$DUMPDIR"
    fi

    local SUPERIMGS=()
    if [ -d "$SRC" ] && [ -f "$SRC"/super.img ]; then
        SUPERIMGS=("$SRC"/super.img)
    elif [ -d "$SRC" ] && [ -f "$SRC"/super.img_sparsechunk.0 ]; then
        readarray -t SUPERIMGS < <(find "$SRC" -name 'super.img_sparsechunk.*' | sort -V)
    fi

    if [ "${#SUPERIMGS[@]}" -ne 0 ]; then
        DUMPDIR="$EXTRACT_TMP_DIR"/super_dump
        mkdir -p "$DUMPDIR"

        echo "Unpacking super.img"
        "$SIMG2IMG" "${SUPERIMGS[@]}" "$DUMPDIR"/super.raw

        for PARTITION in "system" "odm" "product" "system_ext" "vendor"; do
            echo "Preparing $PARTITION"
            if "$LPUNPACK" -p "$PARTITION"_a "$DUMPDIR"/super.raw "$DUMPDIR"; then
                mv "$DUMPDIR"/"$PARTITION"_a.img "$DUMPDIR"/"$PARTITION".img
            else
                "$LPUNPACK" -p "$PARTITION" "$DUMPDIR"/super.raw "$DUMPDIR"
            fi
        done
        rm "$DUMPDIR"/super.raw

        if [ "$KEEP_DUMP" == "true" ] || [ "$KEEP_DUMP" == "1" ]; then
            rm -rf "$KEEP_DUMP_DIR"/super_dump
            cp -a "$DUMPDIR" "$KEEP_DUMP_DIR"/super_dump
        fi

        SRC="$DUMPDIR"
    fi

    if [ -d "$SRC" ] && [ -f "$SRC"/system.img ]; then
        DUMPDIR="$EXTRACT_TMP_DIR"/system_dump
        mkdir -p "$DUMPDIR"

        for PARTITION in "system" "odm" "product" "system_ext" "vendor"; do
            echo "Extracting $PARTITION"
            local IMAGE="$SRC"/"$PARTITION".img
            if [ -f "$IMAGE" ]; then
                if [[ $(file -b "$IMAGE") == EROFS* ]]; then
                    fsck.erofs --extract="$DUMPDIR"/"$PARTITION" "$IMAGE"
                elif [[ $(file -b "$IMAGE") == Linux* ]]; then
                    extract_img_data "$IMAGE" "$DUMPDIR"/"$PARTITION"
                elif [[ $(file -b "$IMAGE") == Android* ]]; then
                    "$SIMG2IMG" "$IMAGE" "$DUMPDIR"/"$PARTITION".raw
                    extract_img_data "$DUMPDIR"/"$PARTITION".raw "$DUMPDIR"/"$PARTITION"/
                else
                    echo "Unsupported $IMAGE"
                fi
            fi
        done

        if [ "$KEEP_DUMP" == "true" ] || [ "$KEEP_DUMP" == "1" ]; then
            rm -rf "$KEEP_DUMP_DIR"/output
            cp -a "$DUMPDIR" "$KEEP_DUMP_DIR"/output
        fi

        SRC="$DUMPDIR"
    fi

    EXTRACT_SRC="$SRC"
    EXTRACT_STATE=1
}

#
# extract:
#
# Positional parameters:
# $1: file containing the list of items to extract (aka proprietary-files.txt)
# $2: path to extracted system folder, an ota zip file, or "adb" to extract from device
# $3: section in list file to extract - optional. Setting section via $3 is deprecated.
#
# Non-positional parameters (coming after $2):
# --section: preferred way of selecting the portion to parse and extract from
#            proprietary-files.txt
# --kang: if present, this option will activate the printing of hashes for the
#         extracted blobs. Useful with --section for subsequent pinning of
#         blobs taken from other origins.
#
function extract() {
    # Consume positional parameters
    local PROPRIETARY_FILES_TXT="$1"
    shift
    local SRC="$1"
    shift
    local SECTION=""
    local KANG=false

    # Consume optional, non-positional parameters
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -s | --section)
                SECTION="$2"
                shift
                ;;
            -k | --kang)
                KANG=true
                DISABLE_PINNING=1
                ;;
            *)
                # Backwards-compatibility with the old behavior, where $3, if
                # present, denoted an optional positional ${SECTION} argument.
                # Users of ${SECTION} are encouraged to migrate from setting it as
                # positional $3, to non-positional --section ${SECTION}, the
                # reason being that it doesn't scale to have more than 1 optional
                # positional argument.
                SECTION="$1"
                ;;
        esac
        shift
    done

    if [ -z "$OUTDIR" ]; then
        echo "Output dir not set!"
        exit 1
    fi

    parse_file_list "${PROPRIETARY_FILES_TXT}" "${SECTION}"

    # Allow failing, so we can try $DEST and/or $FILE
    set +e

    local HASHLIST=("${PRODUCT_COPY_FILES_HASHES[@]}")
    local SRC_LIST=("${PRODUCT_COPY_FILES_SRC[@]}")
    local DEST_LIST=("${PRODUCT_COPY_FILES_DEST[@]}")
    local ARGS_LIST=("${PRODUCT_COPY_FILES_ARGS[@]}")
    local FIXUP_HASHLIST=("${PRODUCT_COPY_FILES_FIXUP_HASHES[@]}")
    local PACKAGE_LIST=("${PRODUCT_COPY_FILES_PACKAGE[@]}")
    local COUNT=${#SRC_LIST[@]}
    local OUTPUT_ROOT="$ANDROID_ROOT"/"$OUTDIR"/proprietary
    local OUTPUT_TMP="$EXTRACT_TMP_DIR"/"$OUTDIR"/proprietary

    if [ "$SRC" = "adb" ]; then
        init_adb_connection
    fi

    if [ "$EXTRACT_STATE" -ne "1" ]; then
        prepare_images "$SRC"
    fi

    if [ "$VENDOR_STATE" -eq "0" ]; then
        echo "Cleaning output directory ($OUTPUT_ROOT).."
        rm -rf "${OUTPUT_TMP:?}"
        mkdir -p "${OUTPUT_TMP:?}"
        if [ -d "$OUTPUT_ROOT" ]; then
            mv "${OUTPUT_ROOT:?}/"* "${OUTPUT_TMP:?}/"
        fi
        VENDOR_STATE=1
    fi

    echo "Extracting ${COUNT} files in ${PROPRIETARY_FILES_TXT} from ${EXTRACT_SRC}:"

    for ((i = 1; i < COUNT + 1; i++)); do
        local IS_PRODUCT_PACKAGE="${PACKAGE_LIST[$i - 1]}"
        local SPEC_SRC_FILE="${SRC_LIST[$i - 1]}"
        local SPEC_DST_FILE="${DEST_LIST[$i - 1]}"
        local SPEC_ARGS="${ARGS_LIST[$i - 1]}"
        local ARGS=(${SPEC_ARGS//;/ })
        local OUTPUT_DIR=
        local TMP_DIR=
        local SRC_FILE=
        local DST_FILE=

        OUTPUT_DIR="${OUTPUT_ROOT}"
        TMP_DIR="${OUTPUT_TMP}"
        SRC_FILE="/system/${SPEC_SRC_FILE}"
        DST_FILE="/system/${SPEC_DST_FILE}"

        local BLOB_DISPLAY_NAME="${SPEC_DST_FILE}"
        local VENDOR_REPO_FILE="$OUTPUT_DIR/${BLOB_DISPLAY_NAME}"
        local DIR="${VENDOR_REPO_FILE%/*}"
        if [ ! -d "$DIR" ]; then
            mkdir -p "$DIR"
        fi

        # Check pinned files
        local HASH="${HASHLIST[$i - 1]}"
        local FIXUP_HASH="${FIXUP_HASHLIST[$i - 1]}"
        local KEEP=""
        if [ "$DISABLE_PINNING" != "1" ] && [ -n "$HASH" ]; then
            if [ -f "${VENDOR_REPO_FILE}" ]; then
                local PINNED="${VENDOR_REPO_FILE}"
            else
                local PINNED="${TMP_DIR}${DST_FILE#/system}"
            fi
            if [ -f "$PINNED" ]; then
                local TMP_HASH=$(get_hash "${PINNED}")
                if [ "${TMP_HASH}" = "${HASH}" ] || [ "${TMP_HASH}" = "${FIXUP_HASH}" ]; then
                    KEEP="1"
                    if [ ! -f "${VENDOR_REPO_FILE}" ]; then
                        cp -p "$PINNED" "${VENDOR_REPO_FILE}"
                    fi
                fi
            fi
        fi

        if [ "${KANG}" = false ]; then
            printf '  - %s\n' "${BLOB_DISPLAY_NAME}"
        fi

        if [ "$KEEP" = "1" ]; then
            if [ -n "${FIXUP_HASH}" ]; then
                printf '    + Keeping pinned file with hash %s\n' "${FIXUP_HASH}"
            else
                printf '    + Keeping pinned file with hash %s\n' "${HASH}"
            fi
        else
            local FOUND=false
            # Try custom target first.
            for CANDIDATE in "${DST_FILE}" "${SRC_FILE}"; do
                get_file "${CANDIDATE}" "${VENDOR_REPO_FILE}" "${EXTRACT_SRC}" && {
                    FOUND=true
                    break
                }
            done

            if [ "${FOUND}" = false ]; then
                colored_echo red "    !! ${BLOB_DISPLAY_NAME}: file not found in source"
                continue
            fi

            # Blob fixup pipeline has 2 parts: one that is fixed and
            # one that is user-configurable
            local PRE_FIXUP_HASH=
            local POST_FIXUP_HASH=

            # Deodex apk|jar if that's the case
            if [[ "$FULLY_DEODEXED" -ne "1" && "${VENDOR_REPO_FILE}" =~ .(apk|jar)$ ]]; then
                PRE_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
                oat2dex "${VENDOR_REPO_FILE}" "${SRC_FILE}" "$EXTRACT_SRC"
                if [ -f "$EXTRACT_TMP_DIR/classes.dex" ]; then
                    touch -t 200901010000 "$EXTRACT_TMP_DIR/classes"*
                    zip -gjq "${VENDOR_REPO_FILE}" "$EXTRACT_TMP_DIR/classes"*
                    rm "$EXTRACT_TMP_DIR/classes"*
                    printf '    (updated %s from odex files)\n' "${SRC_FILE}"
                fi
            elif [[ "$TARGET_DISABLE_XML_FIXING" != true && "${VENDOR_REPO_FILE}" =~ .xml$ ]]; then
                PRE_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
                fix_xml "${VENDOR_REPO_FILE}"
            elif [ "$KANG" = true ]; then
                PRE_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
            fi

            for ARG in "${ARGS[@]}"; do
                if [[ "$ARG" == "FIX_SONAME" ]]; then
                    PRE_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
                    fix_soname "${VENDOR_REPO_FILE}"
                elif [[ "$ARG" == "FIX_XML" ]]; then
                    PRE_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
                    fix_xml "${VENDOR_REPO_FILE}"
                fi
            done

            blob_fixup_dry "$BLOB_DISPLAY_NAME"
            if [ $? -ne 1 ]; then
                if [ -z "$PRE_FIXUP_HASH" ]; then
                    PRE_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
                fi

                # Now run user-supplied fixup function
                blob_fixup "$BLOB_DISPLAY_NAME" "$VENDOR_REPO_FILE"
            fi

            if [ -n "$PRE_FIXUP_HASH" ]; then
                POST_FIXUP_HASH=$(get_hash "$VENDOR_REPO_FILE")
            fi

            if [ "${KANG}" = true ]; then
                print_spec "${IS_PRODUCT_PACKAGE}" "${SPEC_SRC_FILE}" "${SPEC_DST_FILE}" "${SPEC_ARGS}" "${PRE_FIXUP_HASH}" "${POST_FIXUP_HASH}"
            fi

            # Check and print whether the fixup pipeline actually did anything.
            # This isn't done right after the fixup pipeline because we want this print
            # to come after print_spec above, when in kang mode.
            if [ "${PRE_FIXUP_HASH}" != "${POST_FIXUP_HASH}" ]; then
                printf "    + Fixed up %s\n" "${BLOB_DISPLAY_NAME}"
                # Now sanity-check the spec for this blob.
                if [ "${KANG}" = false ] && [ -z "${FIXUP_HASH}" ] && [ -n "${HASH}" ]; then
                    colored_echo yellow "WARNING: The ${BLOB_DISPLAY_NAME} file was fixed up, but it is pinned."
                    colored_echo yellow "This is a mistake and you want to either remove the hash completely, or add an extra one."
                fi
            fi
        fi

    done

    # Don't allow failing
    set -e
}

#
# extract_carriersettings:
#
# Convert prebuilt protobuf CarrierSettings files to CarrierConfig vendor.xml
#
function extract_carriersettings() {
    local CARRIERSETTINGS_EXTRACTOR="$ANDROID_ROOT"/lineage/scripts/carriersettings-extractor/carriersettings_extractor.py
    local SRC="$ANDROID_ROOT"/"$OUTDIR"/proprietary/product/etc/CarrierSettings
    local CARRIERSETTINGS_OUTPUT_DIR="$ANDROID_ROOT"/"$OUTDIR"/rro_overlays/CarrierConfigOverlay/res/xml

    mkdir -p "$CARRIERSETTINGS_OUTPUT_DIR"
    python3 "$CARRIERSETTINGS_EXTRACTOR" -i "$SRC" -v "$CARRIERSETTINGS_OUTPUT_DIR"
}

#
# To be overridden by device-level extract-files.sh
#
function prepare_firmware() {
    :
}

#
# extract_firmware:
#
# $1: file containing the list of items to extract
# $2: path to extracted radio folder
#
function extract_firmware() {
    if [ -z "$OUTDIR" ]; then
        echo "Output dir not set!"
        exit 1
    fi

    parse_file_list "$1"

    # Don't allow failing
    set -e

    local SRC_LIST=("${PRODUCT_COPY_FILES_SRC[@]}")
    local DEST_LIST=("${PRODUCT_COPY_FILES_DEST[@]}")
    local ARGS_LIST=("${PRODUCT_COPY_FILES_ARGS[@]}")
    local COUNT=${#SRC_LIST[@]}
    local SRC="$2"
    local OUTPUT_DIR="$ANDROID_ROOT"/"$OUTDIR"/radio

    if [ "$VENDOR_RADIO_STATE" -eq "0" ]; then
        echo "Cleaning firmware output directory ($OUTPUT_DIR).."
        rm -rf "${OUTPUT_DIR:?}/"*
        VENDOR_RADIO_STATE=1
    fi

    if [ -d "$SRC"/radio ]; then
        EXTRACT_RADIO_STATE=1
    fi

    echo "Extracting $COUNT files in $1 from $SRC:"

    if [ "$EXTRACT_STATE" -ne "1" ]; then
        prepare_images "$SRC"
    fi

    if [ "$EXTRACT_RADIO_STATE" -ne "1" ]; then
        if [ "$KEEP_DUMP" == "true" ] || [ "$KEEP_DUMP" == "1" ]; then
            rm -rf "$KEEP_DUMP_DIR"/radio
            mkdir "$KEEP_DUMP_DIR"/radio
        fi

        prepare_firmware
    fi

    for ((i = 1; i < COUNT + 1; i++)); do
        local SRC_FILE="${SRC_LIST[$i - 1]}"
        local DST_FILE="${DEST_LIST[$i - 1]}"
        local COPY_FILE=

        printf '  - %s \n' "radio/$DST_FILE"

        if [ ! -d "$OUTPUT_DIR" ]; then
            mkdir -p "$OUTPUT_DIR"
        fi
        if [ "$SRC" = "adb" ]; then
            local PARTITION="${DST_FILE%.*}"

            if [ "${ARGS_LIST[$i - 1]}" = "AB" ]; then
                local SLOT=$(adb shell getprop ro.boot.slot_suffix | rev | cut -c1)
                PARTITION="${PARTITION}_${SLOT}"
            fi

            if adb pull "/dev/block/by-name/${PARTITION}" "$OUTPUT_DIR/$DST_FILE"; then
                chmod 644 "$OUTPUT_DIR/$DST_FILE"
            else
                colored_echo yellow "${DST_FILE} not found, skipping copy"
            fi

            continue
        fi
        if [ -f "$SRC" ] && [ "${SRC##*.}" == "zip" ]; then
            # Extract A/B OTA
            if [ -a "$DUMPDIR"/payload.bin ]; then
                "$OTA_EXTRACTOR" --payload "$DUMPDIR"/payload.bin --output_dir "$DUMPDIR" --partitions "$(basename "${DST_FILE%.*}")" 2>&1
                if [ -f "$DUMPDIR/$(basename "$DST_FILE")" ]; then
                    COPY_FILE="$DUMPDIR/$(basename "$DST_FILE")"
                fi
            fi
        else
            if [ -f "$SRC/$SRC_FILE" ]; then
                COPY_FILE="$SRC/$SRC_FILE"
            elif [ -f "$SRC/radio/$SRC_FILE" ]; then
                COPY_FILE="$SRC/radio/$SRC_FILE"
            elif [ -f "$SRC/$DST_FILE" ]; then
                COPY_FILE="$SRC/$DST_FILE"
            fi
            if [[ $(file -b "$COPY_FILE") == Android* ]]; then
                "$SIMG2IMG" "$COPY_FILE" "$SRC"/"$(basename "$COPY_FILE").raw"
                COPY_FILE="$SRC"/"$(basename "$COPY_FILE").raw"
            fi
        fi

        if [ -f "$COPY_FILE" ]; then
            cp "$COPY_FILE" "$OUTPUT_DIR/$DST_FILE"
            chmod 644 "$OUTPUT_DIR/$DST_FILE"
            if [ "$KEEP_DUMP" == "true" ] || [ "$KEEP_DUMP" == "1" ]; then
                cp "$OUTPUT_DIR/$DST_FILE" "$KEEP_DUMP_DIR"/radio/
            fi
        else
            colored_echo yellow "${DST_FILE} not found, skipping copy"
        fi
    done
}

function extract_img_data() {
    local IMAGE_FILE="$1"
    local OUT_DIR="$2"
    local LOG_FILE="$EXTRACT_TMP_DIR/debugfs.log"

    if [ ! -d "$OUT_DIR" ]; then
        mkdir -p "$OUT_DIR"
    fi

    debugfs -R 'ls -p' "$IMAGE_FILE" 2>/dev/null | cut -d '/' -f6 | while read -r ENTRY; do
        debugfs -R "rdump \"$ENTRY\" \"$OUT_DIR\"" "$IMAGE_FILE" >>"$LOG_FILE" 2>&1 || {
            echo "[-] Failed to extract data from '$IMAGE_FILE'"
            abort 1
        }
    done

    local SYMLINK_ERR="rdump: Attempt to read block from filesystem resulted in short read while reading symlink"
    if grep -Fq "$SYMLINK_ERR" "$LOG_FILE"; then
        echo "[-] Symlinks have not been properly processed from $IMAGE_FILE"
        echo "[!] You might not have a compatible debugfs version"
        abort 1
    fi
}

function array_contains() {
    local ELEMENT
    for ELEMENT in "${@:2}"; do [[ "$ELEMENT" == "$1" ]] && return 0; done
    return 1
}

function generate_prop_list_from_image() {
    local IMAGE_FILE="$1"
    local IMAGE_DIR="$EXTRACT_TMP_DIR/image-temp"
    local OUTPUT_LIST="$2"
    local OUTPUT_LIST_TMP="$EXTRACT_TMP_DIR/_proprietary-blobs.txt"
    local -n SKIPPED_FILES="$3"
    local COMPONENT="$4"
    local PARTITION="$COMPONENT"

    mkdir -p "$IMAGE_DIR"

    if [ -f "$EXTRACT_TMP_DIR"/super_dump/"$IMAGE_FILE" ]; then
        IMAGE_FILE="$EXTRACT_TMP_DIR"/super_dump/"$IMAGE_FILE"
    elif [ -f "$EXTRACT_TMP_DIR"/"$IMAGE_FILE" ]; then
        IMAGE_FILE="$EXTRACT_TMP_DIR"/"$IMAGE_FILE"
    elif [ -f "$SRC"/super_dump/"$IMAGE_FILE" ]; then
        IMAGE_FILE="$SRC"/super_dump/"$IMAGE_FILE"
    elif [ -f "$SRC"/"$IMAGE_FILE" ]; then
        IMAGE_FILE="$SRC"/"$IMAGE_FILE"
    elif [ ! -f "$IMAGE_FILE" ]; then
        colored_echo yellow "$IMAGE_FILE not found, skipping $OUTPUT_LIST regen"
        return 0
    fi

    if [[ $(file -b "$IMAGE_FILE") == EROFS* ]]; then
        fsck.erofs --extract="$IMAGE_DIR" "$IMAGE_FILE"
    elif [[ $(file -b "$IMAGE_FILE") == Linux* ]]; then
        extract_img_data "$IMAGE_FILE" "$IMAGE_DIR"
    elif [[ $(file -b "$IMAGE_FILE") == Android* ]]; then
        "$SIMG2IMG" "$IMAGE_FILE" "$IMAGE_DIR"/"$(basename "$IMAGE_FILE").raw"
        extract_img_data "$IMAGE_DIR"/"$(basename "$IMAGE_FILE").raw" "$IMAGE_DIR"
        rm "$IMAGE_DIR"/"$(basename "$IMAGE_FILE").raw"
    else
        colored_echo yellow "Unsupported $IMAGE_FILE filesystem, skipping $OUTPUT_LIST regen"
        return 0
    fi

    if [ -z "$COMPONENT" ]; then
        PARTITION="vendor"
    elif [[ "$COMPONENT" == "carriersettings" ]]; then
        PARTITION="product"
    fi

    echo "# All blobs below are extracted from the release mentioned in proprietary-files.txt" >"$OUTPUT_LIST_TMP"

    find "$IMAGE_DIR" -not -type d | sed "s#^$IMAGE_DIR/##" | while read -r FILE; do
        if [[ "$COMPONENT" == "carriersettings" ]] && ! prefix_match_file "etc/CarrierSettings" "$FILE"; then
            continue
        fi
        if suffix_match_file ".odex" "$FILE" || suffix_match_file ".vdex" "$FILE"; then
            continue
        fi
        # Skip device defined skipped files since they will be re-generated at build time
        if array_contains "$FILE" "${SKIPPED_FILES[@]}"; then
            continue
        fi
        echo "$PARTITION/$FILE" >>"$OUTPUT_LIST_TMP"
    done

    # Sort merged file with all lists
    LC_ALL=C sort -u "$OUTPUT_LIST_TMP" >"$OUTPUT_LIST"

    # Clean-up
    rm -rf "$IMAGE_DIR"
    rm -f "$OUTPUT_LIST_TMP"
}

function colored_echo() {
    IFS=" "
    local COLOR=$1
    shift
    if ! [[ $COLOR =~ ^[0-9]$ ]]; then
        case $(echo "$COLOR" | tr '[:upper:]' '[:lower:]') in
            black) COLOR=0 ;;
            red) COLOR=1 ;;
            green) COLOR=2 ;;
            yellow) COLOR=3 ;;
            blue) COLOR=4 ;;
            magenta) COLOR=5 ;;
            cyan) COLOR=6 ;;
            white | *) COLOR=7 ;; # white or invalid color
        esac
    fi
    if [ -t 1 ]; then tput setaf "$COLOR"; fi
    printf '%s\n' "$*"
    if [ -t 1 ]; then tput sgr0; fi
}

# Helper functions to easily apply modifications to automatically generated vendor blob lists
function set_as_module() {
    sed -i "s|${1}$|-${1}|g" "${2}"
}

function set_disable_checkelf() {
    sed -i "s|${1}$|${1};DISABLE_CHECKELF|g" "${2}"
}

function set_disable_deps() {
    sed -i "s|${1}$|${1};DISABLE_DEPS|g" "${2}"
}

function set_fix_soname() {
    sed -i "s|${1}$|${1};FIX_SONAME|g" "${2}"
}

function set_fix_xml() {
    sed -i "s|${1}$|${1};FIX_XML|g" "${2}"
}

function set_module() {
    sed -i "s|${1}$|${1};MODULE=${2}|g" "${3}"
}

function set_module_suffix() {
    sed -i "s|${1}$|${1};MODULE_SUFFIX=${2}|g" "${3}"
}

function set_presigned() {
    sed -i "s|${1}$|${1};PRESIGNED|g" "${2}"
}

function set_required() {
    sed -i "s|${1}$|${1};REQUIRED=${2}|g" "${3}"
}

function set_symlink() {
    sed -i "s|${1}$|${1};SYMLINK=${2}|g" "${3}"
}
