# SPDX-FileCopyrightText: 2019–2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from copy import copy
from datetime import date
from decimal import Decimal
import os
import re
import subprocess
from typing import List, Tuple

from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from xdg_dirs import getXDGdirectories

class PayfitPdfParser:
    bank_folder = 'payfit'
    file_extension = '.pdf'

    def __init__(self, pdf_file: str):
        if not os.path.exists(pdf_file):
            raise IOError('Unknown file: {}'.format(pdf_file))
        self.pdf_file = pdf_file
        self.num_pages = self._num_pdf_pages()
        self.extract_main_transactions_table()
        self.extract_dates_table()
        self.xdg = getXDGdirectories('bank-statement-parser/'
                                     + self.bank_folder)

    def _num_pdf_pages(self) -> int:
        info = subprocess.run(['pdfinfo', self.pdf_file],
                              capture_output=True, encoding='UTF8',
                              check=True).stdout
        for line in info.split('\n'):
            if line.startswith('Pages:'):
                return int(line.split()[-1])
        raise PayfitPdfParserError('Could not parse number of PDF pages.')

    def extract_main_transactions_table(self) -> None:
        upper_left = (32, 347)
        if self.num_pages > 1:
            main_tables = self.extract_table(1, upper_left, (532, 800), 4)
        else:
            main_tables = self.extract_table(1, upper_left, (532, 709), 4)
        m = self.net_before_taxes_pattern.search(main_tables)
        if m is None:
            raise PayfitPdfParserError('Could not find end of main table.')
        self.transactions_text = main_tables[:m.start()]
        self.summary_text = main_tables[m.start():]

    net_before_taxes_pattern = re.compile(
            r'^ *NET À PAYER AVANT IMPÔT SUR LE REVENU *(\d[ \d]*,\d\d)',
            flags=re.MULTILINE)

    def extract_dates_table(self) -> None:
        self.dates_text = self.extract_table(1, (432, 62), (321, 88), 2)

    def extract_table(self, page: int, upper_left: Tuple[int, int],
                      size: Tuple[int, int], num_cols: int) -> str:
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
        if m is None:
            raise PayfitPdfParserError('Could not find start date.')
        start_date = parse_verbose_date(m.group(1))
        m = re.search(r'FIN +DE +PÉRIODE +(\d\d +\S+ +\d{4})',
                      self.dates_text)
        if m is None:
            raise PayfitPdfParserError('Could not find end date.')
        end_date = parse_verbose_date(m.group(1))
        m = re.search(r'N° +DE +SÉCURITÉ +SOCIALE *(\d*)',
                      self.dates_text)
        if m is None:
            raise PayfitPdfParserError('Could not find social security number.')
        social_security_number = m.group(1) or None
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                )
        return meta

    def parse(self) -> BankStatement:
        m = re.search(r'DATE DE PAIEMENT *(\d\d \S* \d{4})',
                      self.summary_text)
        if m is None:
            raise PayfitPdfParserError('Could not find payment date.')
        payment_date = parse_verbose_date(m.group(1))
        transaction = MultiTransaction('Salaire', payment_date)

        salary_postings, total_gross_salary = self._parse_salary()

        social_security_postings, social_security_total = \
                self._parse_social_security_payments()
        transportation_postings, transportation_reimbursed \
                = self._parse_travel_reimbursement()
        meal_vouchers = self._parse_meal_vouchers()

        m = self.net_before_taxes_pattern.search(self.summary_text)
        if m is None:
            raise PayfitPdfParserError('Could not find net before taxes.')
        net_before_taxes = parse_amount(m.group(1))
        income_tax = self._parse_tax_deducted_at_source()
        payment = self._parse_payment()

        assert(-total_gross_salary - transportation_reimbursed
               + meal_vouchers.amount + social_security_total
               + income_tax.amount + payment.amount == 0)
        transaction.add_posting(payment)
        for p in salary_postings:
            transaction.add_posting(p)
        transaction.add_posting(income_tax)
        for p in social_security_postings:
            transaction.add_posting(p)
        transaction.add_posting(meal_vouchers)
        for p in transportation_postings:
            transaction.add_posting(p)
        return BankStatement(None, [transaction])

    def _parse_salary(self) -> Tuple[List[Posting], Decimal]:
        m = re.search(r'Rémunération brute \(1\) *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Gross salary not found.')
        total_gross_salary = parse_amount(m.group(1))
        end = m.start()
        posting_pattern = re.compile(r'^ *(.*?\S) *(\d[ \d]*,\d\d|)'
                                     r' *(\d+,\d{4}|) *(-?\d[ \d]*,\d\d)$',
                                     flags=re.MULTILINE)
        salary_accounts = {
                'Salaire de base': 'income:salary',
                'Heures supplémentaires contractuelles 25 %':
                                        'income:salary:overtime',
                'Prime de 13ème mois': 'income:salary:bonus',
                'Prime sur objectifs': 'income:salary:bonus',
                'Absence maladie ordinaire': 'income:salary',
                'Maintien employeur maladie ordinaire': 'income:salary',
                'Régularisation Indemnité CP N': 'income:salary:vacation?',
                'Entrée / Sortie en cours de mois': 'income:salary',
                }
        salaries = []
        vacation_salary = Decimal('0.00')
        for m in posting_pattern.finditer(self.transactions_text, 0, end):
            title = m.group(1)
            salary = parse_amount(m.group(4))
            account = salary_accounts.get(title)
            if account is None:
                # Why do the absences/indemnités congés payés sometimes not
                # cancel to 0?
                if 'Congés Payés' in title:
                    vacation_salary += salary
                    continue
                raise RuntimeError(f'Unknown salary type: {title}.')
            if m.group(2) and m.group(3):
                hours = parse_amount(m.group(2))
                hourly_salary = parse_amount(m.group(3))
                comment = f'{title} {hours}h * {hourly_salary} €/h'
            else:
                comment = title
            p = Posting(account, -salary, comment=comment)
            salaries.append(p)
        if vacation_salary != Decimal('0.00'):
            salaries.append(Posting('income:salary:vacation?',
                                    -vacation_salary))
        assert sum(p.amount for p in salaries) + total_gross_salary == 0
        return salaries, total_gross_salary

    def _parse_social_security_payments(self) -> Tuple[List[Posting], Decimal]:
        m = re.search(r"CSG déductible de l'impôt sur le revenu"
                      r' *(\d[ \d]*,\d\d) *(\d,\d{3}) *'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('CSG amount not found.')
        base = parse_amount(m.group(1))
        percentage = parse_amount(m.group(2))
        amount = parse_amount(m.group(3))
        assert round(base * percentage / 100, 2) == amount
        assert percentage == Decimal('6.800')
        deductible_csg = Posting('expenses:taxes:social:deductible',
                                 amount,
                                 comment="CSG déductible de l'impôt"
                                         " sur le revenu")
        m = re.search(r'TOTAL COTISATIONS ET CONTRIBUTIONS SALARIALES \(4\)'
                      r' *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Total of social security payments'
                                       ' not found.')
        total = parse_amount(m.group(1))
        nondeductible_csg = Posting('expenses:taxes:social:nondeductible',
                                    total - deductible_csg.amount)
        return [deductible_csg, nondeductible_csg], total

    def _parse_travel_reimbursement(self) -> Tuple[List[Posting], Decimal]:
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
        m = re.search(r'Remboursement de notes de frais *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is not None:
            travel_reimbursement = parse_amount(m.group(1))
            total_reimbursed += travel_reimbursement
            postings.append(Posting('expenses:reimbursable:transportation',
                                    -travel_reimbursement,
                                    comment='trip: TODO'))
        m = re.search(r'Indemnités non soumises \(2\) *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Total of reimbursements not found.')
        assert(parse_amount(m.group(1)) == total_reimbursed)
        return (postings, total_reimbursed)

    def _parse_meal_vouchers(self) -> Posting:
        m = re.search(r'Titres Restaurant *\d*,\d\d *\d,\d{3} *(\d*,\d\d)',
                      self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Meal voucher expenses not found.')
        return Posting('expenses:food:meal_vouchers',
                       parse_amount(m.group(1)))

    def _parse_tax_deducted_at_source(self) -> Posting:

        m = re.search(r'Impôt sur le revenu prélevé à la source \(\d\)'
                      r' *(\d[ \d]*,\d\d) *(\d+,\d{2})% *(\d[ \d]*,\d\d)',
                      self.summary_text)
        if m is None:
            raise PayfitPdfParserError('Income tax not found.')
        base = parse_amount(m.group(1))
        taux = parse_amount(m.group(2)) / 100
        montant = parse_amount(m.group(3))
        assert(round(base * taux, 2) == montant)
        comment = 'Impôt sur le revenu prélevé à la source {}% * {}€' \
                  .format(m.group(2), m.group(1))
        return Posting('expenses:taxes:income:deducted at source', montant,
                       comment=comment)

    def _parse_payment(self) -> Posting:
        m = re.search(r'NET PAYÉ\s*(\(\d\)( [+-] \(\d\))*) *'
                      r'VIREMENT *(\d[ \d]*,\d\d)',
                      self.summary_text)
        if m is None:
            raise PayfitPdfParserError('Salary payment not found.')
        payment = parse_amount(m.group(3))
        return Posting('assets:receivable:salary', payment)

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(' ', '').replace(',', '.')
    return Decimal(a)

def parse_verbose_date(d: str) -> date:
    day_, month_, year_ = d.split()
    day = int(day_)
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
             'DÉCEMBRE': 12}[month_]
    year = int(year_)
    return date(year, month, day)

class PayfitPdfParserError(RuntimeError):
    pass
