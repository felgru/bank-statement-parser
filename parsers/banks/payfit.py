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

    def __init__(self, pdf_file):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        self.pdf_file = pdf_file
        self.extract_main_transactions_table()
        self.extract_summary_table()
        self.extract_dates_table()
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    def extract_main_transactions_table(self):
        self.transactions_text = self.extract_table(1, (32, 347), (532, 625), 4)

    def extract_summary_table(self):
        self.summary_text = self.extract_table(1, (32, 983), (532, 127), 4)

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

        gross_salary = self._parse_gross_salary()

        misc_expenses = self._parse_misc_expenses()
        transportation_total, transportation_reimbursed \
                = self._parse_public_transportation_fees()
        transportation_remaining = transportation_total \
                                 - transportation_reimbursed
        meal_vouchers = self._parse_meal_vouchers()

        m = re.search(r'NET À PAYER AVANT IMPÔT SUR LE REVENU *'
                      r'(\d[ \d]*,\d\d)', self.summary_text)
        net_before_taxes = parse_amount(m.group(1))
        payment = self._parse_payment()

        simplified_gross_salary = payment + meal_vouchers \
                                          - transportation_reimbursed
        transaction.add_posting(Posting('income::salary',
                                        -simplified_gross_salary))
        transaction.add_posting(Posting('equity::receivable::salary', payment))
        transaction.add_posting(Posting('expenses::food::meal_vouchers',
                                        meal_vouchers))
        transaction.add_posting(
                Posting('expenses::reimbursable::transportation',
                        -transportation_total))
        transaction.add_posting(
                Posting('expenses::transportation::public_transportation',
                        transportation_remaining,
                        comment='nonreimbursed public transportation fees'))
        return BankStatement(None, [transaction])

    def _parse_gross_salary(self):
        m = re.search(r'Rémunération brute \(1\) *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        return parse_amount(m.group(1))

    def _parse_misc_expenses(self):
        m = re.search(r'TOTAL COTISATIONS ET CONTRIBUTIONS SALARIALES \(4\)'
                      r' *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        return parse_amount(m.group(1))

    def _parse_public_transportation_fees(self):
        m = re.search(r'Frais transport public *(\d[ \d]*,\d\d) *(\d,\d{4}) *'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        transportation_total = parse_amount(m.group(1))
        transportation_reimbursement_rate = parse_amount(m.group(2))
        transportation_reimbursed = parse_amount(m.group(3))
        assert(transportation_total * transportation_reimbursement_rate
                == transportation_reimbursed)
        m = re.search(r'Indemnités non soumises \(2\) *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        assert(parse_amount(m.group(1)) == transportation_reimbursed)
        return transportation_total, transportation_reimbursed

    def _parse_meal_vouchers(self):
        m = re.search(r'Titres Restaurant *\d*,\d\d *\d,\d{3} *(\d*,\d\d)',
                      self.transactions_text)
        return parse_amount(m.group(1))

    def _parse_payment(self):
        m = re.search(r'NET PAYÉ\s*(\(\d\)( [+-] \(\d\))*) *'
                      r'VIREMENT *(\d[ \d]*,\d\d)',
                      self.summary_text)
        return parse_amount(m.group(3))

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(' ', '').replace(',', '.')
    return Decimal(a)

def parse_verbose_date(d: str) -> date:
    day, month, year = d.split()
    day = int(day)
    month = {'JANVIER': 1,
             'FEVRIER': 2,
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
