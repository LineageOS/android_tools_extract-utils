#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from os import path
from tarfile import TarFile
from typing import Callable, Generator, Iterable, List, Optional, Set, Union
from zipfile import ZipFile

from extract_utils.fixups import fixups_type, fixups_user_type
from extract_utils.tools import (
    brotli_path,
    lpunpack_path,
    ota_extractor_path,
    sdat2img_path,
    simg2img_path,
)
from extract_utils.utils import (
    Color,
    color_print,
    parallel_input_cmds,
    process_cmds_in_parallel,
)

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
        keep_dump=False,
        extract_fns: Optional[extract_fns_type] = None,
        extract_partitions: Optional[List[str]] = None,
        firmware_partitions: Optional[List[str]] = None,
        firmware_files: Optional[List[str]] = None,
        factory_files: Optional[List[str]] = None,
    ):
        if extract_fns is None:
            extract_fns = {}
        if extract_partitions is None:
            extract_partitions = []
        if firmware_partitions is None:
            firmware_partitions = []
        if firmware_files is None:
            firmware_files = []
        if factory_files is None:
            factory_files = []

        self.keep_dump = keep_dump
        # Files for extract functions are extracted if their name
        # matches the regex
        self.extract_fns = extract_fns
        # Files for partitions are extracted if, after removing the
        # extension, their name matches a partition
        self.extract_partitions = extract_partitions
        self.firmware_partitions = firmware_partitions
        self.extra_partitions: List[str] = []
        # Files are extracted if their name matches as-is
        self.firmware_files = firmware_files
        self.factory_files = factory_files
        self.extra_files: List[str] = []


def file_name_to_partition(file_name: str):
    return file_name.split('.', 1)[0]


def find_files(
    extract_partitions: Optional[List[str]],
    input_path: str,
    magic: Optional[bytes] = None,
    position=0,
    ext: Optional[str] = None,
) -> List[str]:
    file_paths = []
    for file in os.scandir(input_path):
        if not file.is_file():
            continue

        partition = file_name_to_partition(file.name)
        if (
            extract_partitions is not None
            and partition not in extract_partitions
            and file.name not in extract_partitions
        ):
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


def should_extract_pattern_file_name(
    extract_fns: extract_fns_type, file_name: str
):
    for extract_pattern in extract_fns:
        match = re.match(extract_pattern, file_name)
        if match is not None:
            return True

    return False


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


def _filter_files(
    extract_partitions: List[str],
    extract_file_names: List[str],
    extract_fns: extract_fns_type,
    file_paths: List[str],
    found_partitions: Set[str],
) -> List[str]:
    found_file_paths: List[str] = []

    for file_path in file_paths:
        file_name = path.basename(file_path)

        if file_name in extract_partitions:
            found_file_paths.append(file_path)
            found_partitions.add(file_name)
            continue

        partition = file_name_to_partition(file_name)
        if partition in extract_partitions:
            found_file_paths.append(file_path)
            found_partitions.add(partition)
            continue

        if file_name in extract_file_names:
            found_file_paths.append(file_path)
            continue

        for extract_pattern in extract_fns:
            match = re.match(extract_pattern, file_name)
            if match is not None:
                found_file_paths.append(file_path)

    return found_file_paths


def filter_files(
    partition_lists: List[List[str]],
    file_name_lists: List[List[str]],
    found_partitions: Set[str],
    extract_fns: extract_fns_type,
    file_paths: List[str],
) -> List[str]:
    extract_partitions = sum(partition_lists, [])
    extract_file_names = sum(file_name_lists, [])
    found_file_paths: List[str] = []

    while extract_partitions:
        found_file_paths += _filter_files(
            extract_partitions,
            extract_file_names,
            extract_fns,
            file_paths,
            found_partitions,
        )

        # Prevent loop from adding matching files again, only partitions
        # have alternatives
        extract_file_names = []
        extract_fns = {}

        extract_partitions = find_alternate_partitions(
            extract_partitions,
            found_partitions,
        )

    return list(found_file_paths)


