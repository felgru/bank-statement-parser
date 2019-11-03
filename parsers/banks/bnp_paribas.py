# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal
import re

from bank_statement import BankStatementMetadata
from transaction import Balance, Transaction

from ..pdf_parser import PdfParser

class BnpParibasPdfParser(PdfParser):
    bank_folder = 'bnp'
    account = 'assets::bank::checking::BNP'

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        self.debit_start, self.credit_start = self.parse_column_starts()

    def parse_column_starts(self):
        m = self.table_heading.search(self.pdf_pages[0])
        line_start = m.start()
        debit_start = m.start(1) - line_start
        credit_start = m.start(2) - line_start
        return debit_start, credit_start

    def parse_metadata(self):
        m = re.search(r'du (\d{1,2} \S+ \d{4}) au (\d{1,2} \S+ \d{4})',
                      self.pdf_pages[0])
        start_date = parse_verbose_date(m.group(1))
        end_date = parse_verbose_date(m.group(2))
        m = re.search(r'IBAN *: (.+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        m = re.search(r'BIC *: (.+?)\n', self.pdf_pages[0])
        bic = m.group(1)
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                bic=bic,
                iban=iban,
               )
        return meta

    table_heading = re.compile('^ *Date *Nature des opérations *Valeur *'
                               '(Débit) *(Crédit)', flags=re.MULTILINE)
    end_pattern = re.compile(r"^ *\* Commissions sur services et opérations "
                             r"bancaires. Total", flags=re.MULTILINE)
    balance_pattern = re.compile(r'^ *SOLDE CREDITEUR AU (\d{2}.\d{2}.\d{4})'
                                 r' *(\d[ \d]*,\d\d)', flags=re.MULTILINE)

    @classmethod
    def extract_table_from_page(cls, page):
        m = cls.table_heading.search(page)
        if m is None:
            return ''
        line_start = m.start()
        debit_start = m.start(1) - line_start
        credit_start = m.start(2) - line_start
        table_start = m.end()

        m = cls.end_pattern.search(page)
        table_end = m.start()
        return page[table_start:table_end+1]

    def parse_balances(self):
        self.parse_old_balance()
        self.parse_total_and_new_balance()
        assert self.old_balance.date <= self.new_balance.date
        assert self.new_balance.date.year - self.old_balance.date.year <= 1

    def parse_old_balance(self):
        m = self.balance_pattern.search(self.transactions_text)
        old_balance = parse_amount(m.group(2))
        if m.end(2) - m.start() < self.credit_start:
            old_balance = -old_balance
        self.old_balance = Balance(old_balance, parse_date(m.group(1)))
        self.transactions_start = m.end()

    def parse_total_and_new_balance(self):
        total_pattern = re.compile(r'^ *TOTAL DES OPERATIONS\s*'
                                   r'(\d[ \d]*,\d\d)\s*(\d[ \d]*,\d\d|)',
                                   flags=re.MULTILINE)
        m = total_pattern.search(self.transactions_text)
        total_debit = parse_amount(m.group(1))
        total_credit = parse_amount(m.group(2))
        self.total_debit, self.total_credit = total_debit, total_credit
        self.transactions_end = m.start()
        m = self.balance_pattern.search(self.transactions_text, m.end())
        new_balance_linestart = m.start()
        new_balance_date = parse_date(m.group(1))
        new_balance = parse_amount(m.group(2))
        if m.end(2) - new_balance_linestart < self.credit_start:
            new_balance = -new_balance
        self.new_balance = Balance(new_balance, new_balance_date)

    transaction_pattern = re.compile(r'^ * (\d\d.\d\d) *(\S.*?) *(\d\d.\d\d) *'
                                     r'(\d[ \d]*,\d\d)$',
                                     re.MULTILINE)

    def generate_transactions(self, start, end):
        m = self.transaction_pattern.search(self.transactions_text, start, end)
        while m is not None:
            transaction_date = self.parse_short_date(m.group(1))
            description = [m.group(2)]
            value_date = self.parse_short_date(m.group(3))
            amount = parse_amount(m.group(4))
            if m.end(4) - m.start() < self.credit_start:
                amount = -amount
            start = m.end()
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)
            transaction_end = m.start() if m is not None else end
            description.extend(l.strip()
                    for l in self.transactions_text[start:transaction_end]
                                                              .split('\n'))
            description = ' '.join(l for l in description if l)
            yield Transaction(None, description, transaction_date,
                              value_date, amount)

    def parse_short_date(self, d: str) -> date:
        """ parse a date in "dd.mm" format

        The result has to lie between start_date and end_date
        """
        day = int(d[:2])
        month = int(d[3:5])
        start_date = self.old_balance.date
        end_date = self.new_balance.date
        year = start_date.year
        d = date(year, month, day)
        if d < start_date:
            d.year = end_date.year
        assert start_date <= d <= end_date
        return d

def parse_verbose_date(d: str) -> date:
    day, month, year = d.split()
    day = int(day)
    month = {'janvier': 1,
             'février': 2,
             'mars': 3,
             'avril': 4,
             'mai': 5,
             'juin': 6,
             'juillet': 7,
             'août': 8,
             'septembre': 9,
             'octobre': 10,
             'novembre': 11,
             'decembre': 12}[month]
    year = int(year)
    return date(year, month, day)

def parse_date(d: str) -> date:
    """ parse a date in "dd.mm.yyyy" format """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    return date(year, month, day)

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(' ', '').replace(',', '.')
    return Decimal(a)
