#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from contextlib import suppress
from subprocess import SubprocessError
from time import sleep

from extract_utils.utils import run_cmd


def adb_connected():
    output = None
    with suppress(SubprocessError):
        output = run_cmd(['adb', 'get-state'])
    return output == 'device\n'


def init_adb_connection():
    run_cmd(['adb', 'start-server'])
    if not adb_connected():
        print('No device is online. Waiting for one...')
        print('Please connect USB and/or enable USB debugging')
        while not adb_connected():
            sleep(1)

    # TODO: TCP connection

    run_cmd(['adb', 'root'])
    run_cmd(['adb', 'wait-for-device'])
