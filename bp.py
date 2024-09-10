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

    def set(self, k, v):
        if v is not None:
            self.o[k] = v
        return self

    def raw_name(self, name):
        self.set('name', name)
        return self

    def name(self):
        package_name = self.file.package_name
        self.raw_name(package_name)
        return self

    def stem(self):
        return self.set('stem', self.file.stem)

    def owner(self):
        return self.set('owner', VENDOR)

    def src(self):
        return self.set('src', self.file.rel_path)

    def filename(self):
        return self.set('filename', self.file.basename)

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

    def fixup_shared_libs(self, mapping):
        targets = self.o.get('target')
        if targets:
            for target in targets.values():
                deps = target.get('shared_libs')
                if deps is not None:
                    deps = [mapping[d]
                            for d in deps if mapping.get(d) is not None]
                    target['shared_libs'] = deps
        return self

    def multilib(self, bitses):
        bitses_type = type(bitses)
        if bitses_type is list:
            bitses_len = len(bitses)
            if bitses_len == 1:
                bits = bitses[0]
            elif bitses_len == 2:
                bits = 'both'
        else:
            bits = bitses

        return self.set('compile_multilib', bits)

    def check_elf(self):
        if not self.file.enable_checkelf:
            self.set('check_elf_files', False)
        return self

    def no_strip(self):
        return self.set('strip', {
            'none': True,
        })

    def prefer(self):
        return self.set('prefer', True)

    def rel_install_path(self):
        return self.set('relative_install_path', self.file.dirname_without_prefix)

    def sub_dir(self):
        return self.set('sub_dir', self.file.dirname_without_prefix)

    def signature(self):
        if self.file.presigned():
            self.set('preprocessed', True)
            self.set('presigned', True)
        else:
            self.set('certificate', 'platform')
        return self

    def write(self, out):
        out.write(self.rule_name)
        out.write(' ')
        json.dump(self.o, out, indent='\t', cls=BpJSONEnconder)
        out.write('\n')
        out.write('\n')
