#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager, nullcontext, suppress
from os import path
from subprocess import SubprocessError
from time import sleep
from typing import List, Optional

from extract_utils.args import ArgsSource
from extract_utils.extract import ExtractCtx, extract_dump, extract_image_file
from extract_utils.file import File, FileArgs
from extract_utils.utils import run_cmd


class SourceCtx:
    def __init__(
        self,
        source: str | ArgsSource,
        keep_dump: bool,
    ):
        self.source = source
        self.keep_dump = keep_dump


class Source(ABC):
    @abstractmethod
    def _list_sub_path_file_rel_paths(self, sub_path: str) -> List[str]: ...

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

        file_rel_paths = self._list_sub_path_file_rel_paths(sub_path)
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
        self.__init_adb_connection()
        self.__slot_suffix = self.__get_slot_suffix()

    def __get_slot_suffix(self):
        return run_cmd(
            [
                'adb',
                'shell',
                'getprop',
                'ro.boot.slot_suffix',
            ]
        ).strip()

    def __adb_connected(self):
        output = None
        with suppress(SubprocessError):
            output = run_cmd(['adb', 'get-state'])
        return output == 'device\n'

    def __init_adb_connection(self):
        run_cmd(['adb', 'start-server'])
        if not self.__adb_connected():
            print('No device is online. Waiting for one...')
            print('Please connect USB and/or enable USB debugging')
            while not self.__adb_connected():
                sleep(1)

        # TODO: TCP connection

        run_cmd(['adb', 'root'])
        run_cmd(['adb', 'wait-for-device'])

    def _copy_file_path(self, file_path: str, target_file_path: str):
        try:
            run_cmd(['adb', 'pull', file_path, target_file_path])
            return True
        except ValueError:
            return False

    def _list_sub_path_file_rel_paths(self, sub_path: str) -> List[str]:
        return (
            run_cmd(
                [
                    'adb',
                    'shell',
                    f'cd {sub_path}; find * -type f',
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
    def __init__(self, dump_dir: str):
        self.dump_dir = dump_dir

    def _copy_firmware(self, file: File, target_file_path: str) -> bool:
        return self._copy_file_to_path(file, target_file_path)

    def _copy_file_path(
        self,
        file_path: str,
        target_file_path: str,
    ) -> bool:
        file_path = f'{self.dump_dir}/{file_path}'

        if not path.isfile(file_path):
            return False

        with suppress(Exception):
            shutil.copy(file_path, target_file_path)
            return True

        return False

    def _list_sub_path_file_rel_paths(self, sub_path: str) -> List[str]:
        dump_dir_sub_path = path.join(self.dump_dir, sub_path)

        file_rel_paths = []

        for dir_path, _, file_names in os.walk(dump_dir_sub_path):
            dir_rel_path = path.relpath(dir_path, dump_dir_sub_path)
            if dir_rel_path == '.':
                dir_rel_path = ''

            for file_name in file_names:
                if dir_rel_path:
                    file_rel_path = f'{dir_rel_path}/{file_name}'
                else:
                    file_rel_path = file_name

                file_rel_paths.append(file_rel_path)

        return file_rel_paths


def create_disk_source(dump_dir: str, extract_ctx: ExtractCtx):
    extract_dump(dump_dir, extract_ctx)
    return DiskSource(dump_dir)


@contextmanager
def create_extractable_source(
    source: str,
    ctx: SourceCtx,
    extract_ctx: ExtractCtx,
):
    if ctx.keep_dump:
        dump_dir, _ = path.splitext(source)

        if path.exists(dump_dir):
            if not path.isdir(dump_dir):
                raise ValueError(f'Unexpected file type at {dump_dir}')

            extract_image = False
        else:
            extract_image = True

        dump_dir_context = nullcontext(dump_dir)
    else:
        extract_image = True
        dump_dir_context = tempfile.TemporaryDirectory()

    with dump_dir_context as dump_dir:
        if extract_image:
            print(f'Extracting to new dump dir {dump_dir}')
            extract_image_file(source, dump_dir)
        else:
            print(f'Using existing dump dir {dump_dir}')

        yield create_disk_source(dump_dir, extract_ctx)


@contextmanager
def create_source(ctx: SourceCtx, extract_ctx: ExtractCtx):
    source = ctx.source

    if source == ArgsSource.ADB:
        yield AdbSource()
        return

    if not path.isfile(source) and not path.isdir(source):
        raise ValueError(f'Unexpected file type at {source}')

    if path.isdir(source):
        # Source is a directory, try to extract its contents into itself
        print(f'Using source dump dir {source}')
        yield create_disk_source(source, extract_ctx)
        return

    with create_extractable_source(ctx.source, ctx, extract_ctx) as source:
        try:
            yield source
        except GeneratorExit:
            pass
