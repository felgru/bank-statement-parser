# SPDX-FileCopyrightText: 2020–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import re
from typing import cast, Optional

from .cleaning_rules import mercedes_benz as cleaning_rules
from bank_statement import BankStatementMetadata
from transaction import (BaseTransaction, Balance, MultiTransaction,
                         Posting, Transaction)

from ..parser import BaseCleaningParserConfig
from ..pdf_parser import OldPdfParser


class MercedesBenzConfig(BaseCleaningParserConfig):
    bank_name = 'Mercedes Benz Bank'
    bank_folder = 'mercedes-benz'
    DEFAULT_ACCOUNTS = {
        'default account': 'assets:bank:saving:Mercedes-Benz Bank',
    }

class MercedesBenzPdfParser(OldPdfParser[MercedesBenzConfig]):
    num_cols = 4

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self._parse_metadata()
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + r' *(\S.*)\n*',
                flags=re.MULTILINE)
        if self.is_old_format:
            self.cleaning_rules = cleaning_rules.old_rules
        else:
            self.cleaning_rules = cleaning_rules.rules

    table_heading = re.compile(r'^ *Datum *Wert *(Text) *Soll\/Haben\n*',
                               flags=re.MULTILINE)

    def _parse_description_start(self) -> None:
        m = self.table_heading.search(self.pdf_pages[0])
        assert m is not None, 'Could not find table heading.'
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self) -> BankStatementMetadata:
        return self.metadata

    def _parse_metadata(self) -> None:
        m = re.search(r'Kundennummer +(\d+)\n', self.pdf_pages[0])
        if m is not None:  # current format
            kundennummer = m.group(1)
            self.is_old_format = False
        else:  # old format
            self.is_old_format = True
            m = re.search(r'Kontonummer +(\d+)\n', self.pdf_pages[0])
            assert m is not None, 'Could not find Kontonummer.'
            kundennummer = m.group(1)[:-3]
        m = re.search(r'IBAN +(DE[\d ]+?)\n', self.pdf_pages[0])
        assert m is not None, 'Could not find IBAN.'
        iban = m.group(1)
        # Currency code is not in same line as the word "Währung"
        # due to some inaccuracy of the PDF parsing.
        # Since this is in theory always EUR, I'll simply disable
        # this check.
        # m = re.search(r'Währung +([A-Z]{3})\n', self.pdf_pages[0])
        # assert m is not None, 'Could not find currency.'
        # assert m.group(1) == 'EUR', f'Unexpected currency: {m.group(1)}'
        m = re.search(r'Kontoauszug für Ihr Mercedes-Benz Bank Tagesgeldkonto'
                      r' zum (\d{2}.\d{2}.\d{4})', self.pdf_pages[0])
        assert m is not None, 'Could not find IBAN.'
        end_date = parse_date_with_year(m.group(1))
        m = re.search(r'Vortragssaldo vom (\d{2}.\d{2}.\d{2})',
                      self.pdf_pages[0])
        assert m is not None, 'Could not find start date.'
        start_date = parse_date_with_year(m.group(1))
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                iban=iban,
                owner_number=kundennummer,
               )
        self.metadata = meta

    def extract_transactions_table(self) -> str:
        self.footer_start_pattern = re.compile(
                r'\n*^( {{1,{}}})[^ \d]'.format(self.description_start - 1),
                flags=re.MULTILINE)
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def extract_table_from_page(self, page: str) -> str:
        # TODO: I might have to remove the garbage string from left margin if it
        #       ever is next to the transactions table.
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

    def parse_balances(self) -> None:
        m = re.search(r'Vortragssaldo vom (\d{2}.\d{2}.\d{2}) +(-?\d[.\d]*,\d\d)\n',
                      self.transactions_text)
        assert m is not None, 'Could not find old balance.'
        self.transactions_start = m.end()
        self.old_balance = Balance(parse_amount(m.group(2)),
                                   parse_date_with_year(m.group(1)))
        m = re.search(r'Endsaldo vom (\d{2}.\d{2}.\d{2}) +(-?\d[.\d]*,\d\d)\n',
                      self.transactions_text)
        assert m is not None, 'Could not find new balance.'
        self.transactions_end = m.start()
        self.new_balance = Balance(parse_amount(m.group(2)),
                                   parse_date_with_year(m.group(1)))

    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.|) +(\d{2}.\d{2}.) +(.*\S) +(\d[.\d]*,\d\d-?)\n'
            r' *([^\n]*)\n*',
            flags=re.MULTILINE)

    def generate_transactions(self, start: int, end: int,
                              accounts: dict[str, str],
                              ) -> Iterator[Transaction]:
        account = accounts['default account']
        interests = []
        if self.is_old_format:
            abschluss_pattern = re.compile(r'Kontoabschluss zum (\d\d.\d\d.)\n')
            m = abschluss_pattern.search(self.transactions_text, start, end)
            assert m is not None
            abschluss_date = self.parse_short_date(m.group(1))
            abschluss_start = m.end()
            abschluss_end = end
            end = m.start()
            interest_pattern = re.compile(r'^ +(\S.*%) +(\d[.\d]*,\d\d-?)\n',
                                          flags=re.MULTILINE)
            for m in interest_pattern.finditer(self.transactions_text,
                                               abschluss_start,
                                               abschluss_end):
                interests.append(
                        Transaction(account,
                            description=' '.join(m.group(1).split()),
                            operation_date=abschluss_date,
                            value_date=abschluss_date,
                            amount=parse_amount(m.group(2)),
                            metadata={
                                'type': 'Zinsabrechnung',
                                }))
        last_transaction_date: Optional[date] = None
        m = self.transaction_pattern.search(self.transactions_text, start, end)
        while m is not None:
            if m.group(1):
                transaction_date = self.parse_short_date(m.group(1))
                last_transaction_date = transaction_date
            else:
                assert last_transaction_date is not None, \
                    'First transaction has no transaction date!'
                transaction_date = last_transaction_date
            value_date = self.parse_short_date(m.group(2))
            transaction_type = m.group(3)
            metadata = dict(type=transaction_type)
            amount = parse_amount(m.group(4))
            description_lines = [m.group(5)]
            start = m.end()
            m = self.transaction_description_pattern.match(
                            self.transactions_text, start, end)
            while m is not None:
                description_lines.append(m.group(1))
                start = m.end()
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
            description = '\n'.join(description_lines)
            metadata['raw_description'] = description
            yield Transaction(account, description, transaction_date,
                              value_date, amount,
                              metadata=metadata)
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)
        yield from interests

    def check_transactions_consistency(self,
                                       transactions: list[BaseTransaction],
                                       config: MercedesBenzConfig,
                                       ) -> None:
        calculated_balance = self.old_balance.balance \
                             + sum(cast(Transaction, t).amount
                                   for t in transactions)
        assert calculated_balance == self.new_balance.balance, \
               f"new balance {self.new_balance.balance} does not match sum {calculated_balance}."

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

def parse_date_relative_to(s: str, ref_d: date) -> date:
    """parse a date in "dd.mm." format while guessing year relative to date ref_d"""
    day = int(s[:2])
    month = int(s[3:5])
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

def parse_amount(a: str) -> Decimal:
    """parse a decimal amount like 1.200,00-."""
    a = a.replace('.', '').replace(',', '.')
    if a.endswith('-'):
        a = '-' + a[:-1]
    return Decimal(a)
