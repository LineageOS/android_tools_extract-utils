#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from contextlib import contextmanager
import os
import shutil
import tempfile

from os import path
from typing import Callable, List, Optional


from extract_utils.args import ArgsSource
from extract_utils.utils import (
    flatten_dir,
    parallel_input_cmds,
    process_cmds_in_parallel,
)
from extract_utils.tools import (
    brotli_path,
    lpunpack_path,
    ota_extractor_path,
    sdat2img_path,
    simg2img_path,
)

DEFAULT_EXTRACTED_PARTITIONS = [
    'system',
    'odm',
    'product',
    'system_ext',
    'vendor',
]
BROTLI_EXT = '.new.dat.br'
SPARSE_DATA_EXT = '.new.dat'
TRANSFER_LIST_EXT = '.transfer.list'
SPARSE_CHUNK_SUFFIX = '_sparsechunk'


extract_fn_type = Callable[['ExtractCtx', str, str], List[str]]


class ExtractCtx:
    def __init__(
        self,
        source: str | ArgsSource,
        keep_dump: bool,
        extract_fns: List[extract_fn_type],
        extract_partitions: Optional[List[str]],
        firmware_partitions: Optional[List[str]],
    ):
        self.source = source
        self.keep_dump = keep_dump
        self.extract_fns = extract_fns

        if not extract_partitions:
            extract_partitions = DEFAULT_EXTRACTED_PARTITIONS
        self.extract_partitions = extract_partitions

        if not firmware_partitions:
            firmware_partitions = []
        self.firmware_partitions = firmware_partitions


def is_extract_partition_file_name(
    extract_partitions: Optional[List[str]],
    file_name: str,
):
    if extract_partitions is None:
        return True

    if file_name in extract_partitions:
        return True

    root_ext = file_name.split('.', 1)
    if len(root_ext) != 2:
        return False

    return root_ext[0] in extract_partitions


def find_files_with_magic(
    extract_partitions: Optional[List[str]],
    input_path: str,
    magic: bytes,
    position: int = 0,
) -> List[str]:
    file_paths = []
    for file in os.scandir(input_path):
        if not path.isfile(file):
            continue

        if not is_extract_partition_file_name(extract_partitions, file.name):
            continue

        with open(file.path, 'rb') as f:
            f.seek(position)
            file_magic = f.read(len(magic))
            if file_magic == magic:
                file_paths.append(file.path)

    return file_paths


def find_files_with_ext(
    extract_partitions: Optional[List[str]],
    input_path: str,
    ext: str,
):
    file_paths = []
    for file in os.scandir(input_path):
        if not path.isfile(file):
            continue

        if not is_extract_partition_file_name(extract_partitions, file.name):
            continue

        if file.name.endswith(ext):
            file_paths.append(file.path)

    return file_paths


def find_sparse_raw_image_paths(
    extract_partitions: Optional[List[str]],
    input_path: str,
):
    magic = bytes([0x3A, 0xFF, 0x26, 0xED])
    return find_files_with_magic(extract_partitions, input_path, magic)


def find_erofs_paths(
    extract_partitions: Optional[List[str]],
    input_path: str,
):
    magic = bytes([0xE2, 0xE1, 0xF5, 0xE0])
    return find_files_with_magic(extract_partitions, input_path, magic, 1024)


def find_ext4_paths(
    extract_partitions: Optional[List[str]],
    input_path: str,
):
    magic = bytes([0x53, 0xEF])
    return find_files_with_magic(extract_partitions, input_path, magic, 1080)


def find_payload_path(input_path: str) -> Optional[str]:
    payload_paths = find_files_with_magic(None, input_path, b'CrAU')
    if payload_paths:
        assert len(payload_paths) == 1
        return payload_paths[0]

    return None


def find_super_img_path(input_path: str) -> Optional[str]:
    super_img_path = path.join(input_path, 'super.img')
    if path.isfile(super_img_path):
        return super_img_path

    return None


def print_file_paths(file_paths: List[str], file_type: str):
    if not file_paths:
        return

    file_names = [path.basename(fp) for fp in file_paths]
    file_names_str = ', '.join(file_names)
    print(f'Found {file_type} files: {file_names_str}')


def remove_file_paths(file_paths: List[str]):
    if not file_paths:
        return

    file_names = [path.basename(fp) for fp in file_paths]
    file_names_str = ', '.join(file_names)
    print(f'Deleting {file_names_str}')

    for file_path in file_paths:
        os.remove(file_path)


def extract_payload_bin(ctx: ExtractCtx, file_path: str, output_dir: str):
    procs: parallel_input_cmds = []
    for partition in ctx.extract_partitions + ctx.firmware_partitions:
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

    process_cmds_in_parallel(procs)


