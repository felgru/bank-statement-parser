# SPDX-FileCopyrightText: 2019–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from copy import copy
from decimal import Decimal
from pathlib import Path
import re
import subprocess
from typing import Optional

from ..parser import GenericParserConfig, Parser
from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from utils.languages.fr import parse_verbose_date


class PayfitConfig(GenericParserConfig):
    bank_folder = 'payfit'
    bank_name = 'Payfit'
    DEFAULT_ACCOUNTS = {
        'salary balancing account': 'assets:receivable:salary',
        'salary': 'income:salary',
        'overtime': 'income:salary:overtime',
        'bonus/13th month': 'income:salary:bonus',
        'bonus/incentive': 'income:salary:bonus',
        'indemnité CP N': 'income:salary:vacation?',
        'health insurance': 'expenses:insurance:health',
        'retirement insurance': 'expenses:taxes:retirement insurance',
        'nondeductible social taxes': 'expenses:taxes:social:nondeductible',
        'deductible social taxes': 'expenses:taxes:social:deductible',
        'meal vouchers': 'expenses:food:meal_vouchers',
        'transport reimbursement': 'expenses:reimbursable:transportation',
        'nonreimbursed transport': 'expenses:transportation:public transportation',
        'source tax': 'expenses:taxes:income:deducted at source',
    }


class PayfitPdfParser(Parser[PayfitConfig]):
    file_extension = '.pdf'

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self.extractor = PayfitDataExtractor(pdf_file)

    def parse_metadata(self) -> BankStatementMetadata:
        dates_text = self.extractor.extract_dates_table()
        m = re.search(r'D[ÉE]BUT +DE +PÉRIODE +(\d\d +\S+ +\d{4})',
                      dates_text)
        if m is None:
            raise PayfitPdfParserError('Could not find start date.')
        start_date = parse_verbose_date(m.group(1), uppercase=True)
        m = re.search(r'FIN +DE +PÉRIODE +(\d\d +\S+ +\d{4})',
                      dates_text)
        if m is None:
            raise PayfitPdfParserError('Could not find end date.')
        end_date = parse_verbose_date(m.group(1), uppercase=True)
        m = re.search(r'N° +DE +SÉCURITÉ +SOCIALE *(\d*)',
                      dates_text)
        if m is None:
            raise PayfitPdfParserError('Could not find social security number.')
        social_security_number = m.group(1) or None
        meta = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                )
        return meta

    def parse(self, config: PayfitConfig) -> BankStatement:
        parser = PayfitItemParser(self.extractor, config)
        return parser.parse()


class PayfitDataExtractor:
    def __init__(self, pdf_file: Path):
        if not pdf_file.exists():
            raise IOError(f'Unknown file: {pdf_file}')
        self.pdf_file = pdf_file
        self.num_pages = self._num_pdf_pages()

    def _num_pdf_pages(self) -> int:
        info = subprocess.run(['pdfinfo', str(self.pdf_file)],
                              capture_output=True, encoding='UTF8',
                              check=True).stdout
        for line in info.split('\n'):
            if line.startswith('Pages:'):
                return int(line.split()[-1])
        raise PayfitPdfParserError('Could not parse number of PDF pages.')

    def extract_main_transactions_table(self) -> str:
        upper_left = (32, 347)
        if self.num_pages > 1:
            main_tables = self.extract_table(1, upper_left, (532, 800), 4)
        else:
            main_tables = self.extract_table(1, upper_left, (532, 709), 4)
        return main_tables

    def extract_dates_table(self) -> str:
        return self.extract_table(1, (432, 62), (321, 88), 2)

    def extract_table(self, page: int, upper_left: tuple[int, int],
                      size: tuple[int, int], num_cols: int) -> str:
        # pdftotext is provided by Poppler on Debian
        pdftext = subprocess.run(['pdftotext', '-r', '100',
                                  '-f', str(page), '-l', str(page),
                                  '-x', str(upper_left[0]),
                                  '-y', str(upper_left[1]),
                                  '-W', str(size[0]), '-H', str(size[1]),
                                  '-fixed', str(num_cols),
                                  str(self.pdf_file), '-'],
                                 capture_output=True, encoding='UTF8',
                                 check=True).stdout
        return pdftext


