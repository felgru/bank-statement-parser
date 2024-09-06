# SPDX-FileCopyrightText: 2023–2024 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from collections import defaultdict
from pathlib import Path
import re
from typing import Any, Final, Iterator, Optional

from ..parser import BaseParserConfig, load_accounts, Parser, store_accounts
from ..pdf_parser import read_pdf_file
from bank_statement import BankStatement, BankStatementMetadata
from transaction import BaseTransaction, MultiTransaction, Posting, Transaction
from utils.languages.nl import parse_verbose_date


class NederlandseSpoorwegenConfig(BaseParserConfig):
    bank_folder = 'ns'
    display_name = 'Nederlandse Spoorwegen'
    DEFAULT_ACCOUNTS: Final[dict[str, str]] = {
        'assets': 'assets:OV-Chipkaart',  # Used for "Reizen op Saldo".
        'balancing': 'assets:balancing:NS',  # Used for invoices.
        'bus ticket': 'expenses:transportation:bus',
        'correction': 'expenses:transportation:public transport:correction',
        'recharge': 'assets:balancing:OV-Chipkaart',
        'subscriptions': 'expenses:transportation:public transport',
        'train ticket': 'expenses:transportation:train',
    }
    accounts: dict[str, str]

    def __init__(self, accounts: dict[str, str]):
        self.accounts = accounts

    @classmethod
    def load(cls, config_dir: Optional[Path]) -> NederlandseSpoorwegenConfig:
        """Load Parser configuration from given directory.

        If `config_dir` is `None`, return the default configuration.
        """
        config_file = config_dir / cls.bank_folder / 'accounts.cfg' \
                      if config_dir is not None else None
        accounts = load_accounts(config_file,
                                 cls.DEFAULT_ACCOUNTS,
                                 cls.display_name)
        return cls(accounts=accounts)

    def store(self, config_dir: Path) -> None:
        config_file = config_dir / self.bank_folder / 'accounts.cfg'
        store_accounts(config_file, self.accounts)


