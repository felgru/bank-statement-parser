# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Iterable, Iterator
from datetime import date
from typing import TypeVar


T = TypeVar('T')


class PeekableIterator(Iterator[T]):
    def __init__(self, iterable: Iterable[T]):
        self._iter = iter(iterable)
        self._end = False
        self._advance()

    def __next__(self) -> T:
        if self._end:
            raise StopIteration()
        else:
            next_ = self._next
            self._advance()
            return next_

    def peek(self) -> T:
        if self._end:
            raise StopIteration()
        else:
            return self._next

    def _advance(self) -> None:
        try:
            self._next = next(self._iter)
        except StopIteration:
            self._end = True


class UserError(RuntimeError):
    """An error message that should be displayed to the user."""

    def __init__(self, msg: str):
        self.msg = msg


def merge_dateranges(dateranges: list[tuple[date, date]]) -> None:
    dateranges.sort(key=lambda t: t[0])
    for i in reversed(range(len(dateranges)-1)):
        if 0 <= (dateranges[i+1][0] - dateranges[i][1]).days <= 1:
            dateranges[i] = (dateranges[i][0], dateranges[i+1][1])
            dateranges.pop(i+1)
        elif dateranges[i+1] == dateranges[i]:
            dateranges.pop(i+1)
