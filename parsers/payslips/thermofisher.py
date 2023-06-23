# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import chain, groupby
from pathlib import Path
import re
from typing import cast, Final, Optional

from ..parser import BaseParserConfig, load_accounts, Parser
from ..pdf_parser import read_pdf_file
from bank_statement import BankStatement, BankStatementMetadata
from transaction import MultiTransaction, Posting
from utils import PeekableIterator
from utils.dates import end_of_month
from utils.languages.nl import MONTHS


class ThermoFisherConfig(BaseParserConfig):
    bank_folder = 'thermofisher'
    employer_name = 'Thermo Fisher Scientific'
    adp_config: ThermoFisherAdpConfig
    workday_config: ThermoFisherWorkdayConfig

    def __init__(self,
                 adp_config: ThermoFisherAdpConfig,
                 workday_config: ThermoFisherWorkdayConfig):
        self.adp_config = adp_config
        self.workday_config = workday_config

    @classmethod
    def load(cls, config_dir: Optional[Path]) -> ThermoFisherConfig:
        """Load Parser configuration from given directory.

        If `config_dir` is `None`, return the default configuration.
        """
        adp_config = ThermoFisherAdpConfig.load(config_dir)
        workday_config = ThermoFisherWorkdayConfig.load(config_dir)
        return cls(adp_config=adp_config,
                   workday_config=workday_config)


class ThermoFisherAdpConfig(BaseParserConfig):
    bank_folder = 'thermofisher'
    employer_name = 'Thermo Fisher Scientific'
    DEFAULT_ACCOUNTS: Final[dict[str, str]] = {
        'salary balancing account': 'assets:receivable:salary',
        # base salary
        '1110': 'income:salary',          # Salaris
        # bonus payments
        '365Z': 'income:salary:holiday allowance',  # Holiday Allowance
        # taxes
        '=H1': 'income:salary:30%',       # Correctie 30%-regeling
        '=H2': 'income:salary:30%',       # Correctie 30%-regeling BB
        '93PA': 'expenses:taxes:retirement insurance',  # WN-premie pens1
        '=E1': 'expenses:taxes:social',  # WGA Aanvullend
        '=E5': 'expenses:taxes:social',  # WIA Bodem
        '=E6': 'expenses:taxes:social',  # WGA Aanvullend BT
        '=EA': 'expenses:taxes:social',  # WIA Bodem BT
        # other payments
        '4736': 'income:salary',          # Netto thuiswerkvergoeding
        '4737': 'income:salary',          # Representatievergoeding
        '=M9': 'income:salary:30%',       # 30%-regeling kostenverg.
        # taxes
        '=E3': 'expenses:taxes:social',  # PAWW unemployment insurance
        '/406': 'expenses:taxes:income',  # Loonheffing
    }
    salary_balancing_account: str
    accounts: dict[str, str]

    def __init__(self, salary_balancing_account: str,
                 accounts: dict[str, str]):
        self.salary_balancing_account = salary_balancing_account
        self.accounts = accounts

    @classmethod
    def load(cls, config_dir: Optional[Path]) -> ThermoFisherAdpConfig:
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
            accounts=accounts,
        )


