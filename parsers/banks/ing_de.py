# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta
from decimal import Decimal
import os
import re
import subprocess

from bank_statement import BankStatementMetadata
from transaction import Balance, Transaction

from ..pdf_parser import PdfParser

class IngDePdfParser(PdfParser):
    bank_folder = 'ing.de'
    account = 'assets::bank::checking::ING.de' # TODO: actually depends on metadata

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n',
                flags=re.MULTILINE)
        self._parse_metadata()

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext',
                                  '-fixed', '3', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        # Careful: There's a trailing \f on the last page
        self.pdf_pages = pdftext.split('\f')[:-1]
        self._parse_metadata()

    table_heading = re.compile(r'^ *Buchung *(Buchung / Verwendungszweck) *'
                               r'Betrag \(EUR\)\n *Valuta\n*',
                               flags=re.MULTILINE)

    def _parse_description_start(self):
        m = self.table_heading.search(self.pdf_pages[0])
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self):
        return self.metadata

    def parse(self):
        if self.metadata.account_type != 'Girokonto':
            raise NotImplementedError('parsing of %s not supported.'
                                      % self.metadata.account_type)
        return super().parse()

    def _parse_metadata(self):
        self._parse_balances()
        end_date = self.new_balance.date
        # Approximate starting date
        start_date = end_date - timedelta(days=30)
        m = re.search(r'IBAN +(DE[\d ]+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        m = re.search(r'BIC +([A-Z]+?)\n', self.pdf_pages[0])
        bic = m.group(1)
        m = re.search(r'^ *(\w.*?) Nummer (\d{10})\n', self.pdf_pages[0],
                      flags=re.MULTILINE)
        account_type = m.group(1)
        account_number = m.group(2)
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                bic=bic,
                iban=iban,
                account_type=account_type,
                account_number=account_number,
               )
        self.metadata = meta

    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.\d{4}) +(\S+) +(.*?) +(-?\d[.\d]*,\d\d)\d?\n'
            r' *(\d{2}.\d{2}.\d{4}) +([^\n]*)\n',
            flags=re.MULTILINE)

    def extract_transactions_table(self):
        self._parse_description_start()
        self.footer_start_pattern = re.compile(
                '\n*^ {{0,{}}}[^ \d]'.format(self.description_start - 1),
                flags=re.MULTILINE)
        return super().extract_transactions_table()

    def extract_table_from_page(self, page):
        # remove garbage string from left margin, containing account number
        account_number = self.metadata.account_number
        page = re.sub(r'\s* \d\d[A-Z]{4}'+account_number+'_T\n\n*', '\n', page)
        m = self.table_heading.search(page)
        if m is None:
            return ''
        table_start = m.end()
        m = self.footer_start_pattern.search(page, table_start)
        if m is not None:
            table_end = m.start() + 1
            page = page[table_start:table_end]
        else:
            page = page[table_start:]
        return page

    def _parse_balances(self):
        date = parse_date(re.search('Datum +(\d{2}.\d{2}.\d{4})',
                                    self.pdf_pages[0]).group(1))
        old = parse_amount(re.search('Alter Saldo +(-?\d[.\d]*,\d\d)',
                                     self.pdf_pages[0]).group(1))
        new = parse_amount(re.search('Neuer Saldo +(-?\d[.\d]*,\d\d)',
                                     self.pdf_pages[0]).group(1))
        self.old_balance = Balance(old, None)
        self.new_balance = Balance(new, date)

    def parse_balances(self):
        self.transactions_start = 0
        m = re.search('\S*Neuer Saldo *(-?\d[.\d]*,\d\d)',
                      self.transactions_text)
        assert parse_amount(m.group(1)) == self.new_balance.balance
        self.transactions_end = m.start()

    def generate_transactions(self, start, end):
        m = self.transaction_pattern.search(self.transactions_text, start, end)
        while m is not None:
            transaction_date = parse_date(m.group(1))
            transaction_type = m.group(2)
            description = [m.group(3), m.group(6)]
            amount = parse_amount(m.group(4))
            value_date = parse_date(m.group(5))
            start = m.end()
            m = self.transaction_description_pattern.match(
                            self.transactions_text, start, end)
            while m is not None:
                description.append(m.group(1))
                start = m.end()
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
            description = '\n'.join(l for l in description)
            yield Transaction(transaction_type, description, transaction_date,
                              value_date, amount)
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)

    def check_transactions_consistency(self, transactions):
        assert self.old_balance.balance + sum(t.amount for t in transactions) \
               == self.new_balance.balance

def parse_date(d: str) -> date:
    """ parse a date in "dd.mm.yyyy" format """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    return date(year, month, day)

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -1.200,00 """
    a = a.replace('.', '').replace(',', '.')
    return Decimal(a)
