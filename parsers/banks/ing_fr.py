# SPDX-FileCopyrightText: 2019–2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from typing import Iterator, Optional

from .cleaning_rules import ing_fr as cleaning_rules
from bank_statement import BankStatementMetadata
from transaction import AnyTransaction, Balance, Transaction

from ..pdf_parser import PdfParser
from ..qif_parser import QifParser


class IngFrPdfParser(PdfParser):
    bank_folder = 'ing.fr'
    account = 'assets:bank:TODO:ING.fr' # exact account is set in __init__

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        m = re.search('RELEVE ([A-Z ]+)', self.pdf_pages[0])
        assert m is not None, 'Account type not found.'
        self.account_type = m.group(1)
        if self.account_type == 'COMPTE COURANT':
            self.account_type = 'Compte Courant'
            self.account = 'assets:bank:checking:ING.fr'
            self.cleaning_rules = cleaning_rules.checkings_rules
        elif self.account_type == 'LIVRET A':
            self.account_type = 'Livret A'
            self.account = 'assets:bank:saving:ING.fr:Livret A'
            self.cleaning_rules = cleaning_rules.savings_rules
        elif self.account_type == 'LDD':
            self.account = 'assets:bank:saving:ING.fr:LDD'
            self.cleaning_rules = cleaning_rules.savings_rules
        else:
            raise RuntimeError(
                    f'unknown ING.fr account type: {self.account_type}')
        self.debit_start, self.credit_start = self.parse_column_starts()

    table_heading = re.compile(r"^\s*Date de\s*Date de\s*Nature de l'opération\s*"
                               r"(Débit\(EUR\))\s*(Crédit\(EUR\))"
                               r"\n\s*l'opération\s*valeur\n",
                               flags=re.MULTILINE)
    end_pattern = re.compile(r"\n* *\* TAEG: Taux Annuel Effectif Global"
                             r" sur la période")
    first_line_pattern = re.compile(
            r'^\s*(\d{2}\/\d{2}\/\d{4})\s*(\d{2}\/\d{2}\/\d{4}|)'
            r'\s*(\S.+\S)\s+',
            flags=re.MULTILINE)
    amount_pattern = re.compile(r'\s*(\d[ \d]*,\d\d)\n')
    middle_line_pattern = re.compile(r'\s{30}\s*(\S.*?)\n')
    total_pattern = re.compile(r'^ *Total\s*(\d[ \d]*,\d\d)\s*(\d[ \d]*,\d\d|)\s*'
                               r'^( *)Nouveau solde au (\d{2}\/\d{2}\/\d{4})'
                               r'\s*(\d[ \d]*,\d\d)',
                               flags=re.MULTILINE)

    def parse_column_starts(self) -> tuple[int, int]:
        m = self.table_heading.search(self.pdf_pages[0])
        assert m is not None, 'Table heading not found.'
        line_start = m.start()
        debit_start = m.start(1) - line_start
        credit_start = m.start(2) - line_start
        return debit_start, credit_start

    def parse_metadata(self) -> BankStatementMetadata:
        m = re.search(r'Du (\d{2}\/\d{2}\/\d{4}) au (\d{2}\/\d{2}\/\d{4})',
                      self.pdf_pages[0])
        assert m is not None, 'Start date not found.'
        start_date = parse_date(m.group(1))
        end_date = parse_date(m.group(2))
        m = re.search(r'Nom, prénom Titulaire 1 :\n\s*((\S+\s)*)',
                      self.pdf_pages[0], flags=re.MULTILINE)
        assert m is not None, 'Account owner not found.'
        account_owner = m.group(1).strip()
        m = re.search(r'BIC .+?\n *(.+?)\n', self.pdf_pages[0])
        assert m is not None, 'BIC not found.'
        bic = m.group(1)
        m = re.search(r'IBAN\n *(.+?)\n', self.pdf_pages[0])
        assert m is not None, 'IBAN not found.'
        iban = m.group(1)
        card_number: Optional[str]
        if self.account_type == 'Compte Courant':
            m = re.search(r'N° Client Titulaire 1 : (\d+)\s*'
                          r'N° carte Titulaire 1 : ([0-9*]+)\s*'
                          r'N° du Compte Courant : (\d+)',
                          self.pdf_pages[0])
            assert m is not None, 'Account number not found.'
            owner_number = m.group(1)
            card_number = m.group(2)
            account_number = m.group(3)
        elif self.account_type in ('Livret A', 'LDD'):
            account_type = 'livret A' if self.account_type == 'Livret A' \
                          else self.account_type
            m = re.search(r'N° Client Titulaire 1 : (\d+)\s*'
                          r'Total des intérêts (acquis|payés)'
                          r' au (\d{2}/\d{2}/\d{4}) : +([0-9,]+) *€\s*'
                          f'N° du {account_type} : ' r'(\d+)\s*'
                          r'Taux en vigueur au (\d{2}/\d{2}/\d{4}) ?\* : (\d+,\d\d) %',
                          self.pdf_pages[0])
            assert m is not None, 'Account number not found.'
            owner_number = m.group(1)
            card_number = None
            interest_date = m.group(3)
            interest = m.group(4)
            account_number = m.group(5)
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                account_owner=account_owner,
                bic=bic,
                iban=iban,
                owner_number=owner_number,
                card_number=card_number,
                account_number=account_number,
               )
        return meta

    def extract_table_from_page(self, page: str) -> str:
        m = self.table_heading.search(page)
        if m is None:
            # When the last page only contains the Total and Nouveau
            # solde lines, it doesn't contain the header of the table.
            m = self.total_pattern.search(page)
            if m is not None:
                return page[m.start():m.end()]
            else:
                # There can be pages without a table
                return ""
        table_start = m.end()

        m = self.end_pattern.search(page)
        if m is not None:
            table_end = m.start() + 1
        else:
            table_end = len(page)
        return page[table_start:table_end]

    def parse_balances(self) -> None:
        self.parse_old_balance()
        self.parse_total_and_new_balance()

    def parse_old_balance(self) -> None:
        m = re.compile(
                r'^\s*Ancien solde au (\d{2}\/\d{2}\/\d{4})\s*(\d[ \d]*,\d\d)',
                flags=re.MULTILINE) \
              .search(self.transactions_text)
        assert m is not None, 'Old balance not found.'
        old_balance = parse_amount(m.group(2))
        if m.end(2) - m.start() < self.credit_start:
            old_balance = -old_balance
        self.old_balance = Balance(old_balance, parse_date(m.group(1)))
        self.transactions_start = m.end()

    def parse_total_and_new_balance(self) -> None:
        m = self.total_pattern.search(self.transactions_text)
        assert m is not None, 'New balance not found.'
        if m.group(2):
            total_debit = parse_amount(m.group(1))
            total_credit = parse_amount(m.group(2))
        else:
            total_debit = parse_amount(m.group(1))
            total_credit = Decimal('0.00')
            if m.end(1) - m.start() > self.credit_start:
                total_debit, total_credit = total_credit, total_debit
        self.total_debit, self.total_credit = total_debit, total_credit
        new_balance_linestart = m.start(3)
        new_balance_date = parse_date(m.group(4))
        new_balance = parse_amount(m.group(5))
        if m.end(5) - new_balance_linestart < self.credit_start:
            new_balance = -new_balance
        self.new_balance = Balance(new_balance, new_balance_date)
        self.transactions_end = m.start()

    def generate_transactions(self, start: int, end: int) \
                                            -> Iterator[AnyTransaction]:
        if self.account_type == 'Compte Courant':
            yield from self.generate_transactions_compte_courant(start, end)
        elif self.account_type in ('Livret A', 'LDD'):
            yield from self.generate_transactions_ldd(start, end)

    def generate_transactions_compte_courant(self, start: int, end: int) \
                                                -> Iterator[Transaction]:
        transaction_block_start_pattern = re.compile(
                r'^ {30} *(\S.+)\n',
                flags=re.MULTILINE)
        transaction_block_end_pattern = re.compile(
                '^ *Sous total (.+?)\s+(\d[ \d]*,\d\d)\s*(\d[ \d]*,\d\d)\n',
                flags=re.MULTILINE)
        accumulated_sub_totals = [Decimal('0.00'), Decimal('0.00')]
        while True:
            m = transaction_block_start_pattern.search(self.transactions_text,
                                                       start, end)
            if m is None:
                assert accumulated_sub_totals[0] == self.total_debit
                assert accumulated_sub_totals[1] == self.total_credit
                return
            block_start = m.end()
            transaction_type = m.group(1)
            m = transaction_block_end_pattern.search(self.transactions_text,
                                                     block_start, end)
            assert m is not None, 'End of transaction block not found.'
            block_end = m.start()
            assert m.group(1) == transaction_type
            sub_total = parse_amount(m.group(2)), parse_amount(m.group(3))
            start = m.end()
            accumulated_debit = Decimal('0.00')
            accumulated_credit = Decimal('0.00')
            for transaction in self.transactions_in_block(transaction_type,
                                                          block_start,
                                                          block_end):
                if transaction.amount < 0:
                    accumulated_debit -= transaction.amount
                else:
                    accumulated_credit += transaction.amount
                yield transaction
            assert accumulated_debit == sub_total[0]
            assert accumulated_credit == sub_total[1]
            # TODO: might need to filter on value date
            accumulated_sub_totals[0] += sub_total[0]
            accumulated_sub_totals[1] += sub_total[1]

    def generate_transactions_ldd(self, start: int, end: int) \
                                                -> Iterator[Transaction]:
        accumulated_debit = Decimal('0.00')
        accumulated_credit = Decimal('0.00')
        for transaction in self.transactions_in_block(None,
                                                      start,
                                                      end):
            if transaction.amount < 0:
                accumulated_debit -= transaction.amount
            else:
                accumulated_credit += transaction.amount
            yield transaction
        assert accumulated_debit == self.total_debit
        assert accumulated_credit == self.total_credit

    def transactions_in_block(self,
                              transaction_type: Optional[str],
                              start: int, end: int) -> Iterator[Transaction]:
        while True:
            m = self.first_line_pattern.search(self.transactions_text, start,
                                               start+self.debit_start)
            if m is None:
                return
            operation_date = parse_date(m.group(1))
            value_date = parse_date(m.group(2)) if m.group(2) != '' else None
            description_lines = [m.group(3)]
            m = self.amount_pattern.search(self.transactions_text,
                                           start+self.debit_start, end)
            assert m is not None, 'Could not find amount of transaction.'
            amount = parse_amount(m.group(1))
            if m.end(1) - m.start() < self.credit_start - self.debit_start:
                amount = -amount
            start = m.end()
            m = self.first_line_pattern.search(self.transactions_text,
                                               start, end)
            transaction_end = m.start() if m is not None else end
            while True:
                m = self.middle_line_pattern.search(self.transactions_text,
                                                    start, transaction_end)
                if m is None:
                    break
                description_lines.append(m.group(1))
                start = m.end()
            metadata = dict(type=transaction_type,
                            raw_description='\n'.join(description_lines))
            description = ' '.join(description_lines)
            yield Transaction(self.account, description, operation_date,
                              value_date, amount,
                              metadata=metadata)


class IngFrQifParser(QifParser):
    bank_folder = 'ing.fr'
    account = 'assets:bank:TODO:ING.fr' # exact account is set in __init__
    currency = '€'
    cleaning_rules = cleaning_rules.qif_checkings_rules

    def __init__(self, qif_file: Path):
        super().__init__(qif_file)
        # TODO: determine exact account type

    @classmethod
    def parse_date(cls, input_: str) -> date:
        return parse_date(input_)


def parse_date(d: str) -> date:
    """ parse a date in "dd/mm/yyyy" format """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    return date(year, month, day)


def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(' ', '').replace(',', '.')
    return Decimal(a)
