#!/usr/bin/env -S PYTHONPATH=../../../tools/extract-utils python3
#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from extract_utils.file import File
from extract_utils.fixups_blob import (
    BlobFixupCtx,
    blob_fixup,
    blob_fixups_user_type,
)
from extract_utils.fixups_lib import (
    lib_fixup_remove,
    lib_fixups,
    lib_fixups_user_type,
)
from extract_utils.main import (
    ExtractUtils,
    ExtractUtilsModule,
)

namespace_imports = [
    'device/lineage/example',
]


def lib_fixup_vendor_suffix(lib: str, partition: str, *args, **kwargs):
    return f'{lib}_{partition}' if partition == 'vendor' else None


lib_fixups: lib_fixups_user_type = {
    **lib_fixups,
    (
        'vendor.twopac.hardware.xoo@1.0',
        'vendor.twopac.hardware.oxo@1.0',
        'vendor.twopac.hardware.oox@1.0',
    ): lib_fixup_vendor_suffix,
    'libwpa_client': lib_fixup_remove,
}


def blob_fixup_test_flag(
    ctx: BlobFixupCtx,
    file: File,
    file_path: str,
    *args,
    **kwargs,
):
    with open(file_path, 'rb+') as f:
        f.seek(1337)
        f.write(b'\x01')


blob_fixups: blob_fixups_user_type = {
    'vendor/app/Test.apk': blob_fixup()
        .apktool_patch('blob-patches/TestApk.patch', '-s'),
    'vendor/etc/test.conf': blob_fixup()
        .patch_file('blob-patches/TestConf.patch')
        .regex_replace('(LOG_.*_ENABLED)=1', '\\1=0')
        .add_line_if_missing('DEBUG=0'),
    ('vendor/etc/test.0.xml', 'vendor/etc/test.1.xml'): blob_fixup()
        .fix_xml(),
    'vendor/lib/test.so': blob_fixup()
        .patchelf_version('0_17_2')
        .fix_soname()
        .add_needed('to_add.so')
        .remove_needed('to_remove.so')
        .replace_needed('from.so', 'to.so')
        .binary_regex_replace(b'\xFF\x00\x00\x94', b'\xFE\x00\x00\x94')
        .sig_replace('C0 03 5F D6 ?? ?? ?? ?? C0 03 5F D6', '1F 20 03 D5')
        .call(blob_fixup_test_flag),
}  # fmt: skip

module = ExtractUtilsModule(
    'example',
    'lineage',
    blob_fixups=blob_fixups,
    lib_fixups=lib_fixups,
    namespace_imports=namespace_imports,
)

if __name__ == '__main__':
    utils = ExtractUtils.device(module)
    utils.run()
