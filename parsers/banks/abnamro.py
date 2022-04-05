# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from typing import Iterator, Optional

from .cleaning_rules import abnamro as cleaning_rules
from bank_statement import BankStatement, BankStatementMetadata
from transaction import (
        Balance,
        BaseTransaction,
        MultiTransaction,
        Transaction,
        )

from ..pdf_parser import CleaningParser, PdfParser

class AbnAmroPdfParser(PdfParser):
    bank_folder = 'abnamro'
    account = 'assets:bank:ABN AMRO'
    num_cols = None
    cleaning_rules = cleaning_rules.rules

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        meta = self.parse_first_page_metadata()
        self.first_page_metadata = meta
        self.old_balance = Balance(meta.previous_balance,
                                   meta.date.replace(day=1))
        self.new_balance = Balance(meta.new_balance, meta.date)

    @property
    def total_debit(self) -> Decimal:
        return self.first_page_metadata.total_amount_debit

    @property
    def total_credit(self) -> Decimal:
        return self.first_page_metadata.total_amount_credit

    def parse_metadata(self) -> BankStatementMetadata:
        meta = self.first_page_metadata
        return BankStatementMetadata(
                start_date=meta.date.replace(day=1),
                end_date=meta.date,
                bic=meta.bic,
                iban=meta.iban,
                account_type=meta.account_type,
                account_number=meta.account_number,
               )

    def parse_first_page_metadata(self) -> FirstPageMetadata:
        meta = FirstPageMetadata(self.pdf_pages[0])
        assert len(self.pdf_pages) == meta.no_of_pages
        return meta

    def parse_raw(self) -> BankStatement:
        transactions = list(reversed(list(self._iter_main_table())))
        return BankStatement(self.account, transactions,
                             self.old_balance, self.new_balance)

    def _iter_main_table(self) -> MainTableIterator:
        meta = self.first_page_metadata
        return MainTableIterator(
                self.pdf_pages,
                year=meta.date.year,
                currency=meta.currency,
                account=self.account)

    def check_transactions_consistency(self,
                                       transactions: list[BaseTransaction]) \
                                                                    -> None:
        super().check_transactions_consistency(transactions)

        def amount(t: BaseTransaction) -> Decimal:
            if isinstance(t, Transaction):
                return t.amount
            elif isinstance(t, MultiTransaction):
                return sum((p.amount for p in t.postings
                            if p.account == self.account),
                           start=Decimal(0))
            else:
                raise RuntimeError(f'Unknown transaction Type {type(t)}.')

        calculated_credit = sum(a for a in map(amount, transactions) if a > 0)
        calculated_debit = -sum(a for a in map(amount, transactions) if a < 0)
        assert calculated_credit == self.total_credit, \
                f'{calculated_credit} ≠ {self.total_credit}'
        assert calculated_debit == self.total_debit, \
                f'{calculated_debit} ≠ {self.total_debit}'


class FirstPageMetadata:
    def __init__(self, page: str):
        m = re.search(r'Account \(in ([A-Z]+)\) +(BIC)', page)
        assert m is not None
        self.bank_address, self.customer_address = \
                self._parse_addresses(page[:m.start()])
        self.currency = m.group(1)
        line_start = m.end() + 1
        line_end = page.index('\n', line_start)
        bic_start = line_start + m.start(2) - m.start()
        self.account_type = page[m.end() + 1:bic_start].strip().lower()
        self.bic = page[bic_start:line_end].strip()
        d, line_end = self._parse_line_with_header(
                page,
                r'(Account number +) (IBAN +) (Date +) (No of pages)'
                r' +(Page) +(Stmt no)\n',
                line_end,
                )
        self.account_number = d['account_number']
        self.iban = d['iban']
        self.date = parse_date(d['date'])
        self.no_of_pages = int(d['no_of_pages'])
        self.page = int(d['page'])
        self.stmt_no = int(d['stmt_no'])
        d, line_end = self._parse_line_with_header(
                page,
                r'(Previous balance +) (New balance +)'
                r' (Total amount debit +) (Total amount credit)\n',
                line_end,
                )
        self.previous_balance = parse_balance(d['previous_balance'])
        self.new_balance = parse_balance(d['new_balance'])
        self.total_amount_debit = parse_amount(d['total_amount_debit'])
        self.total_amount_credit = parse_amount(d['total_amount_credit'])

    @staticmethod
    def _parse_addresses(text: str) -> tuple[str, str]:
        m = re.search('Statement of account', text)
        assert m is not None
        m = re.compile(r'^( +)', flags=re.MULTILINE).search(text, m.end())
        assert m is not None
        addresses_start = m.start()
        customer_address_offset = m.end(1) - m.start(1)
        bank_address = []
        customer_address = []
        for line in text[addresses_start:].split('\n'):
            left = line[:customer_address_offset].strip()
            right = line[customer_address_offset:].strip()
            if left:
                bank_address.append(left)
            if right:
                customer_address.append(right)
        return '\n'.join(bank_address), '\n'.join(customer_address)

    @staticmethod
    def _parse_line_with_header(page: str,
                                header_pattern: str,
                                start: int) -> tuple[dict[str, str], int]:
        m = re.compile(header_pattern).search(page, start)
        assert m is not None
        assert m.lastindex is not None
        pattern_start = m.start()
        line_start = m.end()
        line_end = page.index('\n', line_start)
        line = page[line_start:line_end]
        parsed: dict[str, str] = {}
        for i in range(1, m.lastindex + 1):
            key = m.group(i).strip().lower().replace(' ', '_')
            group_start = m.start(i) - pattern_start
            group_end = m.end(i) - pattern_start
            parsed[key] = line[group_start:group_end].strip()
        return parsed, line_end


