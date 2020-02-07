# SPDX-FileCopyrightText: 2019–2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal
import re

from .cleaning_rules import ing_fr as cleaning_rules
from bank_statement import BankStatementMetadata
from transaction import Balance, Transaction

from ..pdf_parser import PdfParser

class IngFrPdfParser(PdfParser):
    bank_folder = 'ing.fr'
    account = 'assets:bank:TODO:ING.fr' # exact account is set in __init__

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        m = re.search('RELEVE ([A-Z ]+)', self.pdf_pages[0])
        self.account_type = m.group(1)
        if self.account_type == 'COMPTE COURANT':
            self.account_type = 'Compte Courant'
            self.account = 'assets:bank:checking:ING.fr'
        if self.account_type == 'LDD':
            self.account = 'assets:bank:saving:ING.fr'
            self.cleaning_rules = cleaning_rules.ldd_rules
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

    def parse_column_starts(self):
        m = self.table_heading.search(self.pdf_pages[0])
        line_start = m.start()
        debit_start = m.start(1) - line_start
        credit_start = m.start(2) - line_start
        return debit_start, credit_start

    def parse_metadata(self):
        m = re.search(r'Du (\d{2}\/\d{2}\/\d{4}) au (\d{2}\/\d{2}\/\d{4})',
                      self.pdf_pages[0])
        start_date = parse_date(m.group(1))
        end_date = parse_date(m.group(2))
        m = re.search(r'Nom, prénom Titulaire 1 :\n\s*((\S+\s)*)',
                      self.pdf_pages[0], flags=re.MULTILINE)
        account_owner = m.group(1).strip()
        m = re.search(r'BIC .+?\n *(.+?)\n', self.pdf_pages[0])
        bic = m.group(1)
        m = re.search(r'IBAN\n *(.+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        if self.account_type == 'Compte Courant':
            m = re.search(r'N° Client Titulaire 1 : (\d+)\s*'
                          r'N° carte Titulaire 1 : ([0-9*]+)\s*'
                          r'N° du Compte Courant : (\d+)',
                          self.pdf_pages[0])
            owner_number = m.group(1)
            card_number = m.group(2)
            account_number = m.group(3)
        elif self.account_type == 'LDD':
            m = re.search(r'N° Client Titulaire 1 : (\d+)\s*'
                          r'Total des intérêts (acquis|payés)'
                          r' au (\d{2}/\d{2}/\d{4}) : +([0-9,]+) *€\s*'
                          r'N° du LDD : (\d+)\s*'
                          r'Taux en vigueur au (\d{2}/\d{2}/\d{4}) \* : (\d+,\d\d) %',
                          self.pdf_pages[0])
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

    def extract_table_from_page(self, page):
        m = self.table_heading.search(page)
        if m is None:
            # There can be pages without a table
            return ""
        line_start = m.start()
        debit_start = m.start(1) - line_start
        credit_start = m.start(2) - line_start
        table_start = m.end()

        m = self.end_pattern.search(page)
        if m is not None:
            table_end = m.start() + 1
        else:
            table_end = len(page)
        return page[table_start:table_end]

    def parse_balances(self):
        self.parse_old_balance()
        self.parse_total_and_new_balance()

    def parse_old_balance(self):
        old_balance = re.compile(r'^\s*Ancien solde au (\d{2}\/\d{2}\/\d{4})\s*(\d[ \d]*,\d\d)',
                                 flags=re.MULTILINE)
        m = old_balance.search(self.transactions_text)
        old_balance = parse_amount(m.group(2))
        if m.end(2) - m.start() < self.credit_start:
            old_balance = -old_balance
        self.old_balance = Balance(old_balance, parse_date(m.group(1)))
        self.transactions_start = m.end()

    def parse_total_and_new_balance(self):
        total_pattern = re.compile(r'^ *Total\s*(\d[ \d]*,\d\d)\s*(\d[ \d]*,\d\d|)\s*'
                                   r'^( *)Nouveau solde au (\d{2}\/\d{2}\/\d{4})'
                                   r'\s*(\d[ \d]*,\d\d)',
                                   flags=re.MULTILINE)
        m = total_pattern.search(self.transactions_text)
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

    def generate_transactions(self, start, end):
        if self.account_type == 'Compte Courant':
            yield from self.generate_transactions_compte_courant(start, end)
        elif self.account_type == 'LDD':
            yield from self.generate_transactions_ldd(start, end)

    def generate_transactions_compte_courant(self, start, end):
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

    def generate_transactions_ldd(self, start, end):
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

    def transactions_in_block(self, transaction_type, start, end):
        while True:
            m = self.first_line_pattern.search(self.transactions_text, start,
                                               start+self.debit_start)
            if m is None:
                return
            operation_date = parse_date(m.group(1))
            value_date = parse_date(m.group(2)) if m.group(2) != '' else None
            description = m.group(3)
            m = self.amount_pattern.search(self.transactions_text,
                                           start+self.debit_start, end)
            amount = parse_amount(m.group(1))
            if m.end(1) - m.start() < self.credit_start - self.debit_start:
                amount = -amount
            start = m.end()
            transaction_end = self.first_line_pattern \
                                  .search(self.transactions_text, start, end)
            transaction_end = transaction_end.start() \
                              if transaction_end is not None else end
            while True:
                m = self.middle_line_pattern.search(self.transactions_text,
                                                    start, transaction_end)
                if m is None:
                    break
                description += ' ' + m.group(1)
                start = m.end()
            yield Transaction(self.account, description, operation_date,
                              value_date, amount,
                              metadata=dict(type=transaction_type))

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
