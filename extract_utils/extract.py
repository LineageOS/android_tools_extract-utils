#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
import tarfile
from concurrent.futures import ProcessPoolExecutor
from os import path
from tarfile import is_tarfile
from typing import Callable, Iterable, List, Optional, Union
from zipfile import ZipFile, is_zipfile

from extract_utils.file import File
from extract_utils.fixups import fixups_type, fixups_user_type
from extract_utils.tools import (
    brotli_path,
    lpunpack_path,
    ota_extractor_path,
    sdat2img_path,
    simg2img_path,
)
from extract_utils.utils import Color, color_print, run_cmd, scan_tree

ALTERNATE_PARTITION_PATH_MAP = {
    'product': 'system/product',
    'system_ext': 'system/system_ext',
    'vendor': 'system/vendor',
    'odm': 'vendor/odm',
}


BROTLI_EXT = '.new.dat.br'
SPARSE_DATA_EXT = '.new.dat'
TRANSFER_LIST_EXT = '.transfer.list'
SPARSE_CHUNK_SUFFIX = '_sparsechunk'
PAYLOAD_BIN_FILE_NAME = 'payload.bin'
SUPER_PARTITION_NAME = 'super'
SUPER_IMG_NAME = 'super.img'


extract_fn_type = Callable[['ExtractCtx', str, str], Optional[str]]
extract_fns_value_type = Union[extract_fn_type, List[extract_fn_type]]
extract_fns_user_type = fixups_user_type[extract_fns_value_type]
extract_fns_type = fixups_type[extract_fns_value_type]


class ExtractCtx:
    def __init__(
        self,
        extract_fns: Optional[extract_fns_type] = None,
        extract_partitions: Optional[List[str]] = None,
        firmware_files: Optional[List[File]] = None,
        factory_files: Optional[List[File]] = None,
        extract_all=False,
    ):
        if extract_fns is None:
            extract_fns = {}
        if extract_partitions is None:
            extract_partitions = []
        if firmware_files is None:
            firmware_files = []
        if factory_files is None:
            factory_files = []

        # Files for extract functions are extracted if their name
        # matches the regex
        self.extract_fns = extract_fns
        # Files for partitions are extracted if, after removing the
        # extension, their name matches a partition
        self.extract_partitions = extract_partitions
        # Files are extracted if their name matches as-is
        self.firmware_files = firmware_files
        self.factory_files = factory_files

        self.extract_all = extract_all


def file_name_to_partition(file_name: str):
    return file_name.split('.', 1)[0]


def find_files(
    input_path: str,
    partition: Optional[str] = None,
    name: Optional[str] = None,
    magic: Optional[bytes] = None,
    position=0,
    ext: Optional[str] = None,
) -> List[str]:
    file_paths = []
    for file in scan_tree(input_path):
        if not file.is_file():
            continue

        file_partition_name = file_name_to_partition(file.name)
        if partition is not None and partition != file_partition_name:
            continue

        if name is not None and name != file.name:
            continue

        if ext is not None and not file.name.endswith(ext):
            continue

        if magic is not None:
            with open(file, 'rb') as f:
                f.seek(position)
                file_magic = f.read(len(magic))
                if file_magic != magic:
                    continue

        file_paths.append(file.path)

    return file_paths


def find_file(
    input_path: str,
    partition: Optional[str] = None,
    name: Optional[str] = None,
    magic: Optional[bytes] = None,
    position=0,
    ext: Optional[str] = None,
):
    file_paths = find_files(input_path, partition, name, magic, position, ext)

    assert len(file_paths) <= 1
    if file_paths:
        return file_paths[0]

    return None


