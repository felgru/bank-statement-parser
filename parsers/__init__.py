# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections import ChainMap

from . import banks
from . import payslips
from .autoloader import Parsers
from .parser import Parser

class AllParsers(ChainMap[str, dict[str, type[Parser]]]):
    def __init__(self, *maps: Parsers):
        super().__init__(*maps)

    def __str__(self) -> str:
        return '\n'.join(str(map) for map in self.maps)

parsers = AllParsers(banks.parsers, payslips.parsers)

__all__ = ['parsers']
