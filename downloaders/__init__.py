# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .autoloader import Downloaders


downloaders = Downloaders(__path__[0])  # type: ignore # mypy pull request #9454

__all__ = ['downloaders']
