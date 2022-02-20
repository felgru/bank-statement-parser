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

class Parser(metaclass=ABCMeta):
    bank_folder: str
    file_extension: str
    account: str
    cleaning_rules: Optional[list[TransactionCleanerRule]] = None

    def __init__(self, infile: Path, rules_dir: Optional[Path]):
        self.rules_dir = rules_dir / self.bank_folder \
                         if rules_dir is not None else None

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    @abstractmethod
    def parse(self) -> BankStatement:
        pass

    def clean_up_transactions(self, transactions: Sequence[BaseTransaction]) \
                                                    -> list[BaseTransaction]:
        conf_file = self.rules_dir / 'cleaning_rules.py' \
                    if self.rules_dir is not None else None
        cleaner = TransactionCleaner(conf_file,
                                     builtin_rules=self.cleaning_rules)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self, transactions: list[BaseTransaction]) -> None:
        conf_file = self.rules_dir / 'account_mappings.py' \
                    if self.rules_dir is not None else None
        mapper = AccountMapper(conf_file)
        mapper.map_transactions(transactions)
