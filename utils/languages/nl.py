# SPDX-FileCopyrightText: 2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date


MONTHS = ['Januari',
          'Februari',
          'Maart',
          'April',
          'Mei',
          'Juni',
          'Juli',
          'Augustus',
          'September',
          'Oktober',
          'November',
          'December',
          ]

LOWERCASE_MONTH_LUT = {month.lower(): i
                       for i, month in enumerate(MONTHS, start=1)}


def parse_verbose_date(d: str) -> date:
    day_, month_, year_ = d.split()
    day = int(day_)
    month = LOWERCASE_MONTH_LUT[month_]
    year = int(year_)
    return date(year, month, day)
