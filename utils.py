# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Iterable, Iterator, TypeVar


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
