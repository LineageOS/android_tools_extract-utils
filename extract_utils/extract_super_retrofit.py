#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from functools import partial
from os import path
from typing import List

from extract_utils.extract import ExtractCtx, ExtractFn
from extract_utils.lp import LpImage
from extract_utils.sparse_img import unsparse_images

SUPER_NAME = 'super_'
IMG_EXT = '.img'


def file_path_to_partition(file_path: str):
    file_name = path.basename(file_path)
    assert file_name.startswith(SUPER_NAME)
    assert file_name.endswith(IMG_EXT)
    return file_name[len(SUPER_NAME) : -len(IMG_EXT)]


def key_super_partitions(partitions: List[str], file_path: str):
    partition_name = file_path_to_partition(file_path)
    return partitions.index(partition_name)


def open_image(stack: ExitStack, file_paths: List[str]):
    inputs = [
        stack.enter_context(open(file_path, 'rb')) for file_path in file_paths
    ]

    return LpImage(inputs)


def extract_partition(
    partition: str,
    file_paths: List[str],
    output_file_path: str,
):
    with ExitStack() as stack:
        image = open_image(stack, file_paths)
        image.extract_partition(partition, output_file_path)


def extract_super_retrofit(
    partitions: List[str],
    ctx: ExtractCtx,
    file_paths: List[str],
    work_dir: str,
    *args,
    **kwargs,
):
    key_fn = partial(key_super_partitions, partitions)
    file_paths.sort(key=key_fn)

    unsparsed_file_paths = []
    with ProcessPoolExecutor() as exe:
        for file_path in file_paths:
            output_file_path = f'{file_path}.unsparsed'
            exe.submit(unsparse_images, [file_path], output_file_path)
            unsparsed_file_paths.append(output_file_path)

    with ExitStack() as stack:
        image = open_image(stack, unsparsed_file_paths)
        partition_names = image.get_partition_names()

    with ProcessPoolExecutor() as exe:
        for partition in ctx.extract_partitions:
            if partition not in partition_names:
                continue

            output_file_path = path.join(work_dir, f'{partition}.img')
            image.extract_partition(partition, output_file_path)

    return file_paths + unsparsed_file_paths


class ExtractSuperRetrofit(ExtractFn):
    def __init__(
        self,
        partitions: List[str],
    ):
        fn = partial(extract_super_retrofit, partitions)

        partition_images = [f'^super_{p}\\.img$' for p in partitions]
        key = f'({"|".join(partition_images)})'

        super().__init__(key, paths_fn=fn)