def filter_extract_file_paths(
    ctx: ExtractCtx,
    file_paths: List[str],
):
    return filter_files(
        [
            ctx.extract_partitions,
            ctx.firmware_partitions,
            ctx.extra_partitions,
        ],
        [
            ctx.firmware_files,
            ctx.factory_files,
            ctx.extra_files,
        ],
        set(),
        dict(ctx.extract_fns),
        file_paths,
    )


def filter_extract_partitions(
    extract_partitions: List[str],
    file_paths: List[str],
):
    new_extract_partitions: Set[str] = set()
    filter_files(
        [extract_partitions],
        [],
        new_extract_partitions,
        {},
        file_paths,
    )
    return list(new_extract_partitions)


def update_extract_partitions(ctx: ExtractCtx, input_path: str):
    file_paths = [f.path for f in os.scandir(input_path) if f.is_file()]

    ctx.extract_partitions = filter_extract_partitions(
        ctx.extract_partitions,
        file_paths,
    )


def find_sparse_raw_paths(extract_partitions: List[str], input_path: str):
    magic = 0xED26FF3A.to_bytes(4, 'little')
    return find_files(extract_partitions, input_path, magic)


def find_erofs_paths(extract_partitions: List[str], input_path: str):
    magic = 0xE0F5E1E2.to_bytes(4, 'little')
    return find_files(extract_partitions, input_path, magic, 1024)


def find_ext4_paths(extract_partitions: List[str], input_path: str):
    magic = 0xEF53.to_bytes(2, 'little')
    return find_files(extract_partitions, input_path, magic, 1080)


def find_payload_paths(extract_partitions: List[str], input_path: str):
    return find_files(extract_partitions, input_path, b'CrAU')


def find_super_img_paths(extract_partitions: List[str], input_path: str):
    magic = 0x616C4467.to_bytes(4, 'little')
    return find_files(extract_partitions, input_path, magic, 4096)


def find_brotli_paths(extract_partitions: List[str], input_path: str):
    return find_files(extract_partitions, input_path, ext=BROTLI_EXT)


def find_sparse_data_paths(extract_partitions: List[str], input_path: str):
    return find_files(extract_partitions, input_path, ext=SPARSE_DATA_EXT)


def print_file_paths(file_paths: List[str], file_type: str):
    if not file_paths:
        return

    file_names = [path.basename(fp) for fp in file_paths]
    file_names_str = ', '.join(file_names)
    print(f'Found {file_type} files: {file_names_str}')


def remove_file_paths(file_paths: Iterable[str]):
    if not file_paths:
        return

    file_names = [path.basename(fp) for fp in file_paths]
    file_names_str = ', '.join(file_names)
    print(f'Deleting {file_names_str}')

    for file_path in file_paths:
        os.remove(file_path)


