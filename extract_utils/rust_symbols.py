#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import re
from functools import cache
from glob import glob
from os import environ, path
from typing import Dict, List, Mapping, Optional, Set, Tuple

from extract_utils.elf_parser import ELFFile
from extract_utils.tools import (
    llvm_cxxfilt_path,
    llvm_nm_path,
    rust_config_path,
    rust_toolchain_dir,
)
from extract_utils.utils import run_cmd

# Crate name to the Soong module name and std linkage passed as -C metadata.
CrateMetadata = Mapping[str, Tuple[str, Optional[str]]]

SYSROOT_CRATE_METADATA: CrateMetadata = {
    'core': ('libcore.rust_sysroot', None),
    'alloc': ('liballoc.rust_sysroot', None),
    'std': ('libstd', None),
    'hashbrown': ('libhashbrown.rust_sysroot', None),
}
INTERNAL_CRATE = '__rustc'

TARGET = 'aarch64-linux-android'
PROBE_SOURCE = '#![no_std]\npub fn probe() {}\n'

CRATE_ROOT = re.compile(r'Cs[0-9A-Za-z]+_(\d+)_?')
IMPL_PREFIX = re.compile(r'_RNv[MXY](s[0-9A-Za-z]*_)?')


def _encoded_ident(name: str) -> str:
    separator = '_' if name[0] == '_' or name[0].isdigit() else ''
    return f'{len(name)}{separator}{name}'


def _symbols(file_path: str, kind: str) -> List[str]:
    output = run_cmd([llvm_nm_path, '-D', '-j', kind, file_path])
    return [s for s in output.split() if s.startswith('_R')]


def _demangle(symbols: List[str]) -> List[str]:
    return run_cmd([llvm_cxxfilt_path], data='\n'.join(symbols)).splitlines()


@cache
def _rust_dir() -> str:
    version = environ.get('RUST_PREBUILTS_VERSION')
    if not version:
        with open(rust_config_path, encoding='utf-8') as f:
            match = re.search(r'RustDefaultVersion\s*=\s*"([^"]+)"', f.read())
        if match is None:
            raise ValueError(f'No RustDefaultVersion in {rust_config_path}')
        version = match.group(1)

    rust_dir = path.join(rust_toolchain_dir, 'linux-x86', version)
    if not path.isdir(rust_dir):
        raise ValueError(f'Rust toolchain {rust_dir} does not exist')

    return rust_dir


@cache
def _crate_disambiguator(
    crate: str,
    module: str,
    linkage: Optional[str],
) -> str:
    cmd = [
        path.join(_rust_dir(), 'bin/rustc'),
        f'--target={TARGET}',
        '--crate-type=rlib',
        f'--crate-name={crate}',
        '--emit=llvm-ir',
        f'-Cmetadata={module}',
    ]
    if linkage is not None:
        cmd.append(f'-Cmetadata={linkage}')

    ir = run_cmd(cmd + ['-o', '-', '-'], data=PROBE_SOURCE)

    ident = re.escape(_encoded_ident(crate))
    match = re.search(rf'_RNvC(s[0-9A-Za-z]+_){ident}5probe', ir)
    if match is None:
        raise ValueError(f'rustc probe for {crate} emitted no symbol')

    return match.group(1)


@cache
def _toolchain_libstd() -> str:
    lib_dir = path.join(_rust_dir(), 'lib/rustlib', TARGET, 'lib')
    libs = glob(path.join(lib_dir, 'libstd-*.so'))
    if len(libs) != 1:
        raise ValueError(f'Expected one libstd in {lib_dir}, found {libs}')

    return libs[0]


# The toolchain libstd is built from the same sources as the in-tree
# std, core and alloc, so its impl disambiguators are the in-tree ones.
# Its __rustc symbols are hashed from the rustc version alone, so they
# already carry the in-tree names.
@cache
def _toolchain_reference() -> Dict[str, List[str]]:
    symbols = _symbols(_toolchain_libstd(), '--defined-only')
    reference: Dict[str, List[str]] = {}
    for symbol, demangled in zip(symbols, _demangle(symbols)):
        reference.setdefault(demangled, []).append(symbol)

    return reference


