#!/usr/bin/env python3
#
# Copyright (C) 2020 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Script to extract payload.bin from an OTA update."""

import argparse
import os
import tempfile
import zipfile

import update_payload
from update_payload import applier

def extract_ota(payload_path, list_partitions, output_dir, partitions):
  """Extract OTA payload"""
  payload = update_payload.Payload(payload_path)
  payload.Init()

  new_parts = {}
  new_part_info = {}
  install_operations = []
  for part in payload.manifest.partitions:
    name = part.partition_name
    if list_partitions:
      print(name)
    if partitions and name not in partitions:
      continue
    new_image = os.path.join(output_dir, name + ".img")
    new_parts[name] = new_image
    new_part_info[name] = part.new_partition_info
    install_operations.append((name, part.operations))

  if not list_partitions:
    for name, operations in install_operations:
      applier.PayloadApplier(payload)._ApplyToPartition(
            operations, name, '%s_install_operations' % name, new_parts[name],
            new_part_info[name])

def main():
  parser = argparse.ArgumentParser(
      description="Extract payload.bin from OTA package")
  parser.add_argument(
      "payload",
      help="payload.bin for the OTA package, or a zip of OTA package itself",
      nargs=1
  )
  parser.add_argument(
      "-l",
      dest="list_partitions",
      help="List partitions, without extracting",
      action='store_true')
  parser.add_argument(
      "-o",
      dest="output_dir",
      help="Output directory to put all images, current directory by default"
  )
  parser.add_argument(
      "-p",
      dest="partitions",
      help="List of partitions to extract, all by default",
      nargs="*"
  )
  args = parser.parse_args()

  # pylint: disable=no-member
  with tempfile.TemporaryDirectory() as tempdir:
    payload_path = args.payload[0]
    if zipfile.is_zipfile(payload_path):
      with zipfile.ZipFile(payload_path, "r") as zfp:
        payload_entry_name = 'payload.bin'
        zfp.extract(payload_entry_name, tempdir)
        payload_path = os.path.join(tempdir, payload_entry_name)
    if args.output_dir is None:
      args.output_dir = "."
    if not os.path.exists(args.output_dir):
      os.makedirs(args.output_dir, exist_ok=True)
    assert os.path.isdir(args.output_dir)
    extract_ota(payload_path, args.list_partitions, args.output_dir, args.partitions)


if __name__ == '__main__':
  main()
