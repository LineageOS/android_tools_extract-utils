#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from os import path
from typing import List, Optional

from extract_utils.args import parse_args
from extract_utils.extract import ExtractCtx
from extract_utils.module import (
    ExtractUtilsModule,
)
from extract_utils.postprocess import PostprocessCtx
from extract_utils.source import (
    Source,
    create_source,
)
from extract_utils.tools import android_root
from extract_utils.utils import (
    Color,
    color_print,
    get_module_attr,
    import_module,
)


class ExtractUtils:
    def __init__(
        self,
        device_module: ExtractUtilsModule,
        common_module: Optional[ExtractUtilsModule] = None,
    ):
        self.__args = parse_args()

        self.__device_module = device_module
        self.__common_module = common_module

        self.__modules: List[ExtractUtilsModule] = []
        if self.__args.only_target:
            self.__modules.append(self.__device_module)
        elif self.__args.only_common:
            assert self.__common_module is not None
            self.__modules.append(self.__common_module)
        else:
            self.__modules = [self.__device_module]
            if self.__common_module is not None:
                self.__modules.append(self.__common_module)

    @classmethod
    def device_with_common(
        cls,
        device_module: ExtractUtilsModule,
        device_common,
        vendor_common=None,
    ):
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
        module_path = path.join(
            android_root, 'device', vendor, device, 'extract-files.py'
        )

        module = import_module(module_name, module_path)

        return get_module_attr(module, 'module')

    @classmethod
    def get_module(cls, device: str, vendor: str):
        module = cls.import_module(device, vendor)
        assert module is not None
        return module

    def process_modules(self, source: Source):
        all_copied = True
        for module in self.__modules:
            copied = module.process(
                source,
                self.__args.kang,
                self.__args.no_cleanup,
                self.__args.extract_factory,
                self.__args.section,
            )
            if not copied:
                all_copied = False
        return all_copied

    def parse_modules(self):
        for module in self.__modules:
            module.parse(
                self.__args.regenerate,
                self.__args.section,
            )

    def regenerate_modules(self, source: Source):
        for module in self.__modules:
            module.regenerate(
                source,
                self.__args.regenerate,
            )

    def write_updated_proprietary_files(self):
        for module in self.__modules:
            module.write_updated_proprietary_files(
                self.__args.kang,
                self.__args.regenerate,
            )

    def postprocess_modules(self):
        ctx = PostprocessCtx()

        for module in self.__modules:
            for postprocess_fn in module.postprocess_fns:
                postprocess_fn(ctx)

    def write_makefiles(self):
        for module in self.__modules:
            module.write_makefiles(
                self.__args.legacy,
                self.__args.extract_factory,
            )

    def run(self):
        extract_fns = {}
        extract_partitions = set()
        firmware_partitions = set()
        factory_files = set()
        firmware_files = set()

        self.parse_modules()

        if not self.__args.regenerate_makefiles:
            for module in self.__modules:
                extract_fns.update(module.extract_fns)

                extract_partitions.update(
                    module.get_extract_partitions(),
                )
                firmware_partitions.update(
                    module.get_firmware_partitions(),
                )
                firmware_files.update(
                    module.get_firmware_files(),
                )
                factory_files.update(
                    module.get_factory_files(),
                )

            extract_ctx = ExtractCtx(
                self.__args.keep_dump,
                extract_fns,
                list(extract_partitions),
                list(firmware_partitions),
                list(firmware_files),
                list(factory_files),
            )

            with create_source(self.__args.source, extract_ctx) as source:
                self.regenerate_modules(source)

                all_copied = self.process_modules(source)
                if not all_copied:
                    color_print(
                        'Some files failed to process, exiting',
                        color=Color.RED,
                    )
                    return

            self.postprocess_modules()

        self.write_updated_proprietary_files()
        self.write_makefiles()
