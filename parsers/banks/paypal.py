# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections import defaultdict, OrderedDict
import csv
from datetime import date, timedelta
from decimal import Decimal
import os
import re

from .cleaning_rules import paypal as cleaning_rules
from bank_statement import BankStatement, BankStatementMetadata
from ..parser import Parser
from transaction import MultiTransaction, Posting
from xdg_dirs import getXDGdirectories

class PayPalCsvParser(Parser):
    bank_folder = 'paypal'
    account = 'assets::online::paypal'
    file_extension = '.csv'
    cleaning_rules = cleaning_rules.rules

    def __init__(self, csv_file):
        super().__init__(csv_file)
        self._parse_file(csv_file)

    def _parse_file(self, csv_file):
        if not os.path.exists(csv_file):
            raise IOError('Unknown file: {}'.format(csv_file))
        transactions = OrderedDict()
        related_transactions = defaultdict(list)
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
                transaction = MultiTransaction(description,
                                               transaction_date)
                transaction.add_posting(Posting(
                    account=self.account,
                    amount=net_amount,
                    currency=currency,
                    ))
                type_ = row['Typ']
                if type_.startswith('Allgemeine Gutschrift'):
                    account2 = 'equity::balancing::paypal'
                else:
                    account2 = None
                transaction.add_posting(Posting(
                    account=account2,
                    amount=-net_amount,
                    currency=currency,
                    ))
                transactions[transaction_code] = transaction
                related_transaction = row['Zugehöriger Transaktionscode']
                if related_transaction != '':
                    related_transactions[related_transaction] \
                                                .append(transaction_code)
        to_remove = []
        for transaction_code, to_merge in related_transactions.items():
            transaction = transactions.get(transaction_code)
            if transaction is None:
                continue
            for transaction_code in to_merge:
                mergeable_transaction = transactions[transaction_code]
                for posting in mergeable_transaction.postings:
                    transaction.add_posting(posting)
                to_remove.append(transaction_code)
        for code in to_remove:
            del transactions[code]
        self.transactions = list(transactions.values())

    def parse_metadata(self) -> BankStatementMetadata:
        start_date = min(t.date for t in self.transactions)
        end_date   = max(t.date for t in self.transactions)
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
               )

    def parse(self) -> BankStatement:
        transactions = self.transactions
        #self.check_transactions_consistency(transactions)
        transactions = self.clean_up_transactions(transactions)
        #self.map_accounts(transactions)
        return BankStatement(self.account, transactions)

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
