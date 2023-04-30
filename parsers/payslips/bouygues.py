# SPDX-FileCopyrightText: 2021–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import itertools
from pathlib import Path
import re
import subprocess
from typing import Optional, Union

from ..parser import GenericParserConfig, Parser
from ..pdf_parser import read_pdf_file
from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from utils import PeekableIterator


class BouyguesConfig(GenericParserConfig):
    bank_folder = 'bouygues'
    bank_name = 'Bouygues'
    DEFAULT_ACCOUNTS = {
        'salary balancing account': 'assets:receivable:salary',
        'salary': 'income:salary',
        'bonus': 'income:salary:bonus',
        'health insurance': 'expenses:insurance:health',
        'retirement insurance': 'expenses:taxes:retirement insurance',
        'nondeductible social taxes': 'expenses:taxes:social:nondeductible',
        'deductible social taxes': 'expenses:taxes:social:deductible',
        'meal vouchers': 'expenses:food:meal_vouchers',
        'transport reimbursement': 'expenses:reimbursable:transportation',
        'comité d\'entraide': 'expenses:misc',
        'PEE balancing account': 'assets:receivable:PEE',
        'PEE intéressement': 'income:misc:intéressement PEE',
        'PEE participation': 'income:misc:participation PEE',
        'source tax': 'expenses:taxes:income:deducted at source',
    }


class BouyguesPdfParser(Parser[BouyguesConfig]):
    file_extension = '.pdf'
    num_cols = None

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self._parse_file(pdf_file)

    def _parse_file(self, pdf_file: Path) -> None:
        self.pdf_pages = read_pdf_file(pdf_file, cols=self.num_cols)

    def parse_metadata(self) -> BankStatementMetadata:
        m = re.search(r'Date de paiement\s*Période de paie\n'
                      r'\s*BULLETIN DE PAIE'
                      r'\s*(\d\d/\d\d/\d{4})\s*'
                      r's*DU (\d\d/\d\d/\d{4}) AU (\d\d/\d\d/\d{4})',
                      self.pdf_pages[0])
        if m is None:
            raise BouyguesPdfParserError('Could not find payment date.')
        payment_date = parse_date(m.group(1))
        payment_period = (parse_date(m.group(2)), parse_date(m.group(3)))
        m = re.search(r'^ *Matricule *(N° de sécurité sociale)\n+(.*)$',
                      self.pdf_pages[0],
                      re.MULTILINE)
        if m is None:
            raise BouyguesPdfParserError(
                    'Could not find social security number.')
        social_security_number = m.group(2)[m.start(1) - m.start():].strip()
        meta = BankStatementMetadata(
                start_date=payment_period[0],
                end_date=payment_period[1],
                )
        return meta

    def parse(self, config: BouyguesConfig) -> BankStatement:
        parser = BouyguesItemParser(self.pdf_pages, config)
        return parser.parse()


