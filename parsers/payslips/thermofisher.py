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
from typing import cast, ClassVar, Final, Optional

from ..parser import BaseParserConfig, load_accounts, Parser
from ..pdf_parser import read_pdf_file
from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from utils import PeekableIterator


class ThermoFisherConfig(BaseParserConfig):
    bank_folder = 'thermofisher'
    employer_name = 'Thermo Fisher Scientific'
    DEFAULT_ACCOUNTS: Final[dict[str, str]] = {
        'salary balancing account': 'assets:receivable:salary',
        '1000': 'income:salary',
        '3011': 'income:salary:holiday allowance',  # Vacantiegeld
        '3019': 'income:salary:bonus',
        '4304': 'income:salary:30%',      # Bruto aftrek 30% reg.TB
        '4305': 'income:salary:30%',      # Bruto aftrek 30% reg.BT
        '4461': 'expenses:taxes:retirement insurance',
        '4466': 'expenses:taxes:social',  # WGA Aanvullend
        '4467': 'expenses:taxes:social',  # WIA bodem
        '5150': 'income:salary',          # Netto thuiswerkvergoeding
        '5216': 'income:salary',          # Representatievergoeding
        '5990': 'income:salary:30%',      # Netto 30% regeling
        '7380': 'expenses:taxes:social',  # PAWW unemployment insurance
        '7100': 'expenses:taxes:income',  # Loonheffing Tabel
        '7101': 'expenses:taxes:income',  # Loonheffing BT
        '9721': 'assets:receivable:salary:correction',  # Correctie TWK Bank 1
    }
    salary_balancing_account: str
    accounts: dict[int, str]

    def __init__(self, salary_balancing_account: str,
                 accounts: dict[int, str]):
        self.salary_balancing_account = salary_balancing_account
        self.accounts = accounts

    @classmethod
    def load(cls, config_dir: Optional[Path]) -> ThermoFisherConfig:
        """Load Parser configuration from given directory.

        If `config_dir` is `None`, return the default configuration.
        """
        config_file = config_dir / cls.bank_folder / 'accounts.cfg' \
                      if config_dir is not None else None
        accounts = load_accounts(config_file,
                                 cls.DEFAULT_ACCOUNTS,
                                 cls.employer_name)
        return cls(
            salary_balancing_account=accounts['salary balancing account'],
            accounts={
                int(key): account
                for key, account in accounts.items()
                if key.isnumeric()
            })


