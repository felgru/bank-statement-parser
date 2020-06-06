# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta
from decimal import Decimal
import os
import re
import subprocess

from bank_statement import BankStatement, BankStatementMetadata
from transaction import Balance, MultiTransaction, Posting, Transaction

from ..parser import Parser
from ..pdf_parser import PdfParser

class VTBPdfParser(Parser):
    bank_folder = 'vtb'
    file_extension = '.pdf'

    def __init__(self, pdf_file):
        super().__init__(pdf_file)
        self._parse_file(pdf_file)
        self.parser = self._choose_parser()

    def _parse_file(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext',
                                  '-fixed', '6', pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        # Careful: There's a trailing \f on the last page
        self.pdf_pages = pdftext.split('\f')[:-1]

    def _choose_parser(self) -> Parser:
        m = re.search(r' *_+\n +IHR KONTOSTAND AUF EINEN BLICK\n *_+\n',
                      self.pdf_pages[0])
        if m is not None:
            return VTB2014PdfParser(self.xdg, self.pdf_pages)
        m = re.search('K O N T O A U S Z U G +Kontokorrent',
                      self.pdf_pages[0])
        if m is not None:
            return VTB2012PdfParser(self.xdg, self.pdf_pages)
        return VTB2019PdfParser(self.xdg, self.pdf_pages)

    def parse_metadata(self) -> BankStatementMetadata:
        return self.parser.parse_metadata()

    def parse(self) -> BankStatement:
        return self.parser.parse()

class VTB2019PdfParser(PdfParser):
    # Do not define bank_folder, so that it is not registered as a Parser by
    # the Parsers class. Instead it should only be used through the
    # VTBPdfParser class.
    account = 'assets:bank:saving:VTB Direktbank'

    def __init__(self, xdg, pdf_pages):
        self.xdg = xdg
        self.pdf_pages = pdf_pages
        self._parse_metadata()
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n*',
                flags=re.MULTILINE)

    table_heading = re.compile(r'^ *Bu-Tag *Wert *(Vorgang)\n*',
                               flags=re.MULTILINE)

    def _parse_description_start(self):
        m = self.table_heading.search(self.pdf_pages[0])
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self):
        return self.metadata

    def _parse_metadata(self):
        m = re.search(r'EUR-Konto +Kontonummer +(\d+)\n', self.pdf_pages[0])
        account_number = m.group(1)
        m = re.search(r'IBAN: +(DE[\d ]+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        m = re.search(r'BIC: +([A-Z\d]+)\n', self.pdf_pages[0])
        bic = m.group(1)
        m = re.search(r'erstellt am (\d{2}.\d{2}.\d{4})', self.pdf_pages[0])
        end_date = parse_date_with_year(m.group(1))
        m = re.search(r'alter Kontostand vom (\d{2}.\d{2}.\d{4})',
                      self.pdf_pages[0])
        start_date = parse_date_with_year(m.group(1))
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                iban=iban,
                bic=bic,
                account_number=account_number,
               )
        self.metadata = meta

    def extract_transactions_table(self):
        self.footer_start_pattern = re.compile(
                '\n*^( {{1,{}}})[^ \d]'.format(self.description_start - 1),
                flags=re.MULTILINE)
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def extract_table_from_page(self, page):
        m = self.table_heading.search(page)
        if m is None:
            return ''
        table_start = m.end()
        m = self.footer_start_pattern.search(page, table_start)
        if m is not None:
            table_end = m.start() + 1
            page = page[table_start:table_end]
        else:
            page = page[table_start:]
        return page

    def parse_balances(self):
        m = re.search(r'alter Kontostand vom (\d{2}.\d{2}.\d{4})'
                      r' +(\d[.\d]*,\d\d) +([HS])\n',
                      self.transactions_text)
        self.transactions_start = m.end()
        self.old_balance = Balance(self.parse_amount(m.group(2), m.group(3)),
                                   parse_date_with_year(m.group(1)))
        m = re.search(r'neuer Kontostand vom (\d{2}.\d{2}.\d{4})'
                      r' +(\d[.\d]*,\d\d) +([HS])\n',
                      self.transactions_text)
        self.transactions_end = m.start()
        self.new_balance = Balance(self.parse_amount(m.group(2), m.group(3)),
                                   parse_date_with_year(m.group(1)))

    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.) +(\d{2}.\d{2}.) +(.*\S+) +'
            r'(\d[.\d]*,\d\d) +([HS])\n*',
            flags=re.MULTILINE)

    def generate_transactions(self, start, end):
        while True:
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)
            if m is None: break
            transaction_date = self.parse_short_date(m.group(1))
            value_date = self.parse_short_date(m.group(2))
            amount = self.parse_amount(m.group(4), m.group(5))
            description = [m.group(3)]
            start = m.end()
            while True:
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
                if m is None: break
                description.append(m.group(1))
                start = m.end()
            description = ' '.join(l for l in description)
            yield Transaction(self.account, description, transaction_date,
                              value_date, amount)

    def check_transactions_consistency(self, transactions):
        assert self.old_balance.balance + sum(t.amount for t in transactions) \
               == self.new_balance.balance

    def parse_short_date(self, d: str) -> date:
        return parse_date_relative_to(d, self.new_balance.date)

    @staticmethod
    def parse_amount(a: str, dir: str) -> Decimal:
        """parse a decimal amount like 1.200,00 H

        The suffix H indicates positive amounts (Haben),
        while the suffix S indicates negative ones (Soll).
        """
        a = a.replace('.', '').replace(',', '.')
        a = Decimal(a)
        if dir == 'H':
            return a
        elif dir == 'S':
            return -a
        else:
            raise RuntimeError(f"Unknown argument {dir!r} instead of"
                               " H(aben) or S(oll).")