class MainTableIterator:
    KEYWORDS = re.compile(r'(IBAN|BIC|Naam|Omschrijving|Kenmerk): ')

    def __init__(self, pdf_pages: list[str], *,
                 year: int, currency: str, account: str):
        self.pdf_pages = pdf_pages
        self.year = year
        self.currency = '€' if currency == 'EUR' else currency
        self.account = account
        self._set_page(0)

    def _set_page(self, page: int) -> None:
        header = re.compile(r'^(Bookdate) +(Description +)'
                            r' (Amount debit +) (Amount credit)\n'
                            r'\(Value date\)$',
                            flags=re.MULTILINE)
        m = header.search(self.pdf_pages[page])
        if m is None:
            raise AbnAmroPdfParserError(
                    f'Main table header on page {page} not found.')
        assert m is not None
        assert m.lastindex is not None
        spans = {}
        for i in range(1, m.lastindex + 1):
            key = m.group(i).strip().lower().replace(' ', '_')
            group_start = m.start(i) - m.start()
            group_end = m.end(i) - m.start()
            spans[key] = slice(group_start, group_end)
        # Last column normally protrudes its header
        spans['amount_credit'] = slice(spans['amount_credit'].start, None)
        self.spans: dict[str, slice] = spans
        body_start = m.end() + 1
        self.lines = self.pdf_pages[page][body_start:].split('\n')
        self.current_page = page
        self.current_line = 0

    def __iter__(self) -> MainTableIterator:
        return self

    def __next__(self) -> Transaction:
        # first line
        line = self._next_line()
        if line is None:
            raise StopIteration
        bookdate = parse_short_date(line[self.spans['bookdate']].strip(),
                                    self.year)
        description = [line[self.spans['description']]]
        debit = line[self.spans['amount_debit']].strip()
        credit = line[self.spans['amount_credit']].strip()
        amount = -parse_amount(debit) if debit else parse_amount(credit)
        # second line
        line = self._next_line()
        if line is None:
            raise AbnAmroPdfParserError('Transaction with missing second line.')
        value_date = parse_short_date(line[self.spans['bookdate']].strip(' ()'),
                                      self.year)
        description.append(line[self.spans['description']])
        while (line := self._peek_next_line()) is not None \
                and not line[self.spans['bookdate']].strip():
            self.current_line += 1
            description.append(line[self.spans['description']])
        d = dict[str, str]()
        current_key = 'transaction_type'
        current_value = description[0].rstrip()
        for line in description[1:]:
            m = self.KEYWORDS.match(line)
            if m is None:
                current_value += line
            else:
                d[current_key] = current_value.rstrip()
                current_key = m.group(1)
                current_value = line[m.end():]
        d[current_key] = current_value.rstrip()
        return Transaction(account=self.account,
                           description=d['Omschrijving'],
                           operation_date=bookdate,
                           value_date=value_date,
                           amount=amount,
                           currency=self.currency,
                           metadata=d)

    def _next_line(self) -> Optional[str]:
        line = self._peek_next_line()
        if line is not None:
            self.current_line += 1
        return line

    def _peek_next_line(self) -> Optional[str]:
        if self.current_line >= len(self.lines):
            if self.current_page >= len(self.pdf_pages) - 1:
                return None
            else:
                self._set_page(self.current_page + 1)
        line = self.lines[self.current_line]
        if line:
            return line
        else:
            self.current_line += 1
            return self._peek_next_line()


