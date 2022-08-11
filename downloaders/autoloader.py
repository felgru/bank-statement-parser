# SPDX-FileCopyrightText: 2019, 2021–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic loading of downloaders."""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, Union

from .downloader import Authenticator, Downloader


@dataclass
class Website:
    downloader: type[Downloader]
    authenticators: list[type[Authenticator]]

class Downloaders(dict[str, Website]):
    def __init__(self, module_path: Union[str, Path]) -> None:
        from importlib import import_module
        import inspect

        module_path = Path(module_path)
        downloaders: list[type[Downloader]] = []
        authenticators: dict[type[Downloader],
                             list[type[Authenticator]]] = defaultdict(list)
        for f in module_path.iterdir():
            if f.name == '__init__.py':
                continue
            mod_name = inspect.getmodulename(f)  # type: ignore # Irrespective of what mypy says, we can call getmodulename with a Path.
            if mod_name is None:
                continue
            package_name = module_path.name
            # This assumes that the module to import is a child module
            # of __package__.
            mod = import_module(f'.{mod_name}', __package__)
            for elem_name, elem in inspect.getmembers(mod, inspect.isclass):
                if issubclass(elem, Downloader) and not elem == Downloader:
                    downloaders.append(elem)
                if issubclass(elem, Authenticator):
                    downl = self._downloader_of_authenticator(elem)
                    if inspect.isabstract(elem):
                        # skip abstract base classes
                        continue
                    if isinstance(downl, TypeVar):
                        raise RuntimeError(
                                f'Authenticator {elem.__name__}'
                                ' does not define Downloader type argument.')
                    authenticators[downl] \
                            .append(elem)
        for downloader in downloaders:
            try:
                auths = authenticators.pop(downloader)
            except KeyError:
                raise RuntimeError(f'Downloader {downloader.__name__} has'
                                   ' no associated Authenticator.')
            self.add_downloader(downloader, auths)
        assert not authenticators

    @staticmethod
    def _downloader_of_authenticator(authenticator: type[Authenticator],
                                     ) -> type[Downloader]:
        import typing
        for base in authenticator.__orig_bases__:  # type: ignore # mypy doesn't seem to know __orig_bases__
            args = typing.get_args(base)
            if not args:
                continue
            assert len(args) == 1
            return args[0]
        else:
            raise RuntimeError(f'Authenticator type {authenticator.__name__}'
                               ' has no Donwloader type.')

    def add_downloader(self,
                       downloader: type[Downloader],
                       authenticators: list[type[Authenticator]]) -> None:
        self[downloader.name] = Website(downloader, authenticators)

    def __str__(self) -> str:
        return 'Downloaders:\n' \
               + '\n'.join(f'* {name}' for name in sorted(self.keys()))

__all__ = ['Downloaders']
