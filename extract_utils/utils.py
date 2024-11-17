#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
from contextlib import contextmanager
from enum import Enum
from functools import lru_cache
from io import SEEK_CUR
from mmap import mmap
from os import path
from subprocess import PIPE, run
from typing import BinaryIO, Callable, Generator, Iterable, List, Optional
from urllib.request import Request, urlopen

CHUNK_SIZE = 1024 * 1024


def import_module(module_name, module_path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None:
        return None

    module = importlib.util.module_from_spec(spec)

    loader = spec.loader
    if loader is None:
        return None
    loader.exec_module(module)

    return module


def get_module_attr(module, attr):
    if module is None:
        return None

    return getattr(module, attr, None)


def remove_dir_contents(dir_path: str):
    for f in os.scandir(dir_path):
        if f.name[0] == '.':
            continue

        if f.is_dir():
            shutil.rmtree(f.path)
        elif f.is_file():
            os.remove(f.path)
        else:
            assert False


def file_path_hash(file_path: str, hash_fn):
    file_hash = hash_fn()
    with open(file_path, 'rb') as f:
        while True:
            data = f.read(CHUNK_SIZE)
            if not data:
                break

            file_hash.update(data)
    return file_hash.hexdigest()


def file_path_sha1(file_path: str):
    return file_path_hash(file_path, hashlib.sha1)


def file_path_sha256(file_path: str):
    return file_path_hash(file_path, hashlib.sha256)


class Color(str, Enum):
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    END = '\033[0m'


def color_print(*args, color: Color, **kwargs):
    args_str = ' '.join(str(arg) for arg in args)
    args_str = color.value + args_str + Color.END.value
    print(args_str, **kwargs)


@lru_cache(maxsize=None)
def executable_path(name: str) -> str:
    exe_path = shutil.which(
        name,
        path=os.pathsep.join(
            [
                os.environ.get('PATH', os.defpath),
                '/usr/sbin',
            ]
        ),
    )

    if not exe_path:
        raise ValueError(f'Failed to find executable path for: {name}')

    return exe_path


def run_cmd(cmd: List[str], shell=False):
    cmd[0] = executable_path(cmd[0])
    proc = run(
        cmd,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        shell=shell,
        check=False,
    )
    if proc.returncode != 0:
        cmd_str = ' '.join(cmd)
        s = f'Failed to run command "{cmd_str}":\n'
        s += f'stdout:\n{proc.stdout}\n'
        s += f'stderr:\n{proc.stderr}\n'
        raise ValueError(s)
    return proc.stdout


def uncomment_line(line: str) -> Optional[str]:
    line = line.strip()

    if not line.startswith('#'):
        return None

    return line.strip('# ')


def is_valid_line(line: str):
    line = line.strip()

    if not line:
        return False

    if line.startswith('#'):
        return False

    return True


def split_lines_into_sections(lines: Iterable[str]) -> List[List[str]]:
    sections_lines: List[List[str]] = [[]]

    last_stripped_line = None
    for line in lines:
        # Create a new section if the last line is empty and this one is
        # a non-empty comment
        # It's important to add all lines to a section to be able to
        # recreate the file without changes
        is_last_added_line_empty = last_stripped_line == ''
        uncommented_line = uncomment_line(line)
        if is_last_added_line_empty and uncommented_line:
            sections_lines.append([])

        sections_lines[-1].append(line)

        last_stripped_line = line.strip()

    return sections_lines


def parse_lines(lines: Iterable[str]) -> List[str]:
    valid_lines = []

    for line in lines:
        line = line.strip()

        if is_valid_line(line):
            valid_lines.append(line)

    return valid_lines


@contextmanager
def TemporaryWorkingDirectory(dir_path: str) -> Generator[None, None, None]:
    cwd = os.getcwd()

    os.chdir(dir_path)

    try:
        yield
    finally:
        os.chdir(cwd)


def scan_tree(dir_path: str):
    for entry in os.scandir(dir_path):
        if entry.is_dir(follow_symlinks=False):
            yield from scan_tree(entry.path)
        else:
            yield entry


def get_content_length(url: str):
    req = Request(url, method='HEAD')
    with urlopen(req) as response:
        content_length = response.getheader('Content-Length')
        assert content_length is not None
        return int(content_length)


def check_downloaded_path(
    file_path: str,
    total_size: int,
    expected_sha256: Optional[str] = None,
):
    if not path.exists(file_path):
        return 0

    downloaded_size = path.getsize(file_path)
    if downloaded_size < total_size:
        return downloaded_size

    if downloaded_size > total_size:
        return 0

    if expected_sha256 is not None:
        downloaded_hash = file_path_sha256(file_path)
        if downloaded_hash != expected_sha256:
            return 0

    return total_size


def urlretrieve_resume(
    url: str,
    file_path: str,
    expected_sha256: Optional[str] = None,
    print_fn: Optional[Callable[[int, bool, bool]]] = None,
):
    total_size = get_content_length(url)

    def print_percent(size: int, first=False, last=False):
        percent = int(size / total_size * 100)
        if print_fn is not None:
            print_fn(percent, first, last)

    downloaded_size = check_downloaded_path(
        file_path,
        total_size,
        expected_sha256,
    )

    if downloaded_size == total_size:
        return

    print_percent(downloaded_size, first=True)

    req = Request(url)
    if downloaded_size != 0:
        req.add_header('Range', f'bytes={downloaded_size}-')

    with urlopen(req) as response:
        mode = 'ab' if downloaded_size > 0 else 'wb'
        with open(file_path, mode) as output_file:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    print_percent(downloaded_size, last=True)
                    break

                output_file.write(chunk)
                downloaded_size += len(chunk)
                print_percent(downloaded_size)

    downloaded_size = check_downloaded_path(
        file_path,
        total_size,
        expected_sha256,
    )

    if downloaded_size == 0:
        raise ValueError(f'Invalid file hash, expected {expected_sha256}')


def read_mmap_chunked(
    mm: mmap,
    size: int,
    offset=0,
    chunk_size=0x100000,
):
    while size > 0:
        read_size = min(chunk_size, size)
        data = mm[offset : offset + read_size]
        offset += read_size

        if not data:
            raise ValueError('Size bigger than stream')

        yield data
        size -= len(data)


def write_zero(f: BinaryIO, size: int):
    f.seek(size - 1, SEEK_CUR)
    f.write(b'\x00')
