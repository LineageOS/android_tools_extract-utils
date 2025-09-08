#
# SPDX-FileCopyrightText: 2025 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import json
import re
from os import path
from typing import Dict, List, Union, cast

from extract_utils.extract import ExtractCtx, ExtractFn
from extract_utils.makefiles import MakefilesCtx
from extract_utils.module import ExtractUtilsModule, VirtualPropertietaryFile
from extract_utils.tools import avbtool_path
from extract_utils.utils import run_cmd

vbmeta_data_type = Dict[str, Dict[str, Union[str, int, List[str]]]]

VBMETA_DATA_NAME = 'vbmeta_data.json'


algorithm_re = re.compile(r'^Algorithm:\s*(.+)$', re.MULTILINE)
rollback_index_re = re.compile(r'^Rollback Index:\s*(\d+)$', re.MULTILINE)
rollback_location_re = re.compile(
    r'^Rollback Index Location:\s*(\d+)$', re.MULTILINE
)
partition_re = re.compile(r'^\s*Partition Name:\s*(.+)$', re.MULTILINE)


def extract_avb_info(avb_output: str):
    algorithm_match = algorithm_re.search(avb_output)
    rollback_index_match = rollback_index_re.search(avb_output)
    rollback_location_match = rollback_location_re.search(avb_output)

    assert algorithm_match is not None
    assert rollback_index_match is not None
    assert rollback_location_match is not None

    algorithm = str(algorithm_match.group(1))
    rollback_index = int(rollback_index_match.group(1))
    rollback_location = int(rollback_location_match.group(1))
    partitions: List[str] = partition_re.findall(avb_output)

    return algorithm, rollback_index, rollback_location, partitions


def extract_avb_from_paths(
    ctx: ExtractCtx,
    file_paths: List[str],
    work_dir: str,
):
    vbmeta_data: vbmeta_data_type = {}

    for file_path in file_paths:
        file_name = path.basename(file_path)
        avb_name, _ = path.splitext(file_name)

        avb_output = run_cmd(
            [
                avbtool_path,
                'info_image',
                '--image',
                file_path,
            ]
        )

        algorithm, rollback_index, rollback_location, partitions = (
            extract_avb_info(avb_output)
        )

        vbmeta_data[avb_name] = {
            'algorithm': algorithm,
            'rollback_index': rollback_index,
            'rollback_location': rollback_location,
            'partitions': partitions,
        }

    vbmeta_data_path = path.join(work_dir, 'vbmeta_data.json')
    with open(vbmeta_data_path, 'w') as o:
        o.write(json.dumps(vbmeta_data, indent=4))
        o.write('\n')

    processed_paths: List[str] = []
    return processed_paths


class ExtractAvb(ExtractFn):
    def __init__(self, avb_types: List[str]):
        keys: List[str] = []
        for avb_type in avb_types:
            key = f'{avb_type}.img'
            key = re.escape(key)
            keys.append(key)

        combined_key = f'({"|".join(keys)})'

        super().__init__(key=combined_key, paths_fn=extract_avb_from_paths)


class AvbProperietaryFile(VirtualPropertietaryFile):
    def __init__(self):
        file_list_lines = [f'{VBMETA_DATA_NAME};EXTRACT_ONLY']
        super().__init__('AVB', file_list_lines, '')

    def write_makefiles(self, module: ExtractUtilsModule, ctx: MakefilesCtx):
        vendor_path = path.join(
            module.vendor_path,
            self.vendor_rel_sub_path,
        )

        vbmeta_data_path = path.join(vendor_path, 'vbmeta_data.json')
        with open(vbmeta_data_path, 'r') as i:
            data = i.read()
            vbmeta_data = json.loads(data)

        assert isinstance(vbmeta_data, dict)
        vbmeta_data = cast(vbmeta_data_type, vbmeta_data)

        for avb_name, avb_values in vbmeta_data.items():
            assert isinstance(avb_name, str)
            assert isinstance(avb_values, dict)

            algorithm = avb_values['algorithm']
            rollback_index = avb_values['rollback_index']
            rollback_location = avb_values['rollback_location']
            partitions = avb_values['partitions']

            assert isinstance(algorithm, str)
            assert isinstance(rollback_location, int)
            assert isinstance(rollback_index, int)
            assert isinstance(partitions, list)

            prefix = f'BOARD_AVB_{avb_name.upper()}'

            if avb_name not in ['boot', 'recovery']:
                partitions_str = ' '.join(partitions)
                ctx.board_config_mk_out.write(f'{prefix} := {partitions_str}\n')

            ctx.board_config_mk_out.write(
                f'{prefix}_ALGORITHM := {algorithm}\n',
            )
            ctx.board_config_mk_out.write(
                f'{prefix}_ROLLBACK_INDEX := {rollback_index}\n',
            )
            ctx.board_config_mk_out.write(
                f'{prefix}_ROLLBACK_INDEX_LOCATION := {rollback_location}\n',
            )

            ctx.board_config_mk_out.write('\n')
