
from collections.abc import Iterator
from json import JSONEncoder
import json


class BpJSONEnconder(JSONEncoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.__level = 0
        self.__indent = ' ' * 4

    def __dict_encode(self, o):
        # Special encoding to not add quotes to dictionary keys
        self.__level += 1
        indent = self.indent_str
        output = [indent + f'{k}: {self.encode(v)},\n' for k, v in o.items()]
        self.__level -= 1

        output_str = ''.join(output)

        return f'{{\n{output_str}{self.indent_str}}}'

    def __list_encode(self, o):
        # Need to encode lists too, just because it's not possible to reuse
        # the encoding provided by the base class
        # base encode function calls iterencode, which calls encode again...
        self.__level += 1
        indent = self.indent_str
        output = [indent + f'{self.encode(v)},\n' for v in o]
        self.__level -= 1

        output_str = ''.join(output)

        return f'[\n{output_str}{self.indent_str}]'

    def encode(self, o) -> str:
        if isinstance(o, dict):
            return self.__dict_encode(o)
        elif isinstance(o, list):
            return self.__list_encode(o)

        return json.dumps(o)

    @property
    def indent_str(self) -> str:
        return self.__indent * self.__level

    def iterencode(self, o) -> Iterator[str]:
        return iter([self.encode(o)])
