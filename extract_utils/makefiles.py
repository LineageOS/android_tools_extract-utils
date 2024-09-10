#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from typing import Callable, List, Optional, TextIO

from extract_utils.bp_builder import BpBuilder, ModuleClass
from extract_utils.elf import (
    get_file_arch_bits,
    get_file_arch_bits_libs,
    remove_libs_so_ending,
)
from extract_utils.file import File, FileArgs, FileTree
from extract_utils.fixups_lib import lib_fixups_type, run_libs_fixup
from extract_utils.utils import file_path_sha1

# TODO: find out if partition-less files are a thing
ALL_PARTITIONS = ['system', 'vendor', 'product', 'system_ext', 'odm']
APEX_PARTITIONS = ['system', 'vendor', 'system_ext']
RFSA_PARTITIONS = ['vendor', 'odm']


class MakefilesCtx:
    def __init__(
        self,
        vendor: str,
        abs_path: str,
        rel_path: str,
        rel_sub_path: str,
        namespace_imports: List[str],
        lib_fixups: lib_fixups_type,
        check_elf: bool,
    ):
        self.vendor = vendor
        self.rel_path = rel_path
        self.rel_sub_path = rel_sub_path

        self.namespace_imports = namespace_imports
        self.lib_fixups = lib_fixups
        self.check_elf = check_elf

        self._abs_path = abs_path

    def path(self, file: File):
        return f'{self._abs_path}/{file.dst}'


def file_gen_deps_check_elf(ctx: MakefilesCtx, file: File):
    gen_deps = False
    enable_checkelf = False

    if ctx.check_elf:
        gen_deps = True
        enable_checkelf = True

    if FileArgs.DISABLE_CHECKELF in file.args:
        enable_checkelf = False

    if FileArgs.DISABLE_DEPS in file.args:
        gen_deps = False
        enable_checkelf = False

    return gen_deps, enable_checkelf


def write_sh_package(ctx: MakefilesCtx, builder: BpBuilder):
    (
        builder.cls(ModuleClass.SH_BINARIES)
        .name()
        .stem()
        .owner(ctx.vendor)
        .src(ctx.rel_sub_path)
        .filename()
        .specific()
    )


def write_bin_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()
    partition = builder.get_partition()

    if file.ext == '.sh':
        write_sh_package(ctx, builder)
        return

    gen_deps, enable_checkelf = file_gen_deps_check_elf(ctx, file)
    file_path = ctx.path(file)
    arch, bits, libs = get_file_arch_bits_libs(file_path, gen_deps)
    deps = remove_libs_so_ending(libs)

    if arch is None or bits is None:
        write_sh_package(ctx, builder)
        return

    deps = run_libs_fixup(ctx.lib_fixups, deps, partition)

    (
        builder.cls(ModuleClass.EXECUTABLES)
        .name()
        .stem()
        .owner(ctx.vendor)
        .target(ctx.rel_sub_path, file, arch, deps)
        .multilib(bits)
        .check_elf(enable_checkelf)
        .no_strip()
        .prefer()
        .relative_install_path()
        .specific()
    )


def write_libs_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()
    files = builder.get_files()
    partition = builder.get_partition()

    gen_deps, enable_check_elf = file_gen_deps_check_elf(ctx, file)
    file_path = ctx.path(file)
    arch, bits, libs = get_file_arch_bits_libs(file_path, gen_deps)
    deps = remove_libs_so_ending(libs)
    assert arch is not None
    assert bits is not None
    arches = [arch]
    bitses = [bits]

    deps = run_libs_fixup(ctx.lib_fixups, deps, partition)

    for f in files[1:]:
        f_path = ctx.path(f)
        arch, bits = get_file_arch_bits(f_path)
        assert arch is not None
        assert bits is not None
        arches.append(arch)
        bitses.append(bits)

    (
        builder.cls(ModuleClass.SHARED_LIBRARIES)
        .name()
        .stem()
        .owner(ctx.vendor)
        .no_strip()
        .targets(ctx.rel_sub_path, files, arches, deps)
        .multilibs(bitses)
        .check_elf(enable_check_elf)
        .relative_install_path()
        .prefer()
        .specific()
    )


def write_rfsa_package(ctx: MakefilesCtx, builder: BpBuilder):
    (
        builder.cls(ModuleClass.RFSA)
        .name()
        .filename()
        .owner(ctx.vendor)
        .src(ctx.rel_sub_path)
        .relative_install_path()
        .specific()
    )


def write_apex_package(ctx: MakefilesCtx, builder: BpBuilder):
    (
        builder.cls(ModuleClass.APEX)
        .name()
        .owner(ctx.vendor)
        .src(ctx.rel_sub_path)
        .filename()
        .specific()
    )


def write_app_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()

    # TODO: remove required entries from package_names if actually needed
    # TODO: check if manually specified certificates are needed
    (
        builder.cls(ModuleClass.APPS)
        .name()
        .owner(ctx.vendor)
        .src(ctx.rel_sub_path)
        .set('overrides', file.overrides, optional=True)
        .set('required', file.required, optional=True)
        .signature()
        .set('dex_preopt', {'enabled': False})
        .set('privileged', file.privileged, optional=True)
        .specific()
    )


