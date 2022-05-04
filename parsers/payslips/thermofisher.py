# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import chain, groupby
from pathlib import Path
import re
from typing import Final, Optional

from ..parser import Parser
from ..pdf_parser import read_pdf_file
from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from utils import PeekableIterator


class ThermoFisherPdfParser(Parser):
    bank_folder = 'thermofisher'
    file_extension = '.pdf'

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self._parse_file(pdf_file)

    def _parse_file(self, pdf_file: Path) -> None:
        self.pdf_pages = read_pdf_file(pdf_file)
        self.main_table, self.payment_table, totals_table = \
                get_tables(self.pdf_pages)

    def parse_metadata(self) -> BankStatementMetadata:
        m = re.search(r'^ +(Persnr) +\d+ +(Bedrijfsnr\/Werknr) +\d+-\d+$',
                      self.pdf_pages[0],
                      flags=re.MULTILINE)
        if m is None:
            raise ThermoFisherPdfParserError(
                    'Could not find first line of metadata table.')
        left_table_offset = m.start(1) - m.start()
        right_table_offset = m.start(2) - m.start()
        address_col = []
        left_col = []
        right_col = []
        meta_table_end = self.main_table.main_table_start - 1
        for line in self.pdf_pages[0][:meta_table_end].split('\n'):
            address_col.append(line[0:left_table_offset].strip())
            left_col.append(line[left_table_offset:right_table_offset].rstrip())
            right_col.append(line[right_table_offset:])
        addresses = ['\n'.join(lines)
                     for empty, lines in groupby(address_col,
                                                    key=lambda line: not line)
                     if not empty]
        employer_address = addresses[0]
        employee_address = '\n'.join(addresses[1:-1])
        description = addresses[-1]
        for i, line in enumerate(left_col):
            if 'Deze periode' in line:
                working_hours = left_col[i:]
                left_col = left_col[:i]
                break
        else:
            raise ThermoFisherPdfParserError(
                    'No working hours found in metadata table.')
        meta = {'working_hours': '\n'.join(working_hours)}
        empty_fields = []
        for line in chain(left_col, right_col):
            if not line:
                continue
            m = re.match(r'(.*?)  +(.*)', line)
            if m is None:
                empty_fields.append(line)
                continue
            meta[m.group(1)] = m.group(2)
        # from pprint import pprint
        # pprint(meta)
        return BankStatementMetadata(
                start_date=parse_date(meta['Begin datum']),
                end_date=parse_date(meta['Eind datum']),
                payment_date=parse_date(meta['Verw.datum']),
                employee_number=meta['Persnr'],
                description=description,
                )

    def parse(self, rules_dir: Optional[Path]) -> BankStatement:
        metadata = self.parse_metadata()
        transaction = MultiTransaction(metadata.description,
                                       metadata.payment_date)
        net_total = self._parse_main_table(transaction)
        payment_total = self._parse_payment_table(transaction)
        assert net_total == payment_total
        assert transaction.is_balanced()
        return BankStatement(None, [transaction])

    def _parse_main_table(self, transaction: MultiTransaction) -> Decimal:
        accounts = {
                1000: 'income:salary',
                4461: 'expenses:taxes:retirement insurance',
                4466: 'expenses:taxes:social',  # WGA Aanvullend
                4467: 'expenses:taxes:social',  # WIA bodem
                5150: 'income:salary',          # Netto thuiswerkvergoeding
                5216: 'income:salary',          # Representatievergoeding
                7380: 'expenses:taxes:social',  # PAWW unemployment insurance
                7100: 'expenses:taxes:income',  # Loonheffing Tabel
                }
        net_total: Optional[Decimal] = None
        for item in self.main_table:
            if item.is_total():
                continue
            if item.code == 9900:
                net_total = item.uitbetaling
                continue
            assert item.code is not None
            account = accounts[item.code]
            if item.tabel is not None:
                amount = -item.tabel
            elif item.inhouding is not None:
                amount = item.inhouding
            elif item.uitbetaling is not None:
                amount = -item.uitbetaling
            else:
                raise ThermoFisherPdfParserError(
                        'Missing amount in {item}.')
            p = Posting(account, amount,
                        comment=item.omschrijving)
            transaction.add_posting(p)
        assert net_total is not None
        return net_total

    def _parse_payment_table(self, transaction: MultiTransaction) -> Decimal:
        payment_total = Decimal('0.00')
        for item in self.payment_table:
            description = ' '.join(item.description.split())
            p = Posting('assets:receivable:salary', item.amount,
                        comment=description)
            transaction.add_posting(p)
            payment_total += item.amount
        return payment_total


def parse_date(d: str) -> date:
    # Dutch inverse ISO format
    day, month, year = d.split('-')
    return date(int(year), int(month), int(day))


