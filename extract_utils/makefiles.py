#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os

from typing import List, Protocol, TextIO

from extract_utils.bp_builder import BpBuilder, FileBpBuilder
from extract_utils.elf import (
    get_file_machine_bits,
    get_file_machine_bits_libs,
    remove_libs_so_ending,
)
from extract_utils.file import (
    CommonFileTree,
    File,
    FileArgs,
    FileTree,
    SimpleFileList,
)
from extract_utils.fixups_lib import run_libs_fixup, lib_fixups_type
from extract_utils.utils import file_path_sha1

# TODO: find out if partition-less files are a thing
ALL_PARTITIONS = ['system', 'vendor', 'product', 'system_ext', 'odm']
APEX_PARTITIONS = ['system', 'vendor', 'system_ext']
RFSA_PARTITIONS = ['vendor', 'odm']


class MakefilesCtx:
    def __init__(
        self,
        bp_out: TextIO,
        mk_out: TextIO,
        product_mk_out: TextIO,
        board_config_mk_out: TextIO,
    ):
        self.bp_out = bp_out
        self.product_mk_out = product_mk_out
        self.board_config_mk_out = board_config_mk_out
        self.mk_out = mk_out

    @classmethod
    @contextmanager
    def from_paths(
        cls,
        bp_path: str,
        mk_path: str,
        product_mk_path: str,
        board_config_mk_path: str,
    ):
        file_paths = [
            bp_path,
            mk_path,
            product_mk_path,
            board_config_mk_path,
        ]

        with ExitStack() as stack:
            files = [
                stack.enter_context(open(file_path, 'w'))
                for file_path in file_paths
            ]
            yield MakefilesCtx(*files)


class ProductPackagesCtx:
    def __init__(
        self,
        check_elf: bool,
        vendor: str,
        vendor_prop_path: str,
        vendor_prop_rel_sub_path: str,
        lib_fixups: lib_fixups_type,
    ):
        self.check_elf = check_elf
        self.vendor = vendor
        self.vendor_prop_path = vendor_prop_path
        self.vendor_prop_rel_sub_path = vendor_prop_rel_sub_path
        self.lib_fixups = lib_fixups


class write_package_fn(Protocol):
    def __call__(
        self,
        file: File,
        builder: FileBpBuilder,
        *args,
        **kwargs,
    ) -> str: ...


class write_common_package_fn(Protocol):
    def __call__(
        self,
        files: List[File],
        builder: FileBpBuilder,
        *args,
        **kwargs,
    ) -> str: ...


def file_gen_deps_check_elf(global_check_elf: bool, file: File):
    gen_deps = False
    check_elf = False

    if global_check_elf:
        gen_deps = True
        check_elf = True

    if FileArgs.DISABLE_CHECKELF in file.args:
        check_elf = False

    if FileArgs.DISABLE_DEPS in file.args:
        gen_deps = False
        check_elf = False

    return gen_deps, check_elf


def file_stem_package_name(
    file: File,
    can_have_stem=False,
    any_extension=False,
):
    package_name = file.root
    stem = None

    if any_extension:
        package_name = file.basename

    if can_have_stem:
        if FileArgs.MODULE_SUFFIX in file.args:
            stem = package_name
            module_suffix = file.args[FileArgs.MODULE_SUFFIX]
            assert isinstance(module_suffix, str)
            package_name += module_suffix
        elif FileArgs.MODULE in file.args:
            stem = package_name
            module = file.args[FileArgs.MODULE]
            assert isinstance(module, str)
            package_name = module

    return stem, package_name


def file_subtree_rel_path(file: File, subtree_prefix_len: int) -> str | None:
    remaining = file.dirname[subtree_prefix_len:]
    if not remaining:
        return None

    return remaining


def write_sh_package(file: File, builder: FileBpBuilder):
    stem, package_name = file_stem_package_name(file)

    (
        builder.set_rule_name('sh_binary')
        .name(package_name)
        .stem(stem)
        .owner()
        .src()
        .filename()
        .specific()
    )

    return package_name


