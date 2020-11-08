# SPDX-FileCopyrightText: 2019–2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from datetime import date
import os
import subprocess
from typing import List, Optional

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction import AnyTransaction, Transaction, MultiTransaction
from transaction_sanitation import TransactionCleaner, TransactionCleanerRule
from xdg_dirs import getXDGdirectories

class Parser(metaclass=ABCMeta):
    bank_folder: str
    file_extension: str
    account: str
    cleaning_rules: Optional[List[TransactionCleanerRule]] = None

    def __init__(self, infile):
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    @abstractmethod
    def parse(self) -> BankStatement:
        pass

    def clean_up_transactions(self, transactions: List[AnyTransaction]) \
                                                    -> List[AnyTransaction]:
        cleaner = TransactionCleaner(self.xdg,
                                     builtin_rules=self.cleaning_rules)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self, transactions: List[AnyTransaction]) -> None:
        mapper = AccountMapper(self.xdg)
        mapper.map_transactions(transactions)
