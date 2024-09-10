#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from typing import Callable, List, Optional, TextIO

from .bp_builder import BpBuilder, ModuleClass
from .elf import get_file_arch_bits, get_file_arch_bits_libs
from .file import CommonFileTree, File, FileArgs, FileTree
from .fixups import lib_fixups_type, run_libs_fixup

# TODO: find out if partition-less files are a thing
ALL_PARTITIONS = ['system', 'vendor', 'product', 'system_ext', 'odm']
APEX_PARTITIONS = ['system', 'vendor', 'system_ext']
RFSA_PARTITIONS = ['vendor', 'odm']


class MakefilesCtx:
    def __init__(self, device: str, vendor: str,
                 vendor_files_path: str,
                 vendor_files_rel_path: str,
                 vendor_files_rel_sub_path: str,
                 vendor_imports: Optional[List[str]],
                 lib_fixups: Optional[lib_fixups_type],
                 target_enable_checkelf: bool):
        self.device = device
        self.vendor = vendor
        self.files_rel_path = vendor_files_rel_path
        self.files_path = vendor_files_path
        self.rel_sub_path = vendor_files_rel_sub_path

        self.vendor_imports = vendor_imports
        self.lib_fixups = lib_fixups
        self.target_enable_checkelf = target_enable_checkelf

    def path(self, file: File):
        return f'{self.files_path}/{file.dst}'


def file_gen_deps_check_elf(ctx: MakefilesCtx, file: File):
    gen_deps = False
    enable_checkelf = False

    if ctx.target_enable_checkelf:
        gen_deps = True
        enable_checkelf = True

    if FileArgs.DISABLE_CHECKELF in file.args:
        enable_checkelf = False

    if FileArgs.DISABLE_DEPS in file.args:
        gen_deps = False
        enable_checkelf = False

    return gen_deps, enable_checkelf


def write_sh_package(ctx: MakefilesCtx, builder: BpBuilder):
    return builder \
        .cls(ModuleClass.SH_BINARIES) \
        .name() \
        .stem() \
        .owner(ctx.vendor) \
        .src(ctx.rel_sub_path) \
        .filename() \
        .specific()


def write_bin_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()
    partition = builder.get_partition()

    if file.ext == '.sh':
        return write_sh_package(ctx, builder)

    gen_deps, enable_checkelf = file_gen_deps_check_elf(ctx, file)
    file_path = ctx.path(file)
    arch, bits, deps = get_file_arch_bits_libs(file_path, gen_deps)

    if arch is None or bits is None:
        return write_sh_package(ctx, builder)

    run_libs_fixup(ctx.lib_fixups, deps, partition)

    builder \
        .cls(ModuleClass.EXECUTABLES) \
        .name() \
        .stem() \
        .owner(ctx.vendor) \
        .target(ctx.rel_sub_path, file, arch, deps) \
        .multilib(bits) \
        .check_elf(enable_checkelf) \
        .no_strip() \
        .prefer() \
        .relative_install_path() \
        .specific()


def write_libs_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()
    files = builder.get_files()
    partition = builder.get_partition()

    gen_deps, enable_check_elf = file_gen_deps_check_elf(ctx, file)
    file_path = ctx.path(file)
    arch, bits, deps = get_file_arch_bits_libs(file_path, gen_deps)
    assert arch is not None
    assert bits is not None
    arches = [arch]
    bitses = [bits]

    run_libs_fixup(ctx.lib_fixups, deps, partition)

    for f in files[1:]:
        f_path = ctx.path(f)
        arch, bits = get_file_arch_bits(f_path)
        assert arch is not None
        assert bits is not None
        arches.append(arch)
        bitses.append(bits)

    builder \
        .cls(ModuleClass.SHARED_LIBRARIES) \
        .name() \
        .stem() \
        .owner(ctx.vendor) \
        .no_strip() \
        .targets(ctx.rel_sub_path, files, arches, deps) \
        .multilibs(bitses) \
        .check_elf(enable_check_elf) \
        .relative_install_path() \
        .prefer() \
        .specific()


def write_rfsa_package(ctx: MakefilesCtx, builder: BpBuilder):
    builder \
        .cls(ModuleClass.RFSA) \
        .name() \
        .filename() \
        .owner(ctx.vendor) \
        .src(ctx.rel_sub_path) \
        .relative_install_path() \
        .specific()


def write_apex_package(ctx: MakefilesCtx, builder: BpBuilder):
    builder \
        .cls(ModuleClass.APEX) \
        .name() \
        .owner(ctx.vendor) \
        .src(ctx.rel_sub_path) \
        .filename() \
        .specific()


def write_app_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()

    # TODO: remove required entries from package_names if actually needed
    # TODO: check if manually specified certificates are needed
    builder \
        .cls(ModuleClass.APPS) \
        .name() \
        .owner(ctx.vendor) \
        .src(ctx.rel_sub_path) \
        .set('overrides', file.overrides, optional=True) \
        .set('required', file.required, optional=True) \
        .signature() \
        .set('dex_preopt', {
            'enabled': False
        }) \
        .set('privileged', file.privileged, optional=True) \
        .specific()


def write_framework_package(ctx: MakefilesCtx, builder: BpBuilder):
    builder \
        .cls(ModuleClass.JAVA_LIBRARIES) \
        .name() \
        .owner(ctx.vendor) \
        .src(ctx.rel_sub_path) \
        .specific()


