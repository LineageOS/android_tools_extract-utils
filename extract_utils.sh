#!/bin/bash
#
# SPDX-FileCopyrightText: 2016 The CyanogenMod Project
# SPDX-FileCopyrightText: 2017-2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

PRODUCT_COPY_FILES_HASHES=()
PRODUCT_COPY_FILES_FIXUP_HASHES=()
PRODUCT_COPY_FILES_SRC=()
PRODUCT_COPY_FILES_DEST=()
PRODUCT_COPY_FILES_ARGS=()
PRODUCT_PACKAGES_HASHES=()
PRODUCT_PACKAGES_FIXUP_HASHES=()
PRODUCT_PACKAGES_SRC=()
PRODUCT_PACKAGES_DEST=()
PRODUCT_PACKAGES_ARGS=()
PRODUCT_SYMLINKS_LIST=()
PACKAGE_LIST=()
REQUIRED_PACKAGES_LIST=
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
    export CLANG_BINUTILS="$ANDROID_ROOT"/prebuilts/clang/host/${HOST}-x86/llvm-binutils-stable
    export JDK_BINARIES_LOCATION="$ANDROID_ROOT"/prebuilts/jdk/jdk21/${HOST}-x86/bin
    export COMMON_BINARIES_LOCATION="$ANDROID_ROOT"/prebuilts/extract-tools/common

    export SIMG2IMG="$BINARIES_LOCATION"/simg2img
    export LPUNPACK="$BINARIES_LOCATION"/lpunpack
    export OTA_EXTRACTOR="$BINARIES_LOCATION"/ota_extractor
    export SIGSCAN="$BINARIES_LOCATION"/SigScan
    export STRIPZIP="$BINARIES_LOCATION"/stripzip
    export OBJDUMP="$CLANG_BINUTILS"/llvm-objdump
    export JAVA="$JDK_BINARIES_LOCATION"/java
    export APKTOOL="$COMMON_BINARIES_LOCATION"/apktool/apktool.jar

    for VERSION in 0_8 0_9 0_17_2 0_18; do
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
            local SPEC_ARGS="${ARGS_LIST[$i - 1]}"
            local ARGS=(${SPEC_ARGS//;/ })
            local FILTERED_ARGS=()

            for ARG in "${ARGS[@]}"; do
                if [[ "$ARG" =~ ^SYMLINK= ]]; then
                    continue
                fi
                FILTERED_ARGS+=("$ARG")
            done

            FILTERED_ARGS=$(IFS=";" echo "${FILTERED_ARGS[@]}")

            if [ -z "$FILTERED_ARGS" ]; then
                NEW_ARRAY+=("${FILE#"$PREFIX"}")
            else
                NEW_ARRAY+=("${FILE#"$PREFIX"};${FILTERED_ARGS}")
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

#
# write_product_copy_files:
#
# Creates the PRODUCT_COPY_FILES section in the product makefile for all
# items in the list which do not start with a dash (-).
#
function write_product_copy_files() {
    local COUNT=${#PRODUCT_COPY_FILES_DEST[@]}
    local TARGET=
    local FILE=
    local LINEEND=

    if [ "$COUNT" -eq "0" ]; then
        return 0
    fi

    printf '%s\n' "PRODUCT_COPY_FILES += \\" >>"$PRODUCTMK"
    for ((i = 1; i < COUNT + 1; i++)); do
        TARGET="${PRODUCT_COPY_FILES_DEST[$i - 1]}"
        LINEEND=" \\"
        if [ "$i" -eq "$COUNT" ]; then
            LINEEND=""
        fi

        if prefix_match_file "product/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_PRODUCT)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "system/product/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_PRODUCT)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "system_ext/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_SYSTEM_EXT)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "system/system_ext/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_SYSTEM_EXT)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "odm/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_ODM)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "vendor/odm/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_ODM)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "system/vendor/odm/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_ODM)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "vendor/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_VENDOR)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "vendor_dlkm/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_VENDOR_DLKM)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "system/vendor/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_VENDOR)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "system/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_SYSTEM)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "recovery/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_RECOVERY)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        elif prefix_match_file "vendor_ramdisk/" "$TARGET"; then
            local OUTTARGET=$(truncate_file "$TARGET")
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_VENDOR_RAMDISK)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$OUTTARGET" "$LINEEND" >>"$PRODUCTMK"
        else
            printf '    %s/proprietary/%s:$(TARGET_COPY_OUT_SYSTEM)/%s%s\n' \
                "$OUTDIR" "$TARGET" "$TARGET" "$LINEEND" >>"$PRODUCTMK"
        fi
    done
    return 0
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
# write_package_shared_libs:
#
# $1: File name inside target list
#
function write_package_shared_libs() {
    local SRC="$1"
    local LOCATION="$2"
    local FILE="$3"
    local PARTITION="$4"

    local FILE_PATH="$ANDROID_ROOT/$OUTDIR/$SRC/$LOCATION/$FILE"
    local LIBS=$("$OBJDUMP" -p "$FILE_PATH" 2>/dev/null | sed -n 's/^\s*NEEDED\s*\(.*\).so$/\1/p')
    local PACKAGES=$(
        while IFS= read -r LIB; do
            lib_to_package_fixup "$LIB" "$PARTITION" "$FILE" || echo "$LIB"
        done <<<"$LIBS"
    )
    local PACKAGES_LIST=$(echo "$PACKAGES" | sed 's/\(.\+\)/"\1",/g' | tr '\n' ' ')

    printf '\t\t\tshared_libs: [%s],\n' "$PACKAGES_LIST"
}

