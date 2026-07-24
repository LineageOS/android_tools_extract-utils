#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from os import path
from tarfile import is_tarfile
from typing import Callable, Dict, Iterable, List, Optional, Set, Union
from zipfile import ZipFile, is_zipfile

from extract_utils.ext4 import EXT4_MAGIC, EXT4_MAGIC_OFFSET
from extract_utils.extract_moto_piv import MOTO_PIV_MAGIC, extract_moto_piv
from extract_utils.extract_recovery import extract_recovery_partition
from extract_utils.file import File
from extract_utils.lp import LpImage
from extract_utils.sparse_img import SPARSE_HEADER_MAGIC, unsparse_images
from extract_utils.tools import (
    brotli_path,
    ota_extractor_path,
    sdat2img_path,
)
from extract_utils.utils import (
    Color,
    color_print,
    find_file,
    find_files,
    run_cmd,
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
APPLIED_INCREMENTALS_FILE_NAME = '.applied_incrementals'


extract_fn_type = Callable[['ExtractCtx', str, str], Optional[str]]
extract_fn_paths_type = Callable[['ExtractCtx', List[str], str], List[str]]

extract_fns_value_type = Union[extract_fn_type, List[extract_fn_type]]
extract_fns_dict_type = Dict[str, extract_fns_value_type]


class ExtractFn:
    def __init__(
        self,
        key: str,
        path_fn: Optional[extract_fn_type] = None,
        path_fns: Optional[List[extract_fn_type]] = None,
        paths_fn: Optional[extract_fn_paths_type] = None,
    ):
        if path_fn is not None:
            assert path_fns is None
            path_fns = [path_fn]

        self.key = key
        self.path_fns = path_fns
        self.paths_fn = paths_fn


extract_fns_type = List[ExtractFn]
extract_fns_user_type = Union[extract_fns_dict_type, extract_fns_type]


class ExtractCtx:
    def __init__(
        self,
        extract_fns: Optional[extract_fns_type] = None,
        extract_partitions: Optional[List[str]] = None,
        firmware_files: Optional[List[File]] = None,
        factory_files: Optional[List[File]] = None,
        keep_images: bool = False,
        images_only: bool = False,
    ):
        if extract_fns is None:
            extract_fns = []
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
        # Keep the extracted partition images so the dump can be used as the
        # base of a later incremental extraction
        self.keep_images = keep_images
        # Stop at the partition images instead of unpacking them, for a dump
        # that is only used as the base of an incremental extraction
        self.images_only = images_only


def find_alternate_partitions(
    extract_partitions: List[str],
    found_partitions: Iterable[str],
):
    new_extract_partitions: List[str] = []
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
    magic = SPARSE_HEADER_MAGIC.to_bytes(4, 'little')
    return find_files(input_path, partition, magic=magic)


def find_erofs_path(partition: str, input_path: str):
    magic = 0xE0F5E1E2.to_bytes(4, 'little')
    return find_file(input_path, partition, magic=magic, position=1024)


def find_ext4_path(partition: str, input_path: str):
    return find_file(
        input_path,
        partition,
        magic=EXT4_MAGIC,
        position=EXT4_MAGIC_OFFSET,
    )


def find_payload_path(file_name: str, input_path: str):
    return find_file(input_path, name=file_name, magic=b'CrAU')


def find_super_img_path(partition: str, input_path: str):
    magic = 0x616C4467.to_bytes(4, 'little')
    return find_file(input_path, partition, magic=magic, position=4096)


def find_brotli_path(partition: str, input_path: str):
    return find_file(input_path, partition, ext=BROTLI_EXT)


def find_sparse_data_path(partition: str, input_path: str):
    return find_file(input_path, partition, ext=SPARSE_DATA_EXT)


def find_moto_piv_path(partition: str, input_path: str):
    return find_file(input_path, partition, magic=MOTO_PIV_MAGIC)


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


def extract_payload_bin(
    partition: str,
    file_path: str,
    output_dir: str,
    input_dir: Optional[str] = None,
) -> bool:
    print(f'Extracting {partition}')

    cmd = [
        ota_extractor_path,
        '--payload',
        file_path,
        '--output-dir',
        output_dir,
        '--partitions',
        partition,
    ]

    # Base images to apply the delta on top of for incremental OTAs
    if input_dir is not None:
        cmd += ['--input-dir', input_dir]

    try:
        run_cmd(cmd)
    except ValueError:
        # ota_extractor exits successfully and produces nothing for partitions
        # it does not know about, a failure is a real one
        return False

    return True


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

    unsparse_images(file_paths, output_file_path)


def extract_super_img(partition: str, file_path: str, output_dir: str):
    with open(file_path, 'rb') as i:
        output_file_path = path.join(output_dir, f'{partition}.img')
        image = LpImage(i)
        image.extract_partition(partition, output_file_path)


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


def extract_zip(source: str, dump_dir: str):
    with ZipFile(source) as zip_file:
        for info in zip_file.infolist():
            if info.is_dir():
                continue

            output_file_path = path.join(dump_dir, info.filename)
            output_dir = path.dirname(output_file_path)
            os.makedirs(output_dir, exist_ok=True)

            with zip_file.open(info) as z:
                with open(output_file_path, 'wb') as f:
                    shutil.copyfileobj(z, f)


def extract_tar(source: str, dump_dir: str):
    with tarfile.open(source, 'r:*') as tar:
        tar.extractall(dump_dir)


def extract_7z(source: str, dump_dir: str):
    import py7zr

    with py7zr.SevenZipFile(source, 'r') as archive:
        archive.extractall(dump_dir)


def extract_image_file(source: str, dump_dir: str):
    if is_zipfile(source):
        extract_fn = extract_zip
    elif is_tarfile(source):
        extract_fn = extract_tar
    elif source.endswith('.7z'):
        extract_fn = extract_7z
    else:
        raise ValueError(f'Unexpected file type at {source}')

    print(f'Extracting file {source}')
    extract_fn(source, dump_dir)


def extract_firmware_partition(partition: str, dump_dir: str):
    payload_bin_path = find_payload_path(PAYLOAD_BIN_FILE_NAME, dump_dir)
    if payload_bin_path:
        extract_payload_bin(partition, payload_bin_path, dump_dir)


def extract_partition(partition: str, dump_dir: str, ctx: ExtractCtx):
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

    moto_piv_path = find_moto_piv_path(partition, dump_dir)
    if moto_piv_path:
        print_file_path(moto_piv_path, 'Moto PIV')
        extract_moto_piv(moto_piv_path, dump_dir)
        remove_file_path(moto_piv_path)

    brotli_img_path = find_brotli_path(partition, dump_dir)
    if brotli_img_path:
        print_file_path(brotli_img_path, 'brotli')
        extract_brotli_img(brotli_img_path, dump_dir)
        remove_file_path(brotli_img_path)

    sparse_data_path = find_sparse_data_path(partition, dump_dir)
    if sparse_data_path:
        print_file_path(sparse_data_path, 'sparse data')
        extract_sparse_data_img(sparse_data_path, dump_dir)
        remove_file_path(sparse_data_path)

    if ctx.images_only:
        return

    erofs_path = find_erofs_path(partition, dump_dir)
    if erofs_path:
        print_file_path(erofs_path, 'EROFS')
        extract_erofs(erofs_path, dump_dir)
        if not ctx.keep_images:
            remove_file_path(erofs_path)

    ext4_path = find_ext4_path(partition, dump_dir)
    if ext4_path:
        print_file_path(ext4_path, 'EXT4')
        extract_ext4(ext4_path, dump_dir)
        if not ctx.keep_images:
            remove_file_path(ext4_path)


def find_partition_image_path(partition: str, dump_dir: str):
    return find_erofs_path(partition, dump_dir) or find_ext4_path(
        partition, dump_dir
    )


def find_partitions(dump_dir: str, ctx: ExtractCtx, missing: bool = False):
    partitions: List[str] = []
    for partition in ctx.extract_partitions:
        if ctx.images_only:
            # Nothing is unpacked, a partition is done once its image is there
            exists = find_partition_image_path(partition, dump_dir) is not None
        else:
            dump_partition_dir = path.join(dump_dir, partition)
            exists = (
                path.isdir(dump_partition_dir)
                and len(os.listdir(dump_partition_dir)) != 0
            )

        if exists != missing:
            partitions.append(partition)

    return partitions


def _find_files(dump_dir: str, files: List[File], missing: bool = False):
    found_files: List[File] = []
    for file in files:
        src_file_path = path.join(dump_dir, file.src)
        dst_file_path = path.join(dump_dir, file.dst)
        exists = path.isfile(src_file_path) or path.isfile(dst_file_path)

        if exists != missing:
            found_files.append(file)

    return found_files


def find_firmware_files(dump_dir: str, ctx: ExtractCtx, missing: bool = False):
    return _find_files(dump_dir, ctx.firmware_files, missing)


def find_factory_files(dump_dir: str, ctx: ExtractCtx, missing: bool = False):
    return _find_files(dump_dir, ctx.factory_files, missing)


def files_partitions(files: List[File]):
    partitions: List[str] = []
    for file in files:
        partition, _ = path.splitext(file.dst)
        partitions.append(partition)

    return partitions


def find_firmware_partitions(
    dump_dir: str,
    ctx: ExtractCtx,
    missing: bool = False,
):
    files = find_firmware_files(dump_dir, ctx, missing)

    return files_partitions(files)


def extract_all_partitions(dump_dir: str, ctx: ExtractCtx):
    normal_partitions = find_partitions(dump_dir, ctx, missing=True)
    firmware_partitions = find_firmware_partitions(dump_dir, ctx, missing=True)
    partitions = normal_partitions + firmware_partitions

    while partitions:
        for partition in partitions:
            try:
                if partition == 'recovery':
                    extract_recovery_partition(partition, dump_dir)
                elif partition in firmware_partitions:
                    extract_firmware_partition(partition, dump_dir)
                else:
                    extract_partition(partition, dump_dir, ctx)
            except Exception as e:
                print(f'Warning: Failed to extract partition {partition}: {e}')

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
        move_sar_system_paths(dump_dir)
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

    create_empty_partition_dirs(dump_dir, ctx)


def seed_input_dir_images(dump_dir: str, input_dir: str):
    # Seed the dump with the previous images so that everything left unchanged
    # by the incrementals carries over, an incremental only carries the
    # partitions it changes. Copy every file, firmware images are not
    # necessarily named after a partition.
    for file_name in sorted(os.listdir(input_dir)):
        input_file_path = path.join(input_dir, file_name)
        # Extracted partitions are directories, their images are copied instead
        if not path.isfile(input_file_path):
            continue

        # Skip the containers the images were extracted from, they are the
        # only ones the extraction leaves behind and they would be extracted
        # again over the images produced by the incrementals, and skip the
        # marker so that the incrementals of the input dir are not taken as
        # applied
        if file_name in (
            SUPER_IMG_NAME,
            PAYLOAD_BIN_FILE_NAME,
            APPLIED_INCREMENTALS_FILE_NAME,
        ):
            continue

        dump_file_path = path.join(dump_dir, file_name)
        if path.exists(dump_file_path):
            continue

        # Skip partitions which have already been unpacked, their image
        # would never be used and never be removed
        partition, _ = path.splitext(file_name)
        partition_dir = path.join(dump_dir, partition)
        if path.isdir(partition_dir) and os.listdir(partition_dir):
            continue

        print(f'Seeding {file_name} from input dir')
        shutil.copy(input_file_path, dump_file_path)


def is_sparse_image(file_path: str) -> bool:
    with open(file_path, 'rb') as f:
        return f.read(4) == SPARSE_HEADER_MAGIC.to_bytes(4, 'little')


def reconstruct_firmware_images(dump_dir: str, firmware_files: List[File]):
    for file in firmware_files:
        src_path = path.join(dump_dir, file.src)
        if not path.isfile(src_path):
            continue

        dst_name = path.basename(file.dst)
        dst_path = path.join(dump_dir, dst_name)
        if path.abspath(src_path) == path.abspath(dst_path):
            continue

        if is_sparse_image(src_path):
            print(f'Expanding firmware {file.src} to {dst_name}')
            unsparse_images([src_path], dst_path)
            remove_file_path(src_path)
        else:
            print(f'Renaming firmware {file.src} to {dst_name}')
            shutil.move(src_path, dst_path)


def read_applied_incrementals(output_dir: str) -> List[str]:
    file_path = path.join(output_dir, APPLIED_INCREMENTALS_FILE_NAME)
    if not path.isfile(file_path):
        return []

    with open(file_path, 'r') as f:
        return f.read().splitlines()


def add_applied_incremental(output_dir: str, source_name: str):
    file_path = path.join(output_dir, APPLIED_INCREMENTALS_FILE_NAME)
    with open(file_path, 'a') as f:
        f.write(f'{source_name}\n')


def find_dir_payload_path(payload_dir: str, source: str):
    payload_bin_path = find_payload_path(PAYLOAD_BIN_FILE_NAME, payload_dir)
    if payload_bin_path is None:
        raise ValueError(f'No payload.bin found in {source}')

    return payload_bin_path


@contextmanager
def create_payload(source: str, temp_dir: str):
    # An already extracted incremental can be used as-is
    if path.isdir(source):
        yield find_dir_payload_path(source, source)
        return

    with tempfile.TemporaryDirectory(dir=temp_dir) as payload_dir:
        extract_image_file(source, payload_dir)
        yield find_dir_payload_path(payload_dir, source)


def apply_incremental(
    source: str,
    input_dir: str,
    output_dir: str,
):
    # Keep the temporary directories on the same filesystem as the output dir,
    # the produced images are multiple gigabytes and moving them is then a
    # rename instead of a copy
    temp_dir = path.dirname(path.abspath(output_dir))

    with (
        create_payload(source, temp_dir) as payload_path,
        tempfile.TemporaryDirectory(dir=temp_dir) as image_dir,
    ):
        partitions = [
            path.splitext(image)[0]
            for image in sorted(os.listdir(input_dir))
            if image.endswith('.img')
        ]

        # ota_extractor cannot safely read and write the same directory.
        # Apply the delta with the current images as read-only input and a
        # separate output, then overlay the produced images. The partitions
        # are independent, apply them in parallel, one ota_extractor each.
        with ThreadPoolExecutor() as executor:
            futures = {
                partition: executor.submit(
                    extract_payload_bin,
                    partition,
                    payload_path,
                    image_dir,
                    input_dir,
                )
                for partition in partitions
            }

        source_name = path.basename(source)
        image_names: List[str] = []

        for partition, future in futures.items():
            # ota_extractor fails when the previous images are not the ones
            # the delta was built against, leaving a truncated image behind
            if not future.result():
                raise ValueError(
                    f'Failed to apply {source_name} to {partition}, the '
                    'previous images might not be the ones it was built for'
                )

            image_name = f'{partition}.img'
            image_path = path.join(image_dir, image_name)

            if not path.isfile(image_path):
                print(f'{partition} not part of {source_name}')
                continue

            # Never replace a good image with a truncated one
            if not path.getsize(image_path):
                raise ValueError(f'Empty {partition} image in {source_name}')

            image_names.append(image_name)

        for image_name in image_names:
            shutil.move(
                path.join(image_dir, image_name),
                path.join(output_dir, image_name),
            )


def apply_incremental_chain(
    sources: List[str],
    input_dir: str,
    output_dir: str,
):
    # Produce the images of the last incremental, unpacking them is left to
    # the regular dump extraction, which is then oblivious to the deltas
    os.makedirs(output_dir, exist_ok=True)

    applied = read_applied_incrementals(output_dir)

    # Apply each incremental in order, without unpacking
    for source in sources:
        source_name = path.basename(source)

        if source_name in applied:
            print(f'Skipping already applied incremental {source_name}')
            continue

        if applied:
            # The images of the previously applied incrementals are the input
            # of this one, together with the ones they did not produce
            seed_input_dir_images(output_dir, input_dir)
            source_input_dir = output_dir
        else:
            # Nothing was applied yet, read the previous images where they
            # already are instead of copying gigabytes of them first
            source_input_dir = input_dir

        print(f'Applying incremental {source_name}')
        apply_incremental(source, source_input_dir, output_dir)
        add_applied_incremental(output_dir, source_name)
        applied.append(source_name)

    # Carry over everything the incrementals did not produce
    seed_input_dir_images(output_dir, input_dir)


def create_empty_partition_dirs(dump_dir: str, ctx: ExtractCtx):
    missing_partitions = find_partitions(dump_dir, ctx, missing=True)
    for partition in missing_partitions:
        dump_partition_dir = path.join(dump_dir, partition)
        color_print(f'Partition {partition} not extracted', color=Color.YELLOW)
        # Create empty partition dir to prevent re-extraction
        os.makedirs(dump_partition_dir, exist_ok=True)


def convert_dict_extract_fns(dict_extract_fns: extract_fns_dict_type):
    extract_fns: extract_fns_type = []
    for extract_pattern, extract_fn in dict_extract_fns.items():
        if isinstance(extract_fn, list):
            # TODO: fix typing
            extract_fns.append(
                ExtractFn(
                    key=extract_pattern,
                    path_fns=extract_fn,  # type: ignore
                )
            )
        else:
            extract_fns.append(
                ExtractFn(
                    key=extract_pattern,
                    path_fn=extract_fn,
                )
            )

    return extract_fns


def run_extract_fns(dump_dir: str, ctx: ExtractCtx):
    for value in ctx.extract_fns:
        extract_pattern = value.key

        found_files = find_files(dump_dir, regex=extract_pattern)

        print_file_paths(found_files, f'pattern: "{extract_pattern}"')

        if not found_files:
            continue

        if value.paths_fn is not None:
            processed_files_list = value.paths_fn(ctx, found_files, dump_dir)
            remove_file_paths(processed_files_list)
            continue

        assert value.path_fns is not None

        processed_files: Set[str] = set()
        for file_path in found_files:
            file_name = path.basename(file_path)
            print(f'Processing {file_name}')
            for extract_fn in value.path_fns:
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