def find_alternate_partitions(
    extract_partitions: List[str],
    found_partitions: Iterable[str],
):
    new_extract_partitions = []
    for partition in extract_partitions:
        if partition in found_partitions:
            continue

        alternate_partition_path = ALTERNATE_PARTITION_PATH_MAP.get(partition)
        if alternate_partition_path is None:
            continue

        alternate_partition, _ = alternate_partition_path.split('/', 1)
        if (
            alternate_partition in found_partitions
            or alternate_partition in new_extract_partitions
        ):
            continue

        new_extract_partitions.append(alternate_partition)

    return new_extract_partitions


def find_sparse_raw_paths(partition: str, input_path: str):
    magic = 0xED26FF3A.to_bytes(4, 'little')
    return find_files(input_path, partition, magic=magic)


def find_erofs_path(partition: str, input_path: str):
    magic = 0xE0F5E1E2.to_bytes(4, 'little')
    return find_file(input_path, partition, magic=magic, position=1024)


def find_ext4_path(partition: str, input_path: str):
    magic = 0xEF53.to_bytes(2, 'little')
    return find_file(input_path, partition, magic=magic, position=1080)


def find_payload_path(partition: str, input_path: str):
    return find_file(input_path, partition, magic=b'CrAU')


def find_super_img_path(partition: str, input_path: str):
    magic = 0x616C4467.to_bytes(4, 'little')
    return find_file(input_path, partition, magic=magic, position=4096)


def find_brotli_path(partition: str, input_path: str):
    return find_file(input_path, partition, ext=BROTLI_EXT)


def find_sparse_data_path(partition: str, input_path: str):
    return find_file(input_path, partition, ext=SPARSE_DATA_EXT)


def print_file_paths(file_paths: List[str], file_type: str):
    if not file_paths:
        return

    file_names = [path.basename(fp) for fp in file_paths]
    file_names_str = ', '.join(file_names)
    print(f'Found {file_type} files: {file_names_str}')


def print_file_path(file_path: str, file_type: str):
    print_file_paths([file_path], file_type)


def remove_file_paths(file_paths: Iterable[str]):
    if not file_paths:
        return

    file_names = [path.basename(fp) for fp in file_paths]
    file_names_str = ', '.join(file_names)
    print(f'Deleting {file_names_str}')

    for file_path in file_paths:
        os.remove(file_path)


def remove_file_path(file_path: str):
    remove_file_paths([file_path])


def extract_payload_bin(partition: str, file_path: str, output_dir: str):
    # TODO: switch to python extractor to be able to detect partition
    # names to make this process fatal on failure
    try:
        run_cmd(
            [
                ota_extractor_path,
                '--payload',
                file_path,
                '--output-dir',
                output_dir,
                '--partitions',
                partition,
            ],
        )
    except ValueError:
        pass


def partition_chunk_index(file_path: str):
    _, chunk_index = path.splitext(file_path)

    return int(chunk_index[1:])


def extract_sparse_raw_img(file_paths: List[str], output_dir: str):
    file_path = file_paths[0]
    file_name = path.basename(file_path)

    # Split extension to remove chunk index x from
    # partition.img_sparsechunk.x files
    base_file_name, _ = path.splitext(file_name)

    if base_file_name.endswith(SPARSE_CHUNK_SUFFIX):
        # Sparse chunk, remove _sparsechunk to get the partition name
        output_file_name = base_file_name[: -len(SPARSE_CHUNK_SUFFIX)]
    else:
        # Rename single sparse image to _sparsechunk.0 to avoid naming conflicts
        assert len(file_paths) == 1
        output_file_name = file_name
        sparse_file_path = f'{file_path}{SPARSE_CHUNK_SUFFIX}.0'
        os.rename(file_path, sparse_file_path)
        file_paths.remove(file_path)
        file_paths.append(sparse_file_path)

    file_paths.sort(key=partition_chunk_index)
    output_file_path = path.join(output_dir, output_file_name)

    run_cmd(
        [
            simg2img_path,
            *file_paths,
            output_file_path,
        ]
    )


def unslot_partition(partition_slot: str):
    return re.sub(r'_[abc]$', '', partition_slot)


