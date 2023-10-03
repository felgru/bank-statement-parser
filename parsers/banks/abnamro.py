# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from collections.abc import Iterator
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from typing import Any, cast, Optional

from .cleaning_rules import abnamro as cleaning_rules
from bank_statement import BankStatement, BankStatementMetadata
from transaction import (
        Balance,
        BaseTransaction,
        MultiTransaction,
        Posting,
        Transaction,
        )
from utils import PeekableIterator

from utils.dates import parse_date_relative_to
from ..parser import BaseCleaningParserConfig
from ..pdf_parser import CleaningParser, PdfParser


class AbnAmroConfig(BaseCleaningParserConfig):
    bank_name = 'ABN AMRO'
    bank_folder = 'abnamro'
    DEFAULT_ACCOUNTS = {
        'checking': 'assets:bank:checking:ABN AMRO',
        'banking fees': 'expenses:banking',
    }


class AbnAmroPdfParser(PdfParser[AbnAmroConfig]):
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

    def parse_raw(self, accounts: dict[str, str]) -> BankStatement:
        transactions = list(self._iter_main_table(accounts))
        transactions.reverse()
        return BankStatement(transactions, self.old_balance, self.new_balance)

    def _iter_main_table(self, accounts: dict[str, str]) -> MainTableIterator:
        meta = self.first_page_metadata
        return MainTableIterator(
                self.pdf_pages,
                date=meta.date,
                currency=meta.currency,
                accounts=accounts)

    def check_transactions_consistency(self,
                                       transactions: list[BaseTransaction],
                                       config: AbnAmroConfig,
                                       ) -> None:
        super().check_transactions_consistency(transactions, config)
        this_account = config.accounts['checking']

        def amount(t: BaseTransaction) -> Decimal:
            if isinstance(t, Transaction):
                return t.amount
            elif isinstance(t, MultiTransaction):
                return sum((p.amount for p in t.postings
                            if p.account == this_account),
                           start=Decimal(0))
            else:
                raise RuntimeError(f'Unknown transaction type {type(t)}.')

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
        m = re.search('Statement of account\n+', text,
                      flags=re.MULTILINE)
        assert m is not None
        addresses_start = m.end()
        m = re.compile(r'(  +)').search(text, addresses_start)
        assert m is not None
        customer_address_offset = m.end(1) - addresses_start
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
    def __init__(self, pdf_pages: list[str], *,
                 date: date, currency: str,
                 accounts: dict[str, str]):
        self.lines = PeekableIterator(MainTableLines(pdf_pages))
        self.date = date
        self.description_parser = DescriptionParser(
                currency=currency,
                accounts=accounts)
        # The transaction table might contain as it's first entry
        # some information text. This text can be identified by a
        # missing bookdate.
        # We simply filter it out, but maybe we could put it in a
        # block comment in the future.
        try:
            line = self.lines.peek()
            while not line.bookdate:
                next(self.lines)
                line = self.lines.peek()
        except StopIteration:
            # Handle empty bank statement, just in case.
            pass

    def __iter__(self) -> MainTableIterator:
        return self

    def __next__(self) -> BaseTransaction:
        # first line
        # If next raises StopIteration let it bubble up the call chain.
        line = next(self.lines)
        bookdate = parse_date_relative_to(line.bookdate, self.date)
        description = [line.description]
        debit = line.amount_debit
        credit = line.amount_credit
        amount = -parse_amount(debit) if debit else parse_amount(credit)
        # second line
        try:
            line = next(self.lines)
        except StopIteration:
            raise AbnAmroPdfParserError(
                    'Transaction with missing second line.') from None
        value_date = parse_date_relative_to(line.bookdate.strip('()'),
                                            self.date)
        # second line can be empty except for value date if we're at
        # the end of the page. We filter out those empty description lines.
        if line.description:
            description.append(line.description)
        while True:
            try:
                line = self.lines.peek()
            except StopIteration:
                break
            if line.bookdate:
                break
            line = next(self.lines)
            description.append(line.description)
        return self.description_parser.parse(
                description=description,
                bookdate=bookdate,
                value_date=value_date,
                amount=amount)


