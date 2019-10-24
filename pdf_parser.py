from datetime import date
from decimal import Decimal
import os
import re
import subprocess

from account_mapping import AccountMapper
from bank_statement import BankStatement, BankStatementMetadata
from transaction import Balance, Transaction
from transaction_sanitation import TransactionCleaner
from xdg_dirs import getXDGdirectories

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

def parse_meta_data(pdf_pages):
    m = re.search(r'Du (\d{2}\/\d{2}\/\d{4}) au (\d{2}\/\d{2}\/\d{4})',
                  pdf_pages[0])
    start_date = parse_date(m.group(1))
    end_date = parse_date(m.group(2))
    m = re.search(r'Nom, prénom Titulaire 1 :\n\s*((\S+\s)*)', pdf_pages[0],
                  flags=re.MULTILINE)
    account_owner = m.group(1).strip()
    m = re.search(r'BIC .+?\n *(.+?)\n', pdf_pages[0])
    bic = m.group(1)
    m = re.search(r'IBAN\n *(.+?)\n', pdf_pages[0])
    iban = m.group(1)
    m = re.search(r'N° Client Titulaire 1 : (\d+)\s*'
                  r'N° carte Titulaire 1 : ([0-9*]+)\s*'
                  r'N° du Compte Courant : (\d+)',
                  pdf_pages[0])
    owner_number = m.group(1)
    card_number = m.group(2)
    account_number = m.group(3)
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

class PdfParser:
    def __init__(self, pdf_file):
        self.pdf_pages = self._parse_file(pdf_file)
        self.debit_start, self.credit_start = parse_column_starts(pdf_pages[0])
        self.transactions_text = self.extract_transactions_table()
        self.xdg = getXDGdirectories('bank-statement-parser/ing.fr')

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext', '-fixed', '5', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        pdf_pages = pdftext.split('\f')[:-1] # There's a trailing \f on the last page
        return pdf_pages

    def extract_transactions_table(self):
        return ''.join(extract_table_from_page(p) for p in self.pdf_pages)

    def parse_metadata(self):
        return parse_meta_data(self.pdf_pages)

    def parse(self):
        old_balance, start_pos = \
                parse_old_balance(self.transactions_text, self.credit_start)
        (total_debit, total_credit), new_balance, end_pos = \
                parse_total_and_new_balance(self.transactions_text,
                                            self.credit_start)
        transactions = [t for t in self.generate_transactions(start_pos,
                                                              end_pos,
                                                              total_debit,
                                                              total_credit)]
        assert old_balance.balance + total_credit - total_debit \
                == new_balance.balance
        transactions = self.clean_up_transactions(transactions)
        self.map_accounts(transactions)
        return BankStatement(transactions, old_balance, new_balance)

    def generate_transactions(self, start, end, total_debit, total_credit):
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
                assert accumulated_sub_totals[0] == total_debit
                assert accumulated_sub_totals[1] == total_credit
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

    def transactions_in_block(self, transaction_type, start, end):
        first_line_pattern = re.compile(
                r'^\s*(\d{2}\/\d{2}\/\d{4})\s*(\d{2}\/\d{2}\/\d{4}|)'
                r'\s*(\S.+\S)\s+',
                flags=re.MULTILINE)
        amount_pattern = re.compile(r'\s*(\d[ \d]*,\d\d)\n')
        middle_line_pattern = re.compile(r'\s{30}\s*(\S.*?)\n')
        while True:
            m = first_line_pattern.search(self.transactions_text, start,
                                          start+self.debit_start)
            if m is None:
                return
            operation_date = parse_date(m.group(1))
            value_date = parse_date(m.group(2)) if m.group(2) != '' else None
            description = m.group(3)
            m = amount_pattern.search(self.transactions_text,
                                      start+self.debit_start, end)
            amount = parse_amount(m.group(1))
            if m.end(1) - m.start() < self.credit_start - self.debit_start:
                amount = -amount
            start = m.end()
            transaction_end = first_line_pattern.search(self.transactions_text,
                                                        start, end)
            transaction_end = transaction_end.start() \
                              if transaction_end is not None else end
            while True:
                m = middle_line_pattern.search(self.transactions_text,
                                               start, transaction_end)
                if m is None:
                    break
                description += ' ' + m.group(1)
                start = m.end()
            yield Transaction(transaction_type, description, operation_date,
                              value_date, amount)

    def clean_up_transactions(self, transactions):
        cleaner = TransactionCleaner(self.xdg)
        return [cleaner.clean(t) for t in transactions]

    def map_accounts(self, transactions):
        mapper = AccountMapper(self.xdg)
        mapper.map_transactions(transactions)

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

    end_pattern = re.compile(r"\n* *\* TAEG: Taux Annuel Effectif Global"
                             r" sur la période")
    m = end_pattern.search(page)
    table_end = m.start()
    return page[table_start:table_end+1]

def parse_old_balance(transactions_text, credit_start):
    old_balance = re.compile(r'^\s*Ancien solde au (\d{2}\/\d{2}\/\d{4})\s*(\d[ \d]*,\d\d)',
                             flags=re.MULTILINE)
    m = old_balance.search(transactions_text)
    old_balance = parse_amount(m.group(2))
    if m.end(2) - m.start() < credit_start:
        old_balance = -old_balance
    return Balance(old_balance, parse_date(m.group(1))), m.end()

def parse_total_and_new_balance(transactions_text, credit_start):
    total_pattern = re.compile(r'^ *Total\s*(\d[ \d]*,\d\d)\s*(\d[ \d]*,\d\d|)\s*'
                               r'^( *)Nouveau solde au (\d{2}\/\d{2}\/\d{4})'
                               r'\s*(\d[ \d]*,\d\d)',
                               flags=re.MULTILINE)
    m = total_pattern.search(transactions_text)
    if m.group(2):
        total_debit = parse_amount(m.group(1))
        total_credit = parse_amount(m.group(2))
    else:
        total_debit = parse_amount(m.group(1))
        total_credit = Decimal('0.00')
        if m.end(1) - m.start() > credit_start:
            total_debit, total_credit = total_credit, total_debit
    total = total_debit, total_credit
    new_balance_linestart = m.start(3)
    new_balance_date = parse_date(m.group(4))
    new_balance = parse_amount(m.group(5))
    if m.end(5) - new_balance_linestart < credit_start:
        new_balance = -new_balance
    return total, Balance(new_balance, new_balance_date), m.start()
