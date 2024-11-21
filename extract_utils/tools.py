#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from os import path
from typing import Dict

patchelf_versions = ['0_8', '0_9', '0_17_2', '0_18']
DEFAULT_PATCHELF_VERSION = '0_18'

script_dir = path.dirname(path.realpath(__file__))
android_root = path.realpath(path.join(script_dir, '..', '..', '..'))

extract_utils_dir = path.realpath(path.join(script_dir, '..'))
sdat2img_path = path.join(extract_utils_dir, 'sdat2img.py')

binaries_dir = path.join(android_root, 'prebuilts/extract-tools/linux-x86/bin')
ota_extractor_path = path.join(binaries_dir, 'ota_extractor')
lpunpack_path = path.join(binaries_dir, 'lpunpack')
simg2img_path = path.join(binaries_dir, 'simg2img')
stripzip_path = path.join(binaries_dir, 'stripzip')

patchelf_version_path_map: Dict[str, str] = {}
for version in patchelf_versions:
    patchelf_version_path_map[version] = path.join(
        binaries_dir, f'patchelf-{version}'
    )

build_tools_dir = path.join(android_root, 'prebuilts/build-tools/linux-x86/bin')
brotli_path = path.join(build_tools_dir, 'brotli')


common_binaries_dir = path.join(android_root, 'prebuilts/extract-tools/common')
apktool_path = path.join(common_binaries_dir, 'apktool/apktool.jar')

jdk_binaries_dir = path.join(android_root, 'prebuilts/jdk/jdk21/linux-x86/bin')
java_path = path.join(jdk_binaries_dir, 'java')

llvm_binaries_dir = path.join(
    android_root,
    'prebuilts/clang/host/linux-x86/llvm-binutils-stable',
)
llvm_objdump_path = path.join(llvm_binaries_dir, 'llvm-objdump')

lineage_scripts_dir = path.join(android_root, 'lineage/scripts')
carriersettings_extractor_path = path.join(
    lineage_scripts_dir,
    'carriersettings-extractor/carriersettings_extractor.py',
)
fbpacktool_path = path.join(lineage_scripts_dir, 'fbpacktool/fbpacktool.py')
