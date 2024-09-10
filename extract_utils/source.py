#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager, nullcontext
from os import path
import os
import re
import shutil
import tempfile
from typing import List

from extract_utils.adb import init_adb_connection
from extract_utils.args import ArgsSource
from extract_utils.file import File
from extract_utils.extract import ExtractCtx, extract_image, get_dump_dir
from extract_utils.utils import run_cmd


class Source(ABC):
    @abstractmethod
    def _list_sub_path_file_rel_paths(self, source_path: str) -> List[str]: ...

    @abstractmethod
    def _source_sub_path(self, sub_path: str) -> str: ...

    @abstractmethod
    def _copy_file_from_path(
        self,
        file_path: str,
        target_file_path: str,
    ) -> bool: ...

    def __copy_file_from_rel_path(
        self,
        file_rel_path: str,
        target_file_path: str,
    ) -> bool:
        source_path = self._source_sub_path(file_rel_path)
        file_path = path.join(source_path, file_rel_path)
        return self._copy_file_from_path(file_path, target_file_path)

    def copy_file(self, file: File, target_dir: str) -> bool:
        target_dir_path = path.join(target_dir, file.dirname)
        if not path.exists(target_dir_path):
            os.makedirs(target_dir_path)

        target_file_path = path.join(target_dir, file.dst)

        # TODO: try source before destination by default or allow changing

        if self.__copy_file_from_rel_path(file.dst, target_file_path):
            return True

        # dst is different from src, try src too
        if file.has_dst:
            if self.__copy_file_from_rel_path(file.src, target_file_path):
                return True

        return False

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

            file_src = path.join(sub_path, file_rel_path)
            file_srcs.append(file_src)

        return file_srcs


class AdbSource(Source):
    def __init__(self) -> None:
        pass

    def _source_sub_path(self, sub_path: str):
        # Files are always expected to be in the proper place on device
        return '/'

    def _copy_file_from_path(self, file_path: str, target_file_path: str):
        try:
            run_cmd(['adb', 'pull', file_path, target_file_path])
            return True
        except:
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

    def _source_sub_path(self, sub_path: str):
        if sub_path.startswith('system/'):
            return self.__system_source_path

        return self.__source_path

    def _copy_file_from_path(self, file_path: str, target_file_path: str):
        if not path.isfile(file_path):
            return False

        try:
            shutil.copy(file_path, target_file_path)
            return True
        except Exception:
            pass

        return False

    def _list_sub_path_file_rel_paths(self, source_sub_path: str) -> List[str]:
        file_rel_paths = []

        for dir_path, _, file_names in os.walk(source_sub_path):
            dir_rel_path = path.relpath(dir_path, source_sub_path)

            for file_name in file_names:
                file_rel_path = path.join(dir_rel_path, file_name)
                file_rel_path = path.normpath(file_rel_path)
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
            with tempfile.TemporaryDirectory() as work_dir:
                extract_image(ctx, dump_dir, work_dir)

        yield DiskSource(dump_dir)


class CopyCtx:
    def __init__(self, source: Source, target_dir: str):
        self.source = source
        self.target_dir = target_dir

    def copy_file(self, file: File):
        return self.source.copy_file(file, self.target_dir)
