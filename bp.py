#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

import json

from env import VENDOR


PARTITION_SPECIFIC_MAP = {
    'vendor': 'soc',
    'product': 'product',
    'system_ext': 'system_ext',
    'odm': 'device',
}


class BpJSONEnconder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.level = 0

    # Add an empty space when list is empty to match old output
    # Add an ending comma to match old output
    # TODO: remove
    def __l_encode(self, o, ending_comma=False, space_on_empty=False):
        output_str = ', '.join(json.dumps(el) for el in o)
        if not output_str and space_on_empty:
            output_str = ' '
        elif ending_comma:
            output_str += ', '
        return f'[{output_str}]'

    def __k_v_encode(self, k, v):
        if k == 'shared_libs' and type(v) == list:
            output_str = self.__l_encode(
                v, ending_comma=True, space_on_empty=True)
        else:
            output_str = self.encode(v)

        return f'{k}: {output_str}'

    def encode(self, o):
        if isinstance(o, (list, tuple)):
            return self.__l_encode(o)
        elif isinstance(o, dict):
            self.level += 1
            indent = self.indent_str
            output = [indent + self.__k_v_encode(k, v) for k, v in o.items()]
            self.level -= 1
            output_str = ',\n'.join(output)
            return f'{{\n{output_str},\n{self.indent_str}}}'
        else:
            return json.dumps(o)

    @property
    def indent_str(self) -> str:
        return self.indent * self.level

    def iterencode(self, o):
        return self.encode(o)


class BpBuilder:
    def __init__(self, rule_name, file=None):
        self.rule_name = rule_name
        self.file = file
        self.o = {}

    def raw_name(self, name):
        self.o['name'] = name
        return self

    def name(self):
        package_name = self.file.package_name
        self.raw_name(package_name)
        return self

    def stem(self):
        stem = self.file.stem
        if stem is not None:
            self.o['stem'] = stem
        return self

    def owner(self):
        self.o['owner'] = VENDOR
        return self

    def src(self):
        self.o['src'] = self.file.rel_path
        return self

    def apk(self):
        self.o['apk'] = self.file.rel_path
        return self

    def jars(self):
        self.o['jars'] = [self.file.rel_path]
        return self

    def filename(self):
        self.o['filename'] = self.file.basename
        return self

    def specific_raw(self, part):
        specific = PARTITION_SPECIFIC_MAP.get(part)
        if specific is not None:
            self.o[f'{specific}_specific'] = True

        return self

    def specific(self):
        return self.specific_raw(self.file.part)

    def target(self, f, arch, deps):
        target = self.o.setdefault('target', {})
        target[arch] = {
            'srcs': [f.rel_path]
        }
        if deps is not None:
            target[arch]['shared_libs'] = deps
        return self

    def targets(self, files, arches, deps):
        for f, arch in zip(files, arches):
            self.target(f, arch, deps)
        return self

    def multilib(self, bits):
        if type(bits) == list or type(bits) == tuple:
            if len(bits) == 1:
                bits = str(bits[0])
            elif len(bits) == 2:
                bits = 'both'

        if type(bits) == int:
            bits = str(bits)

        self.o['compile_multilib'] = bits
        return self

    def check_elf(self):
        if not self.file.enable_checkelf:
            self.o['check_elf_files'] = False
        return self

    def no_strip(self):
        self.o['strip'] = {
            'none': True,
        }
        return self

    def prefer(self):
        self.o['prefer'] = True
        return self

    def rel_install_path(self):
        p = self.file.dirname_without_prefix
        if p:
            self.o['relative_install_path'] = p
        return self

    def sub_dir(self):
        p = self.file.dirname_without_prefix
        if p:
            self.o['sub_dir'] = p
        return self

    def signature(self):
        if self.file.presigned():
            self.o['preprocessed'] = True
            self.o['presigned'] = True
        else:
            self.o['certificate'] = 'platform'
        return self

    def overrides(self):
        overrides = self.file.overrides()
        if overrides:
            self.o['overrides'] = overrides
        return self

    def required(self):
        required = self.file.required()
        if required:
            self.o['required'] = required
        return self

    def preopt(self):
        self.o['dex_preopt'] = {
            'enabled': False
        }
        return self

    def privileged(self):
        if self.file.privileged():
            self.o['privileged'] = True
        return self

    def filename_from_src(self):
        self.o['filename_from_src'] = True
        return self

    def installed_location(self, p):
        self.o['installed_location'] = p
        return self

    def symlink_target(self, p):
        self.o['symlink_target'] = p
        return self

    def write(self, out):
        out.write(self.rule_name)
        out.write(' ')
        json.dump(self.o, out, indent='\t', cls=BpJSONEnconder)
        out.write('\n')
        out.write('\n')
