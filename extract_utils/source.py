#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
from abc import ABC, abstractmethod
from contextlib import contextmanager, suppress
from os import path
from typing import List, Optional

from extract_utils.adb import init_adb_connection
from extract_utils.args import ArgsSource
from extract_utils.extract import (
    ExtractCtx,
    extract_image,
    filter_already_extracted_partitions,
    get_dump_dir,
)
from extract_utils.file import File, FileArgs
from extract_utils.utils import run_cmd


class Source(ABC):
    def __init__(self, source_path: str):
        self.source_path = source_path

    @abstractmethod
    def _list_sub_path_file_rel_paths(self, source_path: str) -> List[str]: ...

    @abstractmethod
    def _copy_file_path(
        self,
        file_path: str,
        target_file_path: str,
    ) -> bool: ...

    @abstractmethod
    def _copy_firmware(
        self,
        file: File,
        target_file_path: str,
    ) -> bool: ...

    def _copy_file_to_path(
        self,
        file: File,
        file_copy_path: str,
    ) -> bool:
        if FileArgs.TRYSRCFIRST in file.args:
            first = file.src
            second = file.dst
        else:
            first = file.dst
            second = file.src

        if self._copy_file_path(first, file_copy_path):
            return True

        if file.has_dst and self._copy_file_path(second, file_copy_path):
            return True

        return False

    def copy_file_to_path(
        self,
        file: File,
        file_path: str,
        is_firmware=False,
    ) -> bool:
        file_dir = path.dirname(file_path)
        os.makedirs(file_dir, exist_ok=True)

        if is_firmware:
            return self._copy_firmware(file, file_path)

        return self._copy_file_to_path(file, file_path)

    def get_file_copy_path(self, file: File, copy_dir: str) -> str:
        return path.join(copy_dir, file.dst)

    def copy_file_to_dir(
        self,
        file: File,
        copy_dir: str,
        is_firmware=False,
    ) -> bool:
        file_copy_path = self.get_file_copy_path(file, copy_dir)
        return self.copy_file_to_path(
            file,
            file_copy_path,
            is_firmware,
        )

    def find_sub_dir_files(
        self,
        sub_path: str,
        regex: Optional[str],
        skipped_file_rel_paths: List[str],
    ) -> List[str]:
        skipped_file_rel_paths_set = set(skipped_file_rel_paths)

        compiled_regex = None
        if regex is not None:
            compiled_regex = re.compile(regex)

        file_srcs = []

        source_sub_path = path.join(self.source_path, sub_path)
        file_rel_paths = self._list_sub_path_file_rel_paths(source_sub_path)
        file_rel_paths.sort()

        for file_rel_path in file_rel_paths:
            if (
                compiled_regex is not None
                and compiled_regex.search(file_rel_path) is None
            ):
                continue

            if file_rel_path in skipped_file_rel_paths_set:
                continue

            file_src = f'{sub_path}/{file_rel_path}'
            file_srcs.append(file_src)

        return file_srcs


class AdbSource(Source):
    def __init__(self):
        super().__init__('')

        self.__slot_suffix = run_cmd(
            [
                'adb',
                'shell',
                'getprop',
                'ro.boot.slot_suffix',
            ]
        ).strip()

    def _copy_file_path(self, file_path: str, target_file_path: str):
        try:
            run_cmd(['adb', 'pull', file_path, target_file_path])
            return True
        except ValueError:
            return False

    def _list_sub_path_file_rel_paths(self, source_path: str) -> List[str]:
        return (
            run_cmd(
                [
                    'adb',
                    'shell',
                    f'cd {source_path}; find * -type f',
                ]
            )
            .strip()
            .splitlines()
        )

    def _copy_firmware(self, file: File, target_file_path: str) -> bool:
        partition = file.root

        if FileArgs.AB in file.args:
            partition += self.__slot_suffix

        try:
            run_cmd(
                [
                    'adb',
                    'pull',
                    f'/dev/block/by-name/{partition}',
                    target_file_path,
                ]
            )
            return True
        except ValueError:
            return False


class DiskSource(Source):
    def _copy_firmware(self, file: File, target_file_path: str) -> bool:
        return self._copy_file_to_path(file, target_file_path)

    def _copy_file_path(
        self,
        file_path: str,
        target_file_path: str,
    ) -> bool:
        file_path = f'{self.source_path}/{file_path}'

        if not path.isfile(file_path):
            return False

        with suppress(Exception):
            shutil.copy(file_path, target_file_path)
            return True

        return False

    def _list_sub_path_file_rel_paths(self, source_path: str) -> List[str]:
        file_rel_paths = []

        for dir_path, _, file_names in os.walk(source_path):
            dir_rel_path = path.relpath(dir_path, source_path)
            if dir_rel_path == '.':
                dir_rel_path = ''

            for file_name in file_names:
                if dir_rel_path:
                    file_rel_path = f'{dir_rel_path}/{file_name}'
                else:
                    file_rel_path = file_name

                file_rel_paths.append(file_rel_path)

        return file_rel_paths


@contextmanager
def create_source(source: str | ArgsSource, ctx: ExtractCtx):
    if source == ArgsSource.ADB:
        init_adb_connection()
        yield AdbSource()
        return

    assert not isinstance(source, ArgsSource)

    with get_dump_dir(source, ctx) as dump_dir:
        filter_already_extracted_partitions(dump_dir, ctx)
        # TODO: filter already extracted firmware
        if ctx.extract_partitions:
            extract_image(source, ctx, dump_dir)

        yield DiskSource(dump_dir)
