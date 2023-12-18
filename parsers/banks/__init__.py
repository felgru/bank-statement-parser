# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..autoloader import Parsers


parsers = Parsers('Banks', __path__[0])

__all__ = ['parsers']
