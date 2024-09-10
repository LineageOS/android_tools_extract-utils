#
# SPDX-FileCopyrightText: 2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from typing import (
    Dict,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

T = TypeVar('T')

fixups_user_type = Dict[Union[str, Tuple[str, ...]], T]
fixups_type = Dict[str, T]


def flatten_fixups(
    fixups: Optional[fixups_user_type[T]],
) -> fixups_type[T]:
    fixups_final: fixups_type = {}

    if fixups is None:
        return fixups_final

    for entries, value in fixups.items():
        if isinstance(entries, str):
            fixups_final[entries] = value
        elif isinstance(entries, tuple):
            for entry in entries:
                fixups_final[entry] = value
        else:
            assert False

    return fixups_final
