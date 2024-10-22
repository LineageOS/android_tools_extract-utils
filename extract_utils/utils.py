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
from subprocess import PIPE, Popen, run
from typing import Iterable, List, Tuple


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
    args = list(args)
    args[0] = color.value + str(args[0])
    args[-1] = str(args[-1]) + Color.END.value
    print(*args, **kwargs)


parallel_input_cmds = List[Tuple[str, List[str]]]


def process_cmds_in_parallel(input_cmds: parallel_input_cmds, fatal=False):
    input_procs: List[Tuple[str, Popen]] = []

    for input, cmd in input_cmds:
        print(f'Processing {input}')
        proc = Popen(cmd, stdout=PIPE, stderr=PIPE, text=True)
        input_procs.append((input, proc))

    for input, proc in input_procs:
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            s = f'Failed to process {input}: {stderr.strip()}'
            if fatal:
                raise ValueError(s)
            else:
                print(s)


def run_cmd(cmd: List[str], shell=False):
    proc = run(cmd, stdout=PIPE, stderr=PIPE, text=True, shell=shell)
    if proc.returncode != 0:
        cmd_str = ' '.join(cmd)
        s = f'Failed to run command "{cmd_str}":\n'
        s += f'stdout:\n{proc.stdout}\n'
        s += f'stderr:\n{proc.stderr}\n'
        raise ValueError(s)
    return proc.stdout


def uncomment_line(line: str) -> str | None:
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
def TemporaryWorkingDirectory(dir):
    cwd = os.getcwd()

    os.chdir(dir)

    try:
        yield
    finally:
        os.chdir(cwd)
