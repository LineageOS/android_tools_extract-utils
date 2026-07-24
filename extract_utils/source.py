#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager, nullcontext, suppress
from copy import copy
from os import path
from subprocess import SubprocessError
from time import sleep
from typing import List, Optional
from urllib.parse import urlparse

from extract_utils.args import ArgsSource
from extract_utils.extract import (
    ExtractCtx,
    apply_incremental_chain,
    extract_dump,
    extract_image_file,
    reconstruct_firmware_images,
)
from extract_utils.file import File, FileArgs
from extract_utils.utils import run_cmd, urlretrieve_resume


class SourceCtx:
    def __init__(
        self,
        source: str | ArgsSource | List[str],
        keep_dump: bool,
        download_dir: Optional[str],
        download_sha256: Optional[List[str]],
    ):
        # Multiple sources are a dump to apply the following incremental OTAs
        # over, in order
        self.source = source
        self.keep_dump = keep_dump
        self.download_dir = download_dir
        self.download_sha256 = download_sha256


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
        is_firmware: bool = False,
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
        is_firmware: bool = False,
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

        file_srcs: List[str] = []

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

        file_rel_paths: List[str] = []

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


def is_downloadable_source(source: str) -> bool:
    return urlparse(source).scheme in ['http', 'https']


def download_source(
    source: str,
    download_dir: str,
    expected_sha256: Optional[str] = None,
) -> str:
    source_name = path.basename(urlparse(source).path)
    file_path = path.join(download_dir, source_name)

    def print_percent(percent: int, first: bool, last: bool):
        ret = '' if first else '\r'
        end = '\n' if last else ''
        print(
            f'{ret}Downloading {source_name}: {percent}%',
            end=end,
            flush=True,
        )

    urlretrieve_resume(
        source,
        file_path,
        expected_sha256=expected_sha256,
        print_fn=print_percent,
    )

    return file_path


@contextmanager
def create_download_dir(download_dir: Optional[str]):
    if download_dir is not None:
        os.makedirs(download_dir, exist_ok=True)
        download_dir_context = nullcontext(download_dir)
    else:
        download_dir_context = tempfile.TemporaryDirectory()

    with download_dir_context as download_dir:
        yield download_dir


def download_sha256s(ctx: SourceCtx, count: int) -> List[Optional[str]]:
    # One SHA256 per downloaded source, in order, or none at all
    sha256s = ctx.download_sha256
    if sha256s is None:
        return [None] * count

    if len(sha256s) != count:
        raise ValueError(
            f'Expected {count} download SHA256s, got {len(sha256s)}'
        )

    return list(sha256s)


@contextmanager
def create_downloadable_source(
    source: str,
    ctx: SourceCtx,
    extract_ctx: ExtractCtx,
):
    sha256 = download_sha256s(ctx, 1)[0]

    with create_download_dir(ctx.download_dir) as download_dir:
        file_path = download_source(
            source,
            download_dir,
            sha256,
        )

        with create_extractable_source(
            file_path,
            ctx,
            extract_ctx,
        ) as disk_source:
            try:
                yield disk_source
            except GeneratorExit:
                pass


@contextmanager
def create_input_dir(source: str, ctx: SourceCtx, extract_ctx: ExtractCtx):
    if path.isdir(source):
        yield source
        return

    input_extract_ctx = copy(extract_ctx)
    input_extract_ctx.images_only = True

    with create_extractable_source(
        source,
        ctx,
        input_extract_ctx,
    ) as disk_source:
        assert isinstance(disk_source, DiskSource)

        reconstruct_firmware_images(
            disk_source.dump_dir,
            extract_ctx.firmware_files,
        )

        yield disk_source.dump_dir


@contextmanager
def create_incremental_source(ctx: SourceCtx, extract_ctx: ExtractCtx):
    assert isinstance(ctx.source, list)

    downloadable = [s for s in ctx.source if is_downloadable_source(s)]
    sha256s = iter(download_sha256s(ctx, len(downloadable)))

    with create_download_dir(ctx.download_dir) as download_dir:
        source_paths: List[str] = []

        for source in ctx.source:
            if is_downloadable_source(source):
                source = download_source(
                    source,
                    download_dir,
                    next(sha256s),
                )
            elif not path.isfile(source) and not path.isdir(source):
                raise ValueError(f'Unexpected file type at {source}')

            source_paths.append(source)

        # The incrementals are applied over the images of the first source
        input_source, *sources = source_paths

        # Incrementals are applied one over the other, so only the images of
        # the last one are left to dump, name the dump after it
        last_source = sources[-1]

        if ctx.keep_dump:
            dump_dir, _ = path.splitext(last_source)

            if path.exists(dump_dir) and not path.isdir(dump_dir):
                raise ValueError(f'Unexpected file type at {dump_dir}')

            if path.abspath(dump_dir) == path.abspath(last_source):
                # The payload would be read from the dump dir it is applied
                # into and could overwrite the images it was applied to
                raise ValueError(f'Unexpected dump dir at {dump_dir}')

            dump_dir_context = nullcontext(dump_dir)
        else:
            dump_dir_context = tempfile.TemporaryDirectory()

        with (
            create_input_dir(input_source, ctx, extract_ctx) as input_dir,
            dump_dir_context as dump_dir,
        ):
            if path.abspath(dump_dir) == path.abspath(input_dir):
                # The incrementals would be applied over the base dump itself,
                # destroying it
                raise ValueError(f'Unexpected dump dir at {dump_dir}')

            print(f'Applying incrementals to dump dir {dump_dir}')

            apply_incremental_chain(
                sources,
                input_dir,
                dump_dir,
            )

            yield create_disk_source(dump_dir, extract_ctx)


@contextmanager
def create_source(ctx: SourceCtx, extract_ctx: ExtractCtx):
    source = ctx.source

    if isinstance(source, list):
        if not source:
            raise ValueError('No source to extract from')

        if len(source) > 1:
            with create_incremental_source(ctx, extract_ctx) as source:
                try:
                    yield source
                except GeneratorExit:
                    pass

                return

        source = source[0]

    if source == ArgsSource.ADB:
        yield AdbSource()
        return

    assert isinstance(source, str)

    if is_downloadable_source(source):
        with create_downloadable_source(source, ctx, extract_ctx) as source:
            try:
                yield source
            except GeneratorExit:
                pass

            return

    if not path.isfile(source) and not path.isdir(source):
        raise ValueError(f'Unexpected file type at {source}')

    if path.isdir(source):
        # Source is a directory, try to extract its contents into itself
        print(f'Using source dump dir {source}')
        yield create_disk_source(source, extract_ctx)
        return

    with create_extractable_source(source, ctx, extract_ctx) as source:
        try:
            yield source
        except GeneratorExit:
            pass