def extract_super_img_slot(
    partition: str,
    slot: str,
    file_path: str,
    output_dir: str,
):
    partition_slot = f'{partition}{slot}'

    run_cmd(
        [
            lpunpack_path,
            '--partition',
            partition_slot,
            file_path,
            output_dir,
        ],
    )

    if not slot:
        return

    # Rename slotted partition to unslotted one
    partition_path = path.join(output_dir, f'{partition}.img')
    partition_slot_path = path.join(output_dir, f'{partition_slot}.img')
    os.rename(partition_slot_path, partition_path)


def extract_super_img(partition: str, file_path: str, output_dir: str):
    # TODO: switch to python lpunpack to be able to detect partition
    # names to make this process fatal on failure

    for slot in ['', '_a']:
        try:
            extract_super_img_slot(partition, slot, file_path, output_dir)
            return
        except ValueError:
            pass


def extract_brotli_img(file_path: str, output_path: str):
    file_name = path.basename(file_path)
    output_file_name, _ = path.splitext(file_name)
    output_file_path = path.join(output_path, output_file_name)

    run_cmd(
        [
            brotli_path,
            '-d',
            file_path,
            '-o',
            output_file_path,
        ]
    )


def extract_sparse_data_img(file_path: str, output_path: str):
    assert file_path.endswith(SPARSE_DATA_EXT)

    base_file_path = file_path[: -len(SPARSE_DATA_EXT)]
    transfer_file_path = f'{base_file_path}{TRANSFER_LIST_EXT}'

    base_file_name = path.basename(base_file_path)
    img_file_name = f'{base_file_name}.img'

    output_file_path = path.join(output_path, img_file_name)

    run_cmd(
        [
            sdat2img_path,
            transfer_file_path,
            file_path,
            output_file_path,
        ]
    )


def extract_erofs(file_path: str, output_path: str):
    base_file_name = path.basename(file_path)

    partition_name, _ = path.splitext(base_file_name)
    partition_output_path = path.join(output_path, partition_name)
    os.mkdir(partition_output_path)

    run_cmd(
        [
            'fsck.erofs',
            f'--extract={partition_output_path}',
            file_path,
        ],
    )


def extract_ext4(file_path: str, output_path: str):
    base_file_name = path.basename(file_path)

    partition_name, _ = path.splitext(base_file_name)
    partition_output_path = path.join(output_path, partition_name)
    os.mkdir(partition_output_path)

    run_cmd(
        [
            'debugfs',
            '-R',
            f'rdump / {partition_output_path}',
            file_path,
        ],
    )

    # TODO: check for symlinks like the old code?


def unzip_file(source: str, file_path: str, output_file_path: str):
    with ZipFile(source) as zip_file:
        with zip_file.open(file_path) as z:
            with open(output_file_path, 'wb') as f:
                shutil.copyfileobj(z, f)


def extract_zip(source: str, dump_dir: str):
    with ZipFile(source) as zip_file:
        file_paths = zip_file.namelist()

    with ProcessPoolExecutor(len(file_paths)) as exe:
        for file_path in file_paths:
            output_file_path = path.join(dump_dir, file_path)
            output_dir = path.dirname(output_file_path)
            os.makedirs(output_dir, exist_ok=True)

            exe.submit(unzip_file, source, file_path, output_file_path)


def extract_tar(source: str, dump_dir: str):
    with tarfile.open(source, 'r:*') as tar:
        tar.extractall(dump_dir)


def extract_image_file(source: str, dump_dir: str):
    if is_zipfile(source):
        extract_fn = extract_zip
    elif is_tarfile(source):
        extract_fn = extract_tar
    else:
        raise ValueError(f'Unexpected file type at {source}')

    print(f'Extracting file {source}')
    extract_fn(source, dump_dir)


def extract_firmware_partition(partition: str, dump_dir: str):
    payload_bin_path = find_payload_path(PAYLOAD_BIN_FILE_NAME, dump_dir)
    if payload_bin_path:
        extract_payload_bin(partition, payload_bin_path, dump_dir)


