#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import os
import shutil
import tempfile

from os import path
from typing import List, Tuple
from zipfile import ZipFile
from subprocess import PIPE, Popen, run


from .tools import \
    get_brotli_path, \
    get_lpunpack_path, \
    get_ota_extractor_path, \
    get_sdat2img_path, \
    get_simg2img_path

EXTRACTED_PARTITIONS = [
    'system',
    'odm',
    'product',
    'system_ext',
    'vendor'
]


class ExtractCtx:
    def __init__(self, keep_dump: bool):
        self.keep_dump = keep_dump


def find_super_img_paths(input_path: str):
    paths = []
    super_img_name = 'super.img'
    sparsechunk_prefix = 'super.img_sparsechunk.'

    for file in os.scandir(input_path):
        if file.name == super_img_name or \
                file.name.startswith(sparsechunk_prefix):
            paths.append(file.path)

    paths.sort(key=lambda f: f.removeprefix(sparsechunk_prefix))

    return paths


def find_ext_paths(input_path: str, ext: str):
    paths = []

    for file in os.scandir(input_path):
        if file.name.endswith(ext):
            paths.append(file.path)

    return paths


parallel_input_cmds = List[Tuple[str, List[str]]]


def run_in_parallel(input_cmds: parallel_input_cmds, fatal=False):
    inputs = set()
    input_procs: List[Tuple[str, Popen]] = []
    for input, cmd in input_cmds:
        print(f'Extracting {input}')
        proc = Popen(cmd, stdout=PIPE, stderr=PIPE, text=True)
        inputs.add(input)
        input_procs.append((input, proc))

    for input, proc in input_procs:
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            s = f'Failed to extract {input}: {stderr.strip()}'
            if fatal:
                # TODO: use custom error class
                raise ValueError(s)
            else:
                print(s)


def extract_zip_image(file_path: str, output_dir: str):
    with ZipFile(file_path, 'r') as zip_file:
        zip_file.extractall(output_dir)


def extract_payload_bin(file_path: str, output_dir: str):
    ota_extractor_path = get_ota_extractor_path()

    procs: parallel_input_cmds = []
    for partition in EXTRACTED_PARTITIONS:
        procs.append((partition, [
            ota_extractor_path,
            '--payload', file_path,
            '--output-dir', output_dir,
            '--partitions', partition
        ]))

    run_in_parallel(procs)


def extract_super_imgs(file_paths: List[str], output_dir: str):
    simg2img_path = get_simg2img_path()
    lpunpack_path = get_lpunpack_path()

    super_raw_path = path.join(output_dir, 'super.raw')
    run([simg2img_path] + file_paths + [super_raw_path], check=True)

    procs: parallel_input_cmds = []
    for partition in EXTRACTED_PARTITIONS:
        for slot in ['', '_a']:
            partition_slot = f'{partition}{slot}'
            procs.append((partition_slot, [
                lpunpack_path,
                '--partition', partition_slot,
                super_raw_path, output_dir
            ]))

    run_in_parallel(procs)

    for partition in EXTRACTED_PARTITIONS:
        partition_a_img = f'{partition}_a.img'
        partition_img = f'{partition}.img'

        partition_a_path = path.join(output_dir, partition_a_img)
        partition_path = path.join(output_dir, partition_img)

        if path.exists(partition_a_path):
            os.rename(partition_a_path, partition_path)


def extract_brotli_imgs(file_paths: List[str], output_path: str):
    brotli_path = get_brotli_path()

    procs: parallel_input_cmds = []
    for file_path in file_paths:
        file_name = path.basename(file_path).removesuffix('.br')
        output_file_path = path.join(output_path, file_name)

        procs.append((file_path, [
            brotli_path,
            '-d', file_path,
            '-o', output_file_path
        ]))

    run_in_parallel(procs, fatal=True)


def extract_sparse_imgs(file_paths: List[str], output_path: str):
    sdat2img_path = get_sdat2img_path()

    procs: parallel_input_cmds = []
    for file_path in file_paths:
        base_file_path = file_path.removesuffix('.new.dat')
        base_file_name = path.basename(base_file_path)

        transfer_list_file_path = f'{base_file_path}.transfer.List'
        img_file_name = f'{base_file_name}.img'
        output_file_path = path.join(output_path, img_file_name)

        procs.append((file_path, [
            sdat2img_path,
            transfer_list_file_path,
            file_path,
            output_file_path
        ]))

    run_in_parallel(procs, fatal=True)


