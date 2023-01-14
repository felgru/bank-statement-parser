# SPDX-FileCopyrightText: 2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date


MONTHS = ['janvier',
          'février',
          'mars',
          'avril',
          'mai',
          'juin',
          'juillet',
          'août',
          'septembre',
          'octobre',
          'novembre',
          'décembre',
          ]

MONTH_LUT = {month: i for i, month in enumerate(MONTHS, start=1)}
UPPERCASE_MONTH_LUT = {month.upper(): i
                       for i, month in enumerate(MONTHS, start=1)}


def parse_verbose_date(d: str, *, uppercase: bool = False) -> date:
    day_, month_, year_ = d.split()
    day = int(day_)
    lut = UPPERCASE_MONTH_LUT if uppercase else MONTH_LUT
    month = lut[month_]
    year = int(year_)
    return date(year, month, day)