class DescriptionParser:
    SEPA_IDEAL_KEYWORDS = re.compile(r'(IBAN|BIC|Naam|Omschrijving|Kenmerk): ')
    SEPA_OVERBOEKING_KEYWORDS = re.compile(
            r'(IBAN|BIC|Naam|Omschrijving|Betalingskenm.|Kenmerk): ')
    SEPA_PERIODIEKE_OVERBOEKING_KEYWORDS = re.compile(
            r'(IBAN|BIC|Naam|Omschrijving): ')
    SEPA_INCASSO_KEYWORDS = re.compile(
            r'(Incassant|Naam|Machtiging|Omschrijving|IBAN|Kenmerk|Voor): ')
    OLD_BEA_PATTERN = re.compile(
            r'(BEA) +NR:(?P<NR>\w+) +'
            r'(?P<date>\d{2}\.\d{2}\.\d{2})\/(?P<time>\d{2}\.\d{2})\n'
            r'(?P<store>.*),PAS(?P<pas_nr>\d{3})\n'
            r'(?P<location>.*)$'
            )
    NEW_BEA_PATTERN = re.compile(
            r'(BEA), (?P<card_type>.*)\n'
            r'(?P<store>.*),PAS(?P<pas_nr>\d{3})\n'
            r'NR:(?P<NR>\w+?),? +'
            r'(?P<date>\d{2}\.\d{2}\.\d{2})\/'
            r'(?P<time>\d{2}\.\d{2}|\d{2}:\d{2})\n'
            r'(?P<location>.*)(?P<currency_exchange>.*\n.*\n.*\n.*|)'
            r'(?P<extra>|\nTERUGBOEKING BEA-TRANSACTIE)$'
            )
    GEA_PATTERN = re.compile(
            r'(GEA), (?P<card_type>.*)\n'
            r'(?P<atm_name>.*),PAS(?P<pas_nr>\d{3})\n'
            r'NR:(?P<NR>\w+?),? +'
            r'(?P<date>\d{2}\.\d{2}\.\d{2})\/'
            r'(?P<time>\d{2}\.\d{2}|\d{2}:\d{2})'
            r'(?P<location>\n.*|)$'
            )
    CURRENCY_EXCHANGE_PATTERN = re.compile(
            r'\n(?P<foreign_currency>[A-Z]{3}) (?P<foreign_amount>\d+,?\d*)'
            r' 1(?P<currency>[A-Z]{3})=(?P<exchange_rate>\d+,\d+)'
            r' (?P=foreign_currency)\n'
            r'ECB Koers=(?P<ecb_exchange_rate>\d+,\d+)'
            r' OPSLAG (?P<surcharge>\d+,\d+)%\n'
            r'KOSTEN •(?P<costs>\d+,\d\d) ACHTERAF BEREKEND'
            )

    def __init__(self, *,
                 currency: str,
                 accounts: dict[str, str]):
        self.currency = '€' if currency == 'EUR' else currency
        self.account = accounts['checking']
        self.accounts = accounts

    def parse(self,
              description: list[str],
              bookdate: date,
              value_date: date,
              amount: Decimal) -> BaseTransaction:
        transaction_type = description[0]
        if transaction_type == 'SEPA iDEAL':
            return self._parse_from_keywords(transaction_type,
                                             description,
                                             self.SEPA_IDEAL_KEYWORDS,
                                             bookdate=bookdate,
                                             value_date=value_date,
                                             amount=amount)
        elif transaction_type == 'SEPA Overboeking':
            return self._parse_from_keywords(transaction_type,
                                             description,
                                             self.SEPA_OVERBOEKING_KEYWORDS,
                                             bookdate=bookdate,
                                             value_date=value_date,
                                             amount=amount)
        elif transaction_type == 'SEPA Periodieke overb.':
            return self._parse_from_keywords(transaction_type,
                                             description,
                                             self.SEPA_PERIODIEKE_OVERBOEKING_KEYWORDS,
                                             bookdate=bookdate,
                                             value_date=value_date,
                                             amount=amount)
        elif transaction_type.startswith('SEPA Incasso'):
            return self._parse_from_keywords(transaction_type,
                                             description,
                                             self.SEPA_INCASSO_KEYWORDS,
                                             bookdate=bookdate,
                                             value_date=value_date,
                                             amount=amount)
        elif transaction_type.startswith('BEA'):
            return self._parse_bea(description,
                                   bookdate=bookdate,
                                   value_date=value_date,
                                   amount=amount)
        elif transaction_type == 'ABN AMRO Bank N.V.':
            return self._parse_banking_fees(description,
                                            bookdate=bookdate,
                                            value_date=value_date,
                                            amount=amount)
        elif transaction_type.startswith('GEA, '):
            return self._parse_gea(description,
                                   bookdate=bookdate,
                                   value_date=value_date,
                                   amount=amount)
        elif transaction_type == 'INTEREST':
            return self._parse_interest(description,
                                        bookdate=bookdate,
                                        value_date=value_date,
                                        amount=amount)
        else:
            raise AbnAmroPdfParserError(
                    f'Unknown transaction type: {transaction_type}')

    def _parse_from_keywords(self,
                             transaction_type: str,
                             description: list[str],
                             keywords: re.Pattern,
                             *,
                             bookdate: date,
                             value_date: date,
                             amount: Decimal,
                             ) -> BaseTransaction:
        d = dict[str, str]()
        current_key = 'transaction_type'
        current_value = transaction_type
        for line in description[1:]:
            # Lines are broken after 32 characters. If a field is broken into
            # multiple lines and one of those lines ends with a space, this
            # space is is not there in the pdftotext extract.
            # We therefore have to add spaces to fill each line to 32
            # characters.
            assert len(line) <= 32, f'{line!r} has more than 32 characters.'
            if len(line) < 32:
                line += ' ' * (32 - len(line))
            m = keywords.match(line)
            if m is None:
                current_value += line
            else:
                d[current_key] = current_value.rstrip()
                current_key = m.group(1)
                current_value = line[m.end():]
        d[current_key] = current_value.rstrip()
        omschrijving = d.get('Omschrijving')
        if omschrijving is None:
            # TODO: mypy 0.812 does not understand that d.get('..', '') is a str.
            omschrijving = d.get('Kenmerk') or ''
        return Transaction(account=self.account,
                           description=omschrijving,
                           transaction_date=bookdate,
                           value_date=value_date,
                           amount=amount,
                           currency=self.currency,
                           metadata=d)

    def _parse_bea(self,
                   description: list[str],
                   *,
                   bookdate: date,
                   value_date: date,
                   amount: Decimal,
                   ) -> Transaction:
        joined_description = '\n'.join(description)
        if (m := self.OLD_BEA_PATTERN.match(joined_description)) is not None:
            card_type = None
            currency_exchange = ''
            block_comment: str | None = None
        elif (m := self.NEW_BEA_PATTERN.match(joined_description)) is not None:
            card_type = m.group('card_type')
            currency_exchange = m.group('currency_exchange')
            if m.group('extra').startswith('\nTERUGBOEKING '):
                block_comment = 'Terugboeking BEA-transactie'
            elif m.group('extra') == '':
                block_comment = None
            else:
                raise AbnAmroPdfParserError(
                        'Unexpected extra line in BEA transaction: '
                        f'{m.group("extra")}.')
        else:
            raise AbnAmroPdfParserError(
                    f'Could not parse BEA transaction\n{joined_description}')
        d = dict[str, Any](
                transaction_type='BEA',
                card_type=card_type,
                NR=m.group('NR'),
                date=parse_short_year_date(m.group('date')),
                time=m.group('time').replace('.', ':'),
                store=m.group('store'),
                pas_nr=m.group('pas_nr'),
                location=m.group('location'),
                )
        if block_comment is not None:
            d['block_comment'] = block_comment
        assert d['date'] <= bookdate, \
                f"Date {d['date']} later than bookdate {bookdate}."
        assert bookdate == value_date
        if currency_exchange:
            m = self.CURRENCY_EXCHANGE_PATTERN.match(currency_exchange)
            if m is None:
                raise AbnAmroPdfParserError(
                        'Could not parse currency exchange:\n'
                        f'{currency_exchange}')
            assert m.group('currency') == ('EUR' if self.currency == '€'
                                           else self.currency)
            d['foreign_amount'] = parse_amount(m.group('foreign_amount'))
            d['foreign_currency'] = m.group('foreign_currency')
            d['exchange_rate'] = parse_amount(m.group('exchange_rate'))
            d['ecb_exchange_rate'] = parse_amount(m.group('ecb_exchange_rate'))
            d['surcharge'] = parse_amount(m.group('surcharge')) / Decimal(100)
            d['costs'] = parse_amount(m.group('costs'))
        return Transaction(account=self.account,
                           description=d['store'],
                           transaction_date=bookdate,
                           value_date=value_date,
                           amount=amount,
                           currency=self.currency,
                           metadata=d)

    def _parse_gea(self,
                   description: list[str],
                   *,
                   bookdate: date,
                   value_date: date,
                   amount: Decimal,
                   ) -> Transaction:
        joined_description = '\n'.join(description)
        if (m := self.GEA_PATTERN.match(joined_description)) is not None:
            card_type = m.group('card_type')
        else:
            raise AbnAmroPdfParserError(
                    f'Could not parse GEA transaction\n{joined_description}')
        d = dict[str, Any](
                transaction_type='GEA',
                card_type=card_type,
                NR=m.group('NR'),
                date=parse_short_year_date(m.group('date')),
                time=m.group('time').replace('.', ':'),
                atm_name=m.group('atm_name'),
                pas_nr=m.group('pas_nr'),
                location=m.group('location').lstrip(),
                )
        assert d['date'] == bookdate, \
                f"Date {d['date']} does not match bookdate {bookdate}."
        assert bookdate == value_date
        return Transaction(account=self.account,
                           description=f"Withdrawal {d['card_type']}, {d['atm_name']}",
                           transaction_date=bookdate,
                           value_date=value_date,
                           amount=amount,
                           currency=self.currency,
                           metadata=d)

    def _parse_banking_fees(self,
                            description: list[str],
                            *,
                            bookdate: date,
                            value_date: date,
                            amount: Decimal,
                            ) -> MultiTransaction:
        t = MultiTransaction(description=description[0] + ' | Banking fees',
                             transaction_date=bookdate,
                             metadata={
                                 'transaction_type': 'banking fees',
                             })
        t.add_posting(Posting(account=self.account,
                              amount=amount,
                              currency=self.currency,
                              posting_date=value_date,
                              ))
        for line in description[1:]:
            m = re.match(r'(.+?) +(\d+,\d{2})', line)
            assert m is not None
            t.add_posting(Posting(
                    account=self.accounts['banking fees'],
                    amount=parse_amount(m.group(2)),
                    currency=self.currency,
                    posting_date=value_date,
                    comment=m.group(1)))
        return t

    def _parse_interest(self,
                        description: list[str],
                        *,
                        bookdate: date,
                        value_date: date,
                        amount: Decimal,
                        ) -> Transaction:
        interest_type = description[1]
        m = re.match(r'period (\d{2}\.\d{2}\.\d{4}) - (\d{2}\.\d{2}\.\d{4})',
                     description[2])
        comment = '\n'.join(description[3:])
        if m is None:
            raise AbnAmroPdfParserError('Could not parse interest transaction'
                                        f' period {description[2]!r}.')
        d = dict[str, Any](
                transaction_type=description[0],
                interest_type=interest_type,
                period_start=parse_date(m.group(1), separator='.'),
                period_end=parse_date(m.group(2), separator='.'),
                block_comment=comment,
                )
        assert bookdate == value_date
        descr = f"{interest_type} {d['period_start']} to {d['period_end']}"
        return Transaction(account=self.account,
                           description=descr,
                           transaction_date=bookdate,
                           value_date=value_date,
                           amount=amount,
                           currency=self.currency,
                           metadata=d)