class NederlandseSpoorwegenPdfParser(Parser[NederlandseSpoorwegenConfig]):
    file_extension = '.pdf'

    def __init__(self, pdf_file: Path):
        super().__init__(pdf_file)
        self.pdf_pages = read_pdf_file(pdf_file)
        onderwerp_pattern = re.compile(r'Onderwerp (.*?)(   .+)?\n')
        page_types: list[str] = []
        for page in self.pdf_pages:
            if (m := onderwerp_pattern.search(page)) is not None:
                page_type = m.group(1)
            else:
                try:
                    page_type = page_types[-1]
                except IndexError:
                    raise NederlandseSpoorwegenParserError(
                            'First page has no Onderwerp; expected "Factuur".')
            page_types.append(page_type)
        sections: dict[str, list[int]] = defaultdict(list)
        for i, page_type in enumerate(page_types):
            sections[page_type].append(i)
        self.sections = sections
        self.meta = self._parse_first_page_metadata(self.pdf_pages[0])
        self.tables = self._parse_tables()

    def parse_metadata(self) -> BankStatementMetadata:
        # TODO: Create BankStatementMetadata.
        end_date = self.meta['Factuurdatum']
        if end_date.month > 1:
            start_date = end_date.replace(month=end_date.month - 1)
        else:
            start_date = end_date.replace(year=end_date.year - 1,
                                          month=12)
        return BankStatementMetadata(
                start_date=start_date,
                end_date=end_date,
                account_owner=self.meta['Naam'],
                owner_number=self.meta['Debiteurnummer'],
                card_number=self.meta['Kaartnummer'],
                invoice_number=self.meta['Factuurnummer'],
                )

    @staticmethod
    def _parse_first_page_metadata(page: str) -> dict[str, Any]:
        m = re.search(r'^ *Onderwerp', page, flags=re.MULTILINE)
        if m is None:
            raise NederlandseSpoorwegenParserError(
                    'Could not find start of metadata.')
        start = m.start()
        m = re.search(r'Kaartnummer +(\d{4} \d{4} \d{4} \d{4}) - (.*)',
                      page)
        if m is None:
            raise NederlandseSpoorwegenParserError(
                    'Could not find card number.')
        card_number = m.group(1)
        name = m.group(2)
        end = m.start()
        meta: dict[str, Any] = {}
        for m in re.compile(r'^ *(\S+) (.*?)(|   +(\S+) (.*))$',
                            flags=re.MULTILINE) \
                   .finditer(page, start, end):
            meta[m.group(1)] = m.group(2)
            if m.group(4) is not None:
                meta[m.group(4)] = m.group(5)
        meta['Factuurdatum'] = parse_verbose_date(meta['Factuurdatum'])
        meta['Totaalbedrag'] = parse_amount(meta['Totaalbedrag'])
        meta['Kaartnummer'] = card_number
        meta['Naam'] = name
        return meta

    def _parse_tables(self) -> tuple[SubscriptionTable,
                                     dict[str, FactuurTable],
                                     dict[str, TableIterator]]:
        rest = {}
        for table in self.extract_factuur_tables():
            if table.title.startswith('NS Flex'):
                subscriptions = SubscriptionTable(table)
            else:
                rest[table.title] = FactuurTable(table)
        usage_tables = {t.title: t for t in self.extract_usage_tables()}
        return subscriptions, rest, usage_tables

    def parse(self, config: NederlandseSpoorwegenConfig) -> BankStatement:
        transactions: list[BaseTransaction] = []
        accounts = config.accounts
        transactions.append(self._parse_subscription_transaction(accounts))
        transactions.extend(self._parse_fares_transactions(accounts))
        transactions.sort(key=lambda t: t.transaction_date)
        return BankStatement(transactions)

    def _parse_subscription_transaction(self, accounts: dict[str, str],
                                        ) -> MultiTransaction:
        balancing_account = accounts['balancing']
        subscriptions_account = accounts['subscriptions']
        subscription_table = self.tables[0]
        toelichtingen = {line.toelichting for line in subscription_table}
        assert len(toelichtingen) == 1
        toelichting = '{t[0]} t/m {t[1]}'.format(t=next(iter(toelichtingen)))
        description = f'{subscription_table.title} {toelichting}'
        t = MultiTransaction(description=description,
                             transaction_date=self.meta['Factuurdatum'],
                             )
        t.add_posting(Posting(account=balancing_account,
                              amount=-subscription_table.total,
                              currency='€'))
        for line in subscription_table:
            t.add_posting(Posting(account=subscriptions_account,
                                  amount=line.bedrag,
                                  currency='€',
                                  comment=line.omschrijving,
                                  ))
        return t

    def _parse_fares_transactions(self, accounts: dict[str, str],
                                  ) -> Iterator[Transaction]:
        balancing_account = accounts['balancing']
        for title, table in self.tables[2].items():
            external_account: str | None
            match title:
                case 'Treinreizen' | 'Bus, Tram en Metro reizen':
                    stations_pattern = re.compile(r'^(.*?)  +(.*)$')
                    if title == 'Treinreizen':
                        table_account = accounts['train ticket']
                        description_pattern = (
                                '{line[Dienstverlener]} Trein | '
                                '{start_station} → {end_station}')
                        def make_metadata(line: dict[str, str],
                                          ) -> dict[str, Any]:
                            return {'block_comment': line['Kenmerk']}
                    elif title == 'Bus, Tram en Metro reizen':
                        table_account = accounts['bus ticket']
                        description_pattern = (
                                '{line[Dienstverlener]} {line[Kenmerk]} | '
                                '{start_station} → {end_station}')
                        def make_metadata(line: dict[str, str],
                                          ) -> dict[str, Any]:
                            return {}
                    else:
                        raise RuntimeError(f'Unknown table {title!r}.')
                    for line in table:
                        if line['Kenmerk'].startswith('Correctietarief'):
                            external_account = accounts['correction']
                            description = (
                                f'{line["Dienstverlener"]}'
                                f' {line["Kenmerk"]} | {line["Omschrijving"]}'
                            )
                        else:
                            external_account = table_account
                            m = stations_pattern.match(line['Omschrijving'])
                            assert m is not None
                            description = description_pattern.format(
                                    line=line,
                                    start_station = m.group(1),
                                    end_station = m.group(2),
                                    )
                        yield Transaction(
                                account=balancing_account,
                                description=description,
                                transaction_date=parse_date(line['Datum']),
                                value_date=None,
                                amount=-parse_amount(line['Bedrag incl. BTW']),
                                currency='€',
                                external_account=external_account,
                                metadata=make_metadata(line))
                case 'Klantenservice':
                    for line in table:
                        description = line['Kenmerk']
                        if description == 'Restitutie: Saldo OV-chipkaart':
                            external_account = accounts['assets']
                        else:
                            external_account = None
                        yield Transaction(
                                account=balancing_account,
                                description=line['Kenmerk'],
                                transaction_date=parse_date(line['Datum']),
                                value_date=None,
                                amount=-parse_amount(line['Bedrag incl. BTW']),
                                currency='€',
                                external_account=external_account)
                case _:
                    raise RuntimeError(f'Unknown table {title!r}.')

    def extract_factuur_tables(self) -> Iterator[TableIterator]:
        factuur_pages = self.sections.get('Factuur')
        if factuur_pages is None:
            raise NederlandseSpoorwegenParserError('Page "Factuur" not found.')
        if len(factuur_pages) > 1:
            raise NederlandseSpoorwegenParserError(
                'Handling more than one "Factuur" page'
                ' not yet implemented.')
        factuur_page = self.pdf_pages[factuur_pages[0]]
        del factuur_pages

        yield from self.extract_tables_from_page(factuur_page, 'Subtotaal')

    def extract_usage_tables(self) -> Iterator[TableIterator]:
        usage_pages = self.sections.get('Factuurspecificatie gebruik')
        if usage_pages is None:
            # No page "Factuurspecificatie gebruik"
            return
        if len(usage_pages) > 1:
            raise NederlandseSpoorwegenParserError(
                'Handling more than one "Factuurspecificatie gebruik" page'
                ' not yet implemented.')
        usage_page = self.pdf_pages[usage_pages[0]]
        del usage_pages

        yield from self.extract_tables_from_page(usage_page, 'Totaal')

    def extract_tables_from_page(self,
                                 page_content: str,
                                 total_keyword: str,
                                 ) -> Iterator[TableIterator]:
        for m in re.finditer(
                r'^ *(?P<title>.*)\n'
                r'(?P<header>.* Bedrag incl\. BTW)\n',
                page_content,
                flags=re.MULTILINE):
            yield TableIterator(title=m.group('title'),
                                header=m.group('header'),
                                page_content=page_content,
                                body_start=m.end(),
                                total_keyword=total_keyword,
                                )


