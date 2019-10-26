from datetime import date
import os
import subprocess

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction_sanitation import TransactionCleaner
from xdg_dirs import getXDGdirectories

class PdfParser:
    bank_folder = None

    def __init__(self, pdf_file):
        self.pdf_pages = self._parse_file(pdf_file)
        self.transactions_text = self.extract_transactions_table()
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    def extract_transactions_table(self):
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext', '-fixed', '5', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        pdf_pages = pdftext.split('\f')[:-1] # There's a trailing \f on the last page
        return pdf_pages

    def parse_metadata(self) -> BankStatementMetadata:
        pass

    def parse(self) -> BankStatement:
        self.parse_balances()
        transactions = [t for t in self.generate_transactions(
                                            self.transactions_start,
                                            self.transactions_end)]
        assert self.old_balance.balance \
               + self.total_credit - self.total_debit \
                == self.new_balance.balance
        transactions = self.clean_up_transactions(transactions)
        self.map_accounts(transactions)
        return BankStatement(self.account, transactions,
                             self.old_balance, self.new_balance)

    def clean_up_transactions(self, transactions):
        cleaner = TransactionCleaner(self.xdg)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self, transactions):
        mapper = AccountMapper(self.xdg)
        mapper.map_transactions(transactions)