def write_elfs_package(
    files: List[File],
    builder: FileBpBuilder,
    ctx: ProductPackagesCtx,
    *args,
    is_bin=False,
    **kwargs,
):
    file = files[0]

    gen_deps, enable_check_elf = file_gen_deps_check_elf(ctx.check_elf, file)
    file_path = f'{ctx.vendor_prop_path}/{file.dst}'
    machine, bits, libs = get_file_machine_bits_libs(file_path, gen_deps)
    deps = remove_libs_so_ending(libs)

    if is_bin and (machine is None or bits is None):
        return write_sh_package(files[0], builder)

    assert machine is not None
    assert bits is not None
    machines = [machine]
    bitses = [bits]

    partition = builder.get_partition()
    deps = run_libs_fixup(ctx.lib_fixups, deps, partition)

    for f in files[1:]:
        f_path = f'{ctx.vendor_prop_path}/{f.dst}'
        machine, bits = get_file_machine_bits(f_path)
        assert machine is not None
        assert bits is not None
        machines.append(machine)
        bitses.append(bits)

    stem, package_name = file_stem_package_name(
        file, can_have_stem=True, any_extension=is_bin
    )

    if is_bin:
        rule_name = 'cc_prebuilt_binary'
    else:
        rule_name = 'cc_prebuilt_library_shared'

    (
        builder.set_rule_name(rule_name)
        .name(package_name)
        .stem(stem)
        .owner()
        .no_strip()
        .targets(files, machines, deps)
        .multilibs(bitses)
        .check_elf(enable_check_elf)
        .relative_install_path()
        .prefer()
        .specific()
    )

    return package_name


def write_lib_package(
    file: File,
    builder: FileBpBuilder,
    ctx: ProductPackagesCtx,
):
    return write_elfs_package(
        [file],
        builder,
        ctx,
    )


def write_libs_package(
    files: List[File],
    builder: FileBpBuilder,
    ctx: ProductPackagesCtx,
):
    return write_elfs_package(
        files,
        builder,
        ctx,
    )


def write_bin_package(
    file: File,
    builder: FileBpBuilder,
    ctx: ProductPackagesCtx,
):
    if file.ext == '.sh':
        return write_sh_package(file, builder)

    return write_elfs_package(
        [file],
        builder,
        ctx,
        is_bin=True,
    )


def write_rfsa_package(file: File, builder: FileBpBuilder):
    _, package_name = file_stem_package_name(file, can_have_stem=True)

    (
        builder.set_rule_name('prebuilt_rfsa')
        .name(package_name)
        .filename()
        .owner()
        .src()
        .relative_install_path()
        .specific()
    )
    return package_name


def write_apex_package(file: File, builder: FileBpBuilder):
    _, package_name = file_stem_package_name(file)

    (
        builder.set_rule_name('prebuilt_apex')
        .name(package_name)
        .owner()
        .src()
        .filename()
        .specific()
    )
    return package_name


def write_app_package(file: File, builder: FileBpBuilder):
    _, package_name = file_stem_package_name(file)

    # TODO: remove required entries from package_names if actually needed
    # TODO: check if manually specified certificates are needed
    (
        builder.set_rule_name('android_app_import')
        .name(package_name)
        .owner()
        .apk()
        .set('overrides', file.overrides, optional=True)
        .set('required', file.required, optional=True)
        .signature()
        .set('dex_preopt', {'enabled': False})
        .set('privileged', file.privileged, optional=True)
        .specific()
    )
    return package_name


def write_framework_package(file: File, builder: FileBpBuilder):
    _, package_name = file_stem_package_name(file)

    (
        builder.set_rule_name('dex_import')
        .name(package_name)
        .owner()
        .jars()
        .specific()
    )

    return package_name


def write_etc_package(file: File, builder: FileBpBuilder):
    if file.ext == '.xml':
        rule_name = 'prebuilt_etc_xml'
    else:
        rule_name = 'prebuilt_etc'

    _, package_name = file_stem_package_name(file, any_extension=True)

    (
        builder.set_rule_name(rule_name)
        .name(package_name)
        .owner()
        .src()
        .set('filename_from_src', True)
        .sub_dir()
        .specific()
    )

    return package_name


def create_builder(
    ctx: ProductPackagesCtx,
    file_tree: FileTree,
    file: File,
):
    return FileBpBuilder(
        file,
        file_tree.parts_prefix_len,
        ctx.vendor_prop_rel_sub_path,
    ).set_owner(ctx.vendor)


