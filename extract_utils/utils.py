from os import path
import os
import shutil


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