class PayfitItemParser:
    def __init__(self, extractor: PayfitDataExtractor, config: PayfitConfig):
        self.net_before_taxes_pattern = re.compile(
                r'^ *NET À PAYER AVANT IMPÔT SUR LE REVENU *(\d[ \d]*,\d\d)',
                flags=re.MULTILINE)
        main_tables = extractor.extract_main_transactions_table()
        m = self.net_before_taxes_pattern.search(main_tables)
        if m is None:
            raise PayfitPdfParserError('Could not find end of main table.')
        self.transactions_text = main_tables[:m.start()]
        self.summary_text = main_tables[m.start():]
        self.accounts = config.accounts

    def parse(self) -> BankStatement:
        m = re.search(r'DATE DE PAIEMENT *(\d\d \S* \d{4})',
                      self.summary_text)
        if m is None:
            raise PayfitPdfParserError('Could not find payment date.')
        payment_date = parse_verbose_date(m.group(1), uppercase=True)
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
        return BankStatement([transaction])

    def _parse_salary(self) -> tuple[list[Posting], Decimal]:
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
                'Salaire de base': self.accounts['salary'],
                'Heures supplémentaires contractuelles 25 %':
                                    self.accounts['overtime'],
                'Prime de 13ème mois': self.accounts['bonus/13th month'],
                'Prime sur objectifs': self.accounts['bonus/incentive'],
                'Absence maladie ordinaire': self.accounts['salary'],
                'Maintien employeur maladie ordinaire':
                                    self.accounts['salary'],
                'Régularisation Indemnité CP N':
                                    self.accounts['indemnité CP N'],
                'Entrée / Sortie en cours de mois': self.accounts['salary'],
                }
        time_units = {
                'Salaire de base': 'h',
                'Heures supplémentaires contractuelles 25 %': 'h',
                'Absence maladie ordinaire': 'j',
                'Maintien employeur maladie ordinaire': 'j',
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
                unit = time_units[title]
                base = parse_amount(m.group(2))
                rate = parse_amount(m.group(3))
                comment = f'{title} {base}{unit} * {rate} €/{unit}'
            else:
                comment = title
            p = Posting(account, -salary, comment=comment)
            salaries.append(p)
        if vacation_salary != Decimal('0.00'):
            salaries.append(Posting(self.accounts['indemnité CP N'],
                                    -vacation_salary))
        assert sum(p.amount for p in salaries) + total_gross_salary == 0
        return salaries, total_gross_salary

    def _parse_social_security_payments(self) -> tuple[list[Posting], Decimal]:
        postings: list[Posting] = []
        m = re.search(r"Mutuelle .*?"
                      r' *(\d[ \d]*,\d\d) +(\d,\d{3}) +'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        mutuelle: Optional[Decimal] = None
        if m is not None:
            base = parse_amount(m.group(1))
            percentage = parse_amount(m.group(2))
            amount = parse_amount(m.group(3))
            assert round(base * percentage / 100, 2) == amount
            mutuelle = amount
        m = re.search(r"Complémentaire santé"
                      r' *(\d[ \d]*,\d\d) +(\d,\d{3}) +'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        complement: Optional[Decimal] = None
        if m is not None:
            base = parse_amount(m.group(1))
            percentage = parse_amount(m.group(2))
            amount = parse_amount(m.group(3))
            assert round(base * percentage / 100, 2) == amount
            complement = amount
        if sum(1 for x in [mutuelle, complement] if x is not None) != 1:
            raise PayfitPdfParserError('Mutuelle amount not found.')
        elif mutuelle is None:
            mutuelle = complement
        assert mutuelle is not None
        m = re.search(r"Prévoyance \| Tranche B"
                      r' *(\d[ \d]*,\d\d) +(\d,\d{3}) +'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Prévoyance Tranche B amount not found.')
        base = parse_amount(m.group(1))
        percentage = parse_amount(m.group(2))
        amount = parse_amount(m.group(3))
        assert round(base * percentage / 100, 2) == amount
        prevoyance = amount
        postings.append(Posting(self.accounts['health insurance'],
                                mutuelle + prevoyance,
                                comment=f"Santé ({mutuelle}€ mutuélle "
                                        f"+ {prevoyance}€ prévoyance)"))
        accounted_for = mutuelle + prevoyance
        m = re.search('Retraite', self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Retraite heading not found.')
        retraite_start = m.end()
        m = re.search('Famille', self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Famille heading not found.')
        retraite_end = m.start()
        posting_pattern = re.compile(r"\S.*"
                      r'  +(\d[ \d]*,\d\d) +(\d,\d{3}) +'
                      r'(\d[ \d]*,\d\d)')
        retraite = Decimal('0.00')
        for m in posting_pattern.finditer(self.transactions_text,
                                          retraite_start, retraite_end):
            base = parse_amount(m.group(1))
            percentage = parse_amount(m.group(2))
            amount = parse_amount(m.group(3))
            # Some values are rounded slightly wrong.
            assert round(base * percentage / 100, 2) - amount <= Decimal('0.01')
            retraite += amount
        postings.append(Posting(self.accounts['retirement insurance'],
                                retraite,
                                comment="Retraite"))
        accounted_for += retraite
        m = re.search(r"APEC"
                      r' +(\d[ \d]*,\d\d) +(\d,\d{3}) +'
                      r'(\d[ \d]*,\d\d)', self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('APEC amount not found.')
        base = parse_amount(m.group(1))
        percentage = parse_amount(m.group(2))
        amount = parse_amount(m.group(3))
        assert round(base * percentage / 100, 2) == amount
        postings.append(Posting(self.accounts['nondeductible social taxes'],
                                amount,
                                comment=f"Chômage"))
        accounted_for += amount
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
        postings.append(Posting(self.accounts['deductible social taxes'],
                                amount,
                                comment="CSG déductible de l'impôt"
                                        " sur le revenu"))
        accounted_for += amount
        m = re.search(r'TOTAL COTISATIONS ET CONTRIBUTIONS SALARIALES \(4\)'
                      r' *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is None:
            raise PayfitPdfParserError('Total of social security payments'
                                       ' not found.')
        total = parse_amount(m.group(1))
        postings.append(Posting(self.accounts['nondeductible social taxes'],
                                total - accounted_for))
        return postings, total

    def _parse_travel_reimbursement(self) -> tuple[list[Posting], Decimal]:
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
        postings = [Posting(self.accounts['transport reimbursement'],
                            -transportation_total),
                    Posting(self.accounts['nonreimbursed transport'],
                            transportation_remaining,
                            comment='nonreimbursed public transportation fees')
                   ]
        m = re.search(r'Remboursement de notes de frais *(\d[ \d]*,\d\d)',
                      self.transactions_text)
        if m is not None:
            travel_reimbursement = parse_amount(m.group(1))
            total_reimbursed += travel_reimbursement
            postings.append(Posting(self.accounts['transport reimbursement'],
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
        return Posting(self.accounts['meal vouchers'],
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
        return Posting(self.accounts['source tax'], montant,
                       comment=comment)

    def _parse_payment(self) -> Posting:
        m = re.search(r'NET PAYÉ\s*(\(\d\)( [+-] \(\d\))*) *'
                      r'VIREMENT *(\d[ \d]*,\d\d)',
                      self.summary_text)
        if m is None:
            raise PayfitPdfParserError('Salary payment not found.')
        payment = parse_amount(m.group(3))
        return Posting(self.accounts['salary balancing account'], payment)


def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(' ', '').replace(',', '.')
    return Decimal(a)


class PayfitPdfParserError(RuntimeError):
    pass
