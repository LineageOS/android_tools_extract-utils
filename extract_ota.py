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

def extract_ota(payload_path, output_dir):
  """Extract OTA payload"""
  payload = update_payload.Payload(payload_path)
  payload.Init()

  new_parts = {}
  for part in payload.manifest.partitions:
    name = part.partition_name
    new_image = os.path.join(output_dir, name + ".img")
    new_parts[name] = new_image

  applier.PayloadApplier(payload).Run(new_parts)


def main():
  parser = argparse.ArgumentParser(
      description="Extract payload.bin from OTA package")
  parser.add_argument(
      "-o",
      dest="output_dir",
      help="Output directory to put all images, current directory by default"
  )
  parser.add_argument(
      "payload",
      help="payload.bin for the OTA package, or a zip of OTA package itself",
      nargs=1)
  args = parser.parse_args()
  print(args)

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
    extract_ota(payload_path, args.output_dir)


if __name__ == '__main__':
  main()
