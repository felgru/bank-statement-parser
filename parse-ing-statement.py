#!/usr/bin/python3

import argparse
from collections import namedtuple
from datetime import date
from decimal import Decimal
from itertools import chain
import re
import subprocess

Transaction = namedtuple('Transaction', 'type description operation_date'
                                        ' value_date amount sub_total')

def parse_date(d: str) -> date:
    """ parse a date in "dd/mm/yyyy" format """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    return date(year, month, day)

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    return Decimal(a.replace(',', '.'))

def parse_meta_data(pdf_pages):
    m = re.search(r'Du (\d{2}\/\d{2}\/\d{4}) au (\d{2}\/\d{2}\/\d{4})',
                  pdf_pages[0])
    start_date = parse_date(m.group(1))
    end_date = parse_date(m.group(2))
    m = re.search(r'Nom, prénom Titulaire 1 :\n\s*((\S+\s)*)', pdf_pages[0],
                  flags=re.MULTILINE)
    account_owner = m.group(1).strip()
    return dict(
            start_date=start_date,
            end_date=end_date,
            account_owner=account_owner,
           )

class PdfParser:
    def __init__(self, pdf_file):
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext', '-fixed', '5', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        pdf_pages = pdftext.split('\f')[:-1] # There's a trailing \f on the last page
        self.debit_start, self.credit_start = parse_column_starts(pdf_pages[0])
        self.transactions_text = extract_transactions_table(pdf_pages)

    def parse(self):
        old_balance, start_pos = \
                parse_old_balance(self.transactions_text, self.credit_start)
        (total_debit, total_credit), new_balance, end_pos = \
                parse_total_and_new_balance(self.transactions_text,
                                            self.credit_start)
        transactions = [t for t in self.generate_transactions(start_pos,
                                                              end_pos)]
        # TODO: might need to filter on value date
        assert sum(t[-1][0] for t in transactions) == total_debit
        assert sum(t[-1][1] for t in transactions) == total_credit
        assert old_balance.balance + total_credit - total_debit \
                == new_balance.balance
        return BankStatement(transactions, old_balance, new_balance)

    def generate_transactions(self, start, end):
        transaction_header_pattern = re.compile(
                r'^ {30} *(\S.+)\n',
                flags=re.MULTILINE)
        first_line_pattern = re.compile(
                r'\s*(\d{2}\/\d{2}\/\d{4})\s*(\d{2}\/\d{2}\/\d{4}|)'
                r'\s*(\S.+?)\s+(\d*,\d\d)\n')
        middle_line_pattern = re.compile(r'\s*(\S.*?)\n')
        end_line_pattern = re.compile(
                'Sous total (.+?)\s+(\d*,\d\d)\s*(\d*,\d\d)\n')
        while True:
            m = transaction_header_pattern.search(self.transactions_text, start, end)
            if m is None:
                return
            start = m.end()
            transaction_type = m.group(1)
            m = first_line_pattern.search(self.transactions_text, start, end)
            operation_date = parse_date(m.group(1))
            value_date = parse_date(m.group(2)) if m.group(2) != '' else None
            description = m.group(3)
            amount = parse_amount(m.group(4))
            if m.end(4) - m.start() < self.credit_start:
                amount = -amount
            start = m.end()
            while True:
                m = middle_line_pattern.search(self.transactions_text,
                                               start, end)
                sub_total_text = 'Sous total ' + transaction_type
                if m is None or m.group(1).startswith(sub_total_text):
                    break
                description += ' ' + m.group(1)
                start = m.end()
            m = end_line_pattern.search(self.transactions_text, start, end)
            assert m.group(1) == transaction_type
            sub_total = parse_amount(m.group(2)), parse_amount(m.group(3))
            start = m.end()
            if amount >= 0:
                assert sub_total[1] == amount
            else:
                assert sub_total[0] == -amount
            yield Transaction(transaction_type, description, operation_date,
                              value_date, amount, sub_total)

