# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Iterator
import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import re
from typing import cast, Optional

from .cleaning_rules import ing_de as cleaning_rules
from bank_statement import BankStatement, BankStatementMetadata
from transaction import (BaseTransaction, Balance, MultiTransaction,
                         Posting, Transaction)

from ..parser import BaseCleaningParserConfig, CleaningParser
from ..pdf_parser import OldPdfParser


class IngDeConfig(BaseCleaningParserConfig):
    bank_name = 'ING.de'
    bank_folder = 'ing.de'
    DEFAULT_ACCOUNTS = {
        'Girokonto': 'assets:bank:checking:ING.de',
        'Extra-Konto': 'assets:bank:saving:ING.de',
        'interest': 'income:interest:ING.de',
    }


class IngDePdfParser(OldPdfParser[IngDeConfig]):
    bank_folder = 'ing.de'
    config_type = IngDeConfig
    num_cols = 5

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self._parse_metadata()
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + r' *(\S.*)\n',
                flags=re.MULTILINE)
        if self.metadata.account_type == 'Girokonto':
            self.cleaning_rules = cleaning_rules.rules
        elif self.metadata.account_type == 'Extra-Konto':
            self.cleaning_rules = cleaning_rules.extra_konto_rules
        else:
            raise RuntimeError(
                    f'Unknown account type: {self.metadata.account_type!r}.')

    table_heading = re.compile(r'^ *Buchung *(Buchung / Verwendungszweck) *'
                               r'Betrag \(EUR\)\n *Valuta\n*',
                               flags=re.MULTILINE)

    def _parse_description_start(self) -> None:
        m = self.table_heading.search(self.pdf_pages[0])
        assert m is not None, 'Could not find table heading.'
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self) -> BankStatementMetadata:
        return self.metadata

    def parse_raw(self, accounts: dict[str, str]) -> BankStatement:
        if self.metadata.account_type not in ('Girokonto', 'Extra-Konto'):
            raise NotImplementedError('parsing of %s not supported.'
                                      % self.metadata.account_type)
        bank_statement = super().parse_raw(accounts)
        if self.metadata.account_type == 'Extra-Konto':
            self._add_interest_details(bank_statement, accounts)
        return bank_statement

    def _add_interest_details(self,
                              bank_statement: BankStatement,
                              accounts: dict[str, str]) -> None:
        interests = self.parse_interest_postings(accounts)
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
                r'\n*^ {{0,{}}}[^ \d]'.format(self.description_start - 1),
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

    def parse_interest_postings(self,
                                accounts: dict[str, str]) -> list[Posting]:
        interest_table = self.extract_interest_table()
        postings = []
        for m in re.finditer(r'^ +(.+?)  +(.+?%) +(.+?)  +(.+,\d\d)$',
                             interest_table, flags=re.MULTILINE):
            description = ' '.join(m.group(i) for i in (1, 2, 3))
            postings.append(Posting(accounts['interest'],
                                    -parse_amount(m.group(4)),
                                    comment=description))
        return postings

    def extract_interest_table(self) -> str:
        self.interest_table_heading = re.compile(
                r'^ *Zeitraum *Zins p\.a\. *Ertrag',
                flags=re.MULTILINE)
        self.footer_start_pattern = re.compile(
                r'\n*^ {{0,{}}}[^ \d]'.format(self.description_start - 1),
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
        m = re.search(r'Datum +(\d{2}.\d{2}.\d{4})', self.pdf_pages[0])
        assert m is not None, 'Date of new balance not found.'
        new_date = parse_date(m.group(1))
        m = re.search(r'Alter Saldo +(-?\d[.\d]*,\d\d)', self.pdf_pages[0])
        assert m is not None, 'Old balance not found.'
        old = parse_amount(m.group(1))
        m = re.search(r'Neuer Saldo +(-?\d[.\d]*,\d\d)', self.pdf_pages[0])
        assert m is not None, 'New balance not found.'
        new = parse_amount(m.group(1))
        self.old_balance = Balance(old, cast(date, None))
        self.new_balance = Balance(new, new_date)

    def parse_balances(self) -> None:
        self.transactions_start = 0
        m = re.search(r'\S*Neuer Saldo *(-?\d[.\d]*,\d\d)',
                      self.transactions_text)
        assert m is not None, 'Could not find new balance.'
        assert parse_amount(m.group(1)) == self.new_balance.balance
        self.transactions_end = m.start()

    def generate_transactions(self, start: int, end: int,
                              accounts: dict[str, str],
                              ) -> Iterator[BaseTransaction]:
        account = accounts[self.metadata.account_type]
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
            description = '\n'.join(l for l in description_lines if l)
            yield Transaction(account, description, transaction_date,
                              value_date, amount,
                              metadata=dict(type=transaction_type))
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)

    def check_transactions_consistency(self,
                transactions: list[BaseTransaction],
                config: IngDeConfig) -> None:
        account = config.accounts[self.metadata.account_type]
        def amount(transaction: BaseTransaction):
            if isinstance(transaction, Transaction):
                return transaction.amount
            elif isinstance(transaction, MultiTransaction):
                return sum(p.amount for p in transaction.postings
                           if p.account == account)

        assert self.old_balance.balance + sum(amount(t)
                                              for t in transactions) \
               == self.new_balance.balance