class ThermoFisherWorkdayConfig(BaseParserConfig):
    bank_folder = 'thermofisher'
    employer_name = 'Thermo Fisher Scientific'
    DEFAULT_ACCOUNTS: Final[dict[str, str]] = {
        'salary balancing account': 'assets:receivable:salary',
        # base salary
        '1000': 'income:salary',
        # bonus payments
        '3007': 'income:salary:bonus',    # 13e maand hm
        '3011': 'income:salary:holiday allowance',  # Vacantiegeld
        '3013': 'income:salary:bonus',    # Eindejaarsuitkering
        '3019': 'income:salary:bonus',
        '3069': 'income:salary:bonus',    # Incentive Comp G
        # taxes
        '4304': 'income:salary:30%',      # Bruto aftrek 30% reg.TB
        '4305': 'income:salary:30%',      # Bruto aftrek 30% reg.BT
        '4461': 'expenses:taxes:retirement insurance',
        '4466': 'expenses:taxes:social',  # WGA Aanvullend
        '4467': 'expenses:taxes:social',  # WIA bodem
        # other payments
        '5150': 'income:salary',          # Netto thuiswerkvergoeding
        '5216': 'income:salary',          # Representatievergoeding
        '5990': 'income:salary:30%',      # Netto 30% regeling
        # taxes
        '7380': 'expenses:taxes:social',  # PAWW unemployment insurance
        '7100': 'expenses:taxes:income',  # Loonheffing Tabel
        '7101': 'expenses:taxes:income',  # Loonheffing BT
        # payments
        '9721': 'assets:receivable:salary:correction',  # Correctie TWK Bank 1
    }
    salary_balancing_account: str
    accounts: dict[int, str]

    def __init__(self, salary_balancing_account: str,
                 accounts: dict[int, str]):
        self.salary_balancing_account = salary_balancing_account
        self.accounts = accounts

    @classmethod
    def load(cls, config_dir: Optional[Path]) -> ThermoFisherWorkdayConfig:
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
        self.parser, self._parse = self._choose_parser()

    def _choose_parser(self) -> tuple[Parser,
                                      Callable[[ThermoFisherConfig],
                                               BankStatement]]:
        m = re.match(r' *1PAYSLNL\d+ ', self.pdf_pages[0])
        if m is not None:
            adp_parser = ThermoFisherAdpPdfParser(self.pdf_pages)
            def parse_adp(config: ThermoFisherConfig) -> BankStatement:
                return adp_parser.parse(config.adp_config)
            return adp_parser, parse_adp

        workday_parser = ThermoFisherWorkdayPdfParser(self.pdf_pages)
        def parse_workday(config: ThermoFisherConfig) -> BankStatement:
            return workday_parser.parse(config.workday_config)

        return workday_parser, parse_workday

    def parse_metadata(self) -> BankStatementMetadata:
        return self.parser.parse_metadata()

    def parse(self, config: ThermoFisherConfig) -> BankStatement:
        return self._parse(config)


