#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import sys
import subprocess

from env import ANDROIDBP, PRODUCTMK
from extract_utils import write_product_packages, write_product_copy_files, \
    write_symlink_packages
from file import parse_file_list

FIXUP_MISSING = '__MISSING__'


def lib_script(part, lib):
    return f'''
echo "{lib}"
out=$(lib_to_package_fixup "{lib}" "{part}" || echo "{lib}")
if [ "$out" = "" ]; then
    out={FIXUP_MISSING}
fi
echo "$out"
'''


if __name__ == '__main__':
    file_list_path = sys.argv[1]
    source_makefile = sys.argv[2]

    def lib_fixup(part, lib_mapping):
        script = f'source {source_makefile}\n'
        for lib in lib_mapping.keys():
            script += lib_script(part, lib)

        with subprocess.Popen(script,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True, shell=True) as p:
            for line in p.stdout:
                k = line.strip()

                line = next(p.stdout)
                v = line.strip()

                if v == FIXUP_MISSING:
                    v = None

                if lib_mapping[k] == v:
                    continue

                print(f'Fixed up library {k} to {v}')
                if v is None:
                    lib_mapping.pop(k)
                else:
                    lib_mapping[k] = v

    packages_files = []
    copy_files = []
    packages_symlinks = []

    parse_file_list(file_list_path,
                    packages_files=packages_files,
                    packages_symlinks=packages_symlinks,
                    copy_files=copy_files)

    with open(ANDROIDBP, 'a') as bp_out, \
            open(PRODUCTMK, 'a') as mk_out:
        write_product_copy_files(copy_files, mk_out)
        write_product_packages(packages_files, bp_out,
                               mk_out, lib_fixup=lib_fixup)
        write_symlink_packages(packages_symlinks, bp_out, mk_out)
