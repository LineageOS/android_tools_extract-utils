#
# Copyright (C) 2024 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

from os import path

from file import ModuleClass, startswith_or_contains_path
from bp import BpBuilder

from elf import get_file_arch_bits, get_file_arch_bits_libs


ALL_PARTITIONS = ['', 'system', 'vendor', 'product', 'system_ext', 'odm']
APEX_PARTITIONS = ['', 'system', 'vendor', 'system_ext']
RFSA_PARTITIONS = ['vendor', 'odm']


def sort_filtered_files(d):
    s = sorted(d.items(), key=lambda item: item[0])
    return {k: v for k, v in s}


def _filter_files(files, prefix, cls=None, part=None):
    filtered_files = {}

    for file in files:
        without_prefix = file.remove_prefix(prefix)
        if without_prefix is None:
            continue

        # Cache in the file to avoid passing them everywhere
        if cls is not None:
            file.set_cls(cls)
        if part is not None:
            file.set_part(part)

        filtered_files[without_prefix] = file

    for file in filtered_files.values():
        files.remove(file)

    return sort_filtered_files(filtered_files)


def filter_files(files, prefix, cls=None, part=None):
    filtered_files = _filter_files(files, prefix, cls=cls, part=part)
    return filtered_files.values()


def filter_files_common(files, *prefixes, cls=None, part=None):
    prefixes_filtered_files = [
        _filter_files(files, p, cls=cls, part=part)
        for p in prefixes
    ]

    common_keys = prefixes_filtered_files[0].keys()
    for prefix_filtered_files in prefixes_filtered_files[1:]:
        common_keys = common_keys & prefix_filtered_files.keys()

    common_files = {}
    for key in common_keys:
        common_files[key] = []
        for prefix_filtered_files in prefixes_filtered_files:
            common_files[key].append(prefix_filtered_files[key])
            prefix_filtered_files.pop(key)

    common_files = sort_filtered_files(common_files)

    return [x.values() for x in [common_files] + prefixes_filtered_files]


def write_sh_package(file, **kwargs):
    return BpBuilder('sh_binary', file) \
        .name() \
        .stem() \
        .owner() \
        .src() \
        .filename() \
        .specific()


def populate_lib_mapping(lib_mapping, deps):
    if deps is None or lib_mapping is None:
        return

    for dep in deps:
        lib_mapping[dep] = dep


def write_bin_package(file, lib_mapping=None, **kwargs):
    arch, bits, deps = get_file_arch_bits_libs(file, get_libs=file.gen_deps)

    if file.ext == '.sh' or arch is None:
        return write_sh_package(file)

    # TODO: add back file path if needed
    populate_lib_mapping(lib_mapping, deps)

    return BpBuilder('cc_prebuilt_binary', file) \
        .name() \
        .stem() \
        .owner() \
        .target(file, arch, deps) \
        .multilib(bits) \
        .check_elf() \
        .no_strip() \
        .prefer() \
        .rel_install_path() \
        .specific()


def write_libs_package(files, file_list=False, lib_mapping=None):
    if file_list:
        file = files[0]
    else:
        file = files
        files = [files]

    arch, bits, deps = get_file_arch_bits_libs(file, get_libs=file.gen_deps)
    arches = [arch]
    bitses = [bits]

    for f in files[1:]:
        arch, bits = get_file_arch_bits(f)
        arches.append(arch)
        bitses.append(bits)

    # TODO: add arg for different dependencies across arches
    # TODO: add back file path if needed
    populate_lib_mapping(lib_mapping, deps)

    return BpBuilder('cc_prebuilt_library_shared', file) \
        .name() \
        .stem() \
        .owner() \
        .no_strip() \
        .targets(files, arches, deps) \
        .multilib(bitses) \
        .check_elf() \
        .rel_install_path() \
        .prefer() \
        .specific()


def write_rfsa_package(file, **kwargs):
    return BpBuilder('prebuilt_rfsa', file) \
        .name() \
        .filename() \
        .owner() \
        .src() \
        .rel_install_path() \
        .specific()


def write_apex_package(file, **kwargs):
    return BpBuilder('prebuilt_apex', file) \
        .name() \
        .owner() \
        .src() \
        .filename() \
        .specific()


def write_app_package(file, **kwargs):
    # TODO: remove required entries from package_names if actually needed
    # TODO: check if manually specified certificates are needed
    return BpBuilder('android_app_import', file) \
        .name() \
        .owner() \
        .set('apk', file.rel_path) \
        .set('overrides', file.overrides()) \
        .set('required', file.required()) \
        .signature() \
        .set('dex_preopt', {
            'enabled': False
        }) \
        .set('privileged', file.privileged()) \
        .specific()


def write_framework_package(file, **kwargs):
    return BpBuilder('dex_import', file) \
        .name() \
        .owner() \
        .set('jars', [file.rel_path]) \
        .specific()


def write_etc_package(file, **kwargs):
    if file.ext == '.xml':
        rule_name = 'prebuilt_etc_xml'
    else:
        rule_name = 'prebuilt_etc'

    return BpBuilder(rule_name, file) \
        .name() \
        .owner() \
        .src() \
        .set('filename_from_src', True) \
        .sub_dir() \
        .specific()


cls_fn_map = {
    ModuleClass.SHARED_LIBRARIES: write_libs_package,
    ModuleClass.EXECUTABLES: write_bin_package,
    ModuleClass.RFSA: write_rfsa_package,
    ModuleClass.APEX: write_apex_package,
    ModuleClass.APPS: write_app_package,
    ModuleClass.JAVA_LIBRARIES: write_framework_package,
    ModuleClass.ETC: write_etc_package,
}


