
from collections.abc import Iterator
from json import JSONEncoder
import json


class BpJSONEnconder(JSONEncoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.level = 0
        self.indent = '    '

    def __dict_encode(self, o):
        # Special encoding to not add quotes to dictionary keys
        self.level += 1
        indent = self.indent_str
        output = [indent + f'{k}: {self.encode(v)},\n' for k, v in o.items()]
        self.level -= 1

        output_str = ''.join(output)

        return f'{{\n{output_str}{self.indent_str}}}'

    def __list_encode(self, o):
        # Need to encode lists too, just because it's not possible to reuse
        # the encoding provided by the base class
        # base encode function calls iterencode, which calls encode again...
        self.level += 1
        indent = self.indent_str
        output = [indent + f'{self.encode(v)},\n' for v in o]
        self.level -= 1

        output_str = ''.join(output)

        return f'[\n{output_str}{self.indent_str}]'

    def encode(self, o) -> str:
        # do not allow subclassing for efficiency
        o_type = type(o)
        if o_type == dict:
            return self.__dict_encode(o)
        elif o_type == list:
            return self.__list_encode(o)

        return json.dumps(o)

    @property
    def indent_str(self) -> str:
        assert type(self.indent) == str
        return self.indent * self.level

    def iterencode(self, o) -> Iterator[str]:
        return iter([self.encode(o)])
