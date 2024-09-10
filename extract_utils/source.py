#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from os import path
import os
import re
import shutil
from typing import List

from extract_utils.adb import init_adb_connection
from extract_utils.args import ArgsSource
from extract_utils.file import File, FileArgs
from extract_utils.extract import ExtractCtx, extract_image, get_dump_dir
from extract_utils.utils import run_cmd


class Source(ABC):
    @abstractmethod
    def _list_sub_path_file_rel_paths(self, source_path: str) -> List[str]: ...

    @abstractmethod
    def _source_sub_path(self, sub_path: str) -> str: ...

    @abstractmethod
    def _copy_file_rel_path(
        self,
        file_rel_path: str,
        target_file_path: str,
    ) -> bool: ...

    @abstractmethod
    def _copy_firmware(
        self,
        file: File,
        target_file_path: str,
    ) -> bool: ...

    def _copy_file(
        self,
        file: File,
        target_file_path: str,
    ) -> bool:
        # TODO: try source before destination by default or allow changing

        if self._copy_file_rel_path(file.dst, target_file_path):
            return True

        # dst is different from src, try src too
        if file.has_dst:
            if self._copy_file_rel_path(file.src, target_file_path):
                return True

        return False

    def copy_file(
        self,
        file: File,
        target_dir: str,
        is_firmware: bool,
    ) -> str | None:
        target_file_path = f'{target_dir}/{file.dst}'
        target_dir_path = f'{target_dir}/{file.dirname}'
        if not path.exists(target_dir_path):
            os.makedirs(target_dir_path)

        if is_firmware:
            if self._copy_firmware(file, target_file_path):
                return target_file_path
        else:
            if self._copy_file(file, target_file_path):
                return target_file_path

        return None

    def find_sub_dir_files(
        self,
        sub_path: str,
        regex: str,
        skipped_file_rel_paths: List[str],
    ) -> List[str]:
        skipped_file_rel_paths_set = set(skipped_file_rel_paths)
        compiled_regex = re.compile(regex)

        file_srcs = []

        source_path = self._source_sub_path(sub_path)
        source_sub_path = path.join(source_path, sub_path)
        file_rel_paths = self._list_sub_path_file_rel_paths(source_sub_path)
        file_rel_paths.sort()

        for file_rel_path in file_rel_paths:
            if compiled_regex.search(file_rel_path) is None:
                continue

            if file_rel_path in skipped_file_rel_paths_set:
                continue

            file_src = f'{sub_path}/{file_rel_path}'
            file_srcs.append(file_src)

        return file_srcs


class AdbSource(Source):
    def __init__(self):
        self.__slot_suffix = run_cmd(
            [
                'adb',
                'shell',
                'getprop',
                'ro.boot.slot_suffix',
            ]
        ).strip()

    def _source_sub_path(self, sub_path: str):
        # Files are always expected to be in the proper place on device
        return ''

    def _copy_file_rel_path(self, file_path: str, target_file_path: str):
        try:
            run_cmd(['adb', 'pull', file_path, target_file_path])
            return True
        except Exception:
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
        except Exception:
            return False


class DiskSource(Source):
    def __init__(self, source_path: str):
        self.__source_path = source_path

        # If system directory has a system subdirectory, this is System-as-Root
        # Cache the path and use it as a source for files which are in the
        # system sub-path
        system_path = path.join(source_path, 'system')
        sar_system_path = path.join(system_path, 'system')
        if path.isdir(sar_system_path):
            self.__system_source_path = system_path
        else:
            self.__system_source_path = source_path

    def _copy_firmware(self, file: File, target_file_path: str) -> bool:
        return self._copy_file(file, target_file_path)

    def _source_sub_path(self, sub_path: str) -> str:
        if sub_path.startswith('system/'):
            return self.__system_source_path

        return self.__source_path

    def _copy_file_rel_path(
        self,
        file_path: str,
        target_file_path: str,
    ) -> bool:
        source_path = self._source_sub_path(file_path)
        file_path = f'{source_path}/{file_path}'

        if not path.isfile(file_path):
            return False

        try:
            shutil.copy(file_path, target_file_path)
            return True
        except Exception:
            pass

        return False

    def _list_sub_path_file_rel_paths(self, source_sub_path: str) -> List[str]:
        source_sub_path_len = len(source_sub_path)
        file_rel_paths = []

        for dir_path, _, file_names in os.walk(source_sub_path):
            dir_rel_path = dir_path[source_sub_path_len:]

            for file_name in file_names:
                if dir_rel_path:
                    file_rel_path = f'{dir_rel_path}/{file_name}'
                else:
                    file_rel_path = file_name

                file_rel_paths.append(file_rel_path)

        return file_rel_paths


@contextmanager
def create_source(ctx: ExtractCtx):
    if ctx.source == ArgsSource.ADB:
        init_adb_connection()
        yield AdbSource()
        return

    with get_dump_dir(ctx) as (dump_dir, extract):
        if extract:
            extract_image(ctx, dump_dir)

        yield DiskSource(dump_dir)


class CopyCtx:
    def __init__(self, source: Source, target_dir: str):
        self.source = source
        self.target_dir = target_dir

    def copy_file(self, file: File, is_firmware=False) -> str | None:
        return self.source.copy_file(file, self.target_dir, is_firmware)