def parse_date(d: str) -> date:
    # Dutch inverse ISO format
    day, month, year = d.split('-')
    return date(int(year), int(month), int(day))


def parse_short_date(d: str, year: int) -> date:
    # Dutch inverse ISO format
    day, month = d.split('-')
    return date(year, int(month), int(day))


def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -1.200,00 """
    a = a.replace('.', '').replace(',', '.')
    return Decimal(a)


def parse_balance(a: str) -> Decimal:
    """ parse a balance like -1.200,00 +/CREDIT """
    amount, _, sign = a.partition(' ')
    assert sign[0] in {'+', '-'}
    return parse_amount(sign[0] + amount)


class AbnAmroPdfParserError(RuntimeError):
    pass

class AbnAmroTsvParser(CleaningParser):
    bank_folder = 'abnamro'
    account = 'assets:bank:ABN AMRO'
    file_extension = '.tab'
    num_cols = None
    # cleaning_rules = cleaning_rules.rules

    def __init__(self, tsv_file: Path):
        super().__init__(tsv_file)
        self._parse_file(tsv_file)

    def _parse_file(self, tsv_file: Path) -> None:
        if not tsv_file.exists():
            raise IOError(f'Unknown file: {tsv_file}')
        transactions = []
        key_pattern = re.compile(r'/([A-Z]+)/')
        card_payment_pattern = re.compile(
                r'BEA +NR:([^ ]+) +(\d\d\.\d\d\.\d\d)\/(\d\d\.\d\d) '
                r'(.*),PAS(\d+) +(.*)')
        with open(tsv_file, newline='') as f:
            reader = csv.reader(f, dialect='excel-tab')
            for row in reader:
                account = row[0]
                currency = row[1]
                date1 = parse_compact_date(row[2])
                balance_before = parse_amount(row[3])
                balance_after = parse_amount(row[4])
                date2 = parse_compact_date(row[5])
                assert date1 == date2
                amount = parse_amount(row[6])
                rest = row[7]
                if rest.startswith('/'):
                    matches = list(key_pattern.finditer(rest))
                    meta: dict[str, str] = {}
                    for m1, m2 in zip(matches, matches[1:]):
                        meta[m1.group(1)] = rest[m1.end():m2.start()]
                    meta[matches[-1].group(1)] = rest[matches[-1].end():].rstrip()
                    description = meta['NAME'] + ' | ' + meta['REMI']
                else:
                    m = card_payment_pattern.match(rest)
                    if m is None:
                        raise RuntimeError(f'{rest!r} does not match card'
                                           'payment pattern.')
                    description = m.group(4)
                    meta = {}
                    meta['nr'] = m.group(1)
                    meta['card_number'] = m.group(5)
                    meta['location'] = m.group(6)
                    day, month, year = m.group(2).split('.')
                    pay_date = date(year=int('20'+year),
                                    month=int(month),
                                    day=int(day))
                    pay_time = m.group(3).replace('.', ':')
                    assert pay_date == date2
                transaction = Transaction(
                        account=self.account,
                        description=description,
                        operation_date=date1,
                        value_date=date2,
                        amount=amount,
                        currency='€' if currency == 'EUR' else currency,
                        metadata=meta)
                transactions.append(transaction)
        self.transactions = transactions

    def parse_metadata(self) -> BankStatementMetadata:
        start_date = min(t.operation_date for t in self.transactions)
        end_date   = max(t.operation_date for t in self.transactions)
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
               )

    def parse_raw(self) -> BankStatement:
        #self.check_transactions_consistency(self.transactions)
        return BankStatement(self.account, self.transactions)


def parse_compact_date(d: str) -> date:
    # year month and day glued together without seperator
    year, month, day = int(d[0:4]), int(d[4:6]), int(d[6:8])
    return date(year, month, day)
