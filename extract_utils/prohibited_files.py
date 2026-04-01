import re
import sys

# Case-insensitive pattern, filename-scoped
PROHIBITED_RE = re.compile(
    r'(^|/)(libmeg[^/]*\.so|[^/]*\.lic|[^/]*megvii[^/]*)$',
    re.IGNORECASE
)

def is_prohibited(path: str) -> bool:
    return bool(PROHIBITED_RE.search(path))


def fail_prohibited(paths):
    if not paths:
        return

    RED = "\033[31m"
    RESET = "\033[0m"

    print(f"{RED}ERROR: Prohibited blobs detected during extraction:{RESET}", file=sys.stderr)
    for p in sorted(paths):
        print(f"{RED}  - {p}{RESET}", file=sys.stderr)

    print(
        f"\n{RED}Policy violation:{RESET} libmeg*.so, *.lic, and *megvii* files are not allowed.\n",
        file=sys.stderr
    )

    sys.exit(1)
