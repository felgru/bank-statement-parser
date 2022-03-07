# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import subprocess
from typing import Iterable, Optional, Union

from bank_statement import BankStatement, BankStatementMetadata
from .parser import Parser
from transaction import BaseTransaction, Balance, MultiTransaction, Transaction


def read_pdf_file(pdf_file: Path, *, cols: Optional[int] = None) -> list[str]:
    """Read PDF file.

    This function reads the text of a PDF file into a list of pages.
    Internally it uses the `pdftotext` program which is part of the
    poppler-utils package on Debian.

    Given the `cols` option, the PDF is parsed as a table with that many
    columns, otherwise we try to keep the existing formating.
    """
    if not pdf_file.exists():
        raise IOError(f'Unknown file: {pdf_file}')
    if cols is not None:
        formatting = ['-fixed', str(cols)]
    else:
        formatting = ['-layout']
    # pdftotext is provided by poppler-utils on Debian
    pdftext = subprocess.run(['pdftotext', *formatting, str(pdf_file), '-'],
                             capture_output=True, encoding='UTF8',
                             check=True).stdout
    # Careful: There's a trailing \f on the last page
    pdf_pages = pdftext.split('\f')[:-1]
    return pdf_pages


class PdfParser(Parser, metaclass=ABCMeta):
    file_extension = '.pdf'

    transactions_start: int
    transactions_end: int
    old_balance: Balance
    new_balance: Balance
    total_credit: Decimal
    total_debit: Decimal
    num_cols: Optional[int] = None

    def __init__(self, pdf_file: Path, rules_dir: Optional[Path]):
        super().__init__(pdf_file, rules_dir)
        self._parse_file(pdf_file)

    def _parse_file(self, pdf_file: Path) -> None:
        self.pdf_pages = read_pdf_file(pdf_file, cols=self.num_cols)

    @abstractmethod
    def parse_metadata(self) -> BankStatementMetadata:
        pass

    def check_transactions_consistency(self,
                                       transactions: list[BaseTransaction]) \
                                                                    -> None:
        assert self.old_balance.balance \
               + self.total_credit - self.total_debit \
                == self.new_balance.balance

class OldPdfParser(PdfParser):
    def parse(self) -> BankStatement:
        self.transactions_text = self.extract_transactions_table()
        self.parse_balances()
        transactions = [t for t in self.generate_transactions(
                                            self.transactions_start,
                                            self.transactions_end)]
        self.check_transactions_consistency(transactions)
        transactions = self.clean_up_transactions(transactions)
        self.map_accounts(transactions)
        return BankStatement(self.account, transactions,
                             self.old_balance, self.new_balance)

    @abstractmethod
    def parse_balances(self) -> None:
        pass

    def extract_transactions_table(self) -> str:
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    @abstractmethod
    def extract_table_from_page(self, page: str) -> str: pass

    @abstractmethod
    def generate_transactions(self, start: int, end: int) \
                                    -> Iterable[BaseTransaction]: pass
