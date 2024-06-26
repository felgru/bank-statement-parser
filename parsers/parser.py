# SPDX-FileCopyrightText: 2019–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from collections.abc import Sequence
import configparser
from pathlib import Path
from typing import ClassVar, final, Generic, Optional, TypeVar

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction import BaseTransaction, Transaction, MultiTransaction
from transaction_sanitation import TransactionCleaner, TransactionCleanerRule
from utils import UserError


class ParserConfigError(UserError):
    pass


ConfigSelf = TypeVar('ConfigSelf', bound='BaseParserConfig')


class BaseParserConfig(metaclass=ABCMeta):
    bank_folder: ClassVar[str]

    @classmethod
    @abstractmethod
    def load(cls: type[ConfigSelf], config_dir: Optional[Path]) -> ConfigSelf:
        """Load Parser configuration from given directory.

        If `config_dir` is `None`, return the default configuration.
        """
        pass

    @abstractmethod
    def store(self, config_dir: Path) -> None:
        """Write Parser configuration to given directory.

        This is mainly meant to write an expanded accounts.cfg to aid
        in creating a customized one from the default accounts mapping.
        """
        pass


def load_accounts(config_file: Path | None,
                  default_accounts: dict[str, str],
                  name: str,
                  *,
                  config_section_name: str = 'accounts',
                  config_section_is_optional: bool = False,
                  ) -> dict[str, str]:
    if config_file is None or not config_file.exists():
        return dict(default_accounts)
    else:
        config = read_accounts_file(config_file)
        return load_accounts_from_config_parser(
            config,
            config_file,
            default_accounts,
            name,
            config_section_name=config_section_name,
            config_section_is_optional=config_section_is_optional,
        )


def create_config_parser() -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    # Don't convert keys to lower case.
    config.optionxform = lambda optionstr: optionstr  # type: ignore[method-assign]
    return config


def read_accounts_file(config_file: Path) -> configparser.ConfigParser:
    config = create_config_parser()
    config.read(config_file)
    return config


def load_accounts_from_config_parser(
        config: configparser.ConfigParser,
        config_file: Path,
        default_accounts: dict[str, str],
        name: str,
        *,
        config_section_name: str = 'accounts',
        config_section_is_optional: bool = False,
) -> dict[str, str]:
    try:
        accounts_section = config[config_section_name]
    except KeyError:
        if not config_section_is_optional:
            raise ParserConfigError(
                f'{config_file} has no [{config_section_name}] section.'
            ) from None
        accounts = {}
    else:
        accounts = dict(**accounts_section)
    unknown_accounts = set(accounts.keys()).difference(default_accounts.keys())
    if unknown_accounts:
        raise ParserConfigError(
                f'Unknown accounts in {name} configuration: '
                + ', '.join(unknown_accounts))
    return {
        k: accounts.get(k, v)
        for k, v in default_accounts.items()
    }


def store_accounts(config_file: Path,
                   accounts: dict[str, str]) -> None:
    config = create_config_parser()

    write_accounts_section(config, accounts)

    config_file.parent.mkdir(parents=True, exist_ok=True)
    with config_file.open('w') as f:
        config.write(f)


def write_accounts_section(config: configparser.ConfigParser,
                           accounts: dict[str, str],
                           *,
                           section_name: str = 'accounts') -> None:
    config[section_name] = {k: v for k, v in accounts.items()}


GenericConfigSelf = TypeVar('GenericConfigSelf', bound='GenericParserConfig')


class GenericParserConfig(BaseParserConfig):
    bank_name: ClassVar[str]
    DEFAULT_ACCOUNTS: ClassVar[dict[str, str]]

    def __init__(self, accounts: dict[str, str]):
        self.accounts = accounts

    @classmethod
    def load(cls: type[GenericConfigSelf],
             config_dir: Optional[Path]) -> GenericConfigSelf:
        config_file = config_dir / cls.bank_folder / 'accounts.cfg' \
                      if config_dir is not None else None
        accounts = load_accounts(config_file,
                                 cls.DEFAULT_ACCOUNTS,
                                 cls.bank_name)
        return cls(accounts)

    def store(self, config_dir: Path) -> None:
        config_file = config_dir / self.bank_folder / 'accounts.cfg'
        store_accounts(config_file, self.accounts)


CT = TypeVar('CT', bound=BaseParserConfig)


class Parser(Generic[CT], metaclass=ABCMeta):
    file_extension: ClassVar[str]
    autoload: ClassVar[bool] = True

    def __init__(self, infile: Path):
        pass

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    @abstractmethod
    def parse(self, config: CT) -> BankStatement:
        pass

    @final
    @classmethod
    def config_type(cls) -> type[CT]:
        import typing
        for base in cls.__orig_bases__:  # type: ignore[attr-defined] # mypy doesn't seem to know __orig_bases__
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
                        f'Parser type {cls.__name__} does not define a config'
                        ' type. Please add type argument in file'
                        f' {cls_sourcefile}, line {cls_line}.')
            assert issubclass(config_type, BaseParserConfig)
            return config_type
        else:
            raise TypeError(f'Parser type {cls.__name__}'
                            ' does not define a config type.')


CleaningConfigSelf = TypeVar('CleaningConfigSelf',
                             bound='BaseCleaningParserConfig')


class BaseCleaningParserConfig(BaseParserConfig):
    bank_name: ClassVar[str]
    DEFAULT_ACCOUNTS: ClassVar[dict[str, str]]

    def __init__(self,
                 cleaner: TransactionCleaner,
                 mapper: AccountMapper,
                 accounts: dict[str, str]):
        self.cleaner = cleaner
        self.mapper = mapper
        self.accounts = accounts

    @classmethod
    def load(cls: type[CleaningConfigSelf],
             config_dir: Optional[Path]) -> CleaningConfigSelf:
        if config_dir is not None:
            config_dir = config_dir / cls.bank_folder
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
                                 cls.bank_name)
        return cls(cleaner, mapper, accounts)

    def store(self, config_dir: Path) -> None:
        config_file = config_dir / self.bank_folder / 'accounts.cfg'
        store_accounts(config_file, self.accounts)


CCT = TypeVar('CCT', bound=BaseCleaningParserConfig)


class CleaningParser(Parser[CCT], metaclass=ABCMeta):
    cleaning_rules: Optional[list[TransactionCleanerRule]] = None

    @abstractmethod
    def parse_raw(self, accounts: dict[str, str]) -> BankStatement:
        pass

    def parse(self, config: CCT) -> BankStatement:
        statement = self.parse_raw(config.accounts)
        cleaner = config.cleaner.with_builtin_rules(self.cleaning_rules)
        transactions = [cleaner.clean(t) for t in statement.transactions]
        config.mapper.map_transactions(transactions)
        statement.transactions = transactions
        return statement
