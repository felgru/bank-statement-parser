# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from abc import ABCMeta, abstractmethod
from getpass import getpass
from pathlib import Path
from typing import ClassVar, final, Generic, Optional, TypeVar

from account_mapping import AccountMapper
from bank_statement import BankStatement
from parsers.parser import load_accounts
from transaction_sanitation import TransactionCleaner


ConfigSelf = TypeVar('ConfigSelf', bound='BaseDownloaderConfig')


class BaseDownloaderConfig(metaclass=ABCMeta):
    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def load(cls: type[ConfigSelf], config_dir: Optional[Path]) -> ConfigSelf:
        """Load Downloader configuration from given directory.

        If `config_dir` is `None`, return the default configuration.
        """
        pass


GenericConfigSelf = TypeVar('GenericConfigSelf', bound='GenericDownloaderConfig')


class GenericDownloaderConfig(BaseDownloaderConfig):
    display_name: ClassVar[str]
    DEFAULT_ACCOUNTS: ClassVar[dict[str, str]]

    def __init__(self,
                 cleaner: TransactionCleaner,
                 mapper: AccountMapper,
                 accounts: dict[str, str]):
        self.cleaner = cleaner
        self.mapper = mapper
        self.accounts = accounts

    @classmethod
    def load(cls: type[GenericConfigSelf],
             config_dir: Optional[Path]) -> GenericConfigSelf:
        if config_dir is not None:
            config_dir = config_dir / cls.name
        cleaning_rules = config_dir / 'cleaning_rules.py' \
                         if config_dir is not None else None
        cleaner = TransactionCleaner.from_rules_file(cleaning_rules)
        account_mappings = config_dir / 'account_mappings.py' \
                           if config_dir is not None else None
        mapper = AccountMapper(account_mappings)
        config_file = config_dir / 'accounts.cfg' \
                      if config_dir is not None else None
        accounts = load_accounts(config_file,
                                 cls.DEFAULT_ACCOUNTS,
                                 cls.display_name)
        return cls(cleaner, mapper, accounts)


CT = TypeVar('CT', bound=BaseDownloaderConfig)


class Downloader(Generic[CT], metaclass=ABCMeta):
    @abstractmethod
    def download(self,
                 config: CT,
                 **kwargs) -> BankStatement:
        pass

    def print_current_balance(self) -> None:
        pass

    @final
    @classmethod
    def config_type(cls) -> type[CT]:
        import typing
        for base in cls.__orig_bases__:  # type: ignore # mypy doesn't seem to know __orig_bases__
            args = typing.get_args(base)
            if not args:
                continue
            assert len(args) == 1
            config_type = args[0]
            if isinstance(config_type, TypeVar):
                import inspect
                cls_sourcefile = inspect.getsourcefile(cls)
                cls_line = inspect.getsourcelines(cls)[1]
                raise TypeError(
                        f'Downloader type {cls.__name__} does not define a'
                        ' config type. Please add type argument in file'
                        f' {cls_sourcefile}, line {cls_line}.')
            assert issubclass(config_type, BaseDownloaderConfig)
            return config_type
        else:
            raise TypeError(f'Downloader type {cls.__name__}'
                            ' does not define a config type.')


T = TypeVar('T', bound=Downloader)


class Authenticator(Generic[T], metaclass=ABCMeta):
    @abstractmethod
    def login(self) -> T:
        pass


class PasswordAuthenticator(Authenticator[T]):
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password


class InteractiveAuthenticator(Authenticator[T]):
    def __init__(self) -> None:
        pass


def authenticate_interactively(auth_class: type[Authenticator[T]]) -> T:
    auth: Authenticator[T]
    if issubclass(auth_class, InteractiveAuthenticator):
        auth = auth_class()
    elif issubclass(auth_class, PasswordAuthenticator):
        username = input('Username: ')
        password = getpass('Password: ')
        auth = auth_class(username, password)
    else:
        raise TypeError(f'Unknown Authenticator type: {auth_class.__name__}.')
    return auth.login()
