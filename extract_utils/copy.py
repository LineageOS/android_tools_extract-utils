import os
import shutil

from os import path

from .file import File


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
    file_src_partition = file_src.split('/', 1)[0]
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