class MainTableLines:
    def __init__(self, pdf_pages: list[str]):
        self.pdf_pages = pdf_pages
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

    def __iter__(self) -> MainTableLines:
        return self

    def __next__(self) -> MainTableLine:
        if self.current_line >= len(self.lines):
            if self.current_page >= len(self.pdf_pages) - 1:
                raise StopIteration()
            else:
                self._set_page(self.current_page + 1)
        line = self.lines[self.current_line]
        self.current_line += 1
        if line:
            return MainTableLine(
                    bookdate=line[self.spans['bookdate']].strip(),
                    description=line[self.spans['description']].rstrip(),
                    amount_debit=line[self.spans['amount_debit']].strip(),
                    amount_credit=line[self.spans['amount_credit']].strip(),
                    )
        else:
            return self.__next__()


@dataclass
class MainTableLine:
    bookdate: str
    description: str
    amount_debit: str
    amount_credit: str


def parse_date(d: str, *, separator: str = '-') -> date:
    # Dutch inverse ISO format
    day, month, year = d.split(separator)
    return date(int(year), int(month), int(day))


def parse_short_year_date(d: str) -> date:
    # Dotted year with two digit year
    day, month, year = d.split('.')
    return date(int('20'+year), int(month), int(day))


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


class AbnAmroTsvParserError(RuntimeError):
    pass