def extract_partition(partition: str, dump_dir: str):
    payload_bin_path = find_payload_path(PAYLOAD_BIN_FILE_NAME, dump_dir)
    if payload_bin_path:
        extract_payload_bin(partition, payload_bin_path, dump_dir)

    super_img_path = find_super_img_path(SUPER_PARTITION_NAME, dump_dir)
    if super_img_path:
        extract_super_img(partition, super_img_path, dump_dir)

    sparse_raw_paths = find_sparse_raw_paths(partition, dump_dir)
    if sparse_raw_paths:
        print_file_paths(sparse_raw_paths, 'sparse raw')
        extract_sparse_raw_img(sparse_raw_paths, dump_dir)
        remove_file_paths(sparse_raw_paths)

    brotli_img_path = find_brotli_path(partition, dump_dir)
    if brotli_img_path:
        print_file_path(brotli_img_path, 'brotli')
        extract_brotli_img(brotli_img_path, dump_dir)
        remove_file_path(brotli_img_path)

    sparse_data_path = find_sparse_data_path(partition, dump_dir)
    if sparse_data_path:
        print_file_path(sparse_data_path, 'sparse data')
        extract_sparse_data_img(sparse_data_path, dump_dir)
        remove_file_paths(sparse_data_path)

    erofs_path = find_erofs_path(partition, dump_dir)
    if erofs_path:
        print_file_path(erofs_path, 'EROFS')
        extract_erofs(erofs_path, dump_dir)
        remove_file_paths(erofs_path)

    ext4_path = find_ext4_path(partition, dump_dir)
    if ext4_path:
        print_file_path(ext4_path, 'EXT4')
        extract_ext4(ext4_path, dump_dir)
        remove_file_path(ext4_path)


def find_partitions(dump_dir: str, ctx: ExtractCtx, missing=False):
    partitions = []
    for partition in ctx.extract_partitions:
        dump_partition_dir = path.join(dump_dir, partition)

        if path.isdir(dump_partition_dir) != missing:
            partitions.append(partition)

    return partitions


def _find_files(dump_dir: str, files: List[File], missing=False):
    found_files = []
    for file in files:
        src_file_path = path.join(dump_dir, file.src)
        dst_file_path = path.join(dump_dir, file.dst)
        exists = path.isfile(src_file_path) or path.isfile(dst_file_path)

        if exists != missing:
            found_files.append(file)

    return found_files


def find_firmware_files(dump_dir: str, ctx: ExtractCtx, missing=False):
    return _find_files(dump_dir, ctx.firmware_files, missing)


def find_factory_files(dump_dir: str, ctx: ExtractCtx, missing=False):
    return _find_files(dump_dir, ctx.factory_files, missing)


def find_firmware_partitions(dump_dir: str, ctx: ExtractCtx, missing=False):
    files = find_firmware_files(dump_dir, ctx, missing)

    partitions = []
    for file in files:
        partition, _ = path.splitext(file.dst)
        partitions.append(partition)

    return partitions


def extract_all_partitions(dump_dir: str, ctx: ExtractCtx):
    normal_partitions = find_partitions(dump_dir, ctx, missing=True)
    firmware_partitions = find_firmware_partitions(dump_dir, ctx, missing=True)
    partitions = normal_partitions + firmware_partitions

    while partitions:
        with ProcessPoolExecutor(len(partitions)) as exe:
            for partition in partitions:
                if partition in normal_partitions:
                    fn = extract_partition
                else:
                    fn = extract_firmware_partition

                exe.submit(fn, partition, dump_dir)

        found_partitions = find_partitions(dump_dir, ctx)
        partitions = find_alternate_partitions(partitions, found_partitions)


def _move_files(dump_dir: str, files: List[File]):
    firmware_files = _find_files(dump_dir, files, missing=True)
    for file in firmware_files:
        for file_name in [file.src, file.dst]:
            file_path = find_file(dump_dir, name=file_name)
            if not file_path:
                continue

            shutil.move(file_path, dump_dir)