def extract_imgs(file_paths: List[str], output_path: str):
    procs: parallel_input_cmds = []
    for file_path in file_paths:
        base_file_name = path.basename(file_path)

        # TODO: extract erofs and raw
        partition_name, _ = path.splitext(base_file_name)
        partition_output_path = path.join(output_path, partition_name)
        os.mkdir(partition_output_path)

        procs.append((file_path, [
            'debugfs', '-R',
            f'rdump / {partition_output_path}',
            file_path
        ]))

    # TODO: check for symlinks like the old code?

    run_in_parallel(procs, fatal=True)


def extract_image(ctx: ExtractCtx, source: str):
    source_is_file = path.isfile(source)
    source_basename, source_ext = path.splitext(source)
    create_dump_dir = True

    if ctx.keep_dump:
        if source_is_file:
            dump_dir = source_basename
        elif path.isdir(source):
            dump_dir = source
            # Same dir as source
            create_dump_dir = False
        else:
            # TODO: use custom error class
            raise ValueError(f'Unexpected file type at {source}')
    elif not ctx.keep_dump:
        dump_dir = tempfile.mkdtemp()
        # Already starts out as empty
        create_dump_dir = False

    dump_output_dir = path.join(dump_dir, 'output')

    if ctx.keep_dump:
        # Previous dump output exists, return it
        if source_is_file and path.isdir(dump_output_dir):
            return dump_output_dir

    if create_dump_dir:
        # Remove old dump dir
        if path.exists(dump_dir):
            if not path.isdir(dump_dir):
                # TODO: use custom error class
                raise ValueError(f'Unexpected file at {dump_dir}')

            print(f'Removing old dump dir {dump_dir}')
            shutil.rmtree(dump_dir)

        print(f'Creating dump dir {dump_dir}')
        os.mkdir(dump_dir)

    intermediary_output_dirs = []

    def create_output_dir(name):
        output_dir = path.join(dump_dir, name)
        print(f'Creating output dir {output_dir}')
        os.mkdir(output_dir)
        intermediary_output_dirs.append(output_dir)
        return output_dir

    input_path = source

    if source_is_file:
        if source_ext == '.zip':
            print(f'Found zip file {input_path}')
            output_path = create_output_dir('zip_dump')
            print(f'Extracting zip file {input_path}')
            extract_zip_image(input_path, output_path)
            input_path = output_path
        else:
            raise ValueError(
                f'Unknown source file with extension {source_ext}')

    payload_input_path = path.join(input_path, 'payload.bin')
    if path.isfile(payload_input_path):
        print(f'Found payload file {payload_input_path}')
        output_path = create_output_dir('payload_dump')
        print(f'Extracting payload file {payload_input_path}')
        extract_payload_bin(payload_input_path, output_path)
        input_path = output_path

    brotli_paths = find_ext_paths(input_path, '.new.dat.br')
    if brotli_paths:
        output_path = create_output_dir('brotli_dump')
        extract_brotli_imgs(brotli_paths, output_path)
        input_path = output_path

    sparse_paths = find_ext_paths(input_path, '.new.dat')
    if sparse_paths:
        output_path = create_output_dir('sparse_dump')
        extract_sparse_imgs(brotli_paths, output_path)
        input_path = output_path

    super_img_paths = find_super_img_paths(input_path)
    if super_img_paths:
        print(f'Found {super_img_paths[0]}')
        output_path = create_output_dir('super_dump')
        print(f'Extracting {super_img_paths[0]}')
        extract_super_imgs(super_img_paths, output_path)
        input_path = output_path

    img_paths = find_ext_paths(input_path, '.img')
    if img_paths:
        os.mkdir(dump_output_dir)
        extract_imgs(img_paths, dump_output_dir)
        input_path = dump_output_dir

    for output_dir in intermediary_output_dirs:
        print(f'Removing output dir {output_dir}')
        shutil.rmtree(output_dir)

    return input_path
