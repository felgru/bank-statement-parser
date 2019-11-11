# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
import os
import subprocess

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction_sanitation import TransactionCleaner
from xdg_dirs import getXDGdirectories

class Parser:
    bank_folder = None
    file_extension = None
    cleaning_rules = None

    def __init__(self, infile):
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    def parse_metadata(self) -> BankStatementMetadata:
        pass

    def parse(self) -> BankStatement:
        pass

    def clean_up_transactions(self, transactions):
        cleaner = TransactionCleaner(self.xdg,
                                     builtin_rules=self.cleaning_rules)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self, transactions):
        mapper = AccountMapper(self.xdg)
        mapper.map_transactions(transactions)
