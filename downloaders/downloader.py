# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import Generic, Optional, TypeVar

from account_mapping import AccountMapper
from bank_statement import BankStatement


class Downloader(metaclass=ABCMeta):
    name: str
    account: str

    @abstractmethod
    def download(self,
                 rules_dir: Optional[Path],
                 **kwargs) -> BankStatement:
        pass


T = TypeVar('T', bound=Downloader)


class Authenticator(Generic[T], metaclass=ABCMeta):
    @abstractmethod
    def login(self) -> T:
        pass


class PasswordAuthenticator(Authenticator[T]):
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
