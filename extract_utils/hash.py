
import hashlib
from os import path

from .file import File

HASH_BLOCK_SIZE = 8192


def file_path_hash(file_path: str, hash_fn):
    with open(file_path, 'rb') as f:
        file_hash = hash_fn()
        chunk = f.read(HASH_BLOCK_SIZE)

        while chunk:
            file_hash.update(chunk)
            chunk = f.read(HASH_BLOCK_SIZE)

    return file_hash.hexdigest()


def file_path_sha1(file_path: str):
    return file_path_hash(file_path, hashlib.sha1)
