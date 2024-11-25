#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import os
import shutil
from enum import Enum
from functools import lru_cache
from subprocess import PIPE, Popen, run
from typing import Generator, Iterable, List, Optional, Tuple


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
    with open(file_path, 'rb') as f:
        data = f.read()
        file_hash = hash_fn(data)
        return file_hash.hexdigest()


def file_path_sha1(file_path: str):
    return file_path_hash(file_path, hashlib.sha1)


class Color(str, Enum):
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    END = '\033[0m'


def color_print(*args, color: Color, **kwargs):
    args_str = ' '.join(str(arg) for arg in args)
    args_str = color.value + args_str + Color.END.value
    print(args_str, **kwargs)


parallel_input_cmds = List[Tuple[str, List[str]]]
parallel_input_cmds_ret_success = List[str]
parallel_input_cmds_ret_fail = List[Tuple[str, int, str]]


@lru_cache(maxsize=None)
def executable_path(name: str) -> str:
    path = shutil.which(
        name,
        path=os.pathsep.join(
            [
                os.environ.get('PATH', os.defpath),
                '/usr/sbin',
            ]
        ),
    )

    if not path:
        raise ValueError(f'Failed to find executable path for: {name}')

    return path


def process_cmds_in_parallel(input_cmds: parallel_input_cmds, fatal=False):
    input_procs: List[Tuple[str, Popen]] = []

    for input_id, cmd in input_cmds:
        print(f'Processing {input_id}')
        cmd[0] = executable_path(cmd[0])
        proc = Popen(cmd, stdout=PIPE, stderr=PIPE, text=True)
        input_procs.append((input_id, proc))

    ret_success: parallel_input_cmds_ret_success = []
    ret_fail: parallel_input_cmds_ret_fail = []
    for input_id, proc in input_procs:
        _, stderr = proc.communicate()
        assert isinstance(proc.returncode, int)
        if proc.returncode:
            s = f'Failed to process {input_id}: {stderr.strip()}'
            if fatal:
                raise ValueError(s)

            ret_fail.append((input_id, proc.returncode, stderr))
        else:
            ret_success.append(input_id)

    return ret_fail, ret_success


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


@contextlib.contextmanager
def TemporaryWorkingDirectory(dir_path: str) -> Generator[None, None, None]:
    cwd = os.getcwd()

    os.chdir(dir_path)

    try:
        yield
    finally:
        os.chdir(cwd)
