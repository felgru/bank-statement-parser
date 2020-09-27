# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
import os
import subprocess

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from .parser import Parser
from transaction_sanitation import TransactionCleaner
from xdg_dirs import getXDGdirectories

class PdfParser(Parser):
    file_extension = '.pdf'

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        self._parse_file(pdf_file)

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by poppler-utils on Debian
        pdftext = subprocess.run(['pdftotext', '-fixed', '5', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        # Careful: There's a trailing \f on the last page
        self.pdf_pages = pdftext.split('\f')[:-1]

    def parse_metadata(self) -> BankStatementMetadata:
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

    def extract_transactions_table(self):
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def check_transactions_consistency(self, transactions):
        assert self.old_balance.balance \
               + self.total_credit - self.total_debit \
                == self.new_balance.balance
