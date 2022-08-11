# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction import BaseTransaction, Transaction, MultiTransaction
from transaction_sanitation import TransactionCleaner, TransactionCleanerRule

class Parser(metaclass=ABCMeta):
    bank_folder: str
    file_extension: str
    account: str

    def __init__(self, infile: Path):
        pass

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    @abstractmethod
    def parse(self, rules_dir: Optional[Path]) -> BankStatement:
        pass


class CleaningParser(Parser, metaclass=ABCMeta):
    cleaning_rules: Optional[list[TransactionCleanerRule]] = None

    @abstractmethod
    def parse_raw(self) -> BankStatement:
        pass

    def parse(self, rules_dir: Optional[Path]) -> BankStatement:
        if rules_dir is not None:
            rules_dir = rules_dir / self.bank_folder
        statement = self.parse_raw()
        transactions = self.clean_up_transactions(
                statement.transactions,
                rules_dir)
        self.map_accounts(transactions, rules_dir)
        statement.transactions = transactions
        return statement

    def clean_up_transactions(self,
                              transactions: Sequence[BaseTransaction],
                              rules_dir: Optional[Path],
                              ) -> list[BaseTransaction]:
        conf_file = rules_dir / 'cleaning_rules.py' \
                    if rules_dir is not None else None
        cleaner = TransactionCleaner(conf_file,
                                     builtin_rules=self.cleaning_rules)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self,
                     transactions: list[BaseTransaction],
                     rules_dir: Optional[Path],
                     ) -> None:
        conf_file = rules_dir / 'account_mappings.py' \
                    if rules_dir is not None else None
        mapper = AccountMapper(conf_file)
        mapper.map_transactions(transactions)
