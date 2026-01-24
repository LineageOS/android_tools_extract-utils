#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import copy
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
    fixups_final: fixups_type[T] = {}

    if fixups is None:
        return fixups_final

    for entries, value in fixups.items():
        if isinstance(entries, str):
            if entries in fixups_final:
                fixups_final[entries].merge(value)
            else:
                fixups_final[entries] = copy.deepcopy(value)
        else:
            assert isinstance(entries, tuple)
            for entry in entries:
                if entry in fixups_final:
                    fixups_final[entry].merge(value)
                else:
                    fixups_final[entry] = copy.deepcopy(value)

    return fixups_final