#
# write_blueprint_packages:
#
# $1: The LOCAL_MODULE_CLASS for the given module list
# $2: /system, /odm, /product, /system_ext, or /vendor partition
# $3: type-specific extra flags
# $4: Target list separated by newlines
#
# Internal function which writes out the BUILD_PREBUILT stanzas
# for all modules in the list. This is called by write_product_packages
# after the modules are categorized.
#
function write_blueprint_packages() {
    local CLASS="$1"
    local PARTITION="$2"
    local EXTRA="$3"
    local FILELIST="$4"

    local BASENAME=
    local EXTENSION=
    local PKGNAME=
    local SRC=
    local STEM=
    local OVERRIDEPKG=
    local REQUIREDPKG=
    local DISABLE_CHECKELF=
    local GENERATE_DEPS=

    if [ -z "$EXTRA" ]; then
        if [ "$CLASS" = "RFSA" ]; then
            EXTRA="lib/rfsa"
        elif [ "$CLASS" = "APEX" ]; then
            EXTRA="apex"
        elif [ "$CLASS" = "APPS" ]; then
            EXTRA="app"
        elif [ "$CLASS" = "JAVA_LIBRARIES" ]; then
            EXTRA="framework"
        elif [ "$CLASS" = "ETC" ]; then
            EXTRA="etc"
        elif [ "$CLASS" = "EXECUTABLES" ]; then
            EXTRA="bin"
        fi
    fi

    local SRC_REL=
    if [ -n "$PARTITION" ]; then
        SRC_REL+="$PARTITION"
    fi

    # SHARED_LIBRARIES's EXTRA is not a path
    if [ -n "$EXTRA" ] &&
        [ "$CLASS" != "SHARED_LIBRARIES" ]; then
        if [ -n "$SRC_REL" ]; then
            SRC_REL+="/"
        fi

        SRC_REL+="$EXTRA"
    fi

    # Automatically match everything except SHARED_LIBRARIES and RFSA
    if [ -z "$FILELIST" ] &&
        [ "$CLASS" != "SHARED_LIBRARIES" ] &&
        [ "$CLASS" != "RFSA" ]; then

        FILELIST=$(prefix_match "$SRC_REL/")
    fi

    local SRC="proprietary/$SRC_REL"

    [ "$COMMON" -eq 1 ] && local VENDOR="${VENDOR_COMMON:-$VENDOR}"

    while IFS= read -r P; do
        if [ "$P" = "" ]; then
            continue
        fi

        # prefix_match results already only contain the dest part
        local FILE=$(spec "$P")
        local SPEC_ARGS=$(spec_target_args "$FILE" "$P")
        local ARGS=(${SPEC_ARGS//;/ })
        local DIRNAME="${FILE%/*}"
        if [ "$DIRNAME" = "$FILE" ]; then
            DIRNAME="."
        fi

        local BASENAME="${FILE##*/}"
        EXTENSION=${BASENAME##*.}
        PKGNAME=${BASENAME%.*}

        if ([ "$CLASS" = "EXECUTABLES" ] && [ "$EXTENSION" != "sh" ]) || [ "$PKGNAME" = "" ]; then
            PKGNAME="$BASENAME"
            EXTENSION=""
        fi

        if [ "$CLASS" = "ETC" ] && [ "$EXTENSION" = "xml" ]; then
            PKGNAME="$BASENAME"
        fi

        # Allow overriding module name
        STEM=
        if [ "$TARGET_ENABLE_CHECKELF" != "false" ]; then
            DISABLE_CHECKELF=
            GENERATE_DEPS="true"
        else
            DISABLE_CHECKELF="true"
        fi
        for ARG in "${ARGS[@]}"; do
            if [[ "$ARG" =~ "MODULE_SUFFIX" ]]; then
                STEM="$PKGNAME"
                PKGNAME+=${ARG#*=}
            elif [[ "$ARG" =~ "MODULE" ]]; then
                STEM="$PKGNAME"
                PKGNAME=${ARG#*=}
            elif [[ "$ARG" == "DISABLE_CHECKELF" ]]; then
                DISABLE_CHECKELF="true"
            elif [[ "$ARG" == "DISABLE_DEPS" ]]; then
                DISABLE_CHECKELF="true"
                GENERATE_DEPS=
            fi
        done

        # Add to final package list
        PACKAGE_LIST+=("$PKGNAME")

        if [ "$CLASS" = "SHARED_LIBRARIES" ]; then
            printf 'cc_prebuilt_library_shared {\n'
            printf '\tname: "%s",\n' "$PKGNAME"
            if [ -n "$STEM" ]; then
                printf '\tstem: "%s",\n' "$STEM"
            fi
            printf '\towner: "%s",\n' "$VENDOR"
            printf '\tstrip: {\n'
            printf '\t\tnone: true,\n'
            printf '\t},\n'
            printf '\ttarget: {\n'
            if [ "$EXTRA" = "both" ] || [ "$EXTRA" = "32" ]; then
                printf '\t\t%s: {\n' $(elf_format_android "$ANDROID_ROOT/$OUTDIR/$SRC/lib/$FILE")
                printf '\t\t\tsrcs: ["%s/lib/%s"],\n' "$SRC" "$FILE"
                if [ -n "$GENERATE_DEPS" ]; then
                    write_package_shared_libs "$SRC" "lib" "$FILE" "$PARTITION"
                fi
                printf '\t\t},\n'
            fi

            if [ "$EXTRA" = "both" ] || [ "$EXTRA" = "64" ]; then
                printf '\t\t%s: {\n' $(elf_format_android "$ANDROID_ROOT/$OUTDIR/$SRC/lib64/$FILE")
                printf '\t\t\tsrcs: ["%s/lib64/%s"],\n' "$SRC" "$FILE"
                if [ -n "$GENERATE_DEPS" ]; then
                    write_package_shared_libs "$SRC" "lib64" "$FILE" "$PARTITION"
                fi
                printf '\t\t},\n'
            fi
            printf '\t},\n'
            printf '\tcompile_multilib: "%s",\n' "$EXTRA"
            if [ -n "$DISABLE_CHECKELF" ]; then
                printf '\tcheck_elf_files: false,\n'
            fi
        elif [ "$CLASS" = "RFSA" ]; then
            printf 'prebuilt_rfsa {\n'
            printf '\tname: "%s",\n' "$PKGNAME"
            printf '\tfilename: "%s",\n' "$BASENAME"
            printf '\towner: "%s",\n' "$VENDOR"
            printf '\tsrc: "%s/%s",\n' "$SRC" "$FILE"
        elif [ "$CLASS" = "APEX" ]; then
            printf 'prebuilt_apex {\n'
            printf '\tname: "%s",\n' "$PKGNAME"
            printf '\towner: "%s",\n' "$VENDOR"
            printf '\tsrc: "%s/%s",\n' "$SRC" "$FILE"
            printf '\tfilename: "%s",\n' "$FILE"
        elif [ "$CLASS" = "APPS" ]; then
            printf 'android_app_import {\n'
            printf '\tname: "%s",\n' "$PKGNAME"
            printf '\towner: "%s",\n' "$VENDOR"
            printf '\tapk: "%s/%s",\n' "$SRC" "$FILE"
            USE_PLATFORM_CERTIFICATE="true"
            for ARG in "${ARGS[@]}"; do
                if [ "$ARG" = "PRESIGNED" ]; then
                    USE_PLATFORM_CERTIFICATE="false"
                    printf '\tpreprocessed: true,\n'
                    printf '\tpresigned: true,\n'
                elif [ "$ARG" = "SKIPAPKCHECKS" ]; then
                    printf '\tskip_preprocessed_apk_checks: true,\n'
                elif [[ "$ARG" =~ "OVERRIDES" ]]; then
                    OVERRIDEPKG=${ARG#*=}
                    OVERRIDEPKG=${OVERRIDEPKG//,/\", \"}
                    printf '\toverrides: ["%s"],\n' "$OVERRIDEPKG"
                elif [[ "$ARG" =~ "REQUIRED" ]]; then
                    REQUIREDPKG=${ARG#*=}
                    REQUIRED_PACKAGES_LIST+="$REQUIREDPKG,"
                    printf '\trequired: ["%s"],\n' "${REQUIREDPKG//,/\", \"}"
                elif [[ "$ARG" =~ "SYMLINK" ]]; then
                    continue
                elif [ -n "$ARG" ]; then
                    USE_PLATFORM_CERTIFICATE="false"
                    printf '\tcertificate: "%s",\n' "$ARG"
                fi
            done
            if [ "$USE_PLATFORM_CERTIFICATE" = "true" ]; then
                printf '\tcertificate: "platform",\n'
            fi
        elif [ "$CLASS" = "JAVA_LIBRARIES" ]; then
            printf 'dex_import {\n'
            printf '\tname: "%s",\n' "$PKGNAME"
            printf '\towner: "%s",\n' "$VENDOR"
            printf '\tjars: ["%s/%s"],\n' "$SRC" "$FILE"
        elif [ "$CLASS" = "ETC" ]; then
            if [ "$EXTENSION" = "xml" ]; then
                printf 'prebuilt_etc_xml {\n'
            else
                printf 'prebuilt_etc {\n'
            fi
            printf '\tname: "%s",\n' "$PKGNAME"
            printf '\towner: "%s",\n' "$VENDOR"
            printf '\tsrc: "%s/%s",\n' "$SRC" "$FILE"
            printf '\tfilename_from_src: true,\n'
        elif [ "$CLASS" = "EXECUTABLES" ]; then
            local FILE_PATH="$ANDROID_ROOT/$OUTDIR/$SRC/$FILE"
            local ELF_FORMAT=$(elf_format_android "$FILE_PATH")
            if [ "$ELF_FORMAT" = "" ]; then
                # This is not an elf file, assume it's a shell script that doesn't have an extension
                # Setting extension here does not change the target extension, only the module type
                EXTENSION="sh"
            fi
            if [ "$EXTENSION" = "sh" ]; then
                printf 'sh_binary {\n'
            else
                printf 'cc_prebuilt_binary {\n'
            fi
            printf '\tname: "%s",\n' "$PKGNAME"
            if [ -n "$STEM" ]; then
                printf '\tstem: "%s",\n' "$STEM"
            fi
            printf '\towner: "%s",\n' "$VENDOR"
            if [ "$EXTENSION" != "sh" ]; then
                printf '\ttarget: {\n'
                printf '\t\t%s: {\n' "$ELF_FORMAT"
                printf '\t\t\tsrcs: ["%s/%s"],\n' "$SRC" "$FILE"
                if [ -n "$GENERATE_DEPS" ]; then
                    write_package_shared_libs "$SRC" "" "$FILE" "$PARTITION"
                fi
                printf '\t\t},\n'
                printf '\t},\n'
                if [[ "$ELF_FORMAT" =~ "64" ]]; then
                    printf '\tcompile_multilib: "%s",\n' "64"
                else
                    printf '\tcompile_multilib: "%s",\n' "32"
                fi
                if [ -n "$DISABLE_CHECKELF" ]; then
                    printf '\tcheck_elf_files: false,\n'
                fi
                printf '\tstrip: {\n'
                printf '\t\tnone: true,\n'
                printf '\t},\n'
                printf '\tprefer: true,\n'
            else
                printf '\tsrc: "%s/%s",\n' "$SRC" "$FILE"
                printf '\tfilename: "%s",\n' "$BASENAME"
            fi
        fi
        if [ "$CLASS" = "APPS" ]; then
            printf '\tdex_preopt: {\n'
            printf '\t\tenabled: false,\n'
            printf '\t},\n'
        fi
        if [ "$CLASS" = "SHARED_LIBRARIES" ] || [ "$CLASS" = "EXECUTABLES" ] || [ "$CLASS" = "RFSA" ]; then
            if [ "$DIRNAME" != "." ]; then
                if [ "$EXTENSION" = "sh" ]; then
                    printf '\tsub_dir: "%s",\n' "$DIRNAME"
                else
                    printf '\trelative_install_path: "%s",\n' "$DIRNAME"
                fi
            fi
        fi
        if [ "$CLASS" = "ETC" ]; then
            if [ "$DIRNAME" != "." ]; then
                printf '\tsub_dir: "%s",\n' "$DIRNAME"
            fi
        fi
        if [ "$CLASS" = "SHARED_LIBRARIES" ]; then
            printf '\tprefer: true,\n'
        fi
        if [ "$EXTRA" = "priv-app" ]; then
            printf '\tprivileged: true,\n'
        fi
        if [ "$PARTITION" = "vendor" ]; then
            printf '\tsoc_specific: true,\n'
        elif [ "$PARTITION" = "product" ]; then
            printf '\tproduct_specific: true,\n'
        elif [ "$PARTITION" = "system_ext" ]; then
            printf '\tsystem_ext_specific: true,\n'
        elif [ "$PARTITION" = "odm" ]; then
            printf '\tdevice_specific: true,\n'
        fi
        printf '}\n\n'
    done <<<"$FILELIST"
}

function do_comm() {
    LC_ALL=C comm "$1" <(echo "$2") <(echo "$3")
}

function elf_format_android() {
    local ELF_FORMAT=$("$OBJDUMP" -a "$1" 2>/dev/null | sed -nE "s|^.+file format (.*)$|\1|p")
    if [ "$ELF_FORMAT" = "elf64-littleaarch64" ]; then
        echo "android_arm64"
    elif [ "$ELF_FORMAT" = "elf32-littlearm" ] || [ "$ELF_FORMAT" = "elf32-hexagon" ]; then
        echo "android_arm"
    elif [ "$ELF_FORMAT" = "elf64-x86-64" ]; then
        echo "android_x86_64"
    elif [ "$ELF_FORMAT" = "elf32-i386" ]; then
        echo "android_x86"
    fi
}

#
# write_product_packages:
#
# This function will create prebuilt entries in the
# Android.bp and associated PRODUCT_PACKAGES list in the
# product makefile for all files in the blob list which
# start with a single dash (-) character.
#
function write_product_packages() {
    PACKAGE_LIST=()

    local COUNT=${#PRODUCT_PACKAGES_DEST[@]}

    if [ "$COUNT" = "0" ]; then
        return 0
    fi

    if [ "$TARGET_ENABLE_CHECKELF" == "false" ]; then
        colored_echo yellow "WARNING: TARGET_ENABLE_CHECKELF = false is deprecated and will be removed in Android 16."
    fi

    # Figure out what's 32-bit, what's 64-bit, and what's multilib
    local T_LIB32=$(prefix_match "lib/")
    local T_LIB64=$(prefix_match "lib64/")
    local MULTILIBS=$(do_comm -12 "$T_LIB32" "$T_LIB64")
    local LIB32=$(do_comm -23 "$T_LIB32" "$MULTILIBS")
    local LIB64=$(do_comm -23 "$T_LIB64" "$MULTILIBS")
    {
        write_blueprint_packages "SHARED_LIBRARIES" "" "both" "$MULTILIBS"
        write_blueprint_packages "SHARED_LIBRARIES" "" "32" "$LIB32"
        write_blueprint_packages "SHARED_LIBRARIES" "" "64" "$LIB64"
    } >>"$ANDROIDBP"

    local T_S_LIB32=$(prefix_match "system/lib/")
    local T_S_LIB64=$(prefix_match "system/lib64/")
    local S_MULTILIBS=$(do_comm -12 "$T_S_LIB32" "$T_S_LIB64")
    local S_LIB32=$(do_comm -23 "$T_S_LIB32" "$S_MULTILIBS")
    local S_LIB64=$(do_comm -23 "$T_S_LIB64" "$S_MULTILIBS")
    {
        write_blueprint_packages "SHARED_LIBRARIES" "system" "both" "$S_MULTILIBS"
        write_blueprint_packages "SHARED_LIBRARIES" "system" "32" "$S_LIB32"
        write_blueprint_packages "SHARED_LIBRARIES" "system" "64" "$S_LIB64"
    } >>"$ANDROIDBP"

    local T_V_LIB32=$(prefix_match "vendor/lib/")
    local T_V_LIB64=$(prefix_match "vendor/lib64/")
    local V_RFSA=$(prefix_match "vendor/lib/rfsa/")
    local V_MULTILIBS=$(do_comm -12 "$T_V_LIB32" "$T_V_LIB64")
    local V_LIB32=$(do_comm -23 "$T_V_LIB32" "$V_MULTILIBS")
    local V_LIB32=$(grep -v 'rfsa/' <(echo "$V_LIB32"))
    local V_LIB64=$(do_comm -23 "$T_V_LIB64" "$V_MULTILIBS")
    {
        write_blueprint_packages "SHARED_LIBRARIES" "vendor" "both" "$V_MULTILIBS"
        write_blueprint_packages "SHARED_LIBRARIES" "vendor" "32" "$V_LIB32"
        write_blueprint_packages "SHARED_LIBRARIES" "vendor" "64" "$V_LIB64"
        write_blueprint_packages "RFSA" "vendor" "" "$V_RFSA"
    } >>"$ANDROIDBP"

    local T_P_LIB32=$(prefix_match "product/lib/")
    local T_P_LIB64=$(prefix_match "product/lib64/")
    local P_MULTILIBS=$(do_comm -12 "$T_P_LIB32" "$T_P_LIB64")
    local P_LIB32=$(do_comm -23 "$T_P_LIB32" "$P_MULTILIBS")
    local P_LIB64=$(do_comm -23 "$T_P_LIB64" "$P_MULTILIBS")
    {
        write_blueprint_packages "SHARED_LIBRARIES" "product" "both" "$P_MULTILIBS"
        write_blueprint_packages "SHARED_LIBRARIES" "product" "32" "$P_LIB32"
        write_blueprint_packages "SHARED_LIBRARIES" "product" "64" "$P_LIB64"
    } >>"$ANDROIDBP"

    local T_SE_LIB32=$(prefix_match "system_ext/lib/")
    local T_SE_LIB64=$(prefix_match "system_ext/lib64/")
    local SE_MULTILIBS=$(do_comm -12 "$T_SE_LIB32" "$T_SE_LIB64")
    local SE_LIB32=$(do_comm -23 "$T_SE_LIB32" "$SE_MULTILIBS")
    local SE_LIB64=$(do_comm -23 "$T_SE_LIB64" "$SE_MULTILIBS")
    {
        write_blueprint_packages "SHARED_LIBRARIES" "system_ext" "both" "$SE_MULTILIBS"
        write_blueprint_packages "SHARED_LIBRARIES" "system_ext" "32" "$SE_LIB32"
        write_blueprint_packages "SHARED_LIBRARIES" "system_ext" "64" "$SE_LIB64"
    } >>"$ANDROIDBP"

    local T_O_LIB32=$(prefix_match "odm/lib/")
    local T_O_LIB64=$(prefix_match "odm/lib64/")
    local O_RFSA=$(prefix_match "odm/lib/rfsa/")
    local O_MULTILIBS=$(do_comm -12 "$T_O_LIB32" "$T_O_LIB64")
    local O_LIB32=$(do_comm -23 "$T_O_LIB32" "$O_MULTILIBS")
    local O_LIB32=$(grep -v 'rfsa/' <(echo "$O_LIB32"))
    local O_LIB64=$(do_comm -23 "$T_O_LIB64" "$O_MULTILIBS")
    {
        write_blueprint_packages "SHARED_LIBRARIES" "odm" "both" "$O_MULTILIBS"
        write_blueprint_packages "SHARED_LIBRARIES" "odm" "32" "$O_LIB32"
        write_blueprint_packages "SHARED_LIBRARIES" "odm" "64" "$O_LIB64"
        write_blueprint_packages "RFSA" "odm" "" "$O_RFSA"
    } >>"$ANDROIDBP"

    # APEX
    {
        write_blueprint_packages "APEX" ""
        write_blueprint_packages "APEX" "system"
        write_blueprint_packages "APEX" "vendor"
        write_blueprint_packages "APEX" "system_ext"
    } >>"$ANDROIDBP"

    # Apps
    {
        write_blueprint_packages "APPS" "" ""
        write_blueprint_packages "APPS" "" "priv-app"
        write_blueprint_packages "APPS" "system" ""
        write_blueprint_packages "APPS" "system" "priv-app"
        write_blueprint_packages "APPS" "vendor" ""
        write_blueprint_packages "APPS" "vendor" "priv-app"
        write_blueprint_packages "APPS" "product" ""
        write_blueprint_packages "APPS" "product" "priv-app"
        write_blueprint_packages "APPS" "system_ext" ""
        write_blueprint_packages "APPS" "system_ext" "priv-app"
        write_blueprint_packages "APPS" "odm" ""
        write_blueprint_packages "APPS" "odm" "priv-app"
    } >>"$ANDROIDBP"

    # Framework
    {
        write_blueprint_packages "JAVA_LIBRARIES" ""
        write_blueprint_packages "JAVA_LIBRARIES" "system"
        write_blueprint_packages "JAVA_LIBRARIES" "vendor"
        write_blueprint_packages "JAVA_LIBRARIES" "product"
        write_blueprint_packages "JAVA_LIBRARIES" "system_ext"
        write_blueprint_packages "JAVA_LIBRARIES" "odm"
    } >>"$ANDROIDBP"

    # Etc
    {
        write_blueprint_packages "ETC" ""
        write_blueprint_packages "ETC" "system"
        write_blueprint_packages "ETC" "vendor"
        write_blueprint_packages "ETC" "product"
        write_blueprint_packages "ETC" "system_ext"
        write_blueprint_packages "ETC" "odm"
    } >>"$ANDROIDBP"

    # Executables
    {
        write_blueprint_packages "EXECUTABLES" ""
        write_blueprint_packages "EXECUTABLES" "system"
        write_blueprint_packages "EXECUTABLES" "vendor"
        write_blueprint_packages "EXECUTABLES" "product"
        write_blueprint_packages "EXECUTABLES" "system_ext"
        write_blueprint_packages "EXECUTABLES" "odm"
    } >>"$ANDROIDBP"

    write_package_definition "${PACKAGE_LIST[@]}" >>"$PRODUCTMK"
}

#
# write_symlink_packages:
#
# Creates symlink entries in the Android.bp and related PRODUCT_PACKAGES
# list in the product makefile for all files in the blob list which has
# SYMLINK argument.
#
function write_symlink_packages() {
    local FILE=
    local ARCH=
    local BASENAME=
    local PKGNAME=
    local PREFIX=
    local SYMLINK_BASENAME=
    local SYMLINK_PACKAGES=()

    # Sort the symlinks list for comm
    PRODUCT_SYMLINKS_LIST=($(printf '%s\n' "${PRODUCT_SYMLINKS_LIST[@]}" | LC_ALL=C sort))

    local COUNT=${#PRODUCT_SYMLINKS_LIST[@]}

    if [ "$COUNT" = "0" ]; then
        return 0
    fi

    for LINE in "${PRODUCT_SYMLINKS_LIST[@]}"; do
        FILE=$(target_file "$LINE")
        if [[ "$LINE" =~ '/lib64/' || "$LINE" =~ '/lib/arm64/' ]]; then
            ARCH="64"
        elif [[ "$LINE" =~ '/lib/' ]]; then
            ARCH="32"
        fi
        BASENAME=$(basename "$FILE")
        local SPEC_ARGS=$(target_args "$LINE")
        local ARGS=(${SPEC_ARGS//;/ })
        for ARG in "${ARGS[@]}"; do
            if [[ "$ARG" =~ "SYMLINK" ]]; then
                SYMLINKS=${ARG#*=}
                SYMLINKS=(${SYMLINKS//,/ })
                for SYMLINK in "${SYMLINKS[@]}"; do
                    SYMLINK_BASENAME=$(basename "$SYMLINK")
                    PKGNAME="${BASENAME%.*}_${SYMLINK_BASENAME%.*}_symlink${ARCH}"
                    if [[ "${SYMLINK_PACKAGES[@]}" =~ "$PKGNAME" ]]; then
                        PKGNAME+="_$(grep -o "$PKGNAME" <<<${SYMLINK_PACKAGES[*]} | wc -l)"
                    fi
                    {
                        printf 'install_symlink {\n'
                        printf '\tname: "%s",\n' "$PKGNAME"
                        if prefix_match_file "vendor/" "$SYMLINK"; then
                            PREFIX='vendor/'
                            printf '\tsoc_specific: true,\n'
                        elif prefix_match_file "product/" "$SYMLINK"; then
                            PREFIX='product/'
                            printf '\tproduct_specific: true,\n'
                        elif prefix_match_file "system_ext/" "$SYMLINK"; then
                            PREFIX='system_ext/'
                            printf '\tsystem_ext_specific: true,\n'
                        elif prefix_match_file "odm/" "$SYMLINK"; then
                            PREFIX='odm/'
                            printf '\tdevice_specific: true,\n'
                        fi
                        printf '\tinstalled_location: "%s",\n' "${SYMLINK#"$PREFIX"}"
                        printf '\tsymlink_target: "/%s",\n' "$FILE"
                        printf '}\n\n'
                    } >>"$ANDROIDBP"
                    SYMLINK_PACKAGES+=("$PKGNAME")
                done
            fi
        done
    done

    write_package_definition "${SYMLINK_PACKAGES[@]}" >>"$PRODUCTMK"
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
# write_package_definition:
#
# $@: list of packages
#
# writes out the final PRODUCT_PACKAGES list
#
function write_package_definition() {
    local PACKAGE_LIST=("${@}")
    local PACKAGE_COUNT=${#PACKAGE_LIST[@]}

    if [ "$PACKAGE_COUNT" -eq "0" ]; then
        return 0
    fi

    printf '\n%s\n' "PRODUCT_PACKAGES += \\"
    for ((i = 1; i < PACKAGE_COUNT + 1; i++)); do
        local SKIP=false
        local LINEEND=" \\"
        if [ "$i" -eq "$PACKAGE_COUNT" ]; then
            LINEEND=""
        fi
        for PKG in $(tr "," "\n" <<<"$REQUIRED_PACKAGES_LIST"); do
            if [[ $PKG == "${PACKAGE_LIST[$i - 1]}" ]]; then
                SKIP=true
                break
            fi
        done
        # Skip adding of the package to product makefile if it's in the required list
        if [[ $SKIP == false ]]; then
            printf '    %s%s\n' "${PACKAGE_LIST[$i - 1]}" "$LINEEND" >>"$PRODUCTMK"
        fi
    done
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

    PRODUCT_PACKAGES_HASHES=()
    PRODUCT_PACKAGES_FIXUP_HASHES=()
    PRODUCT_PACKAGES_SRC=()
    PRODUCT_PACKAGES_DEST=()
    PRODUCT_PACKAGES_ARGS=()
    PRODUCT_SYMLINKS_LIST=()
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
        if [[ "$SPEC" =~ 'SYMLINK=' ]]; then
            PRODUCT_SYMLINKS_LIST+=("${SPEC#-}")
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

        # if line contains apex, apk, jar or vintf fragment, it needs to be packaged
        if suffix_match_file ".apex" "$SRC_FILE" ||
            suffix_match_file ".apk" "$SRC_FILE" ||
            suffix_match_file ".jar" "$SRC_FILE" ||
            [[ "$TARGET_ENABLE_CHECKELF" != "false" &&
                ("$SRC_FILE" == *"lib/"*".so" ||
                "$SRC_FILE" == *"lib64/"*".so" ||
                "$SRC_FILE" == *"bin/"* ||
                "$SRC_FILE" == *"lib/rfsa"*) ]] ||
            [[ "$SRC_FILE" == *"etc/vintf/manifest/"* ]]; then
            IS_PRODUCT_PACKAGE=true
        fi

        if [ "$IS_PRODUCT_PACKAGE" = true ]; then
            PRODUCT_PACKAGES_HASHES+=("$HASH")
            PRODUCT_PACKAGES_FIXUP_HASHES+=("$FIXUP_HASH")
            PRODUCT_PACKAGES_SRC+=("$SRC_FILE")
            PRODUCT_PACKAGES_DEST+=("$TARGET_FILE")
            PRODUCT_PACKAGES_ARGS+=("$ARGS")
        else
            PRODUCT_COPY_FILES_HASHES+=("$HASH")
            PRODUCT_COPY_FILES_FIXUP_HASHES+=("$FIXUP_HASH")
            PRODUCT_COPY_FILES_SRC+=("$SRC_FILE")
            PRODUCT_COPY_FILES_DEST+=("$TARGET_FILE")
            PRODUCT_COPY_FILES_ARGS+=("$ARGS")
        fi

    done < <(grep -v -E '(^#|^[[:space:]]*$)' "$LIST" | LC_ALL=C sort | uniq)
}

#
# write_makefiles:
#
# $1: file containing the list of items to extract
# $2: make treble compatible makefile - optional and deprecated, default to true
#
# Calls write_product_copy_files, write_product_packages and
# lastly write_symlink_packages on the given file and appends
# to the Android.bp as well as the product makefile.
#
function write_makefiles() {
    parse_file_list "$1"
    write_product_copy_files
    write_product_packages
    write_symlink_packages
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

    local HASHLIST=("${PRODUCT_COPY_FILES_HASHES[@]}" "${PRODUCT_PACKAGES_HASHES[@]}")
    local SRC_LIST=("${PRODUCT_COPY_FILES_SRC[@]}" "${PRODUCT_PACKAGES_SRC[@]}")
    local DEST_LIST=("${PRODUCT_COPY_FILES_DEST[@]}" "${PRODUCT_PACKAGES_DEST[@]}")
    local ARGS_LIST=("${PRODUCT_COPY_FILES_ARGS[@]}" "${PRODUCT_PACKAGES_ARGS[@]}")
    local FIXUP_HASHLIST=("${PRODUCT_COPY_FILES_FIXUP_HASHES[@]}" "${PRODUCT_PACKAGES_FIXUP_HASHES[@]}")
    local PRODUCT_COPY_FILES_COUNT=${#PRODUCT_COPY_FILES_SRC[@]}
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
        local SPEC_SRC_FILE="${SRC_LIST[$i - 1]}"
        local SPEC_DST_FILE="${DEST_LIST[$i - 1]}"
        local SPEC_ARGS="${ARGS_LIST[$i - 1]}"
        local ARGS=(${SPEC_ARGS//;/ })
        local OUTPUT_DIR=
        local TMP_DIR=
        local SRC_FILE=
        local DST_FILE=
        local IS_PRODUCT_PACKAGE=false
        local TRY_SRC_FILE_FIRST=false

        # Note: this relies on the fact that the ${SRC_LIST[@]} array
        # contains first ${PRODUCT_COPY_FILES_SRC[@]}, then ${PRODUCT_PACKAGES_SRC[@]}.
        if [ "${i}" -gt "${PRODUCT_COPY_FILES_COUNT}" ]; then
            IS_PRODUCT_PACKAGE=true
        fi

        for arg in "${ARGS[@]}"; do
            [ "${arg}" = "TRYSRCFIRST" ] && TRY_SRC_FILE_FIRST=true
        done

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
        local USE_PINNED="no"
        if [ "$DISABLE_PINNING" != "1" ] && [ -n "$HASH" ]; then
            if [ -f "${VENDOR_REPO_FILE}" ]; then
                local PINNED="${VENDOR_REPO_FILE}"
            else
                local PINNED="${TMP_DIR}${DST_FILE#/system}"
            fi
            if [ -f "$PINNED" ]; then
                local TMP_HASH=$(get_hash "${PINNED}")
                if [ "${TMP_HASH}" = "${HASH}" ] || [ "${TMP_HASH}" = "${FIXUP_HASH}" ]; then
                    if [ ! -f "${VENDOR_REPO_FILE}" ]; then
                        cp -p "$PINNED" "${VENDOR_REPO_FILE}"
                    fi
                    if [ -z "${FIXUP_HASH}" ] || [ "${TMP_HASH}" = "${FIXUP_HASH}" ]; then
                        USE_PINNED="yes"
                    else
                        USE_PINNED="fixup"
                    fi
                fi
            fi
        fi

        if [ "${KANG}" = false ]; then
            printf '  - %s\n' "${BLOB_DISPLAY_NAME}"
        fi

        case "$USE_PINNED" in
            yes)
                if [ -n "${FIXUP_HASH}" ]; then
                    printf '    + Keeping pinned file with hash %s\n' "${FIXUP_HASH}"
                else
                    printf '    + Keeping pinned file with hash %s\n' "${HASH}"
                fi
                continue
                ;;
            fixup)
                printf '    + Fixing up pinned file with hash %s\n' "${HASH}"
                ;;
            *)
                local FOUND=false
                local FIRST_CANDIDATE=
                local SECOND_CANDIDATE=
                if $TRY_SRC_FILE_FIRST; then
                    FIRST_CANDIDATE="$SRC_FILE"
                    SECOND_CANDIDATE="$DST_FILE"
                else
                    # Try custom target first by default.
                    FIRST_CANDIDATE="$DST_FILE"
                    SECOND_CANDIDATE="$SRC_FILE"
                fi
                for CANDIDATE in "${FIRST_CANDIDATE}" "${SECOND_CANDIDATE}"; do
                    get_file "${CANDIDATE}" "${VENDOR_REPO_FILE}" "${EXTRACT_SRC}" && {
                        FOUND=true
                        break
                    }
                done

                if [ "${FOUND}" = false ]; then
                    colored_echo red "    !! ${BLOB_DISPLAY_NAME}: file not found in source"
                    continue
                fi
                ;;
        esac

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

        if [ "${KANG}" = false ]; then
            if [ -n "${FIXUP_HASH}" ]; then
                if [ "${FIXUP_HASH}" != "${POST_FIXUP_HASH}" ]; then
                    colored_echo red "    !! ${BLOB_DISPLAY_NAME}: Fixup hash ${FIXUP_HASH} does not match ${POST_FIXUP_HASH}"
                fi
            elif [ -n "${HASH}" ] && [ "${HASH}" != "${POST_FIXUP_HASH}" ]; then
                colored_echo red "    !! ${BLOB_DISPLAY_NAME}: Hash ${HASH} does not match ${POST_FIXUP_HASH}"
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
