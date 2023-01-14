# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date

from .dates import merge_dateranges


def test_merge_overlapping_dateranges() -> None:
    dateranges = [(date(2022, 12, 1), date(2022, 12, 15)),
                  (date(2022, 12, 15), date(2022, 12, 31))]
    merge_dateranges(dateranges)
    assert dateranges == [(date(2022, 12, 1), date(2022, 12, 31))]


def test_merge_unsorted_overlapping_dateranges() -> None:
    dateranges = [(date(2022, 12, 15), date(2022, 12, 31)),
                  (date(2022, 12, 1), date(2022, 12, 15))]
    merge_dateranges(dateranges)
    assert dateranges == [(date(2022, 12, 1), date(2022, 12, 31))]


def test_merge_adjacent_dateranges() -> None:
    dateranges = [(date(2022, 12, 1), date(2022, 12, 15)),
                  (date(2022, 12, 16), date(2022, 12, 31))]
    merge_dateranges(dateranges)
    assert dateranges == [(date(2022, 12, 1), date(2022, 12, 31))]


def test_merge_identical_dateranges() -> None:
    dateranges = [(date(2022, 12, 1), date(2022, 12, 31)),
                  (date(2022, 12, 1), date(2022, 12, 31))]
    merge_dateranges(dateranges)
    assert dateranges == [(date(2022, 12, 1), date(2022, 12, 31))]


def test_dont_merge_seperated_dateranges() -> None:
    dateranges = [(date(2022, 12, 1), date(2022, 12, 14)),
                  (date(2022, 12, 16), date(2022, 12, 31))]
    merge_dateranges(dateranges)
    assert dateranges == [(date(2022, 12, 1), date(2022, 12, 14)),
                          (date(2022, 12, 16), date(2022, 12, 31))]