def write_common_packages_group(
    ctx: ProductPackagesCtx,
    file_tree: CommonFileTree,
    fn: write_common_package_fn,
    package_names: List[str],
    out: TextIO,
    *args,
    **kwargs,
):
    for files in file_tree:
        builder = create_builder(ctx, file_tree, files[0])
        package_name = fn(files, builder, *args, **kwargs)
        builder.write(out)
        package_names.append(package_name)


def write_packages_group(
    ctx: ProductPackagesCtx,
    file_tree: FileTree,
    fn: write_package_fn,
    package_names: List[str],
    out: TextIO,
    *args,
    **kwargs,
):
    for file in file_tree:
        builder = create_builder(ctx, file_tree, file)
        package_name = fn(file, builder, *args, **kwargs)
        builder.write(out)
        package_names.append(package_name)


def write_packages_inclusion(package_names: List[str], out: TextIO):
    if not package_names:
        return

    out.write('\nPRODUCT_PACKAGES +=')

    for package_name in package_names:
        line = f' \\\n    {package_name}'
        out.write(line)

    out.write('\n')


def write_product_packages(
    ctx: ProductPackagesCtx,
    base_file_tree: FileTree,
    bp_out: TextIO,
    mk_out: TextIO,
):
    package_names = []

    def w(fn: write_package_fn, file_tree: FileTree, *args, **kwargs):
        return write_packages_group(
            ctx,
            file_tree,
            fn,
            package_names,
            bp_out,
            *args,
            **kwargs,
        )

    def wp(fn: write_package_fn, partition: str, sub_dir: str, *args, **kwargs):
        file_tree = base_file_tree.filter_prefixed([partition, sub_dir])

        return w(fn, file_tree, *args, **kwargs)

    # Extract these first so that they don't end up in lib32
    for part in RFSA_PARTITIONS:
        lib_rfsa_tree = base_file_tree.filter_prefixed([part, 'lib', 'rfsa'])
        w(write_rfsa_package, lib_rfsa_tree)

    for part in ALL_PARTITIONS:
        lib32_tree = base_file_tree.filter_prefixed([part, 'lib'])
        lib64_tree = base_file_tree.filter_prefixed([part, 'lib64'])

        lib_common_tree = CommonFileTree.common_files(lib32_tree, lib64_tree)

        write_common_packages_group(
            ctx,
            lib_common_tree,
            write_libs_package,
            package_names,
            bp_out,
            ctx,
        )

        w(write_lib_package, lib32_tree, ctx)
        w(write_lib_package, lib64_tree, ctx)

    for part in APEX_PARTITIONS:
        wp(write_apex_package, part, 'apex')

    for part in ALL_PARTITIONS:
        wp(write_app_package, part, 'app')
        wp(write_app_package, part, 'priv-app')

    for part in ALL_PARTITIONS:
        wp(write_framework_package, part, 'framework')

    for part in ALL_PARTITIONS:
        wp(write_etc_package, part, 'etc')

    for part in ALL_PARTITIONS:
        wp(write_bin_package, part, 'bin', ctx)

    assert not list(base_file_tree)

    write_packages_inclusion(package_names, mk_out)


def write_product_copy_files(rel_path: str, files: SimpleFileList, out: TextIO):
    if not files:
        return

    out.write('\nPRODUCT_COPY_FILES +=')

    for file in files:
        target = f'$(TARGET_COPY_OUT_{file.partition.upper()})'
        # Remove partition from destination, keeping the slash after it
        rel_dst = file.dst[len(file.partition) :]
        line = f' \\\n    {rel_path}/{file.dst}:{target}{rel_dst}'

        out.write(line)

    out.write('\n')


def write_symlink_package(
    file: File,
    symlink: str,
    package_names: List[str],
    out: TextIO,
):
    symlink_target = f'/{file.dst}'
    # TODO: symlinks outside of partitions?
    part, location = symlink.split('/', 1)
    package_name = symlink.replace('/', '_')

    (
        BpBuilder()
        .set_rule_name('install_symlink')
        .set_partition(part)
        .name(package_name)
        .specific()
        .set('installed_location', location)
        .set('symlink_target', symlink_target)
        .write(out)
    )

    package_names.append(package_name)