def move_firmware_files(dump_dir: str, ctx: ExtractCtx):
    return _move_files(dump_dir, ctx.firmware_files)


def move_factory_files(dump_dir: str, ctx: ExtractCtx):
    return _move_files(dump_dir, ctx.factory_files)


def extract_dump(dump_dir: str, ctx: ExtractCtx):
    should_extract = filter_already_extracted(dump_dir, ctx)
    if not should_extract:
        return

    run_extract_fns(dump_dir, ctx)

    sparse_raw_paths = find_sparse_raw_paths(SUPER_PARTITION_NAME, dump_dir)
    if sparse_raw_paths:
        print_file_paths(sparse_raw_paths, 'sparse raw')
        extract_sparse_raw_img(sparse_raw_paths, dump_dir)
        remove_file_paths(sparse_raw_paths)

    extract_all_partitions(dump_dir, ctx)

    run_extract_fns(dump_dir, ctx)

    move_firmware_files(dump_dir, ctx)
    move_factory_files(dump_dir, ctx)
    move_sar_system_paths(dump_dir)
    move_alternate_partition_paths(dump_dir)

    # Create empty partition dir to prevent re-extraction
    missing_partitions = find_partitions(dump_dir, ctx, missing=True)
    for partition in missing_partitions:
        color_print(f'Partition {partition} not extracted', color=Color.YELLOW)
        dump_partition_dir = path.join(dump_dir, partition)
        os.mkdir(dump_partition_dir)


def run_extract_fns(dump_dir: str, ctx: ExtractCtx):
    for extract_pattern, extract_fns in ctx.extract_fns.items():
        if not isinstance(extract_fns, list):
            extract_fns = [extract_fns]

        found_files: List[str] = []
        processed_files = set()
        for file in os.scandir(dump_dir):
            match = re.match(extract_pattern, file.name)
            if match is not None:
                found_files.append(file.path)

        print_file_paths(found_files, f'pattern: "{extract_pattern}"')

        for file_path in found_files:
            file_name = path.basename(file_path)
            print(f'Processing {file_name}')
            for extract_fn in extract_fns:
                processed_file = extract_fn(ctx, file_path, dump_dir)
                if processed_file is not None:
                    processed_files.add(processed_file)

        remove_file_paths(processed_files)


def move_alternate_partition_paths(dump_dir: str):
    # Make sure that even for devices that don't have separate partitions
    # for vendor, odm, etc., the partition directories are copied into the root
    # dump directory to simplify file copying
    for (
        partition,
        alternate_partition_path,
    ) in ALTERNATE_PARTITION_PATH_MAP.items():
        partition_path = path.join(dump_dir, partition)
        if path.isdir(partition_path):
            continue

        partition_path = path.join(dump_dir, alternate_partition_path)
        if not path.isdir(partition_path):
            continue

        shutil.move(partition_path, dump_dir)


def move_sar_system_paths(dump_dir: str):
    # For System-as-Root, move system/ to system_root/ and system/system/
    # to system/
    system_dir = path.join(dump_dir, 'system')
    system_system_dir = path.join(system_dir, 'system')
    if path.isdir(system_system_dir):
        system_root_dir = path.join(dump_dir, 'system_root')
        system_root_system_dir = path.join(system_root_dir, 'system')

        shutil.move(system_dir, system_root_dir)
        shutil.move(system_root_system_dir, dump_dir)


def filter_already_extracted(dump_dir: str, ctx: ExtractCtx):
    ctx.extract_partitions = find_partitions(dump_dir, ctx, missing=True)
    ctx.firmware_files = find_firmware_files(dump_dir, ctx, missing=True)
    ctx.factory_files = find_factory_files(dump_dir, ctx, missing=True)
    return ctx.extract_partitions or ctx.firmware_files or ctx.factory_files