class ThermoFisherPdfParser(Parser[ThermoFisherConfig]):
    file_extension = '.pdf'

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self.pdf_pages = read_pdf_file(pdf_file)
        self.tables = [get_tables(self.pdf_pages, i)
                       for i in range(len(self.pdf_pages))]
        self._metadata: Optional[BankStatementMetadata] = None
        self._metadata_per_page = [
            self.parse_metadata_of_page(page)
            for page in range(len(self.pdf_pages))
        ]

    def parse_metadata(self) -> BankStatementMetadata:
        if self._metadata is not None:
            return self._metadata
        if len(self.pdf_pages) > 1:
            metadata = self._metadata_per_page
            if not all('Herberekening' in bs.meta for bs in metadata):
                unexpected = ', '.join(str(i) for i, recalculation
                                       in enumerate('Herberekening' in bs.meta
                                                    for bs in metadata)
                                       if not recalculation)
                raise ThermoFisherPdfParserError(
                        'PDF file has multiple pages, but page(s) '
                        f'{unexpected} is/are no '
                        'recalculation(s) of previous payslips.')
            if len(metadata) % 2 == 1:
                raise ThermoFisherPdfParserError(
                        'Expected even number of bank statements, '
                        'got uneven number.')
            assert len({bs.employee_number for bs in metadata}) == 1
            for i in range(0, len(metadata), 2):
                assert metadata[i].start_date == metadata[i+1].start_date
                assert metadata[i].end_date == metadata[i+1].end_date
            start_date = min(bs.start_date for bs in metadata)
            end_date = max(bs.end_date for bs in metadata)
            return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                employee_number=metadata[0].employee_number,
                description='Recalculation of payslips'
                            f' {start_date} → {end_date}',
                is_recalculation=True,
            )
        else:
            return self.parse_metadata_of_page(0)

    def get_metadata_of_page(self, page_nr: int) -> BankStatementMetadata:
        return self._metadata_per_page[page_nr]

    def parse_metadata_of_page(self, page_nr: int) -> BankStatementMetadata:
        page = self.pdf_pages[page_nr]
        tables = self.tables[page_nr]
        m = re.search(r'^ +(Persnr) +\d+ +(Bedrijfsnr\/Werknr) +\d+-\d+$',
                      page,
                      flags=re.MULTILINE)
        if m is None:
            raise ThermoFisherPdfParserError(
                    'Could not find first line of metadata table.')
        left_table_offset = m.start(1) - m.start()
        right_table_offset = m.start(2) - m.start()
        address_col = []
        left_col = []
        right_col = []
        meta_table_end = tables.main_table.main_table_start - 1
        for line in page[:meta_table_end].split('\n'):
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
        if meta.get('') == 'Afgeboekt':
            del meta['']
            meta['Herberekening'] = 'Afgeboekt'
        if re.match(r'\s*Herberekening\n', page,
                    flags=re.MULTILINE) is not None:
            meta['Herberekening'] = 'Herberekening'
            assert meta[''] == 'Herberekening'
            del meta['']
        start_date = parse_date(meta['Begin datum'])
        end_date = parse_date(meta['Eind datum'])
        payment_date = parse_date(meta['Verw.datum'])
        if not description.startswith('Salaris'):
            maanden = {
                1: 'Januari',
                2: 'Februari',
                3: 'Maart',
                4: 'April',
                5: 'Mei',
                6: 'Juni',
                7: 'Juli',
                8: 'Augustus',
                9: 'September',
                10: 'Oktober',
                11: 'November',
                12: 'December',
            }
            description=f'Salaris {maanden[start_date.month]}'
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                payment_date=payment_date,
                employee_number=meta['Persnr'],
                description=description,
                meta=meta,
                )

    def parse(self, config: ThermoFisherConfig) -> BankStatement:
        if len(self.pdf_pages) > 1:
            return self._parse_corrections(config)
        else:
            return self.parse_page(0, config)

    def _parse_corrections(self, config: ThermoFisherConfig) -> BankStatement:
        correction_account = config.accounts[9721]
        statements = []
        for page in range(len(self.pdf_pages)):
            statements.append(self.parse_page(page, config))
        assert all(len(s.transactions) == 1 for s in statements)
        assert len(statements) == len(self._metadata_per_page)
        transactions = []
        for i in range(0, len(statements), 2):
            old = cast(MultiTransaction, statements[i].transactions[0])
            new = cast(MultiTransaction, statements[i+1].transactions[0])
            assert old.date == new.date
            assert old.description == new.description
            description = 'Correction ' + old.description
            transaction = MultiTransaction(description, old.date)
            # TODO: This assumes that types of postings in new are
            #       a superset of old's postings.
            new_postings = iter(new.postings)
            for old_posting in old.postings:
                new_posting = next(new_postings)
                while (old_posting.account != new_posting.account
                       or old_posting.comment != new_posting.comment):
                    # Take new lines in new posting as-is.
                    transaction.add_posting(new_posting)
                    new_posting = next(new_postings)
                if new_posting.amount == old_posting.amount:
                    # skip identical postings
                    continue
                if new_posting.account != config.salary_balancing_account:
                    # Register difference
                    old_posting.amount = - old_posting.amount
                    transaction.add_posting(old_posting)
                    transaction.add_posting(new_posting)
                else:
                    transaction.add_posting(Posting(
                        account=correction_account,
                        amount=new_posting.amount - old_posting.amount,
                        currency=new_posting.currency,
                        posting_date=new_posting.date,
                        comment=new_posting.comment,
                    ))
            transactions.append(transaction)
        return BankStatement(transactions)

    def parse_page(self, page: int,
                   config: ThermoFisherConfig) -> BankStatement:
        metadata = self.get_metadata_of_page(page)
        transaction = MultiTransaction(metadata.description,
                                       metadata.payment_date)
        net_total = self._parse_main_table(page, transaction, config)
        payment_total = self._parse_payment_table(page, transaction, config)
        assert net_total == payment_total
        assert transaction.is_balanced()
        return BankStatement([transaction])

    def _parse_main_table(self,
                          page: int,
                          transaction: MultiTransaction,
                          config: ThermoFisherConfig) -> Decimal:
        accounts = config.accounts
        net_total: Optional[Decimal] = None
        for item in self.tables[page].main_table:
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
                        f'Missing amount in {item}.')
            p = Posting(account, amount,
                        comment=item.omschrijving)
            transaction.add_posting(p)
        assert net_total is not None
        return net_total

    def _parse_payment_table(self,
                             page: int,
                             transaction: MultiTransaction,
                             config: ThermoFisherConfig) -> Decimal:
        payment_total = Decimal('0.00')
        for item in self.tables[page].payment_table:
            description = ' '.join(item.description.split())
            p = Posting(config.salary_balancing_account,
                        item.amount,
                        comment=description)
            transaction.add_posting(p)
            payment_total += item.amount
        return payment_total


def parse_date(d: str) -> date:
    # Dutch inverse ISO format
    day, month, year = d.split('-')
    return date(int(year), int(month), int(day))


@dataclass
class PayslipTables:
    main_table: MainTable
    payment_table: PaymentTable
    totals_table: str


def get_tables(pdf_pages: list[str], page_nr: int) -> PayslipTables:
    page = pdf_pages[page_nr]
    main_table = MainTable(page)
    m = re.match(r'\s*Herberekening\n', page, flags=re.MULTILINE)
    is_recalculation = m is not None
    m = re.search(r'^ *TOTALEN T\/M DEZE PERIODE', page,
                  flags=re.MULTILINE)
    if m is None:
        raise ThermoFisherPdfParserError('Could not find totals table.')
    totals_table = page[m.start():]
    if is_recalculation:
        m = re.search(r'^Resultaat +\d+,\d\d +Afboeking strook:', page,
                      flags=re.MULTILINE)
        if m is None:
            raise ThermoFisherPdfParserError('Could not find Resultaat.')
    main_table_end = m.start() - 1
    payment_table = PaymentTable(
            page[main_table.betaling_start+len('Betaling\n'):main_table_end],
            main_table.main_table_spans)
    return PayslipTables(
        main_table=main_table,
        payment_table=payment_table,
        totals_table=totals_table,
    )


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
    bt: Optional[Decimal]
    wnv: Optional[Decimal]
    cumulatief: Optional[Decimal]

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> MainTableItem:
        def decimal(value):
            return Decimal(value.replace(',', '.'))

        def parse_optional(value, type):
            return type(value) if value else None

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
                bt=parse_optional(d['bt'], decimal),
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
