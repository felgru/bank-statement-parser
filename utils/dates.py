# SPDX-FileCopyrightText: 2022–2024 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta


def merge_dateranges(dateranges: list[tuple[date, date]]) -> None:
    dateranges.sort(key=lambda t: t[0])
    for i in reversed(range(len(dateranges)-1)):
        if 0 <= (dateranges[i+1][0] - dateranges[i][1]).days <= 1:
            dateranges[i] = (dateranges[i][0], dateranges[i+1][1])
            dateranges.pop(i+1)
        elif dateranges[i+1] == dateranges[i]:
            dateranges.pop(i+1)


def parse_date_relative_to(d: str, ref_d: date) -> date:
    try:
        day = int(d[:2])
        month = int(d[3:5])
        year = ref_d.year
        dd = date(year, month, day)
        half_a_year = timedelta(days=356/2)
        diff = dd - ref_d
        if abs(diff) > half_a_year:
            if diff < timedelta(days=0):
                dd = date(year + 1, month, day)
            else:
                dd = date(year - 1, month, day)
        return dd
    except ValueError as e:
        raise ValueError(
            f"Could not parse date {d!r} relative to {ref_d}."
        ) from e


def end_of_month(d: date) -> date:
    if d.month == 12:
        next_month = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_month = d.replace(month=d.month + 1, day=1)
    return next_month - timedelta(days=1)
