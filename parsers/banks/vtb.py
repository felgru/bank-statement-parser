# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta
from decimal import Decimal
import os
import re
import subprocess

from bank_statement import BankStatementMetadata
from transaction import Balance, MultiTransaction, Posting, Transaction

from ..pdf_parser import PdfParser

class VTBPdfParser(PdfParser):
    bank_folder = 'vtb'
    account = 'assets:bank:saving:VTB Direktbank'

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n*',
                flags=re.MULTILINE)

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext',
                                  '-fixed', '6', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        # Careful: There's a trailing \f on the last page
        self.pdf_pages = pdftext.split('\f')[:-1]
        self._parse_metadata()

    table_heading = re.compile(r'^ *Bu-Tag *Wert *(Vorgang)\n*',
                               flags=re.MULTILINE)

    def _parse_description_start(self):
        m = self.table_heading.search(self.pdf_pages[0])
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self):
        return self.metadata

    def _parse_metadata(self):
        m = re.search(r'EUR-Konto +Kontonummer +(\d+)\n', self.pdf_pages[0])
        account_number = m.group(1)
        m = re.search(r'IBAN: +(DE[\d ]+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        m = re.search(r'BIC: +([A-Z\d]+)\n', self.pdf_pages[0])
        bic = m.group(1)
        m = re.search(r'erstellt am (\d{2}.\d{2}.\d{4})', self.pdf_pages[0])
        end_date = parse_date_with_year(m.group(1))
        m = re.search(r'alter Kontostand vom (\d{2}.\d{2}.\d{4})',
                      self.pdf_pages[0])
        start_date = parse_date_with_year(m.group(1))
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                iban=iban,
                bic=bic,
                account_number=account_number,
               )
        self.metadata = meta

    def extract_transactions_table(self):
        self.footer_start_pattern = re.compile(
                '\n*^( {{1,{}}})[^ \d]'.format(self.description_start - 1),
                flags=re.MULTILINE)
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def extract_table_from_page(self, page):
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

    def parse_balances(self):
        m = re.search(r'alter Kontostand vom (\d{2}.\d{2}.\d{4})'
                      r' +(\d[.\d]*,\d\d) +([HS])\n',
                      self.transactions_text)
        self.transactions_start = m.end()
        self.old_balance = Balance(parse_amount(m.group(2), m.group(3)),
                                   parse_date_with_year(m.group(1)))
        m = re.search(r'neuer Kontostand vom (\d{2}.\d{2}.\d{4})'
                      r' +(\d[.\d]*,\d\d) +([HS])\n',
                      self.transactions_text)
        self.transactions_end = m.start()
        self.new_balance = Balance(parse_amount(m.group(2), m.group(3)),
                                   parse_date_with_year(m.group(1)))

    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.) +(\d{2}.\d{2}.) +(.*\S+) +'
            r'(\d[.\d]*,\d\d) +([HS])\n*',
            flags=re.MULTILINE)

    def generate_transactions(self, start, end):
        while True:
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)
            if m is None: break
            transaction_date = self.parse_short_date(m.group(1))
            value_date = self.parse_short_date(m.group(2))
            amount = parse_amount(m.group(4), m.group(5))
            description = [m.group(3)]
            start = m.end()
            while True:
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
                if m is None: break
                description.append(m.group(1))
                start = m.end()
            description = ' '.join(l for l in description)
            yield Transaction(self.account, description, transaction_date,
                              value_date, amount)

    def check_transactions_consistency(self, transactions):
        assert self.old_balance.balance + sum(t.amount for t in transactions) \
               == self.new_balance.balance

    def parse_short_date(self, d: str) -> date:
        return parse_date_relative_to(d, self.new_balance.date)

def parse_date_with_year(d: str) -> date:
    """parse a date in "dd.mm.yyyy" or "dd.mm.yy" format

    For dates with a two digit year yy, we assume it to refer to 20yy.
    """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    if year < 100:
        year += 2000
    return date(year, month, day)

def parse_date_relative_to(d, ref_d):
    """parse a date in "dd.mm." format while guessing year relative to date ref_d"""
    day = int(d[:2])
    month = int(d[3:5])
    year = ref_d.year
    d = date(year, month, day)
    half_a_year = timedelta(days=356/2)
    diff = d - ref_d
    if abs(diff) > half_a_year:
        if diff < timedelta(days=0):
            d = date(year + 1, month, day)
        else:
            d = date(year - 1, month, day)
    return d

def parse_amount(a: str, dir: str) -> Decimal:
    """parse a decimal amount like 1.200,00 H

    The suffix H indicates positive amounts (Haben),
    while the suffix S indicates negative ones (Soll).
    """
    a = a.replace('.', '').replace(',', '.')
    a = Decimal(a)
    if dir == 'H':
        return a
    elif dir == 'S':
        return -a
    else:
        raise RuntimeError(f"Unknown argument {dir!r} instead of"
                           " H(aben) or S(oll).")
