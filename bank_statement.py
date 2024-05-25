# SPDX-FileCopyrightText: 2019–2022, 2024 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Iterable
from datetime import date
import json
from typing import Any, Optional, TextIO

from transaction import BaseTransaction, Balance

class BankStatement:
    def __init__(self,
                 transactions: Iterable[BaseTransaction],
                 old_balance: Optional[Balance] = None,
                 new_balance: Optional[Balance] = None):
        self.transactions = list(transactions)
        self.old_balance = old_balance
        self.new_balance = new_balance

    def write_ledger(self, outfile: TextIO) -> None:
        if self.old_balance is not None:
            date = self._format_date(self.old_balance.date)
            print(f'; old balance{date}: {self.old_balance.balance} €\n',
                  file=outfile)
        for t in self.transactions:
            print(t.format_as_ledger_transaction(), file=outfile)
        if self.new_balance is not None:
            date = self._format_date(self.new_balance.date)
            print(f'; new balance{date}: {self.new_balance.balance} €',
                  file=outfile)

    def write_raw(self, outfile: TextIO) -> None:
        if self.old_balance is not None:
            date = self._format_date(self.old_balance.date)
            print(f'old balance{date}: {self.old_balance.balance} €\n',
                  file=outfile)
        for transaction in self.transactions:
            print(f'{transaction!r}\n', file=outfile)
        if self.new_balance is not None:
            date = self._format_date(self.new_balance.date)
            print(f'new balance{date}: {self.new_balance.balance} €',
                  file=outfile)

    @staticmethod
    def _format_date(d: Optional[date]) -> str:
            return f' on {d}' if d is not None else ''

class BankStatementMetadata:
    def __init__(self, start_date: date, end_date: date,
                 iban: Optional[str] = None, bic: Optional[str] = None,
                 account_owner: Optional[str] = None,
                 owner_number: Optional[str] = None,
                 card_number: Optional[str] = None,
                 account_number: Optional[str] = None,
                 **extra: Any):
        self.account_owner = account_owner
        self.iban = iban
        self.bic = bic
        self.owner_number = owner_number
        self.card_number = card_number
        self.account_number = account_number
        self.start_date = start_date
        self.end_date = end_date
        self.extra = dict(extra)

    def __getattr__(self, key: str) -> Any:
        return self.extra[key]

    def write(self, outfile: TextIO) -> None:
        print(f'account owner: {self.account_owner}', file=outfile)
        print(f'IBAN: {self.iban}', file=outfile)
        print(f'BIC: {self.bic}', file=outfile)
        print(f'owner number: {self.owner_number}', file=outfile)
        print(f'card number: {self.card_number}', file=outfile)
        print(f'account number: {self.account_number}', file=outfile)
        print(f'start date: {self.start_date}', file=outfile)
        print(f'end date: {self.end_date}', file=outfile)
        for key, value in sorted(self.extra.items()):
            print(f'{key}: {value}', file=outfile)

    def write_json(self, outfile: TextIO) -> None:
        data = {s: str(getattr(self, s)) for s in [
                'account_owner', 'iban', 'bic', 'owner_number', 'card_number',
                'account_number', 'start_date', 'end_date']}
        data.update(self.extra)
        print(json.dumps(data), file=outfile)