def _extract_payload_bin(
    extract_partitions: List[str],
    file_path: str,
    output_dir: str,
):
    # TODO: switch to python extractor to be able to detect partition
    # names to make this process fatal on failure

    procs: parallel_input_cmds = []

    for partition in extract_partitions:
        procs.append(
            (
                partition,
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
        )

    _, ret_success = process_cmds_in_parallel(procs)

    return ret_success


def extract_payload_bin(ctx: ExtractCtx, file_path: str, output_dir: str):
    extract_partitions = ctx.extract_partitions + ctx.firmware_partitions
    while extract_partitions:
        found_partitions = _extract_payload_bin(
            extract_partitions,
            file_path,
            output_dir,
        )

        extract_partitions = find_alternate_partitions(
            extract_partitions,
            found_partitions,
        )


def partition_chunk_index(file_path: str):
    _, chunk_index = path.splitext(file_path)

    return int(chunk_index[1:])


def extract_sparse_raw_imgs(file_paths: List[str], output_dir: str):
    new_file_paths = []

    partition_chunks_map: dict[str, List[str]] = {}
    for file_path in file_paths:
        file_name = path.basename(file_path)

        # Split extension to remove chunk index x from
        # partition.img_sparsechunk.x files
        base_file_name, _ = path.splitext(file_name)

        if base_file_name.endswith(SPARSE_CHUNK_SUFFIX):
            # Sparse chunk, remove _sparsechunk to get the partition name
            output_file_name = base_file_name[: -len(SPARSE_CHUNK_SUFFIX)]
        else:
            output_file_name = file_name
            # Rename single sparse image to _sparsechunk.0 to avoid naming conflicts
            sparse_file_path = f'{file_path}{SPARSE_CHUNK_SUFFIX}.0'
            os.rename(file_path, sparse_file_path)
            file_path = sparse_file_path

        new_file_paths.append(file_path)

        partition_chunks = partition_chunks_map.setdefault(output_file_name, [])
        partition_chunks.append(file_path)

    procs: parallel_input_cmds = []
    for output_file_name, partition_chunks in partition_chunks_map.items():
        output_file_path = path.join(output_dir, output_file_name)

        partition_chunks.sort(key=partition_chunk_index)

        procs.append(
            (
                output_file_name,
                [simg2img_path] + partition_chunks + [output_file_path],
            )
        )

    process_cmds_in_parallel(procs, fatal=True)

    return new_file_paths


def unslot_partition(partition_slot: str):
    return partition_slot.rsplit('_', 1)[0]


def _extract_super_img(
    extract_partitions: List[str],
    file_path: str,
    output_dir: str,
):
    # TODO: switch to python lpunpack to be able to detect partition
    # names to make this process fatal on failure
    procs: parallel_input_cmds = []

    for partition in extract_partitions:
        for slot in ['', '_a']:
            partition_slot = f'{partition}{slot}'
            procs.append(
                (
                    partition_slot,
                    [
                        lpunpack_path,
                        '--partition',
                        partition_slot,
                        file_path,
                        output_dir,
                    ],
                )
            )

    _, ret_success = process_cmds_in_parallel(procs)

    # Make sure that there are no duplicates
    assert len(ret_success) == len(set(ret_success))

    found_partitions = []
    for partition_slot in ret_success:
        partition = unslot_partition(partition_slot)
        found_partitions.append(partition)

        if partition == partition_slot:
            continue

        partition_path = path.join(output_dir, f'{partition}.img')
        partition_slot_path = path.join(output_dir, f'{partition_slot}.img')

        os.rename(partition_slot_path, partition_path)

    return found_partitions


def extract_super_img(ctx: ExtractCtx, file_path: str, output_dir: str):
    extract_partitions = ctx.extract_partitions
    while extract_partitions:
        found_partitions = _extract_super_img(
            extract_partitions,
            file_path,
            output_dir,
        )

        extract_partitions = find_alternate_partitions(
            extract_partitions,
            found_partitions,
        )


def extract_brotli_imgs(file_paths: List[str], output_path: str):
    procs: parallel_input_cmds = []
    for file_path in file_paths:
        file_name = path.basename(file_path)
        output_file_name, _ = path.splitext(file_name)
        output_file_path = path.join(output_path, output_file_name)

        procs.append(
            (file_name, [brotli_path, '-d', file_path, '-o', output_file_path])
        )

    process_cmds_in_parallel(procs, fatal=True)


def extract_sparse_data_imgs(file_paths: List[str], output_path: str):
    procs: parallel_input_cmds = []
    for file_path in file_paths:
        assert file_path.endswith(SPARSE_DATA_EXT)

        base_file_path = file_path[: -len(SPARSE_DATA_EXT)]
        transfer_file_path = f'{base_file_path}{TRANSFER_LIST_EXT}'

        base_file_name = path.basename(base_file_path)
        img_file_name = f'{base_file_name}.img'

        output_file_path = path.join(output_path, img_file_name)

        procs.append(
            (
                base_file_name,
                [
                    sdat2img_path,
                    transfer_file_path,
                    file_path,
                    output_file_path,
                ],
            )
        )

    process_cmds_in_parallel(procs, fatal=True)


def extract_erofs(file_paths: List[str], output_path: str):
    procs: parallel_input_cmds = []
    for file_path in file_paths:
        base_file_name = path.basename(file_path)

        partition_name, _ = path.splitext(base_file_name)
        partition_output_path = path.join(output_path, partition_name)
        os.mkdir(partition_output_path)

        procs.append(
            (
                base_file_name,
                [
                    'fsck.erofs',
                    f'--extract={partition_output_path}',
                    file_path,
                ],
            )
        )

    process_cmds_in_parallel(procs, fatal=True)


def extract_ext4(file_paths: List[str], output_path: str):
    procs: parallel_input_cmds = []
    for file_path in file_paths:
        base_file_name = path.basename(file_path)

        partition_name, _ = path.splitext(base_file_name)
        partition_output_path = path.join(output_path, partition_name)
        os.mkdir(partition_output_path)

        procs.append(
            (
                base_file_name,
                [
                    'debugfs',
                    '-R',
                    f'rdump / {partition_output_path}',
                    file_path,
                ],
            )
        )

    # TODO: check for symlinks like the old code?

    process_cmds_in_parallel(procs, fatal=True)


@contextmanager
def get_dump_dir(
    source: str,
    ctx: ExtractCtx,
) -> Generator[str, None, None]:
    if not path.exists(source):
        raise FileNotFoundError(f'File not found: {source}')

    if not path.isfile(source) and not path.isdir(source):
        raise ValueError(f'Unexpected file type at {source}')

    if path.isdir(source):
        # Source is a directory, try to extract its contents into itself
        print(f'Extracting to source dump dir {source}')
        yield source
        return

    if not ctx.keep_dump:
        # We don't want to keep the dump, ignore previous dump output
        # and use a temporary directory to extract
        with tempfile.TemporaryDirectory() as dump_dir:
            print(f'Extracting to temporary dump dir {dump_dir}')

            try:
                yield dump_dir
            except GeneratorExit:
                pass

            return

    # Remove the extension from the file and use it as a dump dir
    dump_dir, _ = path.splitext(source)

    if path.isdir(dump_dir):
        print(f'Using existing dump dir {dump_dir}')
        # Previous dump output exists, return it and don't extract
        yield dump_dir
        return

    if path.exists(dump_dir):
        raise ValueError(f'Unexpected file type at {dump_dir}')

    print(f'Extracting to new dump dir {dump_dir}')
    os.mkdir(dump_dir)
    yield dump_dir


def unzip_file(source: str, file_path: str, output_file_path: str):
    with ZipFile(source) as zip_file:
        with zip_file.open(file_path) as z:
            with open(output_file_path, 'wb') as f:
                shutil.copyfileobj(z, f)


def untar_file(tar: TarFile, file_path: str, output_file_path: str):
    t = tar.extractfile(file_path)
    if t is None:
        return

    with open(output_file_path, 'wb') as f:
        shutil.copyfileobj(t, f)


def extract_zip(
    source: str,
    ctx: ExtractCtx,
    dump_dir: str,
):
    with ZipFile(source) as zip_file:
        file_paths = zip_file.namelist()

    file_paths = filter_extract_file_paths(ctx, file_paths)

    with ProcessPoolExecutor(len(file_paths)) as exe:
        for file_path in file_paths:
            file_name = path.basename(file_path)
            output_file_path = path.join(dump_dir, file_name)

            print(f'Extracting {file_path}')

            exe.submit(unzip_file, source, file_path, output_file_path)


def extract_tar(source: str, ctx: ExtractCtx, dump_dir: str):
    if source.endswith('gz'):
        mode = 'r:gz'
    else:
        mode = 'r'

    with tarfile.open(source, mode) as tar:
        file_paths = tar.getnames()
        file_paths = filter_extract_file_paths(ctx, file_paths)

        for file_path in file_paths:
            file_name = path.basename(file_path)
            output_file_path = path.join(dump_dir, file_name)

            print(f'Extracting {file_path}')

            t = tar.extractfile(file_path)
            if t is None:
                continue

            with open(output_file_path, 'wb') as f:
                shutil.copyfileobj(t, f)


def extract_image_file(source: str, ctx: ExtractCtx, dump_dir: str):
    if source.endswith('.zip'):
        extract_fn = extract_zip
    elif (
        source.endswith('.tar.gz')
        or source.endswith('.tgz')
        or source.endswith('.tar')
    ):
        extract_fn = extract_tar
    else:
        raise ValueError(f'Unexpected file type at {source}')

    print(f'Extracting file {source}')
    extract_fn(source, ctx, dump_dir)


def extract_image(source: str, ctx: ExtractCtx, dump_dir: str):
    source_is_file = path.isfile(source)

    ctx.extra_partitions.append(SUPER_PARTITION_NAME)
    ctx.extra_files.append(PAYLOAD_BIN_FILE_NAME)

    if source_is_file:
        extract_image_file(source, ctx, dump_dir)

    run_extract_fns(ctx, dump_dir)

    payload_bin_paths = find_payload_paths(
        [PAYLOAD_BIN_FILE_NAME],
        dump_dir,
    )
    if payload_bin_paths:
        assert len(payload_bin_paths) == 1
        print_file_paths(payload_bin_paths, PAYLOAD_BIN_FILE_NAME)
        extract_payload_bin(ctx, payload_bin_paths[0], dump_dir)
        remove_file_paths(payload_bin_paths)

    sparse_raw_paths = find_sparse_raw_paths(
        ctx.extract_partitions + [SUPER_PARTITION_NAME],
        dump_dir,
    )
    if sparse_raw_paths:
        print_file_paths(sparse_raw_paths, 'sparse raw')
        # Single sparse files are renamed to _sparsechunk.0 to avoid naming conflicts
        # Retrieve the updated file paths
        sparse_raw_paths = extract_sparse_raw_imgs(sparse_raw_paths, dump_dir)
        remove_file_paths(sparse_raw_paths)

    super_img_paths = find_super_img_paths([SUPER_IMG_NAME], dump_dir)
    if super_img_paths:
        assert len(super_img_paths) == 1
        print_file_paths(super_img_paths, SUPER_IMG_NAME)
        extract_super_img(ctx, super_img_paths[0], dump_dir)
        remove_file_paths(super_img_paths)

    # Now that all partitions that could have been unpacked from their
    # containers have been unpacked, update the extract_partitions
    # to handle alternate partitions
    update_extract_partitions(ctx, dump_dir)

    brotli_paths = find_brotli_paths(ctx.extract_partitions, dump_dir)
    if brotli_paths:
        print_file_paths(brotli_paths, 'brotli')
        extract_brotli_imgs(brotli_paths, dump_dir)
        remove_file_paths(brotli_paths)

    sparse_data_paths = find_sparse_data_paths(ctx.extract_partitions, dump_dir)
    if sparse_data_paths:
        print_file_paths(sparse_data_paths, 'sparse data')
        extract_sparse_data_imgs(sparse_data_paths, dump_dir)
        remove_file_paths(sparse_data_paths)

    erofs_paths = find_erofs_paths(ctx.extract_partitions, dump_dir)
    if erofs_paths:
        print_file_paths(erofs_paths, 'EROFS')
        extract_erofs(erofs_paths, dump_dir)
        remove_file_paths(erofs_paths)

    ext4_paths = find_ext4_paths(ctx.extract_partitions, dump_dir)
    if ext4_paths:
        print_file_paths(ext4_paths, 'EXT4')
        extract_ext4(ext4_paths, dump_dir)
        remove_file_paths(ext4_paths)

    run_extract_fns(ctx, dump_dir)

    move_sar_system_paths(dump_dir)

    move_alternate_partition_paths(dump_dir)

    for partition in ctx.extract_partitions:
        dump_partition_dir = path.join(dump_dir, partition)

        if path.isdir(dump_partition_dir):
            continue

        color_print(f'Partition {partition} not extracted', color=Color.YELLOW)
        # Create empty partition dir to prevent re-extraction
        os.mkdir(dump_partition_dir)


def run_extract_fns(ctx: ExtractCtx, dump_dir: str):
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


def filter_already_extracted_partitions(dump_dir: str, ctx: ExtractCtx):
    not_extracted_partitions = []

    for partition in ctx.extract_partitions:
        dump_partition_dir = path.join(dump_dir, partition)

        if path.isdir(dump_partition_dir):
            continue

        if path.exists(dump_partition_dir):
            raise ValueError(f'Unexpected file type at {dump_partition_dir}')

        not_extracted_partitions.append(partition)

    ctx.extract_partitions = not_extracted_partitions