class ThermoFisherAdpPdfParser(Parser[ThermoFisherAdpConfig]):
    """Parser for ADP payslips.

    Since May 2023, Thermo Fisher changed its payslip format and
    provides the payslips on the ADP platform. These payslips are parsed
    by this class.
    """
    autoload = False
    file_extension = '.pdf'

    def __init__(self, pdf_pages: list[str]):
        self.pdf_pages = pdf_pages
        if len(self.pdf_pages) > 1:
            raise ThermoFisherPdfParserError(
                    'We can only handle 1-page payslips, but found'
                    f' {len(self.pdf_pages)} pages.')
        self.main_table = AdpMainTable(self.pdf_pages[0])
        self._metadata: BankStatementMetadata | None = None

    def parse_metadata(self) -> BankStatementMetadata:
        if self._metadata is not None:
            return self._metadata
        page1 = self.pdf_pages[0]
        # parse first line
        m = re.match(r' *\d+PAYSLNL\d+'
                     r' (?P<gv_employee_id>\d+?)'
                     r'(?P<month>\d\d)\/(?P<short_year>\d\d)(?P<num>\d+)'
                     r' +(?P<month_name>[A-Z][a-z]+) (?P<year>\d+)'
                     r' +Pagina +: +(?P<page>\d+)\n', page1)
        if m is None:
            raise ThermoFisherPdfParserError(
                    'Could not parse first line of payslip.')
        first_line = m.groupdict()
        meta_start = m.end() + 1
        m = re.search(r'\n( +)GV personeelsnr', page1)
        if m is None:
            raise ThermoFisherPdfParserError(
                    'Could not find first metadata line.')
        right_table_offset = m.end(1) - m.start(1)
        main_table_header_start = self.main_table.main_table_start

        left_col = []
        right_col = []
        for line in page1[meta_start:main_table_header_start-1].split('\n'):
            left_col.append(line[0:right_table_offset].strip())
            right_col.append(line[right_table_offset:])

        addresses = []
        current_address: list[str] = []
        num_empty = 0
        for line in left_col:
            if not line:
                num_empty += 1
                continue
            if num_empty >= 1 and current_address:
                addresses.append('\n'.join(current_address))
                current_address = []
            num_empty = 0
            current_address.append(line)
        if current_address:
            addresses.append('\n'.join(current_address))
        if not len(addresses) == 2:
            raise ThermoFisherPdfParserError(
                    f'Expected 2 addresses, but found {len(addresses)}:\n'
                    + '\n======\n'.join(addresses)
                    + '\n======\nRaw address column:\n'
                    + '\n'.join(left_col))
        employer_address = addresses[0]
        employee_address = addresses[1]

        right_column: dict[str, str] = {}
        for line in right_col:
            if not line:
                continue
            key, colon, value = line.partition(':')
            right_column[key.rstrip()] = value.lstrip()

        start_date = date(year=int(first_line['year']),
                          month=int(first_line['month']),
                          day=1)
        end_date = end_of_month(start_date)
        self._metadata = BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                payment_date=end_date,
                employee_number=right_column['Pers.Nr.'],
                description=first_line['month_name'] + ' ' + first_line['year'],
                )
        return self._metadata

    def parse(self, config: ThermoFisherAdpConfig) -> BankStatement:
        metadata = self.parse_metadata()
        transaction = MultiTransaction(metadata.description,
                                       metadata.payment_date)
        self._parse_main_table(transaction, config)
        assert transaction.is_balanced()
        return BankStatement([transaction])

    def _parse_main_table(self,
                          transaction: MultiTransaction,
                          config: ThermoFisherAdpConfig) -> None:
        accounts = config.accounts
        net_total: Decimal | None = None
        unknown_codes: list[AdpMainTableItem] = []
        for item in self.main_table:
            assert item.code is not None
            if item.code == '/550':  # Nettoloon
                nettoloon = item.uitbetaling
                assert -sum(p.amount for p in transaction.postings) == nettoloon
                continue
            if item.code == '/560':
                net_total = item.uitbetaling
                continue
            if item.code == '/404':  # Basis LH
                continue  # ignore
            try:
                account = accounts[item.code]
            except KeyError:
                unknown_codes.append(item)
                continue
            if item.uitbetaling is not None:
                amount = -item.uitbetaling
            else:
                raise ThermoFisherPdfParserError(
                        f'Missing amount in {item}.')
            p = Posting(account, amount,
                        comment=item.omschrijving)
            transaction.add_posting(p)
        if unknown_codes:
            raise ThermoFisherPdfParserError(
                    f'Encountered {len(unknown_codes)} unknown code(s):\n'
                    + '\n'.join(f'  {item.code}: {item.omschrijving}'
                                for item in unknown_codes))
        assert net_total is not None
        assert net_total == self.main_table.payment
        p = Posting(config.salary_balancing_account,
                    net_total,
                    comment='Uitbetalingsbedrag')
        transaction.add_posting(p)


class AdpMainTable:
    HEADER: Final[str] = (
            r'^ +(Basis *) (Tarief *) (Tabelloon *) (Bijz.loon *)'
            r' (Uitbetaling)$')

    def __init__(self, page: str):
        self.pdf_page = page
        m = re.search(self.HEADER, page, flags=re.MULTILINE)
        if m is None:
            raise ThermoFisherPdfParserError(
                    'Could not find main table header.')
        self.main_table_start = m.start()
        self.main_table_body_start = m.end() + 1
        assert m.lastindex is not None
        m2 = re.compile(r'^1110 +(Salaris)',
                        flags=re.MULTILINE,
                        ).search(page, pos=self.main_table_body_start)
        if m2 is None:
            raise ThermoFisherPdfParserError(
                    'Could not find Salaris in main table.')
        self.main_table_spans = {
            'Code': slice(0, m2.start(1) - m2.start()),
            'Omschrijving': slice(m2.start(1) - m2.start(),
                                  m.start(1) - m.start()),
        }
        for i in range(1, m.lastindex):
            self.main_table_spans[m.group(i).strip()] = slice(
                m.start(i) - m.start(),
                m.end(i) - m.start(),
            )
        self.main_table_spans[m.group(m.lastindex).strip()] = slice(
                m.start(m.lastindex) - m.start(), None)

        m = re.search(r'^ *Uitbetalingsbedrag +([\d\.]+,\d\d)$', page,
                      flags=re.MULTILINE)
        if m is None:
            raise ThermoFisherPdfParserError('Could not find payment.')
        self.payment = Decimal(m.group(1).replace('.', '').replace(',', '.'))
        self.main_table_end = m.start() - 1

    def __iter__(self) -> AdpMainTableIterator:
        return AdpMainTableIterator(
                self.pdf_page[self.main_table_body_start:self.main_table_end],
                self.main_table_spans,
                )