def write_packages_group(cls, part, group, package_names, out,
                         file_list=False, lib_fixup=None):
    if cls not in cls_fn_map:
        return

    fn = cls_fn_map[cls]
    lib_mapping = {}
    builders = []

    for files in group:
        builder = fn(files, file_list=file_list,
                     lib_mapping=lib_mapping)
        builders.append(builder)

        file = files
        if file_list:
            file = files[0]

        package_name = file.package_name
        package_names.append(package_name)

    lib_fixup(part, lib_mapping)

    for builder in builders:
        builder.fixup_shared_libs(lib_mapping)
        builder.write(out)


def write_packages_inclusion(out, package_names):
    if not package_names:
        return

    out.write('\n')
    out.write('PRODUCT_PACKAGES +=')
    for package_name in package_names:
        # Continue last line
        out.write(' \\\n')

        # Use spaces to match old output
        # TODO: switch to tabs
        out.write('    ')
        out.write(package_name)

    out.write('\n')


def write_product_packages(files, bp_out, mk_out, lib_fixup=None):
    package_names = []

    def w(cls, part, group, file_list=False):
        return write_packages_group(cls, part, group, package_names,
                                    bp_out, file_list=file_list,
                                    lib_fixup=lib_fixup)

    def wf(cls, part, sub_dir):
        prefix = ''
        if part:
            prefix += part + '/'
        if sub_dir:
            prefix += sub_dir + '/'

        group = filter_files(files, prefix, cls=cls, part=part)

        return w(cls, part, group)

    for part in ALL_PARTITIONS:
        # Extract this first so that it doesn't end up in lib32
        lib_rfsa_group = None
        if part in RFSA_PARTITIONS:
            lib_rfsa_group = filter_files(
                files, f'{part}/lib/rfsa/', cls=cls, part=part)

        cls = ModuleClass.SHARED_LIBRARIES
        lib_common_group, lib32_group, lib64_group = filter_files_common(
            files, f'{part}/lib/', f'{part}/lib64/', cls=cls, part=part)
        w(cls, part, lib_common_group, file_list=True)
        w(cls, part, lib32_group)
        w(cls, part, lib64_group)

        # Add it last to match old output
        # TODO: output it right after filtering it
        if lib_rfsa_group is not None:
            w(ModuleClass.RFSA, part, lib_rfsa_group)

    for part in APEX_PARTITIONS:
        wf(ModuleClass.APEX, part, 'apex')

    for part in ALL_PARTITIONS:
        for sub_dir in ['app', 'priv-app']:
            wf('APPS', part, sub_dir)

    for part in ALL_PARTITIONS:
        wf(ModuleClass.JAVA_LIBRARIES, part, 'framework')

    for part in ALL_PARTITIONS:
        wf(ModuleClass.ETC, part, 'etc')

    for part in ALL_PARTITIONS:
        wf(ModuleClass.EXECUTABLES, part, 'bin')

    write_packages_inclusion(mk_out, package_names)


COPY_FILES_PREFIX_TARGET_MAP = {
    'product/': 'PRODUCT',
    'system/product/': 'PRODUCT',
    'system_ext/': 'SYSTEM_EXT',
    'system/system_ext/': 'SYSTEM_EXT',
    'odm/': 'ODM',
    'vendor/odm/': 'ODM',
    'system/vendor/odm/': 'ODM',
    'vendor/': 'VENDOR',
    'vendor_dlkm/': 'VENDOR_DLKM',
    'system/vendor/': 'VENDOR',
    'system/': 'SYSTEM',
    'recovery/': 'RECOVERY',
    'vendor_ramdisk/': 'VENDOR_RAMDISK',
}


def write_product_copy_files(files, out):
    if not files:
        return

    out.write('PRODUCT_COPY_FILES +=')

    for file in files:
        for prefix, target in COPY_FILES_PREFIX_TARGET_MAP.items():
            without_prefix = file.remove_prefix(prefix)
            if without_prefix is not None:
                break

        # TODO: is this okay?
        if without_prefix is None:
            raise ValueError(f'Failed to find prefix for {file.dst}')

        out.write(' \\\n')
        out.write('    ')
        out.write(file.root_path)
        out.write(':')
        out.write('$(TARGET_COPY_OUT_')
        out.write(target)
        out.write(')')
        out.write('/')
        out.write(without_prefix)

    out.write('\n')


SYMLINK_PART_LIST = ['vendor', 'product', 'system_ext', 'odm']


def write_symlink_package(file, arch, symlink, package_names, out):
    basename = path.basename(symlink)
    root, _ = path.splitext(basename)
    package_name = f'{file.root}_{root}_symlink{arch}'
    symlink_target = f'/{file.dst}'
    without_prefix = None

    for part in SYMLINK_PART_LIST:
        part_path = f'{part}/'

        if symlink.startswith(part_path):
            without_prefix = symlink.removeprefix(part_path)

        if without_prefix is not None:
            break

    if without_prefix is None:
        part = None

    BpBuilder('install_symlink') \
        .raw_name(package_name) \
        .specific_raw(part) \
        .set('installed_location', without_prefix) \
        .set('symlink_target', symlink_target) \
        .write(out)

    package_names.append(package_name)


def write_symlink_packages(files, bp_out, mk_out):
    package_names = []

    # Cache the arch to match old output, even if it is erroneus
    # TODO: remove
    arch = ''
    for file in files:
        if startswith_or_contains_path(file.dst, 'lib64/') \
                or startswith_or_contains_path(file.dst, 'lib/arm64/'):
            arch = 64
        elif startswith_or_contains_path(file.dst, 'lib/'):
            arch = 32

        symlinks = file.symlinks()

        for symlink in symlinks:
            write_symlink_package(file, arch, symlink, package_names, bp_out)

    write_packages_inclusion(mk_out, package_names)
