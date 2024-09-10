from enum import Enum


class Color(str, Enum):
    RED = '\033[0;31m'
    GREEN = "\033[0;32m"
    YELLOW = '\033[1;33m'
    END = '\033[0m'


def color_print(*args, color: Color, **kwargs):
    print(color.value, *args, Color.END.value, **kwargs)
