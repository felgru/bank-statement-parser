# SPDX-FileCopyrightText: 2019–2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from datetime import date
from decimal import Decimal
import os
import subprocess
from typing import Iterable, List, Union

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from .parser import Parser
from transaction import Balance, MultiTransaction, Transaction
from transaction_sanitation import TransactionCleaner
from xdg_dirs import getXDGdirectories

class PdfParser(Parser, metaclass=ABCMeta):
    file_extension = '.pdf'

    transactions_start: int
    transactions_end: int
    old_balance: Balance
    new_balance: Balance
    total_credit: Decimal
    total_debit: Decimal

    def __init__(self, pdf_file: str):
        super().__init__(pdf_file)
        self._parse_file(pdf_file)

    def _parse_file(self, pdf_file: str) -> None:
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by poppler-utils on Debian
        pdftext = subprocess.run(['pdftotext', '-fixed', '5', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        # Careful: There's a trailing \f on the last page
        self.pdf_pages = pdftext.split('\f')[:-1]

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    @abstractmethod
    def parse_balances(self) -> None:
        pass

    def parse(self) -> BankStatement:
        self.transactions_text = self.extract_transactions_table()
        self.parse_balances()
        transactions = [t for t in self.generate_transactions(
                                            self.transactions_start,
                                            self.transactions_end)]
        self.check_transactions_consistency(transactions)
        transactions = self.clean_up_transactions(transactions)
        self.map_accounts(transactions)
        return BankStatement(self.account, transactions,
                             self.old_balance, self.new_balance)

    def extract_transactions_table(self) -> str:
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    @abstractmethod
    def extract_table_from_page(self, page: str) -> str: pass

    @abstractmethod
    def generate_transactions(self, start: int, end: int) \
                                    -> Iterable[Transaction]: pass

    def check_transactions_consistency(self,
                                       transactions: List[Transaction]) -> None:
        assert self.old_balance.balance \
               + self.total_credit - self.total_debit \
                == self.new_balance.balance