def get_tables(pdf_pages: list[str]) -> tuple[MainTable, PaymentTable, str]:
    assert len(pdf_pages) == 1
    page = pdf_pages[0]
    main_table = MainTable(page)
    m = re.search(r'^ *TOTALEN T\/M DEZE PERIODE', page,
                  flags=re.MULTILINE)
    if m is None:
        raise ThermoFisherPdfParserError('Could not find totals table.')
    totals_table = page[m.start():]
    payment_table = PaymentTable(
            page[main_table.betaling_start+len('Betaling\n'):m.start()-1],
            main_table.main_table_spans)
    return main_table, payment_table, totals_table


class MainTable:
    HEADER: Final[str] = (
        r'(Code) +(Omschrijving +)(Aantal) +(Eenheid) +(Waarde) +'
        r'(Uitbetaling)( +Inhouding )( +Tabel)( +BT)( +WnV +)(Cumulatief)'
        )

    def __init__(self, page: str):
        self.pdf_page = page
        m = re.search(self.HEADER, page)
        if m is None:
            raise ThermoFisherPdfParserError(
                    'Could not find main table header.')
        self.main_table_start = m.start()
        self.main_table_body_start = m.end() + 1
        assert m.lastindex is not None
        self.main_table_spans = {
                m.group(i).strip(): slice(m.start(i) - m.start(),
                                          m.end(i) - m.start())
                for i in range(1, m.lastindex + 1)}

        m = re.search(r'^ *Betaling$', page,
                      flags=re.MULTILINE)
        if m is None:
            raise ThermoFisherPdfParserError('Could not find payment table.')
        self.betaling_start = m.start()

    def __iter__(self) -> MainTableIterator:
        return MainTableIterator(
                self.pdf_page[self.main_table_body_start:self.betaling_start-1],
                self.main_table_spans,
                )


class MainTableIterator:
    def __init__(self,
                 table_text: str,
                 spans: dict[str, slice],
                 ):
        self._spans = spans
        self._lines = iter(table_text.split('\n'))

    def __iter__(self) -> MainTableIterator:
        return self

    def __next__(self) -> MainTableItem:
        line = next(self._lines)
        while not line:
            line = next(self._lines)
        parts = {key.lower(): line[span].strip()
                 for key, span in self._spans.items()}
        return MainTableItem.from_dict(parts)


@dataclass
class MainTableItem:
    code: Optional[int]
    omschrijving: str
    aantal: Optional[Decimal]
    eenheid: str
    waarde: Optional[Decimal]
    uitbetaling: Optional[Decimal]
    inhouding: Optional[Decimal]
    tabel: Optional[Decimal]
    bt: None
    wnv: Optional[Decimal]
    cumulatief: Optional[Decimal]

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> MainTableItem:
        def decimal(value):
            return Decimal(value.replace(',', '.'))

        def parse_optional(value, type):
            return type(value) if value else None

        assert not d['bt']

        try:
            code = parse_optional(d['code'], int)
            omschrijving = d['omschrijving']
        except ValueError:
            code = None
            omschrijving = d['code'] + d['omschrijving']
        return cls(
                code=code,
                omschrijving=omschrijving,
                aantal=parse_optional(d['aantal'], decimal),
                eenheid=d['eenheid'],
                waarde=parse_optional(d['aantal'], decimal),
                uitbetaling=parse_optional(d['uitbetaling'], decimal),
                inhouding=parse_optional(d['inhouding'], decimal),
                tabel=parse_optional(d['tabel'], decimal),
                bt=None,
                wnv=parse_optional(d['wnv'], decimal),
                cumulatief=parse_optional(d['cumulatief'], decimal),
                )

    def is_total(self) -> bool:
        return self.omschrijving == 'Totalen'


class PaymentTable:
    def __init__(self, text: str, main_table_spans: dict[str, slice]):
        self.text = text
        self.main_table_spans = main_table_spans

    def __iter__(self) -> PaymentTableIterator:
        return PaymentTableIterator(
                self.text,
                self.main_table_spans,
                )


class PaymentTableIterator:
    def __init__(self,
                 table_text: str,
                 spans: dict[str, slice],
                 ):
        self._spans = spans
        self._lines = PeekableIterator[str](table_text.split('\n'))

    def __iter__(self) -> PaymentTableIterator:
        return self

    def __next__(self) -> PaymentTableItem:
        line = next(self._lines)
        while not line:
            line = next(self._lines)
        code_span = self._spans['Code']
        code = int(line[code_span].strip())
        uit_span = self._spans['Uitbetaling']
        uitbetaling = Decimal(line[uit_span].strip().replace(',', '.'))
        description = line[code_span.stop:uit_span.start].strip()
        try:
            next_line = self._lines.peek()
            if next_line and not next_line[code_span].strip():
                line = next(self._lines)
                description += line[code_span.stop:uit_span.start].strip()
        except StopIteration:
            pass
        return PaymentTableItem(code=code,
                                description=description,
                                amount=uitbetaling)


@dataclass
class PaymentTableItem:
    code: int
    description: str
    amount: Decimal


class ThermoFisherPdfParserError(RuntimeError):
    pass