def write_symlink_packages(
    files: SimpleFileList,
    bp_out: TextIO,
    mk_out: TextIO,
):
    package_names = []

    for file in files:
        symlinks = file.symlinks
        assert isinstance(symlinks, list)

        for symlink in symlinks:
            write_symlink_package(file, symlink, package_names, bp_out)

    write_packages_inclusion(package_names, mk_out)


def write_mk_firmware_ab_partitions(files: SimpleFileList, out: TextIO):
    has_ab = False
    for file in files:
        if FileArgs.AB in file.args:
            has_ab = True
            break

    if not has_ab:
        return

    out.write('\nAB_OTA_PARTITIONS +=')

    for file in files:
        line = f' \\\n    {file.dst}'
        out.write(line)

    out.write('\n')


def write_mk_firmware(
    vendor_path: str,
    rel_sub_path: str,
    files: SimpleFileList,
    out: TextIO,
):
    for file in files:
        file_path = f'{vendor_path}/{rel_sub_path}/{file.dst}'
        hash = file_path_sha1(file_path)

        line = (
            f'\n$(call add-radio-file-sha1-checked,'
            f'{rel_sub_path}/{file.dst},{hash})'
        )
        out.write(line)

    out.write('\n')


AUTO_GENERATED_MESSAGE = 'Automatically generated file. DO NOT MODIFY'


def write_mk_header(out: TextIO):
    out.write(
        f"""
#
# {AUTO_GENERATED_MESSAGE}
#
""".lstrip()
    )


def write_bp_header(out: TextIO):
    out.write(
        f"""
//
// {AUTO_GENERATED_MESSAGE}
//
""".lstrip()
    )


def write_xml_header(out: TextIO):
    out.write(
        f"""
<?xml version="1.0" encoding="utf-8"?>
<!--
    {AUTO_GENERATED_MESSAGE}
-->
""".lstrip()
    )


def write_mk_soong_namespace(path: str, out: TextIO):
    out.write(
        f"""
PRODUCT_SOONG_NAMESPACES += \\
    {path}
"""
    )


def write_bp_soong_namespaces(namespace_imports: List[str], out: TextIO):
    if not namespace_imports:
        return

    (
        BpBuilder()
        .set_rule_name('soong_namespace')
        .set('imports', namespace_imports)
        .write(out)
    )


def write_androidmanifest_rro(
    target_package_name: str,
    partition: str,
    out: TextIO,
):
    write_xml_header(out)

    out.write(
        f"""
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{target_package_name}.{partition}"
    android:versionCode="1"
    android:versionName="1.0">
    <application android:hasCode="false" />
    <overlay
        android:targetPackage="{target_package_name}"
        android:isStatic="true"
        android:priority="0"/>
</manifest>
""".lstrip()
    )


def write_bp_rro(
    package_name: str,
    partition: str,
    out: TextIO,
):
    write_bp_header(out)

    (
        BpBuilder()
        .set_partition(partition)
        .set_rule_name('runtime_resource_overlay')
        .name(package_name)
        .set('theme', package_name)
        .set('sdk_version', 'current')
        .set(
            'aaptflags',
            [
                '--keep-raw-values',
            ],
        )
        .specific()
        .write(out)
    )


def write_rro_package(
    abs_path: str,
    package_name: str,
    target_package_name: str,
    partition: str,
    mk_out: TextIO,
):
    package_path = f'{abs_path}/{package_name}'
    rro_bp_path = f'{package_path}/Android.bp'
    rro_manifest_path = f'{package_path}/AndroidManifest.xml'

    os.makedirs(package_path, exist_ok=True)

    with open(rro_bp_path, 'w') as rro_bp_out:
        write_bp_rro(package_name, target_package_name, rro_bp_out)

    with open(rro_manifest_path, 'w') as rro_manifest_out:
        write_androidmanifest_rro(
            target_package_name,
            partition,
            rro_manifest_out,
        )

    write_packages_inclusion([package_name], mk_out)


def write_mk_guard_begin(name: str, value: str, mk_out: TextIO, invert=False):
    rule = 'ifeq' if not invert else 'ifneq'
    mk_out.write(f'\n{rule} ($({name}),{value})\n')


def write_mk_guard_end(mk_out: TextIO):
    mk_out.write('\nendif\n')
