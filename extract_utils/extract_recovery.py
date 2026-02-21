#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional

from extract_utils.tools import unpack_bootimg_path
from extract_utils.utils import find_files, run_cmd, run_cmd_bytes

BOOT_MAGIC = b'ANDROID!'

VENDOR_BOOT_MAGIC = b'VNDRBOOT'


@dataclass(frozen=True)
class VendorRamdiskFragment:
    path: Path
    ramdisk_type: int
    name: str = ''


def parse_mkbootimg_fragments(data: str):
    argv = shlex.split(data, posix=True)

    fragments: List[VendorRamdiskFragment] = []
    fragment_type: Optional[int] = None
    fragment_name: Optional[str] = None

    i = 0
    n = len(argv)
    while i < n:
        assert argv[i].startswith('--')

        match argv[i]:
            case '--ramdisk_type':
                assert i + 1 < n
                fragment_type = int(argv[i + 1], 0)
            case '--ramdisk_name':
                assert i + 1 < n
                fragment_name = argv[i + 1]
            case '--vendor_ramdisk_fragment':
                assert i + 1 < n
                assert fragment_type is not None
                assert fragment_name is not None
                fragment = VendorRamdiskFragment(
                    Path(argv[i + 1]),
                    fragment_type,
                    fragment_name,
                )
                fragments.append(fragment)
                fragment_type = None
                fragment_name = None
            case _:
                pass

        i += 2

    return fragments


def unpack_bootimg(file_path: str, output_path: str):
    output = run_cmd(
        [
            unpack_bootimg_path,
            '--boot_img',
            file_path,
            '--out',
            output_path,
            '--format',
            'mkbootimg',
        ]
    )
    return output


def extract_ramdisk(ramdisk: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with ramdisk.open('rb') as f:
        head6 = f.read(6)
        head4 = head6[:4]

    CPIO_NEWC = b'070701'
    CPIO_CRC = b'070702'
    GZIP_MAGIC = 0x8B1F.to_bytes(2, 'little')
    LZ4F_MAGIC_MODERN = 0x184D2204.to_bytes(4, 'little')
    LZ4F_MAGIC_LEGACY = 0x184C2102.to_bytes(4, 'little')

    decompressor: Optional[List[str]] = None
    if head4.startswith(GZIP_MAGIC):
        decompressor = [
            'gzip',
            '-dc',
            str(ramdisk),
        ]
    elif head4 == LZ4F_MAGIC_MODERN or head4 == LZ4F_MAGIC_LEGACY:
        decompressor = [
            'lz4',
            '-dc',
            str(ramdisk),
        ]
    elif head6 in (CPIO_NEWC, CPIO_CRC):
        decompressor = None
    else:
        assert False, head6

    cpio_cmd = ['cpio', '-id', '--no-absolute-filenames']

    if decompressor is not None:
        output_data = run_cmd_bytes(decompressor)
    else:
        output_data = ramdisk.read_bytes()

    run_cmd_bytes(cpio_cmd, data=output_data, cwd=out_dir)


def find_android_paths(input_path: str):
    return find_files(
        input_path,
        magic=BOOT_MAGIC,
    )


def find_vndrboot_paths(input_path: str):
    return find_files(
        input_path,
        magic=VENDOR_BOOT_MAGIC,
    )


def extract_recovery_paths(
    file_paths: List[str],
    partition: str,
    dump_dir: str,
):
    recovery_path = Path(dump_dir, partition)

    for file_path in file_paths:
        with TemporaryDirectory() as tmp_dir:
            output = unpack_bootimg(file_path, tmp_dir)
            fragments = parse_mkbootimg_fragments(output)
            for fragment in fragments:
                # TODO: we probably need more checks here, as vendor_kernel_boot
                # seems to contain a ramdisk with ramdisk_type=1 too
                if fragment.ramdisk_type == 1:
                    extract_ramdisk(fragment.path, recovery_path)
                    break


def extract_recovery_partition(partition: str, dump_dir: str):
    vndrboot_paths = find_vndrboot_paths(dump_dir)
    extract_recovery_paths(vndrboot_paths, partition, dump_dir)

    android_paths = find_android_paths(dump_dir)
    extract_recovery_paths(android_paths, partition, dump_dir)