def write_framework_package(ctx: MakefilesCtx, builder: BpBuilder):
    (
        builder.cls(ModuleClass.JAVA_LIBRARIES)
        .name()
        .owner(ctx.vendor)
        .src(ctx.rel_sub_path)
        .specific()
    )


def write_etc_package(ctx: MakefilesCtx, builder: BpBuilder):
    file = builder.get_file()

    if file.ext == '.xml':
        cls = ModuleClass.ETC_XML
    else:
        cls = ModuleClass.ETC

    (
        builder.cls(cls)
        .name()
        .owner(ctx.vendor)
        .src(ctx.rel_sub_path)
        .set('filename_from_src', True)
        .sub_dir()
        .specific()
    )


def write_packages_group(
    ctx: MakefilesCtx,
    fn: Callable[[MakefilesCtx, BpBuilder], None],
    file_tree: FileTree,
    package_names: List[str],
    out: TextIO,
):
    for file in file_tree:
        builder = (
            BpBuilder()
            .prefix_len(file_tree.parts_prefix_len)
            .partition(file_tree.parts[0])
        )

        if isinstance(file, list):
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

    out.write('\nPRODUCT_PACKAGES +=')

    for package_name in package_names:
        line = f' \\\n    {package_name}'
        out.write(line)

    out.write('\n')


def write_product_packages(
    ctx: MakefilesCtx, base_file_tree: FileTree, bp_out: TextIO, mk_out: TextIO
):
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
        lib_common_tree = FileTree.common_files(lib32_tree, lib64_tree)

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


def write_product_copy_files(
    ctx: MakefilesCtx, file_tree: FileTree, out: TextIO
):
    if not file_tree:
        return

    out.write('\nPRODUCT_COPY_FILES +=')

    for file in file_tree:
        assert isinstance(file, File)

        partition = file.parts[0]
        target = f'$(TARGET_COPY_OUT_{partition.upper()})'
        rel_dst = file.dst[len(partition) :]
        line = f' \\\n    {ctx.rel_path}/{file.dst}:{target}{rel_dst}'

        out.write(line)

    out.write('\n')


def write_symlink_package(
    file: File, symlink: str, package_names: List[str], out: TextIO
):
    symlink_target = f'/{file.dst}'
    # TODO: symlinks outside of partitions?
    part, location = symlink.split('/', 1)
    package_name = symlink.replace('/', '_')

    (
        BpBuilder()
        .cls(ModuleClass.SYMLINK)
        .partition(part)
        .raw_name(package_name)
        .specific()
        .set('installed_location', location)
        .set('symlink_target', symlink_target)
        .write(out)
    )

    package_names.append(package_name)


def write_symlink_packages(
    ctx: MakefilesCtx, file_tree: FileTree, bp_out: TextIO, mk_out: TextIO
):
    package_names = []

    for file in file_tree:
        assert isinstance(file, File)
        symlinks = file.symlinks
        assert isinstance(symlinks, list)

        for symlink in symlinks:
            write_symlink_package(file, symlink, package_names, bp_out)

    write_packages_inclusion(mk_out, package_names)


def write_mk_firmware_ab_partitions(
    ctx: MakefilesCtx,
    file_tree: FileTree,
    out: TextIO,
):
    has_ab = False
    for file in file_tree:
        assert isinstance(file, File)
        if FileArgs.AB in file.args:
            has_ab = True
            break

    if not has_ab:
        return

    out.write('\nAB_OTA_PARTITIONS +=')

    for file in file_tree:
        assert isinstance(file, File)
        line = f' \\\n    {file.dst}'
        out.write(line)

    out.write('\n')


def write_mk_firmware(
    ctx: MakefilesCtx,
    file_tree: FileTree,
    out: TextIO,
):
    for file in file_tree:
        assert isinstance(file, File)
        file_path = ctx.path(file)
        hash = file_path_sha1(file_path)

        line = (
            f'\n$(call add-radio-file-sha1-checked,'
            f'{ctx.rel_sub_path}/{file.dst},{hash})'
        )
        out.write(line)


AUTO_GENERATED_MESSAGE = 'Automatically generated file. DO NOT MODIFY'


def write_mk_header(ctx: MakefilesCtx, out: TextIO):
    out.write(
        f"""
#
# {AUTO_GENERATED_MESSAGE}
#
""".lstrip()
    )


def write_bp_header(ctx: MakefilesCtx, out: TextIO):
    out.write(
        f"""
//
// {AUTO_GENERATED_MESSAGE}
//
""".lstrip()
    )


def write_mk_soong_namespaces(ctx: MakefilesCtx, out: TextIO):
    out.write(
        f"""
PRODUCT_SOONG_NAMESPACES += \\
    {ctx.rel_path}
""".lstrip()
    )


def write_bp_soong_namespaces(ctx: MakefilesCtx, out: TextIO):
    if not ctx.namespace_imports:
        return

    (
        BpBuilder()
        .rule_name('soong_namespace')
        .set('imports', ctx.namespace_imports)
        .write(out)
    )
