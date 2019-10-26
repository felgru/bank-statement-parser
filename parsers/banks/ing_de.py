from datetime import date
from decimal import Decimal
import os
import re
import subprocess

from bank_statement import BankStatementMetadata
from transaction import Balance, Transaction

from ..pdf_parser import PdfParser

class IngDePdfParser(PdfParser):
    bank_folder = 'ing.de'
    account = 'assets::bank::checking::ING.de' # TODO: actually depends on metadata

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        self.transactions_start = 0
        self.transactions_end = len(self.transactions_text)
        print(self.transactions_text)
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n',
                flags=re.MULTILINE)

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext',
                                  '-fixed', '3', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        pdf_pages = pdftext.split('\f')[:-1] # There's a trailing \f on the last page
        return pdf_pages

    table_heading = re.compile(r'^ *Buchung *(Buchung / Verwendungszweck) *'
                               r'Betrag \(EUR\)\n *Valuta',
                               flags=re.MULTILINE)

    def _parse_description_start(self):
        m = self.table_heading.search(self.pdf_pages[0])
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self):
        self.parse_balances()

    end_pattern = re.compile(r'\n* *ING-DiBa AG · Theodor-Heuss-Allee 2 · '
                             r'60486 Frankfurt am Main · '
                             r'Vorsitzender des Aufsichtsrates:')
    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.\d{4}) +(\S+) +(.*?) +(-?\d[.\d]*,\d\d)\n'
            r' *(\d{2}.\d{2}.\d{4}) +([^\n]*)\n',
            flags=re.MULTILINE)

    def extract_table_from_page(cls, page):
        m = cls.table_heading.search(page)
        if m is None:
            return ''
        table_start = m.end()
        m = cls.end_pattern.search(page)
        if m is None:
            m = re.search(r'\s+Kunden-Information\n'
                          r' +Vorliegender Freistellungsauftrag',
                          page)
        table_end = m.start()
        # TODO: remove garbage string from left margin, containing account number
        return page[table_start:table_end+1]

    def parse_balances(self):
        date = parse_date(re.search('Datum +(\d{2}.\d{2}.\d{4})',
                                    self.pdf_pages[0]).group(1))
        old = parse_amount(re.search('Alter Saldo +(-?\d[.\d]*,\d\d)',
                                     self.pdf_pages[0]).group(1))
        new = parse_amount(re.search('Neuer Saldo +(-?\d[.\d]*,\d\d)',
                                     self.pdf_pages[0]).group(1))
        self.old_balance = Balance(old, None)
        self.new_balance = Balance(new, date)

    def generate_transactions(self, start, end):
        m = self.transaction_pattern.search(self.transactions_text, start, end)
        while m is not None:
            transaction_date = parse_date(m.group(1))
            transaction_type = m.group(2)
            description = [m.group(3), m.group(6)]
            amount = parse_amount(m.group(4))
            value_date = parse_date(m.group(5))
            start = m.end()
            m = self.transaction_description_pattern.match(
                            self.transactions_text, start, end)
            while m is not None:
                description.append(m.group(1))
                start = m.end()
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
            description = '\n'.join(l for l in description)
            yield Transaction(transaction_type, description, transaction_date,
                              value_date, amount)
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)

    def check_transactions_consistency(self):
        pass

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