class AbnAmroTsvParser(CleaningParser[AbnAmroConfig]):
    file_extension = '.tab'
    num_cols = None
    cleaning_rules = cleaning_rules.rules

    def __init__(self, tsv_file: Path):
        super().__init__(tsv_file)
        if not tsv_file.exists():
            raise IOError(f'Unknown file: {tsv_file}')
        self._raw_transactions = AbnAmroTsvRow.read_rows_from_file(tsv_file)

    def parse_metadata(self) -> BankStatementMetadata:
        start_date = min(t.date1 for t in self._raw_transactions)
        end_date   = max(t.date1 for t in self._raw_transactions)
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
               )

    def parse_raw(self, accounts: dict[str, str]) -> BankStatement:
        parser = AbnAmroTsvRowParser(accounts)
        transactions = [parser.parse(row) for row in self._raw_transactions]
        #self.check_transactions_consistency(transactions)
        return BankStatement(transactions)


@dataclass
class AbnAmroTsvRow:
    account: str
    currency: str
    date1: date
    balance_before: Decimal
    balance_after: Decimal
    date2: date
    amount: Decimal
    rest: str

    @classmethod
    def read_rows_from_file(cls, tsv_file: Path) -> list[AbnAmroTsvRow]:
        with open(tsv_file, newline='') as f:
            reader = csv.reader(f, dialect='excel-tab')
            rows = []
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
                rows.append(cls(
                    account=account,
                    currency=currency,
                    date1=date1,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    date2=date2,
                    amount=amount,
                    rest=rest,
                ))
        return rows