class BouyguesItemParser:
    def __init__(self, pdf_pages: list[str], config: BouyguesConfig):
        self.pdf_pages = pdf_pages
        self.accounts = config.accounts

    def iter_main_table(self) -> MainTableIterator:
        return MainTableIterator(self.pdf_pages)

    def parse(self) -> BankStatement:
        m = re.search(r'Date de paiement\s*Période de paie\n'
                      r'\s*BULLETIN DE PAIE'
                      r'\s*(\d\d/\d\d/\d{4})\s*'
                      r's*DU (\d\d/\d\d/\d{4}) AU (\d\d/\d\d/\d{4})',
                      self.pdf_pages[0])
        if m is None:
            raise BouyguesPdfParserError('Could not find payment date.')
        payment_date = parse_date(m.group(1))
        payment_period = (parse_date(m.group(2)), parse_date(m.group(3)))
        description = f'Salaire du {payment_period[0]} au {payment_period[1]}'
        transaction = MultiTransaction(description, payment_date)

        lines = self.iter_main_table()
        header = lines.peek()
        if (header.is_section_header()
            and header.description == 'ELEMENTS DE REVENU BRUT'):
            salary_postings, total_gross_salary \
                    = self._parse_gross_income(lines)
        elif (header.description == "AUTRES CONTRIBUTIONS DUES PAR L'EMPLOYEUR"
              or header.description == 'COTISATIONS ET CONTRIBUTIONS SOCIALES'):
            salary_postings = []
            total_gross_salary = Decimal('0.00')
        else:
            raise BouyguesPdfParserError(
                    f'Unexpected header: {header.description}')
        social_security_postings, social_security_total = \
                self._parse_social_security_payments(lines)
        misc_postings, misc_total = self._parse_misc(lines)
        net_before_taxes, income_tax = self._parse_net_income(lines)
        payment = self._parse_payment(lines)

        assert(total_gross_salary
               + social_security_total
               + misc_total
               - income_tax.amount - payment.amount == 0)
        transaction.add_posting(payment)
        for p in salary_postings:
            transaction.add_posting(p)
        transaction.add_posting(income_tax)
        for p in social_security_postings:
            transaction.add_posting(p)
        for p in misc_postings:
            transaction.add_posting(p)
        assert transaction.is_balanced()
        return BankStatement([transaction])

    def _parse_gross_income(self,
                            lines: MainTableIterator,
                            ) -> tuple[list[Posting], Decimal]:
        header = next(lines)
        if not (header.is_section_header()
                and header.description == 'ELEMENTS DE REVENU BRUT'):
            raise BouyguesPdfParserError(
                    f'Unexpected header: {header.description}')
        postings: list[Posting] = []
        for line in lines:
            assert line.montant_employee is not None
            if line.description == 'TOTAL BRUT':
                total_gross_salary = line.montant_employee
                assert sum(p.amount for p in postings) \
                       + total_gross_salary == 0
                return postings, total_gross_salary
            if '13ème mois' in line.description:
                account = self.accounts['bonus']
            else:
                account = self.accounts['salary']
            p = Posting(account, -line.montant_employee,
                        comment=' '.join(line.description.split()))
            postings.append(p)
        raise BouyguesPdfParserError('Missing TOTOAL BRUT.')

    def _parse_social_security_payments(self,
                                        lines: MainTableIterator,
                                        ) -> tuple[list[Posting], Decimal]:
        header = next(lines)
        if header.description == "AUTRES CONTRIBUTIONS DUES PAR L'EMPLOYEUR":
            header = next(lines)
        assert header.is_section_header()
        if not (header.is_section_header()
                and header.description == 'COTISATIONS ET CONTRIBUTIONS SOCIALES'):
            raise BouyguesPdfParserError(
                    f'Unexpected header: {header.description}')
        postings: list[Posting] = []
        header = next(lines)
        if header.is_section_header():
            assert header.description == 'SANTÉ'
            sante = Decimal('0.00')
            while True:
                line = next(lines)
                if line.is_section_header():
                    header = line
                    break
                if line.montant_employee is not None:
                    sante -= line.montant_employee
            postings.append(Posting(self.accounts['health insurance'],
                                     sante,
                                     comment="Santé"))
            assert header.description == 'RETRAITE'
            retraite = Decimal('0.00')
            while True:
                line = next(lines)
                if line.is_section_header():
                    header = line
                    break
                if line.montant_employee is not None:
                    retraite -= line.montant_employee
            postings.append(Posting(self.accounts['retirement insurance'],
                                     retraite,
                                     comment="Retraite"))
            assert header.description == 'ASSURANCE CHOMAGE'
            chomage = Decimal('0.00')
            while True:
                line = next(lines)
                if line.description == "AUTRES CONTRIBUTIONS DUES PAR L'EMPLOYEUR":
                    header = line
                    break
                if line.montant_employee is not None:
                    chomage -= line.montant_employee
            postings.append(Posting(
                self.accounts['nondeductible social taxes'],
                chomage,
                comment="Assurance chômage"))
            total_nondeductible = Decimal(0)
            for line in lines:
                if line.description == "CSG déductible de l'impôt sur le revenu":
                    assert line.montant_employee is not None
                    postings.append(Posting(
                        self.accounts['deductible social taxes'],
                        -line.montant_employee,
                        comment="CSG déductible de l'impôt sur le revenu"))
                    continue
                if line.description == 'TOTAL DES COTISATIONS ET CONTRIBUTIONS':
                    assert line.montant_employee is not None
                    total = line.montant_employee
                    postings.append(Posting(
                        self.accounts['nondeductible social taxes'],
                        -total_nondeductible))
                    assert sum(p.amount for p in postings) == -total
                    return postings, total
                if line.montant_employee is not None:
                    total_nondeductible += line.montant_employee
            raise BouyguesPdfParserError(
                    'Missing TOTAL DES COTISATIONS ET CONTRIBUTIONS.')
        else:
            for line in itertools.chain([header], lines):
                if line.description == "CSG déductible de l'impôt sur le revenu":
                    assert line.montant_employee is not None
                    postings.append(Posting(
                        self.accounts['deductible social taxes'],
                        -line.montant_employee,
                        comment="CSG déductible de l'impôt sur le revenu"))
                    continue
                elif line.description == "CSG/CRDS déductible de l'impôt sur le revenu":
                    assert line.montant_employee is not None
                    postings.append(Posting(
                        self.accounts['deductible social taxes'],
                        -line.montant_employee,
                        comment=line.description))
                    continue
                elif line.description == "CSG/CRDS non déductible de l'impôt sur le revenu":
                    assert line.montant_employee is not None
                    postings.append(Posting(
                        self.accounts['nondeductible social taxes'],
                        -line.montant_employee,
                        comment=line.description))
                    continue
                elif line.description == 'TOTAL DES COTISATIONS ET CONTRIBUTIONS':
                    assert line.montant_employee is not None
                    total = line.montant_employee
                    assert sum(p.amount for p in postings) == -total
                    return postings, total
                else:
                    raise BouyguesPdfParserError('Unexpected social security'
                                                 f' item: {line.description}')
            raise BouyguesPdfParserError(
                    'Missing TOTAL DES COTISATIONS ET CONTRIBUTIONS.')

    def _parse_misc(self,
                    lines: MainTableIterator,
                    ) -> tuple[list[Posting], Decimal]:
        header = next(lines)
        if not header.is_section_header() \
           or not header.description == 'AUTRES ELEMENTS DE PAIE':
                raise BouyguesPdfParserError('Missing AUTRES ELEMENTS DE PAIE.')
        postings: list[Posting] = []
        for line in lines:
            assert line.montant_employee is not None
            if line.description == 'TOTAL AUTRES ELEMENTS DE PAIE':
                total = line.montant_employee
                assert sum(p.amount for p in postings) + total == 0
                return postings, total
            description = ' '.join(line.description.split())
            if description.startswith('Titres restaurants'):
                account = self.accounts['meal vouchers']
                # remove excessive whitespaces
                description = ' '.join(description.split())
            elif description.startswith('Rbt Part Patronale TR'):
                account = self.accounts['salary']
            elif description.startswith('Frais de transports'):
                account = self.accounts['transport reimbursement']
                # remove excessive whitespaces
                description = ' '.join(description.split())
            elif description == "Versement mensuel PEE":
                account = self.accounts['PEE balancing account']
            elif description == "Comité d'entraide":
                account = self.accounts['comité d\'entraide']
            elif description == 'Intéressement Brut':
                account = self.accounts['PEE intéressement']
            elif description == 'Participation Brute':
                account = self.accounts['PEE participation']
            elif description == 'Placement INT dans PACTEO':
                account = self.accounts['PEE balancing account']
            elif description == 'Placement PART dans FCPE':
                account = self.accounts['PEE balancing account']
            else:
                raise BouyguesPdfParserError(f'Unknown posting: {description}.')
            p = Posting(account, -line.montant_employee, comment=description)
            postings.append(p)
        raise BouyguesPdfParserError('Missing TOTAL AUTRES ELEMENTS DE PAIE.')

    def _parse_net_income(self,
                          lines: MainTableIterator,
                          ) -> tuple[Decimal, Posting]:
        source_tax_description \
                = 'Impôt sur le revenu prélevé à la source - Taux '
        net_line = next(lines)
        if net_line.description == 'NET A PAYER AVANT IMPOT SUR LE REVENU':
            # A normal payslip.
            if net_line.montant_employee is None:
                raise BouyguesPdfParserError('Missing net payment amount.')
            else:
                net_payment = net_line.montant_employee

            # skip "Dont évolution de la rénumération…"
            for line in lines:
                if line.description.startswith(source_tax_description):
                    break
            else:
                raise BouyguesPdfParserError('Missing Impôt sur le revenu.')
        elif net_line.description.startswith(source_tax_description):
            # payslip without any pay.
            net_payment = Decimal('0.00')
            line = net_line
        else:
            raise BouyguesPdfParserError(
                    'Missing NET A PAYER AVANT IMPOT SUR LE REVENU.')
        base = line.base
        taux = line.taux_employee
        montant = line.montant_employee
        assert base is not None and taux is not None and montant is not None
        base = base.quantize(Decimal('.01'))
        taux = taux.quantize(Decimal('.1'))
        montant = -montant
        assert round(base * taux / 100, 2) == montant
        comment = 'Impôt sur le revenu prélevé à la source {}% * {}€' \
                  .format(taux, base)
        return (net_payment,
                Posting(self.accounts['source tax'], montant, comment=comment))

    def _parse_payment(self,
                       lines: MainTableIterator,
                       ) -> Posting:
        page = self.pdf_pages[lines.current_page()]
        pattern = re.compile(r'En Euros\s*(\d+,\d\d)\n')
        m = pattern.search(page, lines.current_table_end())
        if m is None:
            raise BouyguesPdfParserError('Salary payment not found.')
        payment = Decimal(m.group(1).replace(',', '.'))
        return Posting(self.accounts['salary balancing account'], payment)


