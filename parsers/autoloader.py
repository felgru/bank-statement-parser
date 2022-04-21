# SPDX-FileCopyrightText: 2019, 2021–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic loading of parsers."""

from pathlib import Path
from typing import Type, Union

from .parser import Parser

class Parsers(dict[str, dict[str, Type[Parser]]]):
    def __init__(self, category: str, module_path: Union[str, Path]) -> None:
        from importlib import import_module
        import inspect
        import os
        self.category = category
        _, _, filenames = next(os.walk(module_path))
        for f in filenames:
            if f == '__init__.py':
                continue
            mod_name = inspect.getmodulename(os.path.join(module_path, f))
            if mod_name is None:
                continue
            package_name = os.path.basename(module_path)
            # This assumes that the module to import is in a child module
            # of __package__.
            mod = import_module(f'.{package_name}.{mod_name}',
                                __package__)
            for elem_name in dir(mod):
                elem = getattr(mod, elem_name)
                if (inspect.isclass(elem)
                    and getattr(elem, 'bank_folder', None) is not None
                    and getattr(elem, 'file_extension', None) is not None):
                        self.add_format(elem)

    def add_format(self, format_class: Type[Parser]) -> None:
        bank = format_class.bank_folder
        if bank not in self:
            self[bank] = {}
        ext = format_class.file_extension
        self[bank][ext] = format_class

    def __str__(self) -> str:
        return self.category + ':\n' \
               + '\n'.join(f'* {bank}' for bank in sorted(self.keys()))

__all__ = ['Parsers']
