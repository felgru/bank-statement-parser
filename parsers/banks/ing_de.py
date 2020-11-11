# SPDX-FileCopyrightText: 2019–2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta
from decimal import Decimal
import os
import re
import subprocess
from typing import cast, Iterator, List

from .cleaning_rules import ing_de as cleaning_rules
from bank_statement import BankStatement, BankStatementMetadata
from transaction import (AnyTransaction, Balance, MultiTransaction,
                         Posting, Transaction)

from ..pdf_parser import PdfParser

class IngDePdfParser(PdfParser):
    bank_folder = 'ing.de'
    account = 'assets:bank:TODO:ING.de' # exact account is set in __init__

    def __init__(self, pdf_file: str):
        super().__init__(pdf_file)
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n',
                flags=re.MULTILINE)
        if self.metadata.account_type == 'Girokonto':
            self.account = 'assets:bank:checking:ING.de'
            self.cleaning_rules = cleaning_rules.rules
        elif self.metadata.account_type == 'Extra-Konto':
            self.account = 'assets:bank:saving:ING.de'
            self.cleaning_rules = cleaning_rules.extra_konto_rules

    def _parse_file(self, pdf_file: str):
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

    def _parse_description_start(self) -> None:
        m = self.table_heading.search(self.pdf_pages[0])
        assert m is not None, 'Could not find table heading.'
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self) -> BankStatementMetadata:
        return self.metadata

    def parse(self) -> BankStatement:
        if self.metadata.account_type not in ('Girokonto', 'Extra-Konto'):
            raise NotImplementedError('parsing of %s not supported.'
                                      % self.metadata.account_type)
        bank_statement = super().parse()
        if self.metadata.account_type == 'Extra-Konto':
            self._add_interest_details(bank_statement)
        return bank_statement

    def _add_interest_details(self, bank_statement: BankStatement) -> None:
        interests = self.parse_interest_postings()
        interest_transaction = cast(Transaction,
                                    bank_statement.transactions[-1])
        assert interest_transaction.type == 'Zinsertrag'
        interests_sum = sum(i.amount for i in interests)
        assert interest_transaction.amount + interests_sum == 0
        interest_transaction.to_multi_transaction()

        mt = MultiTransaction(
                description=interest_transaction.description,
                transaction_date=interest_transaction.operation_date,
                metadata=interest_transaction.metadata)
        mt.add_posting(Posting(
                        interest_transaction.account,
                        interest_transaction.amount,
                        interest_transaction.currency,
                        interest_transaction.value_date))
        for posting in interests:
            mt.add_posting(posting)

        bank_statement.transactions[-1] = mt


    def _parse_metadata(self) -> None:
        self._parse_balances()
        end_date = self.new_balance.date
        m = re.search(r'IBAN +(DE[\d ]+?)\n', self.pdf_pages[0])
        assert m is not None, 'Could not find IBAN.'
        iban = m.group(1)
        m = re.search(r'BIC +([A-Z]+?)\n', self.pdf_pages[0])
        assert m is not None, 'Could not find BIC.'
        bic = m.group(1)
        m = re.search(r'^ *(\w.*?) Nummer (\d{10})\n', self.pdf_pages[0],
                      flags=re.MULTILINE)
        assert m is not None, 'Could not find account type and number.'
        account_type = m.group(1)
        account_number = m.group(2)
        # Approximate starting date
        if account_type == 'Extra-Konto':
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)
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
            r' *(\d{2}.\d{2}.\d{4}) *([^\n]*)\n',
            flags=re.MULTILINE)

    def extract_transactions_table(self) -> str:
        self.footer_start_pattern = re.compile(
                '\n*^ {{0,{}}}[^ \d]'.format(self.description_start - 1),
                flags=re.MULTILINE)
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def extract_table_from_page(self, page: str) -> str:
        # remove garbage string from left margin, containing account number
        account_number = self.metadata.account_number
        assert account_number is not None
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

    def parse_interest_postings(self) -> List[Posting]:
        interest_table = self.extract_interest_table()
        postings = []
        for m in re.finditer(r'^  +(.+?)  +(.+?%)  +(.+?)  +(.+,\d\d)$',
                             interest_table, flags=re.MULTILINE):
            description = ' '.join(m.group(i) for i in (1, 2, 3))
            postings.append(Posting('income:bank:interest:ING.de',
                                    -parse_amount(m.group(4)),
                                    comment=description))
        return postings

    def extract_interest_table(self) -> str:
        self.interest_table_heading = re.compile(
                r'^ *Zeitraum *Zins p\.a\. *Ertrag',
                flags=re.MULTILINE)
        self.footer_start_pattern = re.compile(
                '\n*^ {{0,{}}}[^ \d]'.format(self.description_start - 1),
                flags=re.MULTILINE)
        return ''.join(self.extract_interest_table_from_page(p)
                       for p in self.pdf_pages)

    def extract_interest_table_from_page(self, page: str) -> str:
        # remove garbage string from left margin, containing account number
        account_number = self.metadata.account_number
        assert account_number is not None
        page = re.sub(r'\s* \d\d[A-Z]{4}'+account_number+'_T\n\n*', '\n', page)
        m = self.interest_table_heading.search(page)
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

    def _parse_balances(self) -> None:
        m = re.search('Datum +(\d{2}.\d{2}.\d{4})', self.pdf_pages[0])
        assert m is not None, 'Date of new balance not found.'
        date = parse_date(m.group(1))
        m = re.search('Alter Saldo +(-?\d[.\d]*,\d\d)', self.pdf_pages[0])
        assert m is not None, 'Old balance not found.'
        old = parse_amount(m.group(1))
        m = re.search('Neuer Saldo +(-?\d[.\d]*,\d\d)', self.pdf_pages[0])
        assert m is not None, 'New balance not found.'
        new = parse_amount(m.group(1))
        self.old_balance = Balance(old, None)
        self.new_balance = Balance(new, date)

    def parse_balances(self) -> None:
        self.transactions_start = 0
        m = re.search('\S*Neuer Saldo *(-?\d[.\d]*,\d\d)',
                      self.transactions_text)
        assert m is not None, 'Could not find new balance.'
        assert parse_amount(m.group(1)) == self.new_balance.balance
        self.transactions_end = m.start()

    def generate_transactions(self, start: int, end: int) \
                                            -> Iterator[AnyTransaction]:
        m = self.transaction_pattern.search(self.transactions_text, start, end)
        while m is not None:
            transaction_date = parse_date(m.group(1))
            transaction_type = m.group(2)
            description_lines = [m.group(3), m.group(6)]
            amount = parse_amount(m.group(4))
            value_date = parse_date(m.group(5))
            start = m.end()
            m = self.transaction_description_pattern.match(
                            self.transactions_text, start, end)
            while m is not None:
                description_lines.append(m.group(1))
                start = m.end()
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
            description = '\n'.join(description_lines)
            yield Transaction(self.account, description, transaction_date,
                              value_date, amount,
                              metadata=dict(type=transaction_type))
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)

    def check_transactions_consistency(self,
                transactions: List[AnyTransaction]) -> None:
        assert self.old_balance.balance + sum(cast(Transaction, t).amount
                                              for t in transactions) \
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