class AbnAmroTsvRowParser:
    def __init__(self, accounts: dict[str, str]):
        self.this_account = accounts['checking']
        self.accounts = accounts
        self.key_pattern = re.compile(r'/([A-Z]+)/')
        self.old_bea_pattern = re.compile(
                r'(BEA) +NR:(?P<NR>\w+) +'
                r'(?P<date>\d{2}\.\d{2}\.\d{2})\/(?P<time>\d{2}\.\d{2}) +'
                r'(?P<store>.*),PAS(?P<pas_nr>\d{3}) +'
                r'(?P<location>.*)'
                )
        self.new_bea_pattern = re.compile(
                r'(BEA), (?P<card_type>.*?) +'
                r'(?P<store>.*),PAS(?P<pas_nr>\d{3}) +'
                r'NR:(?P<NR>\w+?),? +'
                r'(?P<date>\d{2}\.\d{2}\.\d{2})\/'
                r'(?P<time>\d{2}\.\d{2}|\d{2}:\d{2}) +'
                r'(?P<location>.*)(?P<currency_exchange>.*? +.*? +.*? +.*?|)'
                r'(?P<extra>| +TERUGBOEKING BEA-TRANSACTIE)'
                )
        # TODO: Currency exchange pattern guessed from PDF parser, might need
        #       adjustments.
        self.currency_exchange_pattern = re.compile(
                r'\n(?P<foreign_currency>[A-Z]{3}) (?P<foreign_amount>\d+,?\d*)'
                r' 1(?P<currency>[A-Z]{3})=(?P<exchange_rate>\d+,\d+)'
                r' (?P=foreign_currency) +'
                r'ECB Koers=(?P<ecb_exchange_rate>\d+,\d+)'
                r' OPSLAG (?P<surcharge>\d+,\d+)% +'
                r'KOSTEN •(?P<costs>\d+,\d\d) ACHTERAF BEREKEND'
                )
        self.banking_fee_pattern = re.compile(
                r'(ABN AMRO Bank N.V.) +((.+? +\d+,\d\d)+)')
        self.banking_fee_item_pattern = re.compile(
                r'(.+?) +(\d+,\d\d)')

    def parse(self, row: AbnAmroTsvRow) -> BaseTransaction:
        currency = '€' if row.currency == 'EUR' else row.currency
        rest = row.rest
        if rest.startswith('/'):
            matches = list(self.key_pattern.finditer(rest))
            meta: dict[str, str] = {}
            for m1, m2 in zip(matches, matches[1:]):
                meta[m1.group(1)] = rest[m1.end():m2.start()].rstrip()
            meta[matches[-1].group(1)] = rest[matches[-1].end():].rstrip()
            description = meta['NAME'] + ' | ' \
                        + cast(str, meta.get('REMI', meta.get('EREF')))
            meta['transaction_type'] = meta['TRTP']
        elif rest.startswith('BEA'):
            if (m := self.old_bea_pattern.match(rest)) is not None:
                card_type = None
                currency_exchange = ''
                block_comment: str | None = None
            elif (m := self.new_bea_pattern.match(rest)) is not None:
                card_type = m.group('card_type')
                currency_exchange = m.group('currency_exchange')
                if m.group('extra').lstrip().startswith('TERUGBOEKING '):
                    block_comment = 'Terugboeking BEA-transactie'
                elif m.group('extra') == '':
                    block_comment = None
                else:
                    raise AbnAmroTsvParserError(
                            'Unexpected extra line in BEA transaction: '
                            f'{m.group("extra")}.')
            else:
                raise AbnAmroTsvParserError(f'Unknown BEA pattern: {rest!r}.')
            d = dict[str, Any](
                    transaction_type='BEA',
                    card_type=card_type,
                    NR=m.group('NR'),
                    date=parse_short_year_date(m.group('date')),
                    time=m.group('time').replace('.', ':'),
                    store=m.group('store'),
                    pas_nr=m.group('pas_nr'),
                    location=m.group('location').rstrip(),
                    )
            if block_comment is not None:
                d['block_comment'] = block_comment
            assert d['date'] == row.date1, \
                    f"Date {d['date']} does not match bookdate {row.date1}."
            if currency_exchange:
                m = self.currency_exchange_pattern.match(currency_exchange)
                if m is None:
                    raise AbnAmroTsvParserError(
                            'Could not parse currency exchange:\n'
                            f'{currency_exchange}')
                assert m.group('currency') == row.currency
                d['foreign_amount'] = parse_amount(m.group('foreign_amount'))
                d['foreign_currency'] = m.group('foreign_currency')
                d['exchange_rate'] = parse_amount(m.group('exchange_rate'))
                d['ecb_exchange_rate'] = parse_amount(
                        m.group('ecb_exchange_rate'))
                d['surcharge'] = parse_amount(m.group('surcharge')) \
                                 / Decimal(100)
                d['costs'] = parse_amount(m.group('costs'))
            description = d['store']
            meta = d
        elif (m := self.banking_fee_pattern.match(rest)) is not None:
            t = MultiTransaction(
                    description=f'{m.group(1)} | Banking fees',
                    transaction_date=row.date1,
                    metadata={
                        'transaction_type': 'banking fees',
                    })
            t.add_posting(Posting(
                account=self.this_account,
                amount=row.amount,
                currency=currency,
                posting_date=row.date2,
                ))
            for m in self.banking_fee_item_pattern.finditer(m.group(2)):
                t.add_posting(Posting(
                        account=self.accounts['banking fees'],
                        amount=parse_amount(m.group(2)),
                        currency=currency,
                        posting_date=row.date2,
                        comment=m.group(1)))
            return t
        else:
            raise RuntimeError(f'{rest!r} does not match any known '
                               'transaction pattern.')
        return Transaction(
                account=self.this_account,
                description=description,
                transaction_date=row.date1,
                value_date=row.date2,
                amount=row.amount,
                currency=currency,
                metadata=meta)


def parse_compact_date(d: str) -> date:
    # year month and day glued together without seperator
    year, month, day = int(d[0:4]), int(d[4:6]), int(d[6:8])
    return date(year, month, day)