class VTB2014PdfParser(PdfParser):
    # Do not define bank_folder, so that it is not registered as a Parser by
    # the Parsers class. Instead it should only be used through the
    # VTBPdfParser class.
    account = 'assets:bank:saving:VTB Direktbank'

    def __init__(self, xdg, pdf_pages):
        self.xdg = xdg
        self.pdf_pages = pdf_pages
        self._parse_metadata()
        self._parse_description_start()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n*',
                flags=re.MULTILINE)

    table_heading = re.compile(r'^( *_+\n) *DATUM *(BUCHUNGSVORGANG) *(SOLL) *(HABEN)\n*',
                               flags=re.MULTILINE)

    def _parse_description_start(self):
        m = self.table_heading.search(self.pdf_pages[0])
        self.row_divider = m.group(1)
        self.description_start = m.start(2) - m.end(1)

    def parse_metadata(self):
        return self.metadata

    def _parse_metadata(self):
        m = re.search(r'Kontonummer: +([\d ]+)\n', self.pdf_pages[0])
        account_number = ''.join(m.group(1).split())
        m = re.search(r'IBAN: +(DE[\d ]+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        m = re.search(r'BIC: +([A-Z\d]+)\n', self.pdf_pages[0])
        bic = m.group(1)
        m = re.search(r' *_+\n +IHR KONTOSTAND AUF EINEN BLICK\n *_+\n'
                      r' *alt \((\d{2}.\d{2}.\d{4})\) *(\d+,\d\d[+-])\n'
                      r' *neu \((\d{2}.\d{2}.\d{4})\) *(\d+,\d\d[+-])\n',
                      self.pdf_pages[0])
        start_date = parse_date_with_year(m.group(1))
        self.old_balance = Balance(self.parse_amount(m.group(2)),
                                   start_date)
        end_date = parse_date_with_year(m.group(3))
        self.new_balance = Balance(self.parse_amount(m.group(4)),
                                   end_date)
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                iban=iban,
                bic=bic,
                account_number=account_number,
               )
        self.metadata = meta

    def extract_transactions_table(self):
        self.row_divider_pattern = re.compile('^' + self.row_divider,
                                              flags=re.MULTILINE)
        return ''.join(self.extract_table_from_page(p) for p in self.pdf_pages)

    def extract_table_from_page(self, page):
        m = self.table_heading.search(page)
        if m is None:
            return ''
        table_start = m.end()
        for m in self.row_divider_pattern.finditer(page, table_start):
            table_end = m.end()
        return page[table_start:table_end]

    def parse_balances(self):
        m = re.search(r'ALTER KONTOSTAND VOM (\d{2}.\d{2}.\d{4}) IN EUR'
                      r' +(\d[.\d]*,\d\d[+-])\n',
                      self.transactions_text)
        self.transactions_start = m.end()
        assert self.parse_amount(m.group(2)) == self.old_balance.balance
        m = re.search(r' +GESAMTUMSATZ +(\d[.\d]*,\d\d[+-])\n'
                      r' +NEUER KONTOSTAND VOM (\d{2}.\d{2}.\d{4}) IN EUR'
                      r' +(\d[.\d]*,\d\d[+-])\n',
                      self.transactions_text)
        self.transactions_end = m.start()
        assert self.parse_amount(m.group(3)) == self.new_balance.balance
        self.transactions_sum = self.parse_amount(m.group(1))
        assert self.old_balance.balance + self.transactions_sum \
                == self.new_balance.balance

    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.)( +Wertstellung: +(\d{2}.\d{2}.))? +(\S+) +'
            r'(\d[.\d]*,\d\d[+-])\n',
            flags=re.MULTILINE)

    def generate_transactions(self, start, end):
        while True:
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)
            if m is None: break
            transaction_date = self.parse_short_date(m.group(1))
            if m.group(2) is not None:
                value_date = self.parse_short_date(m.group(3))
            else:
                value_date = transaction_date
            transaction_type = m.group(4)
            amount = self.parse_amount(m.group(5))
            start = m.end()
            description = []
            while True:
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
                if m is None: break
                description.append(m.group(1))
                start = m.end()
            description = ' '.join(l for l in description)
            yield Transaction(self.account, description, transaction_date,
                              value_date, amount,
                              metadata=dict(type=transaction_type))

    def check_transactions_consistency(self, transactions):
        assert sum(t.amount for t in transactions) == self.transactions_sum

    def parse_short_date(self, d: str) -> date:
        return parse_date_relative_to(d, self.new_balance.date)

    @staticmethod
    def parse_amount(a: str) -> Decimal:
        """parse a decimal amount like 1.200,00+"""
        a = a.replace('.', '').replace(',', '.')
        a = a[-1] + a[:-1]
        return Decimal(a)

