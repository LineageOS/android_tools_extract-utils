import argparse
import os

parser = argparse.ArgumentParser(description='Extract utils')

group = parser.add_mutually_exclusive_group()
group.add_argument('--only-common', action='store_true',
                   help='only extract common module')
group.add_argument('--only-target', action='store_true',
                   help='only extract target module')

parser.add_argument('--keep_dump', action='store_true',
                    help='keep dump after extraction')
parser.add_argument('-n', '--no-cleanup', action='store_true',
                    help='do not cleanup vendor')
parser.add_argument('-k', '--kang', action='store_true',
                    help='kang and modify hashes')
parser.add_argument('-s', '--section', action='store',
                    help='only apply to section')
parser.add_argument('--keep-dump', action='store_true',
                    help='keep the dump directory')

parser.add_argument('src', help='sources from which to extract')


def parse_args():
    args = parser.parse_args()

    if args.section is not None:
        args.no_cleanup = True

    keep_dump = os.environ.get('KEEP_DUMP', '').lower()
    if keep_dump == '1' or keep_dump == 'true':
        args.keep_dump = True

    return args
