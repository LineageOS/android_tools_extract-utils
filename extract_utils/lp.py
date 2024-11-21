#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from ctypes import Structure, c_char, c_uint16, c_uint32, c_uint64, sizeof
from enum import Enum
from mmap import ACCESS_READ, MAP_PRIVATE, mmap
from os import path
from typing import BinaryIO, List, Optional, TypeVar

from extract_utils.utils import read_mmap_chunked

LP_PARTITION_RESERVED_BYTES = 4096

LP_METADATA_GEOMETRY_SIZE = 4096
LP_METADATA_GEOMETRY_MAGIC = 0x616C4467

LP_METADATA_HEADER_MAGIC = 0x414C5030

LP_SECTOR_SIZE = 512


class LpMetadataGeometry(Structure):
    _fields_ = [
        # Magic signature
        ('magic', c_uint32),
        # Size of the `LpMetadataGeometry`
        ('struct_size', c_uint32),
        # SHA256 checksum
        ('checksum', c_char * 32),
        # Maximum amount of space a single copy of the metadata can use
        ('metadata_max_size', c_uint32),
        # Number of copies of the metadata to keep
        ('metadata_slot_count', c_uint32),
        # Logical block size
        ('logical_block_size', c_uint32),
    ]
    _pack_ = 1


class LpMetadataTableDescriptor(Structure):
    _fields_ = [
        # Location of the table, relative to the end of the metadata header
        ('offset', c_uint32),
        # Number of entries in the table
        ('num_entries', c_uint32),
        # Size of each entry in the table, in bytes
        ('entry_size', c_uint32),
    ]
    _pack_ = 1


class LpMetadataPartition(Structure):
    _fields_ = [
        # Name of this partition in ASCII characters (36 bytes, padded with nulls if unused)
        ('name', c_char * 36),
        # Attributes for the partition (LP_PARTITION_ATTR_* flags)
        ('attributes', c_uint32),
        # Index of the first extent owned by this partition
        ('first_extent_index', c_uint32),
        # Number of extents in the partition (minimum of 1)
        ('num_extents', c_uint32),
        # Group this partition belongs to
        ('group_index', c_uint32),
    ]
    _pack_ = 1


class LpMetadataExtent(Structure):
    _fields_ = [
        # Length of this extent, in 512-byte sectors
        ('num_sectors', c_uint64),
        # Target type for device-mapper (LP_TARGET_TYPE_* values)
        ('target_type', c_uint32),
        # Contents depend on target_type:
        #   - LINEAR: The sector on the physical partition that this extent maps onto
        #   - ZERO: This field must be 0
        ('target_data', c_uint64),
        # Contents depend on target_type:
        #   - LINEAR: Index into the block devices table
        ('target_source', c_uint32),
    ]
    _pack_ = 1


class LpMetadataHeader(Structure):
    _fields_ = [
        # Four bytes equal to `LP_METADATA_HEADER_MAGIC`
        ('magic', c_uint32),
        # Version number required to read this metadata (major version)
        ('major_version', c_uint16),
        # Minor version (libraries supporting newer features should read older versions)
        ('minor_version', c_uint16),
        # The size of this header struct
        ('header_size', c_uint32),
        # SHA256 checksum of the header, computed as if this field were set to 0
        ('header_checksum', c_char * 32),
        # The total size of all tables, contiguous with no gaps between them
        ('tables_size', c_uint32),
        # SHA256 checksum of all table contents
        ('tables_checksum', c_char * 32),
        # Partition table descriptor
        ('partitions', LpMetadataTableDescriptor),
        # Extent table descriptor
        ('extents', LpMetadataTableDescriptor),
        # Groups table descriptor
        ('groups', LpMetadataTableDescriptor),
        # Block devices table descriptor
        ('block_devices', LpMetadataTableDescriptor),
        # Header flags
        ('flags', c_uint32),
        # Reserved (pad to 256 bytes)
        ('reserved', c_char * 124),
    ]
    _pack_ = 1


class LpImageError(Exception):
    pass


class LpTargetType(Enum):
    LINEAR = 0
    ZERO = 1


def remove_partition_name_suffix(partition_name: bytes, slot=0):
    decoded_name = partition_name.decode('utf-8')

    parts = decoded_name.rsplit('_', 1)
    if len(parts) == 1:
        assert slot == 0
    else:
        slot_char = chr(ord('a') + slot)
        assert parts[1] == slot_char

    return parts[0]