def parse_column_starts(page):
    table_heading = re.compile(r"^\s*Date de\s*Date de\s*Nature de l'opération\s*"
                               r"(Débit\(EUR\))\s*(Crédit\(EUR\))"
                               r"\n\s*l'opération\s*valeur\n",
                               flags=re.MULTILINE)
    m = table_heading.search(page)
    line_start = m.start()
    debit_start = m.start(1) - line_start
    credit_start = m.start(2) - line_start
    return debit_start, credit_start

def extract_transactions_table(pdf_pages):
    return ''.join(extract_table_from_page(p) for p in pdf_pages)

def extract_table_from_page(page):
    table_heading = re.compile(r"^\s*Date de\s*Date de\s*Nature de l'opération\s*"
                               r"(Débit\(EUR\))\s*(Crédit\(EUR\))"
                               r"\n\s*l'opération\s*valeur\n",
                               flags=re.MULTILINE)
    m = table_heading.search(page)
    line_start = m.start()
    debit_start = m.start(1) - line_start
    credit_start = m.start(2) - line_start
    table_start = m.end()

    end_pattern = re.compile(r"\n\n *\* TAEG: Taux Annuel Effectif Global"
                             r" sur la période")
    m = end_pattern.search(page)
    table_end = m.start()
    return page[table_start:table_end]

Balance = namedtuple('Balance', 'balance date')

def parse_old_balance(transactions_text, credit_start):
    old_balance = re.compile(r'^\s*Ancien solde au (\d{2}\/\d{2}\/\d{4})\s*(\d*,\d\d)',
                             flags=re.MULTILINE)
    m = old_balance.search(transactions_text)
    old_balance = parse_amount(m.group(2))
    if m.end(2) - m.start() < credit_start:
        old_balance = -old_balance
    return Balance(old_balance, parse_date(m.group(1))), m.end()

def parse_total_and_new_balance(transactions_text, credit_start):
    total_pattern = re.compile(r'^ *Total\s*(\d*,\d\d)\s*(\d*,\d\d)\s*'
                               r'^( *)Nouveau solde au (\d{2}\/\d{2}\/\d{4})'
                               r'\s*(\d*,\d\d)',
                               flags=re.MULTILINE)
    m = total_pattern.search(transactions_text)
    total = parse_amount(m.group(1)), parse_amount(m.group(2))
    new_balance_linestart = m.start(3)
    new_balance_date = parse_date(m.group(4))
    new_balance = parse_amount(m.group(5))
    if m.end(5) - new_balance_linestart < credit_start:
        new_balance = -new_balance
    return total, Balance(new_balance, new_balance_date), m.start()

class BankStatement:
    def __init__(self, transactions, old_balance, new_balance):
        self.transactions = transactions
        self.old_balance = old_balance
        self.new_balance = new_balance

    def write_ledger(self):
        print('; old balance on {}: €{}\n'.format(self.old_balance.date,
                                                  self.old_balance.balance))
        for transaction in self.transactions:
            self.write_ledger_transaction(transaction)
            print()
        print('; new balance on {}: €{}'.format(self.new_balance.date,
                                                self.new_balance.balance))

    def write_ledger_transaction(self, t):
        print(f'{t.operation_date} {t.description}')
        value_date = f' ; date:{t.value_date}' if t.value_date is not None \
                     else ''
        print(f'    assets::bank::ING.fr  €{t.amount}{value_date}')
        print('    TODO::assign_account')

aparser = argparse.ArgumentParser(
        description='parse an ING.fr account statement PDF')
aparser.add_argument('-o', metavar='OUTFILE', dest='outfile', default=None,
                     help='write to OUTFILE instead of stdout')
aparser.add_argument('pdf', action='store',
                     help='PDF file of the account statement')

args = aparser.parse_args()

assert args.pdf.endswith('.pdf')
transactions_parser = PdfParser(args.pdf)
bank_statement = transactions_parser.parse()
bank_statement.write_ledger()
