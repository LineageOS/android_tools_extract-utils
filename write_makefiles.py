#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import sys

from env import ANDROIDBP, PRODUCTMK
from extract_utils import write_product_packages, write_product_copy_files, \
    write_symlink_packages
from file import parse_file_list

if __name__ == '__main__':
    file_list_path = sys.argv[1]

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
        write_product_packages(packages_files, bp_out, mk_out)
        write_symlink_packages(packages_symlinks, bp_out, mk_out)
