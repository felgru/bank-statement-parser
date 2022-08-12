# SPDX-FileCopyrightText: 2021–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional, TextIO

from bank_statement import BankStatement, BankStatementMetadata
from .parser import CleaningParser
from transaction import Transaction

class QifParser(CleaningParser, metaclass=ABCMeta):
    file_extension = '.qif'
    currency: str

    def __init__(self, qif_file: Path):
        super().__init__(qif_file)
        self.qif_file = qif_file

    def parse_metadata(self) -> BankStatementMetadata:
        start_date = None  # TODO
        end_date = None  # TODO
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date)

    def parse_raw(self) -> BankStatement:
        if not self.qif_file.exists():
            raise IOError(f'Unknown file: {self.qif_file}')
        with open(self.qif_file) as f:
            header = f.readline()
            if not header == '!Type:Bank\n':
                raise RuntimeError(f'Unknown QIF account type: {header}')
            transactions = self._parse_transactions(f)
        return BankStatement(transactions=transactions)

    def _parse_transactions(self, file_: TextIO) -> list[Transaction]:
        transactions = []
        while line := file_.readline():
            type_, rest = line[0], line[1:]
            if type_ == 'D':
                date = self.parse_date(rest)
            elif type_ == 'T':
                amount = Decimal(rest)
            elif type_ == 'P':
                description = rest.removesuffix('\n')
            elif type_ == '^':
                transactions.append(Transaction(
                    account=self.account,
                    description=description,
                    operation_date=date,
                    value_date=None,
                    amount=amount,
                    currency=self.currency,
                    metadata={
                        'raw_description': description,
                        }))
            else:
                raise RuntimeError(f'Unknown QIF code: {type_}')
        if type_ != '^':
            raise RuntimeError('QIF file does not end in ^.')
        return list(reversed(transactions))

    @classmethod
    @abstractmethod
    def parse_date(cls, input_: str) -> date:
        pass
