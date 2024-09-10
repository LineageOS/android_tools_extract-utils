import os
import shutil
import hashlib
import importlib.util

from enum import Enum
from os import path


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

        if path.isdir(f.path):
            shutil.rmtree(f.path)
        elif path.isfile(f.path):
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
    GREEN = "\033[0;32m"
    YELLOW = '\033[1;33m'
    END = '\033[0m'


def color_print(*args, color: Color, **kwargs):
    args = list(args)
    args[0] = color.value + str(args[0])
    args[-1] = str(args[-1]) + Color.END.value
    print(*args, **kwargs)