class AdpMainTableIterator:
    def __init__(self,
                 table_text: str,
                 spans: dict[str, slice],
                 ):
        self._spans = spans
        self._lines = iter(table_text.split('\n'))

    def __iter__(self) -> AdpMainTableIterator:
        return self

    def __next__(self) -> AdpMainTableItem:
        line = next(self._lines)
        while (not line
               or line[self._spans['Uitbetaling']].strip() == '-----------'):
            line = next(self._lines)
        parts = {key.lower(): line[span].strip()
                 for key, span in self._spans.items()}
        return AdpMainTableItem.from_dict(parts)


@dataclass
class AdpMainTableItem:
    code: str
    omschrijving: str
    basis: None
    tarief: None
    tabelloon: Decimal | None
    bijz_loon: Decimal | None
    uitbetaling: Decimal | None

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> AdpMainTableItem:
        def decimal(value):
            value = value.replace('.', '').replace(',', '.')
            if value.endswith('-'):
                value = '-' + value[:-1]
            return Decimal(value)

        def parse_optional(value, type):
            return type(value) if value else None

        assert not d['basis']
        assert not d['tarief']
        return cls(
                code=d['code'],
                omschrijving=d['omschrijving'],
                basis=None,
                tarief=None,
                tabelloon=parse_optional(d['tabelloon'], decimal),
                bijz_loon=parse_optional(d['bijz.loon'], decimal),
                uitbetaling=parse_optional(d['uitbetaling'], decimal),
                )