def write_etc_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()

    if file.ext == '.xml':
        cls = ModuleClass.ETC_XML
    else:
        cls = ModuleClass.ETC

    builder \
        .cls(cls) \
        .name() \
        .owner(ctx.vendor) \
        .src(ctx.rel_sub_path) \
        .set('filename_from_src', True) \
        .sub_dir() \
        .specific()


def write_packages_group(ctx: MakefilesCtx, fn: Callable[[MakefilesCtx, BpBuilder], None],
                         file_tree: FileTree, package_names: List[str], out: TextIO):
    # If the file tree is a common one, the contents might be lists of files
    file_is_list = isinstance(file_tree, CommonFileTree)

    for file in file_tree:
        builder = BpBuilder() \
            .prefix_len(file_tree.parts_prefix_len) \
            .partition(file_tree.parts[0])

        if file_is_list:
            assert isinstance(file, list)
            builder.files(file)
        else:
            builder.file(file)

        fn(ctx, builder)

        builder.write(out)

        package_name = builder.get_package_name()
        package_names.append(package_name)


def write_packages_inclusion(out, package_names):
    if not package_names:
        return

    out.write('\n')
    out.write('PRODUCT_PACKAGES +=')
    for package_name in package_names:
        line = f' \\\n    {package_name}'
        out.write(line)
    out.write('\n')


def write_product_packages(ctx: MakefilesCtx, base_file_tree: FileTree,
                           bp_out: TextIO, mk_out: TextIO):
    package_names = []

    def w(fn, file_tree):
        return write_packages_group(ctx, fn, file_tree, package_names, bp_out)

    def wp(fn, part, sub_dir):
        file_tree = base_file_tree.filter_prefixed([part, sub_dir])

        return w(fn, file_tree)

    # Extract these first so that they don't end up in lib32
    for part in RFSA_PARTITIONS:
        parts = [part, 'lib', 'rfsa']
        lib_rfsa_tree = base_file_tree.filter_prefixed(parts)
        w(write_rfsa_package, lib_rfsa_tree)

    for part in ALL_PARTITIONS:
        parts = [part]

        lib32_tree = base_file_tree.filter_prefixed(parts + ['lib'])
        lib64_tree = base_file_tree.filter_prefixed(parts + ['lib64'])
        lib_common_tree = CommonFileTree.common_files(lib32_tree, lib64_tree)

        fn = write_libs_package
        w(fn, lib_common_tree)
        w(fn, lib32_tree)
        w(fn, lib64_tree)

    for part in APEX_PARTITIONS:
        wp(write_apex_package, part, 'apex')

    for part in ALL_PARTITIONS:
        for sub_dir in ['app', 'priv-app']:
            wp(write_app_package, part, sub_dir)

    for part in ALL_PARTITIONS:
        wp(write_framework_package, part, 'framework')

    for part in ALL_PARTITIONS:
        wp(write_etc_package, part, 'etc')

    for part in ALL_PARTITIONS:
        wp(write_bin_package, part, 'bin')

    assert not list(base_file_tree)

    write_packages_inclusion(mk_out, package_names)


def write_product_copy_files(ctx: MakefilesCtx, file_tree: FileTree,
                             out: TextIO):
    if not file_tree:
        return

    out.write('PRODUCT_COPY_FILES +=')

    for file in file_tree:
        partition = file.parts[0]
        target = f'$(TARGET_COPY_OUT_{partition.upper()})'
        rel_dst = file.dst[len(partition):]
        line = f' \\\n    {ctx.files_rel_path}/{file.dst}:{target}{rel_dst}'

        out.write(line)

    out.write('\n')


def write_symlink_package(file: File, symlink: str,
                          package_names: List[str], out: TextIO):
    symlink_target = f'/{file.dst}'
    # TODO: symlinks outside of partitions?
    part, location = symlink.split('/', 1)
    package_name = symlink.replace('/', '_')

    BpBuilder() \
        .cls(ModuleClass.SYMLINK) \
        .partition(part) \
        .raw_name(package_name) \
        .specific() \
        .set('installed_location', location) \
        .set('symlink_target', symlink_target) \
        .write(out)

    package_names.append(package_name)


def write_symlink_packages(ctx: MakefilesCtx, file_tree: FileTree,
                           bp_out: TextIO, mk_out: TextIO):
    package_names = []

    for file in file_tree:
        for symlink in file.symlinks:
            write_symlink_package(file, symlink, package_names, bp_out)

    write_packages_inclusion(mk_out, package_names)


def write_mk_header(ctx: MakefilesCtx, out: TextIO):
    out.write(
        f'''
# Automatically generated file. DO NOT MODIFY
#
# This file is generated by device/{ctx.vendor}/{ctx.device}/extract-files.py

'''.lstrip()
    )


def write_bp_header(ctx: MakefilesCtx, out: TextIO):
    out.write(
        f'''
// Automatically generated file. DO NOT MODIFY
//
// This file is generated by device/{ctx.vendor}/{ctx.device}/extract-files.py

'''.lstrip()
    )


def write_mk_soong_namespaces(ctx: MakefilesCtx, out: TextIO):
    out.write(
        f'''
PRODUCT_SOONG_NAMESPACES += \\
    vendor/{ctx.vendor}/{ctx.device}
'''.lstrip()
    )


def write_bp_soong_namespaces(ctx: MakefilesCtx, out: TextIO):
    BpBuilder() \
        .rule_name('soong_namespace') \
        .set('imports', ctx.vendor_imports) \
        .write(out)
