# SPDX-FileCopyrightText: 2019, 2021–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections import defaultdict, OrderedDict
import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import re
from typing import Optional, TypedDict

from .cleaning_rules import paypal as cleaning_rules
from bank_statement import BankStatement, BankStatementMetadata
from ..parser import BaseCleaningParserConfig, CleaningParser
from transaction import BaseTransaction, MultiTransaction, Posting


class PayPalConfig(BaseCleaningParserConfig):
    bank_name = 'PayPal'
    bank_folder = 'paypal'
    DEFAULT_ACCOUNTS = {
        'balancing account': 'assets:balancing:paypal',
    }


class PostingDict(TypedDict):
    type: str
    account: Optional[str]
    description: str
    date: date
    amount: Decimal
    currency: str


class PayPalCsvParser(CleaningParser[PayPalConfig]):
    file_extension = '.csv'
    cleaning_rules = cleaning_rules.rules

    def __init__(self, csv_file: Path):
        super().__init__(csv_file)
        self._parse_file(csv_file)

    def _parse_file(self, csv_file: Path) -> None:
        if not csv_file.exists():
            raise IOError(f'Unknown file: {csv_file}')
        postings: OrderedDict[str, list[PostingDict]] = OrderedDict()
        related_postings = defaultdict(list)
        with open(csv_file, newline='', encoding='UTF-8-sig') as f:
            reader = csv.DictReader(f, dialect='unix')
            for row in reader:
                gross_amount = parse_amount(row['Brutto'])
                net_amount = parse_amount(row['Netto'])
                fee_amount = parse_amount(row['Gebühr'])
                # Handling of transaction fees not implemented, yet.
                assert fee_amount == 0
                assert gross_amount == net_amount
                currency = translate_currency(row['Währung'])
                transaction_code = row['Transaktionscode']
                transaction_date = parse_date(row['Datum'])
                name = row['Name']
                description = row['Betreff']
                if name != '':
                    if description != '':
                        description = name + ' | ' + description
                    else:
                        description = name
                type_ = row['Typ']
                amount = -net_amount
                if type_ == 'Allgemeine Währungsumrechnung':
                    type_ = 'currency_conversion'
                    amount = net_amount
                elif (type_.startswith('Allgemeine Gutschrift')
                      # can contain trailing whitespace :-(
                      or type_.startswith('Bankgutschrift auf PayPal-Konto')):
                    type_ = 'credit'
                else:
                    type_ = 'expense'
                posting: PostingDict = dict(
                    type=type_,
                    account=None,
                    description=description,
                    date=transaction_date,
                    amount=amount,
                    currency=currency,
                    )
                postings[transaction_code] = [posting]
                related_transaction = row['Zugehöriger Transaktionscode']
                if related_transaction != '':
                    related_postings[related_transaction] \
                                                .append(transaction_code)
        to_remove = []
        for transaction_code, to_merge in related_postings.items():
            posting_list = postings.get(transaction_code)
            if posting_list is None:
                continue
            for transaction_code in to_merge:
                posting_list.extend(postings[transaction_code])
                to_remove.append(transaction_code)
        for code in to_remove:
            del postings[code]
        self.raw_postings = postings

    def parse_metadata(self) -> BankStatementMetadata:
        start_date = min(p['date']
                         for l in self.raw_postings.values()
                         for p in l)
        end_date   = max(p['date']
                         for l in self.raw_postings.values()
                         for p in l)
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
               )

    def parse_raw(self, accounts: dict[str, str]) -> BankStatement:
        balancing_account = accounts['balancing account']
        for posting_list in self.raw_postings.values():
            for posting in posting_list:
                if posting['type'] == 'credit':
                    posting['account'] = balancing_account
        transactions = []
        known_keys = {'credit', 'expense', 'currency_conversion'}
        for posting_list in self.raw_postings.values():
            by_type: defaultdict[str, list[PostingDict]] = defaultdict(list)
            for posting in posting_list:
                by_type[posting['type']].append(posting)
            assert known_keys.issuperset(by_type.keys())
            credit = by_type.get('credit')
            assert credit is not None and len(credit) == 1
            expenses = by_type.get('expense')
            assert expenses is not None and len(expenses) == 1
            transaction = MultiTransaction(
                    expenses[0]['description'],
                    expenses[0]['date'])
            credit_posting = Posting(
                    account=credit[0]['account'],
                    amount=credit[0]['amount'],
                    currency=credit[0]['currency'],
                    )
            expense_posting = Posting(
                    account=expenses[0]['account'],
                    amount=expenses[0]['amount'],
                    currency=expenses[0]['currency'],
                    )
            currency_conversion = by_type.get('currency_conversion')
            if currency_conversion is not None:
                assert len(currency_conversion) == 2
                for cc in currency_conversion:
                    if cc['currency'] == expense_posting.currency:
                        assert expense_posting.amount == cc['amount']
                    else:
                        assert credit_posting.currency == cc['currency']
                        assert credit_posting.amount == cc['amount']
                        expense_posting.conversion_price = (
                                -cc['amount'],
                                cc['currency'])
            transaction.add_posting(credit_posting)
            transaction.add_posting(expense_posting)
            transactions.append(transaction)
        #self.check_transactions_consistency(transactions)
        return BankStatement(transactions)


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


def translate_currency(currency: str) -> str:
    if currency == 'EUR':
        return '€'
    else:
        return currency
