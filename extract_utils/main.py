from .makefiles import \
    MakefilesCtx, \
    write_bp_header, \
    write_bp_soong_namespaces, \
    write_mk_header, \
    write_mk_soong_namespaces, \
    write_product_copy_files, \
    write_product_packages, \
    write_symlink_packages
from enum import Enum
import shutil
import tempfile

import importlib.util
import inspect

from os import path
from typing import List, Optional, TextIO

from .args import parse_args
from .copy import CopyCtx, copy_file
from .extract import ExtractCtx, extract_image
from .file import File, FileArgs, FileList
from .hash import file_path_sha1
from .print import Color, color_print
from .tools import get_android_root

from .fixups import \
    BlobFixupCtx, \
    blob_fixup, \
    fix_xml, \
    flatten_fixups, \
    blob_fixups_user_type, \
    lib_fixups_user_type


ANDROID_ROOT = get_android_root()


class FileCopyResult(str, Enum):
    FORCE_FIXUP = 'force-fixup'
    TEST_FIXUP = 'test-fixup'
    DONE = 'done'
    ERROR = 'error'


def import_module(module_name, module_path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None:
        return None

    module = importlib.util.module_from_spec(spec)

    loader = spec.loader
    if loader is None:
        return None
    loader.exec_module(module)

    return module


def get_module_attr(module, attr):
    if module is None:
        return None

    return getattr(module, attr, None)


class ProprietaryFile:
    def __init__(self, name, partition=None, skip_files=None, skip_exts=None):
        self.name = name
        self.partition = partition
        self.skip_files = skip_files
        self.skip_exts = skip_exts
        self.is_generated = partition is not None
        self.file_list: Optional[FileList] = None


class ExtractUtilsModule:
    def __init__(self, device, vendor,
                 blob_fixups: Optional[blob_fixups_user_type] = None,
                 lib_fixups: Optional[lib_fixups_user_type] = None,
                 vendor_imports: Optional[List[str]] = None):
        self.device = device
        self.vendor = vendor
        self.vendor_imports = vendor_imports
        self.proprietary_files: List[ProprietaryFile] = []

        self.__blob_fixups = flatten_fixups(blob_fixups)
        self.__lib_fixups = flatten_fixups(lib_fixups)

        # Automatically compute module path
        calling_module_stack = inspect.stack()[1]
        calling_module_path = path.normpath(calling_module_stack.filename)
        self.dir_path = path.dirname(calling_module_path)

        self.vendor_rel_path = path.join('vendor', vendor, device)
        self.vendor_path = path.join(ANDROID_ROOT, self.vendor_rel_path)
        self.vendor_files_rel_sub_path = 'proprietary'

        self.vendor_files_rel_path = path.join(
            self.vendor_rel_path, self.vendor_files_rel_sub_path)
        self.vendor_files_path = path.join(
            self.vendor_path, self.vendor_files_rel_sub_path)
        self.vendor_backup_files_path = tempfile.mkdtemp()

        self.add_proprietary_file('proprietary-files.txt')

    def lib_fixup(self, lib: str, file_path: str) -> str:
        if self.__lib_fixups is None:
            return lib

        lib_fixup_fn = self.__lib_fixups.get(lib)
        if lib_fixup_fn is None:
            return lib

        fixed_up_lib = lib_fixup_fn(lib, file_path)
        if fixed_up_lib is None:
            return lib

        return fixed_up_lib

    def blob_fixup(self, ctx: Optional[BlobFixupCtx], file: File,
                   file_path: Optional[str] = None) -> bool:
        if self.__blob_fixups is None:
            return False

        blob_fixup_fn = self.__blob_fixups.get(file.dst)
        if blob_fixup_fn is None:
            return False

        if file_path is None:
            return True

        if isinstance(blob_fixup_fn, blob_fixup):
            assert ctx is not None
            blob_fixup_fn.run(ctx, file, file_path)
        else:
            blob_fixup_fn(file.dst, file_path)

        return True

    def add_generated_carriersettings(self):
        name = 'proprietary-files-carriersettings.txt'
        return self.add_proprietary_file_raw(name, skip_exts=[])

    def add_proprietary_file_raw(self, name, partition=None,
                                 skip_files=None, skip_exts=None):
        if skip_exts is None:
            skip_exts = ['.odex', '.vdex']

        proprietary_file = ProprietaryFile(
            name, partition=partition,
            skip_files=skip_files, skip_exts=skip_exts)

        self.proprietary_files.append(proprietary_file)

        return proprietary_file

    def add_proprietary_file(self, name):
        proprietary_file = ProprietaryFile(name)
        self.proprietary_files.append(proprietary_file)
        return proprietary_file


class ExtractUtils:
    def __init__(self, device_module: ExtractUtilsModule,
                 common_module: Optional[ExtractUtilsModule] = None):
        self.__device_module = device_module
        self.__common_module = common_module

        self.__enable_check_elf = False

        args = parse_args()

        self.__keep_dump = args.keep_dump
        self.__no_cleanup = args.no_cleanup
        self.__kang = args.kang
        self.__section = args.section
        self.__source = args.src
        self.__extracted_source = None

        self.__modules: List[ExtractUtilsModule] = []
        if args.only_target:
            self.__modules.append(self.__device_module)
        elif args.only_common:
            assert self.__common_module is not None
            self.__modules.append(self.__common_module)
        else:
            self.__modules = [self.__device_module]
            if self.__common_module is not None:
                self.__modules.append(self.__common_module)

    @classmethod
    def device_with_common(cls, device_module: ExtractUtilsModule,
                           device_common, vendor_common=None):
        if vendor_common is None:
            vendor_common = device_module.vendor
        common_module = cls.get_module(device_common, vendor_common)
        return cls(device_module, common_module)

    @classmethod
    def device(cls, device_module: ExtractUtilsModule):
        return cls(device_module)

    @classmethod
    def import_module(cls, device, vendor) -> Optional[ExtractUtilsModule]:
        module_name = f'{vendor}_{device}'
        module_path = path.join(ANDROID_ROOT, 'device',
                                vendor, device, 'extract-files.py')

        module = import_module(module_name, module_path)

        return get_module_attr(module, 'module')

    @classmethod
    def get_module(cls, device: str, vendor: str):
        module = cls.import_module(device, vendor)
        assert module is not None
        return module

    def enable_check_elf(self):
        self.__enable_check_elf = True

    def extract_source(self):
        # TODO: implement ADB
        assert self.__source != 'adb'

        ctx = ExtractCtx(self.__keep_dump)
        self.__extracted_source = extract_image(ctx, self.__source)

    def cleanup_source(self):
        if not self.__keep_dump:
            assert self.__extracted_source is not None
            shutil.rmtree(self.__extracted_source)

    def write_file_list_makefiles(self, ctx: MakefilesCtx, bp_out: TextIO,
                                  mk_out: TextIO, file_list: FileList):

        write_product_copy_files(ctx, file_list.copy_files, mk_out)

        write_product_packages(ctx, file_list.packages_files,
                               bp_out, mk_out)

        write_symlink_packages(ctx, file_list.packages_files_symlinks,
                               bp_out, mk_out)

    def file_hash_str(self, file: File):
        msg = ''

        if file.hash is not None:
            msg += f'with hash {hash} '

        if file.fixup_hash is not None:
            msg += f'and fixup hash {file.fixup_hash} '

        return msg

    def print_file_find_err(self, file: File, source_str: str):
        msg = f'{file.dst}: file '
        msg += self.file_hash_str(file)

        msg += f'not found in {source_str}'
        if source_str == 'source':
            color = Color.YELLOW
            msg += ', trying backup'
        else:
            color = Color.RED

        color_print(msg, color=color)

    def process_pinned_file_hash(self, file: File, hash: str,
                                 source_str: str) -> FileCopyResult:
        found_msg = f'{file.dst}: file '
        found_msg += self.file_hash_str(file)
        found_msg += f'found in {source_str}'

        # The hash matches the pinned hash and there's no fixup hash
        # This means that the file needs no fixups, keep it
        if hash == file.hash and file.fixup_hash is None:
            print(found_msg)
            return FileCopyResult.DONE

        # The hash does not match the pinned hash
        # but matches the pinned fixup hash
        # This means that the file has already had fixups applied
        if hash == file.fixup_hash:
            print(found_msg)
            return FileCopyResult.DONE

        # The hash matches the pinned hash, but there's also a
        # pinned fixup hash
        # This means that the file needs fixups to be applied
        if hash == file.hash:
            color_print(f'{found_msg}, needs fixup', color=Color.YELLOW)
            return FileCopyResult.FORCE_FIXUP

        return FileCopyResult.ERROR

    def copy_file_source(self, file: File, file_path: str, source_str: str,
                         ctx: CopyCtx):
        result = FileCopyResult.ERROR
        hash = None

        # If success, assume file needs fixups
        if copy_file(ctx, file):
            result = FileCopyResult.TEST_FIXUP

            if file.hash is not None:
                # File has hashes, find if they match or if the file needs fixups
                hash = file_path_sha1(file_path)
                result = self.process_pinned_file_hash(
                    file, hash, source_str)

        if result == FileCopyResult.ERROR:
            self.print_file_find_err(file, source_str)

        return result, hash

    def copy_file(self, file: File, file_path: str,
                  copy_ctx: CopyCtx, restore_ctx: CopyCtx):
        copy_result, source_file_hash = self.copy_file_source(
            file, file_path, 'source', copy_ctx)

        if copy_result == FileCopyResult.ERROR:
            copy_result, source_file_hash = self.copy_file_source(
                file, file_path, 'backup', restore_ctx)

        return copy_result, source_file_hash

    def should_module_fixup_file(self, module: ExtractUtilsModule, file: File):
        if FileArgs.FIX_XML in file.args:
            return True

        if FileArgs.FIX_SONAME in file.args:
            return True

        if module.blob_fixup(None, file):
            return True

        return False

    def fixup_module_file(self, module: ExtractUtilsModule, file: File,
                          file_path: str):
        if FileArgs.FIX_XML in file.args:
            # TODO: implement
            assert False

        ctx = BlobFixupCtx(module.dir_path)

        if FileArgs.FIX_SONAME in file.args:
            blob_fixup() \
                .fix_soname() \
                .run(ctx, file, file_path)

        module.blob_fixup(ctx, file, file_path)

    def process_module_file(self, module: ExtractUtilsModule, file: File,
                            copy_ctx: CopyCtx, restore_ctx: CopyCtx):
        file_path = path.join(module.vendor_files_path, file.dst)

        copy_result, pre_fixup_hash = self.copy_file(
            file, file_path, copy_ctx, restore_ctx)

        # TODO: implement dex2oat

        if copy_result == FileCopyResult.ERROR \
                or copy_result == FileCopyResult.DONE:
            return

        if copy_result == FileCopyResult.FORCE_FIXUP:
            should_fixup = True
        elif copy_result == FileCopyResult.TEST_FIXUP:
            should_fixup = self.should_module_fixup_file(module, file)
        else:
            assert False

        if not should_fixup:
            return

        if pre_fixup_hash is None:
            pre_fixup_hash = file_path_sha1(file_path)

        self.fixup_module_file(module, file, file_path)

        postfixup_hash = file_path_sha1(file_path)

        if pre_fixup_hash == postfixup_hash:
            color_print(
                f'{file.dst}: no fixups applied, file must have been '
                'fixed up already',
                color=Color.YELLOW
            )
            return

        if not self.__kang and file.hash is not None:
            if file.fixup_hash is not None:
                color_print(
                    f'{file.dst}: fixed up pinned file with no fixup hash, '
                    f'fixup hash is {file.fixup_hash}',
                    color=Color.YELLOW
                )
            else:
                color_print(
                    f'{file.dst}: fixed up pinned file with fixup hash, '
                    f'but hash {postfixup_hash} different from fixup hash '
                    f'{file.fixup_hash}',
                    color=Color.YELLOW
                )
            return

        color_print(f'{file.dst}: fixed up', color=Color.GREEN)

    def process_module_proprietary_files(self, module: ExtractUtilsModule):
        if not self.__no_cleanup and path.isdir(module.vendor_files_path):
            # Creating it again is handled automatically by copy_file
            shutil.rmtree(module.vendor_files_path)

        assert self.__extracted_source is not None
        copy_ctx = CopyCtx(self.__extracted_source, module.vendor_files_path)

        restore_ctx = CopyCtx(
            module.vendor_backup_files_path,
            module.vendor_files_path
        )

        for proprietary_files in module.proprietary_files:
            file_list = proprietary_files.file_list
            assert file_list is not None

            for file in file_list.all_files:
                self.process_module_file(module, file, copy_ctx, restore_ctx)

    def process_proprietary_files(self):
        for module in self.__modules:
            self.process_module_proprietary_files(module)

    def parse_proprietary_file(self, module: ExtractUtilsModule,
                               proprietary_file: ProprietaryFile):
        files_list_name = proprietary_file.name
        files_list_path = path.join(module.dir_path, files_list_name)
        proprietary_file.file_list = FileList(
            files_list_path, section=self.__section,
            target_enable_checkelf=self.__enable_check_elf,
            kang=self.__kang,
        )

    def parse_proprietary_files(self):
        for module in self.__modules:
            for proprietary_file in module.proprietary_files:
                self.parse_proprietary_file(module, proprietary_file)

    def copy_pinned_files(self):
        for module in self.__modules:
            for proprietary_file in module.proprietary_files:
                file_list = proprietary_file.file_list
                assert file_list is not None

                source_path = module.vendor_files_path
                destination_path = module.vendor_backup_files_path
                backup_ctx = CopyCtx(source_path, destination_path)
                for file in file_list.pinned_files:
                    copy_success = copy_file(backup_ctx, file)
                    if copy_success:
                        print(f'Backed up {file.dst}')
                    else:
                        color_print(f'Failed to back up {file.dst}',
                                    color=Color.YELLOW)

    def clean_pinned_files_backup(self):
        for module in self.__modules:
            shutil.rmtree(module.vendor_backup_files_path)

    def write_makefiles(self):
        for module in self.__modules:
            ctx = MakefilesCtx(
                module.device,
                module.vendor,
                module.vendor_files_path,
                module.vendor_files_rel_path,
                module.vendor_files_rel_sub_path,
                module.vendor_imports,
                module.lib_fixup,
                self.__enable_check_elf,
            )

            android_bp_path = path.join(module.vendor_path, 'Android.bp')
            product_mk_path = path.join(module.vendor_path,
                                        f'{module.device}-vendor.mk')
            board_config_mk_path = path.join(module.vendor_path,
                                             'BoardConfigVendor.mk')

            with open(android_bp_path, 'w') as bp_out, \
                    open(product_mk_path, 'w') as mk_out, \
                    open(board_config_mk_path, 'w') as board_mk_out:

                write_bp_header(ctx, bp_out)
                write_bp_soong_namespaces(ctx, bp_out)

                write_mk_header(ctx, mk_out)
                write_mk_soong_namespaces(ctx, mk_out)

                write_mk_header(ctx, board_mk_out)

                for proprietary_file in module.proprietary_files:
                    file_list = proprietary_file.file_list
                    assert file_list is not None
                    self.write_file_list_makefiles(
                        ctx, bp_out, mk_out, file_list)

    def run(self):
        self.extract_source()
        self.parse_proprietary_files()
        self.copy_pinned_files()
        # TODO: regenerate
        self.process_proprietary_files()
        self.write_makefiles()
        self.clean_pinned_files_backup()