class ThermoFisherWorkdayPdfParser(Parser[ThermoFisherWorkdayConfig]):
    """Parser for Workday payslips.

    Before the introduction of the ADP payslip platform in May 2023,
    Thermo Fisher provided payslips via Workday in a different format.
    These payslips are parsed by this class.
    """
    autoload = False
    file_extension = '.pdf'

    def __init__(self, pdf_pages: list[str]):
        self.pdf_pages = pdf_pages
        self.tables = [WorkdayPayslipTables.create(self.pdf_pages, i)
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
        # Split address field if at least 6 empty lines.
        # (Sometimes pdftotext creates empty lines in an address, but
        #  fortunately there are more blank lines as seperation between
        #  addresses.)
        addresses = []
        current_address: list[str] = []
        num_empty = 0
        for line in address_col:
            if not line:
                num_empty += 1
                continue
            if num_empty > 5 and current_address:
                addresses.append('\n'.join(current_address))
                current_address = []
            num_empty = 0
            current_address.append(line)
        if current_address:
            addresses.append('\n'.join(current_address))
        if not (2 <= len(addresses) <= 3):
            raise RuntimeError(
                    f'Expected 2 or 3 addresses, but found {len(addresses)}:\n'
                    + '\n======\n'.join(addresses)
                    + '\n======\nRaw address column:\n'
                    + '\n'.join(address_col))
        employer_address = addresses[0]
        employee_address = addresses[1]
        if len(addresses) > 2:
            description = addresses[-1]
        else:
            description = ''
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
        if not description:
            description=f'Salaris {MONTHS[start_date.month - 1]}'
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                payment_date=payment_date,
                employee_number=meta['Persnr'],
                description=description,
                meta=meta,
                )

    def parse(self, config: ThermoFisherWorkdayConfig) -> BankStatement:
        if len(self.pdf_pages) > 1:
            return self._parse_corrections(config)
        else:
            return self.parse_page(0, config)

    def _parse_corrections(self,
                           config: ThermoFisherWorkdayConfig) -> BankStatement:
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
            assert old.transaction_date == new.transaction_date
            assert old.description == new.description
            description = 'Correction ' + old.description
            transaction = MultiTransaction(description, old.transaction_date)
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
                   config: ThermoFisherWorkdayConfig) -> BankStatement:
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
                          config: ThermoFisherWorkdayConfig) -> Decimal:
        accounts = config.accounts
        net_total: Optional[Decimal] = None
        unknown_codes: list[WorkdayMainTableItem] = []
        for item in self.tables[page].main_table:
            if item.is_total():
                continue
            if item.code == 9900:
                net_total = item.uitbetaling
                continue
            assert item.code is not None
            try:
                account = accounts[item.code]
            except KeyError:
                unknown_codes.append(item)
                continue
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
        if unknown_codes:
            raise RuntimeError(
                    f'Encountered {len(unknown_codes)} unknown code(s):\n'
                    + '\n'.join(f'  {item.code}: {item.omschrijving}'
                                for item in unknown_codes))
        assert net_total is not None
        return net_total

    def _parse_payment_table(self,
                             page: int,
                             transaction: MultiTransaction,
                             config: ThermoFisherWorkdayConfig) -> Decimal:
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
class WorkdayPayslipTables:
    main_table: WorkdayMainTable
    payment_table: WorkdayPaymentTable
    totals_table: str

    @classmethod
    def create(cls, pdf_pages: list[str], page_nr: int) -> WorkdayPayslipTables:
        page = pdf_pages[page_nr]
        main_table = WorkdayMainTable(page)
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
        payment_table = WorkdayPaymentTable(
            page[main_table.betaling_start+len('Betaling\n'):main_table_end],
            main_table.main_table_spans,
        )
        return cls(
            main_table=main_table,
            payment_table=payment_table,
            totals_table=totals_table,
        )


class WorkdayMainTable:
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

    def __iter__(self) -> WorkdayMainTableIterator:
        return WorkdayMainTableIterator(
                self.pdf_page[self.main_table_body_start:self.betaling_start-1],
                self.main_table_spans,
                )


class WorkdayMainTableIterator:
    def __init__(self,
                 table_text: str,
                 spans: dict[str, slice],
                 ):
        self._spans = spans
        self._lines = iter(table_text.split('\n'))

    def __iter__(self) -> WorkdayMainTableIterator:
        return self

    def __next__(self) -> WorkdayMainTableItem:
        line = next(self._lines)
        while not line:
            line = next(self._lines)
        parts = {key.lower(): line[span].strip()
                 for key, span in self._spans.items()}
        return WorkdayMainTableItem.from_dict(parts)


@dataclass
class WorkdayMainTableItem:
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
    def from_dict(cls, d: dict[str, str]) -> WorkdayMainTableItem:
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


class WorkdayPaymentTable:
    def __init__(self, text: str, main_table_spans: dict[str, slice]):
        self.text = text
        self.main_table_spans = main_table_spans

    def __iter__(self) -> WorkdayPaymentTableIterator:
        return WorkdayPaymentTableIterator(
                self.text,
                self.main_table_spans,
                )


class WorkdayPaymentTableIterator:
    def __init__(self,
                 table_text: str,
                 spans: dict[str, slice],
                 ):
        self._spans = spans
        self._lines = PeekableIterator[str](table_text.split('\n'))

    def __iter__(self) -> WorkdayPaymentTableIterator:
        return self

    def __next__(self) -> WorkdayPaymentTableItem:
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
        return WorkdayPaymentTableItem(code=code,
                                       description=description,
                                       amount=uitbetaling)


@dataclass
class WorkdayPaymentTableItem:
    code: int
    description: str
    amount: Decimal


class ThermoFisherPdfParserError(RuntimeError):
    pass
