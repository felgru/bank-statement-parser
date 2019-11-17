# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from copy import copy
from datetime import date
from decimal import Decimal
import os
import re
import subprocess

from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from xdg_dirs import getXDGdirectories

class PayfitPdfParser:
    bank_folder = 'payfit'
    file_extension = '.pdf'

    def __init__(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        self.pdf_file = pdf_file
        self.num_pages = self._num_pdf_pages()
        self.extract_main_transactions_table()
        self.extract_dates_table()
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    def _num_pdf_pages(self):
        info = subprocess.run(['pdfinfo', self.pdf_file],
                              capture_output=True, encoding='UTF8',
                              check=True).stdout
        for line in info.split('\n'):
            if line.startswith('Pages:'):
                return int(line.split()[-1])

    def extract_main_transactions_table(self):
        upper_left = (32, 347)
        if self.num_pages > 1:
            main_tables = self.extract_table(1, upper_left, (532, 763), 4)
        else:
            main_tables = self.extract_table(1, upper_left, (532, 709), 4)
        m = self.net_before_taxes_pattern.search(main_tables)
        self.transactions_text = main_tables[:m.start()]
        self.summary_text = main_tables[m.start():]

    net_before_taxes_pattern = re.compile(
            r'^ *NET À PAYER AVANT IMPÔT SUR LE REVENU *(\d[ \d]*,\d\d)',
            flags=re.MULTILINE)

    def extract_dates_table(self):
        self.dates_text = self.extract_table(1, (432, 62), (321, 88), 2)

    def extract_table(self, page, upper_left, size, num_cols):
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext', '-r', '100',
                                  '-f', str(page), '-l', str(page),
                                  '-x', str(upper_left[0]),
                                  '-y', str(upper_left[1]),
                                  '-W', str(size[0]), '-H', str(size[1]),
                                  '-fixed', str(num_cols),
                                  self.pdf_file, '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        return pdftext

    def parse_metadata(self) -> BankStatementMetadata:
        m = re.search(r'D[ÉE]BUT +DE +PÉRIODE +(\d\d +\S+ +\d{4})',
                      self.dates_text)
        start_date = parse_verbose_date(m.group(1))
        m = re.search(r'FIN +DE +PÉRIODE +(\d\d +\S+ +\d{4})',
                      self.dates_text)
        end_date = parse_verbose_date(m.group(1))
        m = re.search(r'N° +DE +SÉCURITÉ +SOCIALE *(\d*)',
                      self.dates_text)
        social_security_number = m.group(1) or None
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                )
        return meta

    def parse(self) -> BankStatement:
        m = re.search(r'DATE DE PAIEMENT *(\d\d \S* \d{4})',
                      self.summary_text)
        payment_date = parse_verbose_date(m.group(1))
        transaction = MultiTransaction('Salaire', payment_date)

        gross_salary, bonus = self._parse_salary()

        misc_expenses = self._parse_misc_expenses()
        transportation_postings, transportation_reimbursed \
                = self._parse_travel_reimbursement()
        meal_vouchers = self._parse_meal_vouchers()

        m = self.net_before_taxes_pattern.search(self.summary_text)
        net_before_taxes = parse_amount(m.group(1))
        payment = self._parse_payment()

        simplified_gross_salary = payment.amount + meal_vouchers.amount \
                                                 - transportation_reimbursed
        if bonus is not None:
            transaction.add_posting(bonus)
            simplified_gross_salary += bonus.amount
        transaction.add_posting(Posting('income:salary',
                                        -simplified_gross_salary))
        transaction.add_posting(payment)
        transaction.add_posting(meal_vouchers)
        for p in transportation_postings:
            transaction.add_posting(p)
        return BankStatement(None, [transaction])

    def _parse_salary(self):
        m = re.search(r'Prime sur objectifs *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is not None:
            bonus = parse_amount(m.group(1))
            bonus = Posting('income:salary:bonus', -bonus)
        else:
            bonus = None
        m = re.search(r'Rémunération brute \(1\) *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        return parse_amount(m.group(1)), bonus

    def _parse_misc_expenses(self):
        m = re.search(r'TOTAL COTISATIONS ET CONTRIBUTIONS SALARIALES \(4\)'
                      r' *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        return parse_amount(m.group(1))

    def _parse_travel_reimbursement(self):
        m = re.search(r'Frais transport public *(\d[ \d]*,\d\d) *(\d,\d{4}) *'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        if m is None:
            return ([], Decimal('0.00'))
        transportation_total = parse_amount(m.group(1))
        transportation_reimbursement_rate = parse_amount(m.group(2))
        transportation_reimbursed = parse_amount(m.group(3))
        total_reimbursed = copy(transportation_reimbursed)
        transportation_remaining = transportation_total \
                                 - transportation_reimbursed
        assert(transportation_total * transportation_reimbursement_rate
                == transportation_reimbursed)
        postings = [Posting('expenses:reimbursable:transportation',
                            -transportation_total),
                    Posting('expenses:transportation:public transportation',
                            transportation_remaining,
                            comment='nonreimbursed public transportation fees')
                   ]
        travel_reimbursement = re.search(
                r'Remboursement de notes de frais *(\d[ \d]*,\d\d)',
                self.transactions_text)
        if travel_reimbursement is not None:
            travel_reimbursement = parse_amount(travel_reimbursement.group(1))
            total_reimbursed += travel_reimbursement
            postings.append(Posting('expenses:reimbursable:transportation',
                                    -travel_reimbursement,
                                    comment='trip: TODO'))
            postings.append(Posting('equity:travel reimbursement',
                                    travel_reimbursement))
        m = re.search(r'Indemnités non soumises \(2\) *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        assert(parse_amount(m.group(1)) == total_reimbursed)
        return (postings, transportation_reimbursed)

    def _parse_meal_vouchers(self):
        m = re.search(r'Titres Restaurant *\d*,\d\d *\d,\d{3} *(\d*,\d\d)',
                      self.transactions_text)
        return Posting('expenses:food:meal_vouchers',
                       parse_amount(m.group(1)))

    def _parse_payment(self):
        m = re.search(r'NET PAYÉ\s*(\(\d\)( [+-] \(\d\))*) *'
                      r'VIREMENT *(\d[ \d]*,\d\d)',
                      self.summary_text)
        payment = parse_amount(m.group(3))
        return Posting('equity:receivable:salary', payment)

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(' ', '').replace(',', '.')
    return Decimal(a)

def parse_verbose_date(d: str) -> date:
    day, month, year = d.split()
    day = int(day)
    month = {'JANVIER': 1,
             'FÉVRIER': 2,
             'MARS': 3,
             'AVRIL': 4,
             'MAI': 5,
             'JUIN': 6,
             'JUILLET': 7,
             'AOÛT': 8,
             'SEPTEMBRE': 9,
             'OCTOBRE': 10,
             'NOVEMBRE': 11,
             'DECEMBRE': 12}[month]
    year = int(year)
    return date(year, month, day)
