#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

import json
from json import JSONEncoder
from typing import Iterator


class BpJSONEncoder(JSONEncoder):
    def __init__(self, *args, legacy=False, **kwargs):
        super().__init__(*args, **kwargs)

        self.__level = 0
        self.__legacy = legacy
        self.__indent = ' ' * 4
        if legacy:
            self.__indent = '\t'

    def __k_v_encode(self, k, v):
        if isinstance(v, list):
            if self.__legacy:
                if k == 'shared_libs':
                    return self.__list_encode(
                        v, ending_comma=True, space_on_empty=True
                    )

                if k == 'imports':
                    return self.__list_encode(v, newlines=True)

            return self.__list_encode(v)

        return self.encode(v)

    def __dict_encode(self, o):
        # Special encoding to not add quotes to dictionary keys
        self.__level += 1
        indent = self.indent_str
        output = [
            f'{indent}{k}: {self.__k_v_encode(k, v)},\n' for k, v in o.items()
        ]
        self.__level -= 1

        output_str = ''.join(output)

        return f'{{\n{output_str}{self.indent_str}}}'

    def __list_encode(
        self,
        o,
        ending_comma=False,
        space_on_empty=False,
        newlines=None,
    ):
        if newlines is None:
            newlines = not self.__legacy

        # Need to encode lists too, just because it's not possible to reuse
        # the encoding provided by the base class
        # base encode function calls iterencode, which calls encode again...
        if not newlines:
            output_str = ', '.join(json.dumps(el) for el in o)
            if not output_str and space_on_empty:
                output_str = ' '
            elif ending_comma:
                output_str += ', '
            return f'[{output_str}]'

        self.__level += 1
        indent = self.indent_str
        output = [f'{indent}{self.encode(v)},\n' for v in o]
        self.__level -= 1

        output_str = ''.join(output)

        return f'[\n{output_str}{self.indent_str}]'

    def encode(self, o) -> str:
        if isinstance(o, dict):
            return self.__dict_encode(o)

        if isinstance(o, list):
            return self.__list_encode(o)

        return json.dumps(o)

    @property
    def indent_str(self) -> str:
        return self.__indent * self.__level

    def iterencode(self, o, _one_shot=False) -> Iterator[str]:
        return iter([self.encode(o)])
