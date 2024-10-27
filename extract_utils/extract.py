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
from functools import partial
from os import path
from tarfile import TarFile
from typing import Callable, Generator, List, Optional
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
    parallel_input_cmds,
    process_cmds_in_parallel,
)

ALTERNATE_PARTITION_PATH_MAP = {
    'product': [
        'system/product',
    ],
    'system_ext': [
        'system/system_ext',
    ],
    'odm': [
        'vendor/odm',
        'system/vendor/odm',
    ],
    'vendor': [
        'system/vendor',
    ],
}


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


extract_fn_type = Callable[['ExtractCtx', str, str], str]
extract_fns_user_type = fixups_user_type[extract_fn_type]
extract_fns_type = fixups_type[extract_fn_type]


class ExtractCtx:
    def __init__(
        self,
        keep_dump: bool,
        extract_fns: extract_fns_type,
        extract_partitions: List[str],
        firmware_partitions: List[str],
        firmware_files: List[str],
    ):
        self.keep_dump = keep_dump
        self.extract_fns = extract_fns
        self.extract_partitions = extract_partitions
        self.firmware_partitions = firmware_partitions
        self.firmware_files = firmware_files


def should_extract_partition_file_name(
    extract_partitions: Optional[List[str]],
    file_name: str,
):
    if extract_partitions is None:
        return True

    if file_name in extract_partitions:
        return True

    root_rest = file_name.split('.', 1)

    return root_rest[0] in extract_partitions


def find_files_with_magic(
    extract_partitions: Optional[List[str]],
    input_path: str,
    magic: bytes,
    position: int = 0,
) -> List[str]:
    file_paths = []
    for file in os.scandir(input_path):
        if not file.is_file():
            continue

        if not should_extract_partition_file_name(
            extract_partitions,
            file.name,
        ):
            continue

        with open(file, 'rb') as f:
            f.seek(position)
            file_magic = f.read(len(magic))
            if file_magic == magic:
                file_paths.append(file.path)

    return file_paths


def find_files_with_ext(
    extract_partitions: List[str],
    input_path: str,
    ext: str,
):
    file_paths = []
    for file in os.scandir(input_path):
        if not file.is_file():
            continue

        if not should_extract_partition_file_name(
            extract_partitions,
            file.name,
        ):
            continue

        if file.name.endswith(ext):
            file_paths.append(file.path)

    return file_paths


def find_sparse_raw_image_paths(extract_partitions: List[str], input_path: str):
    magic = bytes([0x3A, 0xFF, 0x26, 0xED])
    return find_files_with_magic(extract_partitions, input_path, magic)


def find_erofs_paths(extract_partitions: List[str], input_path: str):
    magic = bytes([0xE2, 0xE1, 0xF5, 0xE0])
    return find_files_with_magic(extract_partitions, input_path, magic, 1024)


def find_ext4_paths(extract_partitions: List[str], input_path: str):
    magic = bytes([0x53, 0xEF])
    return find_files_with_magic(extract_partitions, input_path, magic, 1080)


def find_payload_paths(extract_partitions: List[str], input_path: str):
    return find_files_with_magic(extract_partitions, input_path, b'CrAU')


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

    # TODO: switch to python extractor to be able to detect partition
    # names to make this process fatal on failure
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
    # TODO: switch to python lpunpack to be able to detect partition
    # names to make this process fatal on failure
    for partition in ctx.extract_partitions:
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

    for partition in ctx.extract_partitions:
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
            yield dump_dir
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


def should_extract_file_path(
    ctx: ExtractCtx,
    extract_partitions: List[str],
    extract_file_names: List[str],
    file_path: str,
):
    file_name = path.basename(file_path)

    if should_extract_partition_file_name(
        ctx.extract_partitions + extract_partitions,
        file_name,
    ):
        return True

    if file_name in ctx.firmware_files + extract_file_names:
        return True

    for extract_pattern in ctx.extract_fns:
        match = re.match(extract_pattern, file_name)
        if match is not None:
            return True

    return False