def extract_sparse_raw_imgs(file_paths: List[str], output_dir: str):
    new_file_paths = []

    partition_chunks_map = {}
    for file_path in file_paths:
        file_name = path.basename(file_path)

        # Split extension to get chunk index x from
        # partition.img_sparsechunk.x files
        base_file_name, chunk_index = path.splitext(file_name)

        if base_file_name.endswith(SPARSE_CHUNK_SUFFIX) and chunk_index:
            # Sparse chunk, remove the suffix to get the partition name
            output_file_name = base_file_name[: -len(SPARSE_CHUNK_SUFFIX)]
            # Remove dot from extension and cast to int find chunk index
            chunk_index = int(chunk_index[1:])
        else:
            output_file_name = file_name
            chunk_index = 0

            # Rename single sparse image to .sparse to avoid naming conflicts
            sparse_file_path = f'{file_path}.sparse'
            os.rename(file_path, sparse_file_path)
            file_path = sparse_file_path

        new_file_paths.append(file_path)

        # Create a sparse list of the chunks, should be completely filled
        # after iterating over all the file paths
        # Do this to avoid splitting the file paths again to sort at the end
        partition_chunks_map.setdefault(output_file_name, [])
        partition_chunks = partition_chunks_map[output_file_name]
        assert isinstance(partition_chunks, list)

        missing_indices = chunk_index - len(partition_chunks) + 1
        partition_chunks.extend([None] * missing_indices)
        assert partition_chunks[chunk_index] is None

        partition_chunks[chunk_index] = file_path

    procs: parallel_input_cmds = []
    for output_file_name, partition_chunks in partition_chunks_map.items():
        output_file_path = path.join(output_dir, output_file_name)

        procs.append(
            (
                output_file_name,
                [simg2img_path] + partition_chunks + [output_file_path],
            )
        )

    process_cmds_in_parallel(procs, fatal=True)

    return new_file_paths


def extract_super_img(ctx: ExtractCtx, file_path: str, output_dir: str):
    procs: parallel_input_cmds = []
    for partition in ctx.extract_partitions + ctx.firmware_partitions:
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

    process_cmds_in_parallel(procs)

    for partition in ctx.extract_partitions + ctx.firmware_partitions:
        partition_a_img = f'{partition}_a.img'
        partition_img = f'{partition}.img'

        partition_a_path = path.join(output_dir, partition_a_img)
        partition_path = path.join(output_dir, partition_img)

        if path.exists(partition_a_path):
            os.rename(partition_a_path, partition_path)


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
def get_dump_dir(ctx: ExtractCtx):
    source = ctx.source
    assert isinstance(source, str)

    if not path.isfile(source) and not path.isdir(source):
        # TODO: use custom error class
        raise ValueError(f'Unexpected file type at {source}')

    if path.isdir(source):
        # Source is a directory, try to extract its contents into itself
        print(f'Extracting to source dump dir {source}')
        yield source, True
        return

    if not ctx.keep_dump:
        # We don't want to keep the dump, ignore previous dump output
        # and use a temporary directory to extract
        with tempfile.TemporaryDirectory() as dump_dir:
            print(f'Extracting to temporary dump dir {dump_dir}')
            yield dump_dir, True
            return

    # Remove the extension from the file and use it as a dump dir
    dump_dir, _ = path.splitext(source)

    try:
        os.rmdir(dump_dir)
    except Exception:
        pass

    if path.isdir(dump_dir):
        print(f'Using existing dump dir {dump_dir}')
        # Previous dump output exists, return it and don't extract
        yield dump_dir, False
        return

    if path.exists(dump_dir):
        raise ValueError(f'Unexpected file type at {dump_dir}')

    print(f'Extracting to new dump dir {dump_dir}')
    os.mkdir(dump_dir)
    yield dump_dir, True


def extract_image(ctx: ExtractCtx, dump_dir: str):
    source = ctx.source
    assert isinstance(source, str)

    source_is_file = path.isfile(source)

    if source_is_file:
        print(f'Extracting file {source}')
        shutil.unpack_archive(source, extract_dir=dump_dir)
        flatten_dir(dump_dir)

    payload_bin_path = find_payload_path(dump_dir)
    if payload_bin_path:
        print_file_paths([payload_bin_path], 'payload.bin')
        extract_payload_bin(ctx, payload_bin_path, dump_dir)
        remove_file_paths([payload_bin_path])

    sparse_raw_paths = find_sparse_raw_image_paths(['super'], dump_dir)
    if sparse_raw_paths:
        print_file_paths(sparse_raw_paths, 'sparse raw')
        # Single sparse files are renamed to .sparse to avoid naming conflicts
        # Retrieve the updated file paths
        sparse_raw_paths = extract_sparse_raw_imgs(sparse_raw_paths, dump_dir)
        remove_file_paths(sparse_raw_paths)

    super_img_path = find_super_img_path(dump_dir)
    if super_img_path:
        print_file_paths([super_img_path], 'super.img')
        extract_super_img(ctx, super_img_path, dump_dir)
        remove_file_paths([super_img_path])

    brotli_paths = find_files_with_ext(
        ctx.extract_partitions,
        dump_dir,
        BROTLI_EXT,
    )
    if brotli_paths:
        print_file_paths(brotli_paths, 'brotli')
        extract_brotli_imgs(brotli_paths, dump_dir)
        remove_file_paths(brotli_paths)

    sparse_data_paths = find_files_with_ext(
        ctx.extract_partitions,
        dump_dir,
        SPARSE_DATA_EXT,
    )
    if sparse_data_paths:
        print_file_paths(sparse_data_paths, 'sparse data')
        extract_sparse_data_imgs(sparse_data_paths, dump_dir)
        remove_file_paths(sparse_data_paths)

    erofs_paths = find_erofs_paths(
        ctx.extract_partitions,
        dump_dir,
    )
    if erofs_paths:
        print_file_paths(erofs_paths, 'EROFS')
        extract_erofs(erofs_paths, dump_dir)
        remove_file_paths(erofs_paths)

    ext4_paths = find_ext4_paths(
        ctx.extract_partitions,
        dump_dir,
    )
    if ext4_paths:
        print_file_paths(ext4_paths, 'EXT4')
        extract_ext4(ext4_paths, dump_dir)
        remove_file_paths(ext4_paths)

    for extract_fn in ctx.extract_fns:
        processed_files = extract_fn(ctx, dump_dir, dump_dir)
        remove_file_paths(processed_files)