class VTB2012PdfParser(PdfParser):
    # Do not define bank_folder, so that it is not registered as a Parser by
    # the Parsers class. Instead it should only be used through the
    # VTBPdfParser class.
    account = 'assets:bank:saving:VTB Direktbank'

    def __init__(self, xdg, pdf_pages):
        self.xdg = xdg
        self.pdf_pages = pdf_pages
        self._parse_description_start()
        self._parse_metadata()
        self.transaction_description_pattern = re.compile(
                '^' + ' ' * self.description_start + ' *(\S.*)\n*',
                flags=re.MULTILINE)

    def _parse_description_start(self):
        self.table_heading = re.compile(
                r'^ *BU-TAG *(VORGANG) *letzter Auszug vom'
                r' (\d{2}.\d{2}.\d{2}) *SALDO ALT *EUR *'
                r'(\d[.\d]*,\d\d[+-])\n *_+\n',
                flags=re.MULTILINE)
        m = self.table_heading.search(self.pdf_pages[0])
        if m is not None:
            self.old_balance = Balance(self.parse_amount(m.group(3)),
                                       parse_date_with_year(m.group(2)))
        else:
            m = re.search(r' erstellt am +(\d{2}.\d{2}.\d{2})',
                          self.pdf_pages[0])
            end_date = parse_date_with_year(m.group(1))
            self.table_heading = re.compile(
                    r'^ *BU-TAG *(VORGANG) *SALDO ALT *EUR *'
                    r'(\d[.\d]*,\d\d[+-])\n *_+\n',
                    flags=re.MULTILINE)
            m = self.table_heading.search(self.pdf_pages[0])
            self.old_balance = Balance(self.parse_amount(m.group(2)),
                                       end_date.replace(day=1))
        self.description_start = m.start(1) - m.start()

    def parse_metadata(self):
        return self.metadata

    def _parse_metadata(self):
        m = re.search(r'BIC +([A-Z\d]+)\n', self.pdf_pages[0])
        bic = m.group(1)
        m = re.search(r'IBAN +(DE[\d ]+?)\n', self.pdf_pages[0])
        iban = m.group(1)
        m = re.search(r'Kontonummer +([\d ]+)\n', self.pdf_pages[0])
        account_number = ''.join(m.group(1).split())
        m = re.search(r'Auszug +\d+ +Blatt +\d+\n * vom +(\d{2}.\d{2}.\d{2})',
                      self.pdf_pages[0])
        if m is None:
            m = re.search(r' erstellt am +(\d{2}.\d{2}.\d{2})',
                          self.pdf_pages[0])
        end_date = parse_date_with_year(m.group(1))
        start_date = self.old_balance.date
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                iban=iban,
                bic=bic,
                account_number=account_number,
               )
        self.metadata = meta

    def extract_transactions_table(self):
        # Let's assume everything is on page 1 until I find a document
        # that proves otherwise.
        return self.extract_table_from_page(self.pdf_pages[0])

    def extract_table_from_page(self, page):
        m = self.table_heading.search(page)
        table_start = m.end()
        m = re.search(r' *_+\n +SALDO NEU +EUR +(\d[.\d]*,\d\d[+-])\n', page)
        self.new_balance = Balance(self.parse_amount(m.group(1)),
                                   self.metadata.end_date)
        table_end = m.start()
        page = page[table_start:table_end]
        self.transactions_start = 0
        self.transactions_end = len(page)
        return page

    def parse_balances(self):
        pass

    transaction_pattern = re.compile(
            r'^ *(\d{2}.\d{2}.)( +Wert: +(\d{2}.\d{2}.))? +(.*\S+) +'
            r'(\d[.\d]*,\d\d[+-])\n',
            flags=re.MULTILINE)

    def generate_transactions(self, start, end):
        while True:
            m = self.transaction_pattern.search(self.transactions_text,
                                                start, end)
            if m is None: break
            transaction_date = self.parse_short_date(m.group(1))
            if m.group(2) is not None:
                value_date = self.parse_short_date(m.group(3))
            else:
                value_date = transaction_date
            transaction_type = m.group(4)
            amount = self.parse_amount(m.group(5))
            start = m.end()
            description = []
            while True:
                m = self.transaction_description_pattern.match(
                                self.transactions_text, start, end)
                if m is None: break
                description.append(m.group(1))
                start = m.end()
            if description:
                description = transaction_type + ' | ' \
                            + ' '.join(l for l in description)
            else:
                description = transaction_type
            yield Transaction(self.account, description, transaction_date,
                              value_date, amount,
                              metadata=dict(type=transaction_type))

    def check_transactions_consistency(self, transactions):
        assert self.old_balance.balance + sum(t.amount for t in transactions) \
               == self.new_balance.balance

    def parse_short_date(self, d: str) -> date:
        return parse_date_relative_to(d, self.new_balance.date)

    @staticmethod
    def parse_amount(a: str) -> Decimal:
        """parse a decimal amount like 1.200,00+"""
        a = a.replace('.', '').replace(',', '.')
        a = a[-1] + a[:-1]
        return Decimal(a)

def parse_date_with_year(d: str) -> date:
    """parse a date in "dd.mm.yyyy" or "dd.mm.yy" format

    For dates with a two digit year yy, we assume it to refer to 20yy.
    """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    if year < 100:
        year += 2000
    return date(year, month, day)

def parse_date_relative_to(d, ref_d):
    """parse a date in "dd.mm." format while guessing year relative to date ref_d"""
    day = int(d[:2])
    month = int(d[3:5])
    year = ref_d.year
    d = date(year, month, day)
    half_a_year = timedelta(days=356/2)
    diff = d - ref_d
    if abs(diff) > half_a_year:
        if diff < timedelta(days=0):
            d = date(year + 1, month, day)
        else:
            d = date(year - 1, month, day)
    return d