def parse_date(d: str) -> date:
    day, month, year = d.split('/')
    return date(int(year), int(month), int(day))


class MainTableIterator:
    def __init__(self, pdf_pages: list[str]):
        self.orig_iter = iter(NonpeekableMainTableIterator(pdf_pages))
        self.iter = PeekableIterator(self.orig_iter)
        _, self.page, self.end = self.iter.peek()

    def __iter__(self) -> MainTableIterator:
        return self

    def __next__(self) -> MainTableItem:
        item, self.page, self.end = next(self.iter)
        return item

    def peek(self) -> MainTableItem:
        item, self.page, self.end = self.iter.peek()
        return item

    def current_page(self) -> int:
        return self.page

    def current_table_end(self) -> int:
        return self.end


class NonpeekableMainTableIterator:
    def __init__(self, pdf_pages: list[str]):
        self.pdf_pages = pdf_pages
        self.page = 0
        range_ = self._parse_main_table_header(self.page)
        if range_ is None:
            raise BouyguesPdfParserError('Could not find main table.')
        self.pos, self.end = range_

    def _parse_main_table_header(self, page: int) -> Optional[tuple[int, int]]:
        m = re.search(r' *Nombre\s*Collaborateur\s*Employeur\n'
                      r'\s*Libellé\n'
                      r'( *)(ou base)\s*(Taux)\s*(Montant)\s*(Taux)\s*(Montant)\n',
                      self.pdf_pages[page],
                      flags=re.MULTILINE)
        if m is None:
            return None
        line_start = m.start(1)
        self.field_offsets = [
            m.start(2) - line_start,
            m.start(3) - line_start,
            m.start(4) - line_start,
            m.start(5) - line_start,
            m.start(6) - line_start,
            ]
        start = m.end()
        m = re.search(r'\s*Prélèvement à\s*Total versé\s*Allégement de\n'
                      r'\s*Totaux\s*Brut\s*Net imposable\s*NET A PAYER',
                      self.pdf_pages[page],
                      flags=re.MULTILINE)
        if m is None:
            raise BouyguesPdfParserError(
                    f'Could not find end of main table on page {page}.')
        return start, m.start() + 1  # Keep the '\n' at the end of the line

    def __iter__(self) -> NonpeekableMainTableIterator:
        return self

    def __next__(self) -> tuple[MainTableItem, int, int]:
        page = self.pdf_pages[self.page]
        while self.pos < self.end:
            eol = page.index('\n', self.pos, self.end)
            if eol - self.pos > 1:  # Non-empty line
                break
            self.pos = eol + 1
        else:
            self.page += 1
            range_ = self._parse_main_table_header(self.page)
            if range_ is None:
                raise StopIteration()
            self.pos, self.end = range_
            return self.__next__()
        line = page[self.pos:eol]
        self.pos = eol + 1
        description = line[:self.field_offsets[0]].strip()
        amounts: list[Optional[Decimal]] = []
        for field_start, field_end in zip(self.field_offsets,
                                          itertools.chain(
                                              self.field_offsets[1:],
                                              itertools.repeat(eol))):
            field = line[field_start:field_end].strip()
            value: Optional[Decimal]
            if field:
                value = Decimal(field.replace(',', '.'))
            else:
                value = None
            amounts.append(value)
        return MainTableItem(description, *amounts), self.page, self.end


@dataclass
class MainTableItem:
    description: str
    base: Optional[Decimal]
    taux_employee: Optional[Decimal]
    montant_employee: Optional[Decimal]
    taux_employer: Optional[Decimal]
    montant_employer: Optional[Decimal]

    def is_section_header(self) -> bool:
        return all(field is None for field in (
            self.base,
            self.taux_employee,
            self.montant_employee,
            self.taux_employer,
            self.montant_employer,
            ))

    def concerns_employee(self) -> bool:
        return self.montant_employee is not None


class BouyguesPdfParserError(RuntimeError):
    pass