def _generic_shape(demangled: str) -> str:
    erased = re.sub(r'(?<=\w)<[^<>]*>', '#', demangled)
    return demangled if erased == demangled else _generic_shape(erased)


# An impl disambiguator belongs to the impl, not to an instantiation, so
# a std, core or alloc generic instantiated over another crate's types
# takes it from any toolchain libstd instantiation of the same method.
@cache
def _toolchain_impls() -> Dict[str, Set[str]]:
    impls: Dict[str, Set[str]] = {}
    for demangled, symbols in _toolchain_reference().items():
        for symbol in symbols:
            match = IMPL_PREFIX.match(symbol)
            if match is not None:
                impls.setdefault(_generic_shape(demangled), set()).add(match[0])

    return impls


def _with_toolchain_impl(symbol: str, demangled: str) -> str:
    match = IMPL_PREFIX.match(symbol)
    impls = _toolchain_impls().get(_generic_shape(demangled), set())
    if match is None or len(impls) != 1:
        return symbol

    return next(iter(impls)) + symbol[match.end() :]


def _imported_crates(imports: List[str]) -> Set[str]:
    return {
        symbol[m.end() : m.end() + int(m[1])]
        for symbol in imports
        for m in CRATE_ROOT.finditer(symbol)
    }


# Soong names a Rust library lib<crate>, or lib<crate>_rust when a C
# library already has that name, and a dylib dependency is always the
# dylib-std variant.
def _dylib_crate_metadata(
    file_path: str,
    crates: Set[str],
) -> CrateMetadata:
    with open(file_path, 'rb') as f:
        dylibs = {
            lib.removesuffix('.dylib.so')
            for lib in ELFFile(f).get_libs() or []
            if lib.endswith('.dylib.so')
        }

    metadata: Dict[str, Tuple[str, Optional[str]]] = {}
    for crate in sorted(crates):
        for module in (f'lib{crate}', f'lib{crate}_rust'):
            if module in dylibs:
                metadata[crate] = (module, 'dylib-std')
                break
        else:
            raise ValueError(
                f'{file_path}: no NEEDED Rust dylib for crate {crate} in '
                f'{sorted(dylibs)}, pass it as fix_rust_symbols('
                f"{{'{crate}': ('<module>', 'dylib-std')}})"
            )

    return metadata


def rust_symbol_renames(
    file_path: str,
    crate_metadata: Optional[CrateMetadata] = None,
) -> Dict[str, str]:
    imports = _symbols(file_path, '--undefined-only')
    overrides = {**SYSROOT_CRATE_METADATA, **(crate_metadata or {})}
    crates = _imported_crates(imports) - overrides.keys() - {INTERNAL_CRATE}
    metadata = {**_dylib_crate_metadata(file_path, crates), **overrides}

    disambiguators = {
        _encoded_ident(crate): _crate_disambiguator(crate, module, linkage)
        for crate, (module, linkage) in metadata.items()
    }
    alternatives = '|'.join(re.escape(ident) for ident in disambiguators)
    pattern = re.compile(rf'Cs[0-9A-Za-z]+_(?=({alternatives}))')
    reference = _toolchain_reference()

    demangled_imports = dict(zip(imports, _demangle(imports)))
    renames: Dict[str, str] = {}
    for symbol, demangled in demangled_imports.items():
        candidates = reference.get(demangled, [])
        if len(candidates) == 1:
            source = candidates[0]
        else:
            source = _with_toolchain_impl(symbol, demangled)

        renamed = pattern.sub(lambda m: 'C' + disambiguators[m[1]], source)
        if renamed != symbol:
            renames[symbol] = renamed

    for symbol, demangled in zip(renames, _demangle(list(renames.values()))):
        if demangled != demangled_imports[symbol]:
            raise ValueError(
                f'{file_path}: renaming {symbol} to {renames[symbol]} '
                f'changes its demangled name to {demangled}'
            )

    return renames
