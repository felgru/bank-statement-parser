# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction import BaseTransaction, Transaction, MultiTransaction
from transaction_sanitation import TransactionCleaner, TransactionCleanerRule
from xdg_dirs import getXDGdirectories

class Parser(metaclass=ABCMeta):
    bank_folder: str
    file_extension: str
    account: str
    cleaning_rules: Optional[list[TransactionCleanerRule]] = None

    def __init__(self, infile: Path):
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    @abstractmethod
    def parse(self) -> BankStatement:
        pass

    def clean_up_transactions(self, transactions: Sequence[BaseTransaction]) \
                                                    -> list[BaseTransaction]:
        conf_file = self.xdg['config'] / 'cleaning_rules.py'
        cleaner = TransactionCleaner(conf_file,
                                     builtin_rules=self.cleaning_rules)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self, transactions: list[BaseTransaction]) -> None:
        conf_file = self.xdg['config'] / 'account_mappings.py'
        mapper = AccountMapper(conf_file)
        mapper.map_transactions(transactions)
