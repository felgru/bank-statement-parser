# SPDX-FileCopyrightText: 2019, 2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Type

from ..parser import Parser

class Parsers(dict[str, dict[str, Type[Parser]]]):
    def __init__(self) -> None:
        from importlib import import_module
        import inspect
        import os
        module_path: str = __path__[0]  # type: ignore # mypy pull request #9454
        _, _, filenames = next(os.walk(module_path))
        for f in filenames:
            if f == '__init__.py':
                continue
            mod_name = inspect.getmodulename(os.path.join(module_path, f))
            if mod_name is None:
                continue
            mod = import_module('.' + mod_name, __name__)
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

parsers = Parsers()

__all__ = ['parsers']