def filter_extract_file_paths(
    ctx: ExtractCtx,
    extract_partitions: List[str],
    extract_file_names: List[str],
    file_paths: List[str],
):
    fn = partial(
        should_extract_file_path,
        ctx,
        extract_partitions,
        extract_file_names,
    )
    return list(filter(fn, file_paths))


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
    extract_partitions: List[str],
    extract_file_names: List[str],
    dump_dir: str,
):
    with ZipFile(source) as zip_file:
        file_paths = zip_file.namelist()

    print_file_paths(file_paths, 'in zip')

    file_paths = filter_extract_file_paths(
        ctx,
        extract_partitions,
        extract_file_names,
        file_paths,
    )

    with ProcessPoolExecutor(len(file_paths)) as exe:
        for file_path in file_paths:
            file_name = path.basename(file_path)
            output_file_path = path.join(dump_dir, file_name)

            print(f'Extracting {file_path}')

            exe.submit(unzip_file, source, file_path, output_file_path)


def extract_tar(
    source: str,
    ctx: ExtractCtx,
    extract_partitions: List[str],
    extract_file_names: List[str],
    dump_dir: str,
):
    if source.endswith('gz'):
        mode = 'r:gz'
    else:
        mode = 'r'

    with tarfile.open(source, mode) as tar:
        file_paths = tar.getnames()
        file_paths = filter_extract_file_paths(
            ctx,
            extract_partitions,
            extract_file_names,
            file_paths,
        )

        print_file_paths(file_paths, 'in tar')

        for file_path in file_paths:
            file_name = path.basename(file_path)
            output_file_path = path.join(dump_dir, file_name)

            print(f'Processing {file_path}')

            t = tar.extractfile(file_path)
            if t is None:
                continue

            with open(output_file_path, 'wb') as f:
                shutil.copyfileobj(t, f)


def extract_image_file(
    source: str,
    ctx: ExtractCtx,
    extract_partitions: List[str],
    extract_file_names: List[str],
    dump_dir: str,
):
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
    extract_fn(
        source,
        ctx,
        extract_partitions,
        extract_file_names,
        dump_dir,
    )


def extract_image(source: str, ctx: ExtractCtx, dump_dir: str):
    source_is_file = path.isfile(source)

    extract_partitions = [
        'super',
    ]

    extract_file_names = [
        'payload.bin',
    ]

    if source_is_file:
        extract_image_file(
            source,
            ctx,
            extract_partitions,
            extract_file_names,
            dump_dir,
        )

    payload_bin_paths = find_payload_paths(extract_file_names, dump_dir)
    if payload_bin_paths:
        assert len(payload_bin_paths) == 1
        print_file_paths(payload_bin_paths, 'payload.bin')
        extract_payload_bin(ctx, payload_bin_paths[0], dump_dir)
        remove_file_paths(payload_bin_paths)

    sparse_raw_paths = find_sparse_raw_image_paths(extract_partitions, dump_dir)
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

    # Match leftover files with extract functions
    for file in os.scandir(dump_dir):
        for extract_pattern, extract_fn in ctx.extract_fns.items():
            match = re.match(extract_pattern, file.name)
            if match is None:
                continue

            print_file_paths([file.path], f'pattern: "{extract_pattern}"')
            print(f'Processing {file.name}')
            processed_file = extract_fn(ctx, file.path, dump_dir)
            remove_file_paths([processed_file])

    move_alternate_partition_paths(dump_dir)


def move_alternate_partition_paths(dump_dir: str):
    # Make sure that even for devices that don't have separate partitions
    # for vendor, odm, etc., the partition directories are copied into the root
    # dump directory to simplify file copying
    for (
        partition,
        alternate_partition_paths,
    ) in ALTERNATE_PARTITION_PATH_MAP.items():
        partition_path = path.join(dump_dir, partition)
        if path.isdir(partition_path):
            continue

        for partition_sub_path in alternate_partition_paths:
            partition_path = path.join(dump_dir, partition_sub_path)

            if not path.isdir(partition_path):
                continue

            shutil.move(partition_path, dump_dir)

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
