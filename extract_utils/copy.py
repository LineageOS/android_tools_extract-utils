#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import os
import shutil

from enum import Enum
from os import path

from .file import File
from .utils import Color, color_print, file_path_sha1


class FileCopyResult(str, Enum):
    FORCE_FIXUP = 'force-fixup'
    TEST_FIXUP = 'test-fixup'
    DONE = 'done'
    ERROR = 'error'


class CopyCtx:
    def __init__(self, source: str, target_dir: str):
        self.source = source
        self.target_dir = target_dir


def copy_file_from_source(source: str, file_src: str, target_file_path: str):
    file_path = f'{source}/{file_src}'

    if not path.isfile(file_path):
        return False

    try:
        shutil.copy(file_path, target_file_path)
        return True
    except:
        pass

    return False


def copy_file_from_sar_source(source: str, file_src: str, target_file_path: str):
    file_src_partition, _ = file_src.split('/', 1)
    if file_src_partition != 'system':
        return False

    return copy_file_from_source(f'{source}/system', file_src, target_file_path)


def copy_file(ctx: CopyCtx, file: File) -> bool:
    target_dir_path = path.join(ctx.target_dir, file.dirname)
    if not path.exists(target_dir_path):
        os.makedirs(target_dir_path)

    target_file_path = path.join(ctx.target_dir, file.dst)

    # Try file.dst first
    if copy_file_from_source(ctx.source, file.dst, target_file_path):
        return True

    # Then try file.src
    if copy_file_from_source(ctx.source, file.src, target_file_path):
        return True

    # If neither were found, and the partition of the file is system,
    # we might be dealing with system-as-root, try using system/ as a prefix
    if copy_file_from_sar_source(ctx.source, file.src, target_file_path):
        return True

    if copy_file_from_sar_source(ctx.source, file.dst, target_file_path):
        return True

    return False


def file_hash_str(file: File):
    msg = ''

    if file.hash is not None:
        msg += f'with hash {hash} '

    if file.fixup_hash is not None:
        msg += f'and fixup hash {file.fixup_hash} '

    return msg


def print_file_find_err(file: File, source_str: str):
    msg = f'{file.dst}: file '
    msg += file_hash_str(file)

    msg += f'not found in {source_str}'
    if source_str == 'source':
        color = Color.YELLOW
        msg += ', trying backup'
    else:
        color = Color.RED

    color_print(msg, color=color)


def process_pinned_file_hash(file: File, hash: str,
                             source_str: str) -> FileCopyResult:
    found_msg = f'{file.dst}: file '
    found_msg += file_hash_str(file)
    found_msg += f'found in {source_str}'

    # The hash matches the pinned hash and there's no fixup hash
    # This means that the file needs no fixups, keep it
    if hash == file.hash and file.fixup_hash is None:
        print(found_msg)
        return FileCopyResult.DONE

    # The hash does not match the pinned hash
    # but matches the pinned fixup hash
    # This means that the file has already had fixups applied
    if hash == file.fixup_hash:
        print(found_msg)
        return FileCopyResult.DONE

    # The hash matches the pinned hash, but there's also a
    # pinned fixup hash
    # This means that the file needs fixups to be applied
    if hash == file.hash:
        color_print(f'{found_msg}, needs fixup', color=Color.YELLOW)
        return FileCopyResult.FORCE_FIXUP

    return FileCopyResult.ERROR


def copy_file_source(file: File, file_path: str, source_str: str, ctx: CopyCtx):
    result = FileCopyResult.ERROR
    hash = None

    # If success, assume file needs fixups
    if copy_file(ctx, file):
        result = FileCopyResult.TEST_FIXUP

        if file.hash is not None:
            # File has hashes, find if they match or if the file needs fixups
            hash = file_path_sha1(file_path)
            result = process_pinned_file_hash(
                file, hash, source_str)

    if result == FileCopyResult.ERROR:
        print_file_find_err(file, source_str)

    return result, hash


def copy_file_with_hashes(file: File, file_path: str,
                          copy_ctx: CopyCtx, restore_ctx: CopyCtx):
    copy_result, source_file_hash = copy_file_source(
        file, file_path, 'source', copy_ctx)

    if copy_result == FileCopyResult.ERROR:
        copy_result, source_file_hash = copy_file_source(
            file, file_path, 'backup', restore_ctx)

    return copy_result, source_file_hash