class TableIterator:
    def __init__(self,
                 *,
                 title: str,
                 header: str,
                 page_content: str,
                 body_start: int,
                 total_keyword: str,
                 ):
        self.title = title
        self.columns = self.parse_header(header)
        self.page = page_content
        self.body_start = body_start

        # Find table end.
        table_end_pattern = re.compile(f'^ +{total_keyword}'
                                       r' +(€ *\d+,\d\d ?-?)$',
                                       flags=re.MULTILINE)
        m = table_end_pattern.search(page_content, pos=body_start)
        if m is None:
            raise NederlandseSpoorwegenParserError(
                    f'Could not find end of {title} table.')
        self.total = parse_amount(m.group(1))
        euro_pos = m.start(1) - m.start()
        name, sl = self.columns[-2]
        self.columns[-2] = (name, slice(sl.start, euro_pos))
        name, sl = self.columns[-1]
        self.columns[-1] = (name, slice(euro_pos, sl.stop))
        self.body_end = m.start() - 1
        self.offset = body_start

    def __iter__(self) -> TableIterator:
        return self

    def __next__(self) -> dict[str, str]:
        if self.offset >= self.body_end:
            raise StopIteration()
        n = self.page.find('\n', self.offset)
        if n < 0:
            n = self.body_end
        assert n > self.offset
        line = self.page[self.offset:n]
        self.offset = n + 1

        return {name: line[sl].strip() for name, sl in self.columns}

    @staticmethod
    def parse_header(header: str) -> list[tuple[str, slice]]:
        columns: list[tuple[str, slice]] = []
        i = 0
        while i < len(header):
            # skip spaces
            for i, c in enumerate(header[i:], start=i):
                if c != ' ':
                    break
            else:
                raise RuntimeError('Reached end of header line, but found'
                                   ' only spaces.')
            # read column name
            start = i
            successive_spaces = 0
            for i, c in enumerate(header[i:], start=i):
                if c == ' ':
                    successive_spaces += 1
                    if successive_spaces > 1:
                        # time to parse next column
                        end = i - successive_spaces + 1
                        columns.append((header[start:end],
                                        slice(start, None)))
                        break
                else:
                    successive_spaces = 0
            else:
                # end of header
                columns.append((header[start:], slice(start, None)))
                break  # while loop

        # Set end of column slices (last one remains unbound).
        for i, ((name, sl), (_, next_sl)) \
                in enumerate(zip(columns, columns[1:])):
            columns[i] = (name, slice(sl.start, next_sl.start))
        return columns


class SubscriptionTable:
    def __init__(self, table: TableIterator):
        def parse_toelichting(t: str) -> tuple[date, date]:
            d1, _, d2 = t.partition(' t/m ')
            return parse_date(d1), parse_date(d2)

        self.title = table.title
        self.total = table.total
        self.lines = [SubscriptionTableLine(
                        omschrijving=d['Omschrijving'],
                        toelichting=parse_toelichting(d['Toelichting']),
                        bedrag=parse_amount(d['Bedrag incl. BTW']))
                      for d in table]

    def __iter__(self) -> Iterator[SubscriptionTableLine]:
        yield from self.lines


@dataclass
class SubscriptionTableLine:
    omschrijving: str
    toelichting: tuple[date, date]
    bedrag: Decimal


class FactuurTable:
    def __init__(self, table: TableIterator):
        self.title = table.title
        self.total = table.total
        self.lines = [FactuurTableLine(
                        omschrijving=d['Omschrijving'],
                        bedrag=parse_amount(d['Bedrag incl. BTW']))
                      for d in table]

    def __iter__(self) -> Iterator[FactuurTableLine]:
        yield from self.lines


@dataclass
class FactuurTableLine:
    omschrijving: str
    bedrag: Decimal


class NederlandseSpoorwegenParserError(RuntimeError):
    pass


def parse_amount(s: str) -> Decimal:
    s = s.removeprefix('€').lstrip().replace(',', '.')
    if s.endswith('-'):
        s = '-' + s[:-1].rstrip()
    return Decimal(s)


def parse_date(d: str, *, separator: str = '-') -> date:
    # Dutch inverse ISO format
    try:
        day, month, year = d.split(separator)
        return date(int(year), int(month), int(day))
    except ValueError as e:
        raise ValueError(f'Could not parse date {d!r}.') from e


def parse_daterange(d: str) -> tuple[date, date]:
    first, last = d.split(' t/m ')
    return (parse_date(first), parse_date(last))
