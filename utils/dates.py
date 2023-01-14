# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date


def merge_dateranges(dateranges: list[tuple[date, date]]) -> None:
    dateranges.sort(key=lambda t: t[0])
    for i in reversed(range(len(dateranges)-1)):
        if 0 <= (dateranges[i+1][0] - dateranges[i][1]).days <= 1:
            dateranges[i] = (dateranges[i][0], dateranges[i+1][1])
            dateranges.pop(i+1)
        elif dateranges[i+1] == dateranges[i]:
            dateranges.pop(i+1)