def get_geometry_primary_offset(
    geometry: LpMetadataGeometry, slot_number: int = 0
) -> int:
    base = LP_PARTITION_RESERVED_BYTES + (LP_METADATA_GEOMETRY_SIZE * 2)
    return base + geometry.metadata_max_size * slot_number


def is_partition_for_slot(partition: LpMetadataPartition, slot: int):
    return partition.group_index == slot + 1


T = TypeVar('T', bound=Structure)


class LpImage:
    def __init__(self, i: BinaryIO):
        mm = mmap(i.fileno(), 0, access=ACCESS_READ | MAP_PRIVATE)
        self.__mm = mm

        geometry = LpMetadataGeometry.from_buffer(
            mm, LP_PARTITION_RESERVED_BYTES
        )

        if geometry.magic != LP_METADATA_GEOMETRY_MAGIC:
            raise LpImageError(
                f'Invalid geometry magic {hex(geometry.magic)}, '
                f'expected {hex(LP_METADATA_GEOMETRY_MAGIC)}'
            )

        if geometry.metadata_slot_count == 0:
            raise LpImageError(
                f'Invalid metadata slot count {geometry.metadata_slot_count}, '
                'expected 0'
            )

        if geometry.metadata_max_size % LP_SECTOR_SIZE != 0:
            raise LpImageError(
                f'Invalid metadata max size {geometry.metadata_max_size}, '
                f'expected {LP_SECTOR_SIZE}'
            )

        primary_offset = get_geometry_primary_offset(geometry)
        header = LpMetadataHeader.from_buffer(mm, primary_offset)

        if header.magic != LP_METADATA_HEADER_MAGIC:
            raise LpImageError(
                f'Invalid header magic {hex(header.magic)}, '
                f'expected {hex(LP_METADATA_HEADER_MAGIC)}'
            )

        metadata_end_offset = primary_offset + sizeof(header)

        self.__partitions = self.get_table_descriptor_data(
            metadata_end_offset,
            header.partitions,
            LpMetadataPartition,
        )

        self.__extents = self.get_table_descriptor_data(
            metadata_end_offset,
            header.extents,
            LpMetadataExtent,
        )

    def _write_extent_to_file(self, extent: LpMetadataExtent, o: BinaryIO):
        offset = extent.target_data * LP_SECTOR_SIZE
        size = extent.num_sectors * LP_SECTOR_SIZE

        for data_chunk in read_mmap_chunked(self.__mm, size, offset):
            o.write(data_chunk)

    def get_table_descriptor_data(
        self,
        base_offset: int,
        table_descriptor: LpMetadataTableDescriptor,
        t: type[T],
    ) -> List[T]:
        assert sizeof(t) == table_descriptor.entry_size
        entries = []

        for i in range(table_descriptor.num_entries):
            offset = (
                base_offset
                + table_descriptor.offset
                + i * table_descriptor.entry_size
            )
            entry = t.from_buffer(self.__mm, offset)
            entries.append(entry)

        return entries

    def get_partition_names(self, slot=0):
        names = []
        for partition in self.__partitions:
            if not is_partition_for_slot(partition, slot):
                continue

            unslotted_name = remove_partition_name_suffix(partition.name, slot)
            names.append(unslotted_name)
        return names

    def find_partition(
        self,
        partition_name: str,
        slot=0,
    ) -> Optional[LpMetadataPartition]:
        for partition in self.__partitions:
            if not is_partition_for_slot(partition, slot):
                continue

            unslotted_name = remove_partition_name_suffix(partition.name, slot)
            if unslotted_name != partition_name:
                continue

            return partition

        return None

    def extract_partition(
        self,
        partition_name: str,
        output_file_path: str,
        slot=0,
    ):
        partition = self.find_partition(partition_name, slot)
        if partition is None:
            raise LpImageError(
                f'Failed to find partition with name {partition_name}'
            )

        first_extent = partition.first_extent_index
        last_extent = first_extent + partition.num_extents
        extents = self.__extents[first_extent:last_extent]

        with open(output_file_path, 'wb') as o:
            for extent in extents:
                self._write_extent_to_file(extent, o)


def lpunpack(input_path: str, output_dir: str):
    with open(input_path, 'rb') as i:
        image = LpImage(i)
        partition_names = image.get_partition_names()

        for partition_name in partition_names:
            output_file_path = path.join(output_dir, f'{partition_name}.img')
            print(f'Start extract {partition_name}')
            image.extract_partition(
                partition_name,
                output_file_path,
            )
            print(f'Finish extract {partition_name}')