class IngDeCsvParser(CleaningParser[IngDeConfig]):
    bank_folder = 'ing.de'
    file_extension = '.csv'
    config_type = IngDeConfig

    def __init__(self, csv_file: Path):
        super().__init__(csv_file)
        self.csv_file = csv_file
        self._parse_metadata()

    def _parse_metadata(self) -> None:
        metadata = dict[str, str]()
        with self.csv_file.open(encoding='LATIN1') as f:
            line = f.readline()
            assert line.startswith('Umsatzanzeige;')
            while (line := f.readline()) != '\n':
                pass
            while (line := f.readline()) != '\n':
                key, semicolon, value = line.partition(';')
                assert semicolon != ''
                metadata[key] = value.rstrip('\n')
            while (line := f.readline()) != '':
                line = line.rstrip('\n')
                if line == (
                        'Buchung;Valuta;Auftraggeber/Empfänger;Buchungstext;'
                        'Verwendungszweck;Saldo;Währung;Betrag;Währung'):
                    self.csv_start = f.tell()
                    self.csv_fieldnames = line.split(';')
                    self.csv_fieldnames[self.csv_fieldnames.index('Währung')] \
                            = 'SaldoWährung'
                    break
            else:
                raise RuntimeError(f'{self.csv_file} does not contain any CSV data.')
        start, _, end = metadata['Zeitraum'].partition(' - ')
        meta = BankStatementMetadata(
                start_date=parse_date(start),
                end_date=parse_date(end),
                iban=metadata['IBAN'],
                account_owner=metadata['Kunde'],
                account_type=metadata['Kontoname'],
               )
        self.metadata = meta
        balance, _, currency = metadata['Saldo'].partition(';')
        self.balance = parse_amount(balance)
        self.currency = currency

    def parse_metadata(self) -> BankStatementMetadata:
        return self.metadata

    def parse_raw(self, accounts: dict[str, str]) -> BankStatement:
        try:
            account = accounts[self.metadata.account_type]
        except:
            raise RuntimeError(
                    f'Unknown account type: {self.metadata.account_type!r}.')
        with self.csv_file.open(encoding='LATIN1', newline='') as f:
            f.seek(self.csv_start)
            reader = csv.DictReader(f, fieldnames=self.csv_fieldnames,
                                    dialect=IngCsvDialect)
            transactions: list[BaseTransaction] = []
            for row in reader:
                transaction_type = row['Buchungstext']
                destination = row['Auftraggeber/Empfänger']
                description = destination + ' | ' + row['Verwendungszweck']
                currency = row['Währung']
                if currency == 'EUR':
                    currency = '€'
                transaction = Transaction(
                        account=account,
                        description=description,
                        operation_date=parse_date(row['Buchung']),
                        value_date=parse_date(row['Valuta']),
                        amount=parse_amount(row['Betrag']),
                        currency=currency,
                        metadata={
                            'type': transaction_type,
                            },
                        )
                transactions.append(transaction)
            transactions = list(reversed(transactions))
            return BankStatement(transactions)


class IngCsvDialect(csv.Dialect):
    delimiter: str = ';'
    quoting = csv.QUOTE_NONE
    lineterminator = '\n'


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
