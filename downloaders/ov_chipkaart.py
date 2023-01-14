# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import re
import sys
from typing import cast, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
import requests

from bank_statement import BankStatement
from transaction import Transaction
from .downloader import (
    Downloader,
    GenericDownloaderConfig,
    PasswordAuthenticator,
)


class OvChipkaartConfig(GenericDownloaderConfig):
    name = 'ov-chipkaart'
    display_name = 'OV-Chipkaart'
    DEFAULT_ACCOUNTS = {
        'assets': 'assets:OV-Chipkaart',
        'recharge': 'assets:balancing:OV-Chipkaart',
        'train ticket': 'expenses:transportation:train',
        'bus ticket': 'expenses:transportation:bus',
    }


class OvChipkaartDownloader(Downloader[OvChipkaartConfig]):
    def __init__(self, website: MijnOvChipkaartWebsite):
        self.api = website
        self.card_id = self.api.get_card_number()

    def download(self,
                 config: OvChipkaartConfig,
                 **kwargs) -> BankStatement:
        # TODO: start_date and end_date can at most be a month apart.
        try:
            start_date: date = kwargs.pop('start_date')
        except KeyError:
            raise RuntimeError(f'{self.__class__.__name__}.download is'
                               ' missing the start_date argument.')
        try:
            end_date: date = kwargs.pop('end_date')
        except KeyError:
            raise RuntimeError(f'{self.__class__.__name__}.download is'
                               ' missing the end_date argument.')
        if kwargs:
            raise RuntimeError(
                    f'Unknown keyword arguments: {", ".join(kwargs.keys())}')
        transactions = self.api.transactions_for_card(
                self.card_id, start_date, end_date)

        statement = travel_history_to_bank_statement(
                transactions,
                config.accounts)
        cleaner = config.cleaner
        statement.transactions = [cleaner.clean(t)
                                  for t in statement.transactions]
        config.mapper.map_transactions(statement.transactions)
        return statement

    def print_current_balance(self) -> None:
        balance, dt = self.api.current_balance(self.card_id)
        print(f'Your current balance on {dt:%Y-%m-%d %H:%M} is {balance} €',
              file=sys.stderr)


class OvChipkaartAuthenticator(PasswordAuthenticator[OvChipkaartDownloader]):
    def login(self) -> OvChipkaartDownloader:
        website = MijnOvChipkaartWebsite.login(self.username, self.password)
        return OvChipkaartDownloader(website)


class MijnOvChipkaartWebsite:
    BASE_URL = 'https://www.ov-chipkaart.nl'
    LOGIN_PAGE = f'{BASE_URL}/mijn-ov-chip.htm'
    LOGIN_SAMLSSO = 'https://login.ov-chipkaart.nl/samlsso'
    MIJN_OV_CHIP = f'{BASE_URL}/web/mijn-ov-chip.htm'
    TRAVEL_HISTORY = f'{BASE_URL}/mijn-ov-chip/mijn-ov-reishistorie.htm'

    def __init__(self, session: requests.Session):
        """Initialize Mijn OV Chipkaart website API.

        session is expected to contain the API_TOKEN and JSESSIONID cookies.

        Use the `login` class method to create a `MijnOvChipkaartWebsite`
        for given login credentials.
        """
        self.session = session

    @classmethod
    def login(cls, username: str, password: str) -> MijnOvChipkaartWebsite:
        s = requests.Session()
        res = s.get(cls.LOGIN_PAGE) # This sets the JSESSIONID cookie
        res.raise_for_status()
        # Now we have to jump through some hoops for the SAML SSO.
        soup = BeautifulSoup(res.text, 'html.parser')
        meta = soup.find('meta')
        assert isinstance(meta, Tag)
        url: str = cls.BASE_URL + cast(str, meta['content']).partition('=')[2]
        res = s.get(url)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        assert isinstance(form, Tag)
        res = s.post(
                cast(str, form['action']),
                data={i['name']: i['value']
                      for i in form.find_all('input')
                      if i.has_attr('name')},
                )
        res.raise_for_status()
        # Now we're at the actual login page with username and password fields.
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        assert isinstance(form, Tag)
        session_data_key = cast(Tag, form.find(lambda tag:
                tag.name == 'input' and tag['name'] =='sessionDataKey'
        ))['value']
        url = urljoin(urlparse(res.url)._replace(query='').geturl(),
                      cast(str, form['action']))
        # This post takes a while, probably because SSO infrastructure needs
        # to verify my credentials.
        res = s.post(
                url,
                data={
                    'username': username,
                    'password': password,
                    'chkRemember': False,
                    'sessionDataKey': session_data_key,
                },
                )
        res.raise_for_status()
        if '&authFailure=true' in urlparse(res.url).query:
            raise RuntimeError('Authentication failure.'
                               ' Please check username and password.')
        # … and another SAML SSO redirect…
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        if form is None:
            raise RuntimeError('Unexpected error: Could not find SAML SSO form.'
                               ' Please try again.')
        assert isinstance(form, Tag)
        res = s.post(
                cast(str, form['action']),
                data={i['name']: i['value']
                      for i in form.find_all('input')
                      if i.has_attr('name')},
                )
        res.raise_for_status()
        # Now we're finally logged in and res.url contains
        # 'https://www.ov-chipkaart.nl/web/mijn-ov-chip.htm'.
        return cls(session=s)

    def get_card_number(self) -> str:
        res = self.session.get(
                self.TRAVEL_HISTORY,
                )
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form', id="cardselector_form")
        assert isinstance(form, Tag)
        cards = form.find_all('ol')
        assert len(cards) == 1
        return cards[0].find('span', class_='cs-card-number')['data-hashed']

    def card_information(self, hashed_medium_id: str) -> dict:
        # TODO: Somehow this fails with error 403
        url = self.BASE_URL + '/web/medium_information'
        res = self.session.post(
                url,
                data={
                    "hashedMediumId": hashed_medium_id,
                    "languagecode": "nl-NL",
                },
                )
        res.raise_for_status()
        return res.json()

    def current_balance(self,
                        hashed_medium_id: str) -> tuple[Decimal, datetime]:
        return self._last_two_weeks_history_for_card(hashed_medium_id) \
                   .current_balance

    def last_two_weeks_transactions_for_card(self,
                                             hashed_medium_id: str,
                                             ) -> list[OvTransaction]:
        hist = self._last_two_weeks_history_for_card(hashed_medium_id)
        num_pages = hist.num_pages()
        raw_transactions = hist.extract_raw_travel_history()
        for page in range(2, num_pages + 1):
            hist = self._last_two_weeks_history_for_card(hashed_medium_id,
                                                         page)
            raw_transactions.extend(hist.extract_raw_travel_history())
        raw_transactions.reverse()
        return self._parse_raw_transactions(raw_transactions)

    def transactions_for_card(self,
                              hashed_medium_id: str,
                              begin_date: date,
                              end_date: date,
                              ) -> list[OvTransaction]:
        hist = self._history_for_card(hashed_medium_id,
                                      begin_date,
                                      end_date)
        num_pages = hist.num_pages()
        raw_transactions = hist.extract_raw_travel_history()
        for page in range(2, num_pages + 1):
            hist = self._history_for_card(hashed_medium_id,
                                          begin_date,
                                          end_date,
                                          page)
            raw_transactions.extend(hist.extract_raw_travel_history())
        return self._parse_raw_transactions(raw_transactions)

    def _last_two_weeks_history_for_card(self,
                                         hashed_medium_id: str,
                                         page_number: Optional[int] = None,
                                         ) -> HistoryPage:
        """Return travel history of last two weeks.

        Careful: The returned items are in reverse chronological order,
        as opposed to the items returned by `history_for_card`!
        """
        params = {'mediumid': hashed_medium_id}
        if page_number is not None:
            params['pagenumber'] = str(page_number)
        res = self.session.get(
                self.TRAVEL_HISTORY,
                params=params,
                )
        res.raise_for_status()
        return HistoryPage.from_html(res.text)

    def _history_for_card(self,
                          hashed_medium_id: str,
                          begin_date: date,
                          end_date: date,
                          page_number: Optional[int] = None) -> HistoryPage:
        params = {
            'mediumid': hashed_medium_id,
            'begindate': f'{begin_date:%d-%m-%Y}',
            'enddate': f'{end_date:%d-%m-%Y}',
            # type can be used to filter the list of transactions.
            # An empty string shows all types of transactions.
            'type': '',
        }
        if page_number is not None:
            params['pagenumber'] = str(page_number)
        res = self.session.get(
                self.TRAVEL_HISTORY,
                params=params,
                )
        res.raise_for_status()
        return HistoryPage.from_html(res.text)

    @staticmethod
    def _parse_raw_transactions(raw_transactions: list[RawTransaction]) -> list[OvTransaction]:
        state = TransactionParserState()
        for transaction in raw_transactions:
            state.push(transaction)
        return state.get_parsed_transactions()


class TransactionParserState:
    def __init__(self) -> None:
        self.transactions: list[OvTransaction] = []
        self.next_transaction: list[RawTransaction] = []

    def push(self, transaction: RawTransaction) -> None:
        if transaction.type == 'Saldo automatisch opgeladen':
            self._handle_queued_transactions()
            self.transactions.append(OvTransaction.recharge(transaction))
        elif transaction.type == 'Check-in':
            self._handle_queued_transactions()
            self.next_transaction.append(transaction)
        elif transaction.type == 'Check-uit':
            assert len(self.next_transaction) == 1
            assert self.next_transaction[0].type == 'Check-in'
            self.transactions.append(
                    OvTransaction.ride(self.next_transaction[0], transaction))
            self.next_transaction.clear()
        elif transaction.type == 'Saldo teruggave':
            self._handle_queued_transactions()
            self.transactions.append(
                    OvTransaction.balance_return(transaction))
        elif transaction.type == 'Automatisch opladen':
            # ignore
            pass
        elif transaction.type == 'Product op kaart geladen':
            # ignore
            pass
        else:
            raise RuntimeError(f'Unknown transaction type {transaction.type!r}.')

    def _handle_queued_transactions(self) -> None:
        if not self.next_transaction:
            return
        # Unbalanced Check-in
        assert len(self.next_transaction) == 1
        assert self.next_transaction[0].type == 'Check-in'
        self.transactions.append(
                OvTransaction.unbalanced_check_in(self.next_transaction[0]))
        self.next_transaction.clear()

    def get_parsed_transactions(self) -> list[OvTransaction]:
        assert not self.next_transaction
        return self.transactions


@dataclass
class HistoryPage:
    soup: BeautifulSoup

    @classmethod
    def from_html(cls, html: str) -> HistoryPage:
        soup = BeautifulSoup(html, 'html.parser')
        return cls(soup=soup)

    def num_pages(self) -> int:
        pagination = self.soup.find('div', class_='transaction-pagination')
        if pagination is None:
            # No search results.
            transactions = self.soup.find('div', id='transactions')
            assert isinstance(transactions, Tag)
            page_info = transactions.find('span', class_='page-info')
            assert isinstance(page_info, Tag)
            m = re.search('\(\s*(\d+)\s*transacties', page_info.text)
            assert m is not None
            num_transactions = int(m.group(1))
            if num_transactions > 0:
                raise RuntimeError(f'{num_transactions} transactions,'
                                   ' but no pagination found.')
            return 0
        assert isinstance(pagination, Tag)
        buttons = pagination.find_all('button', attrs={'name': 'pagenumber'})
        return int(buttons[-1]['value'])

    @property
    def current_balance(self) -> tuple[Decimal, datetime]:
        info = self.soup.find('table', id='card-info-table')
        assert isinstance(info, Tag)
        label = info.find('td', class_='table-label')
        assert isinstance(label, Tag) and label.text == 'Saldo'
        td = label.find_next_sibling('td')
        assert isinstance(td, Tag)
        grey = td.find('span', class_='grey')
        assert isinstance(grey, Tag)
        amount = parse_amount(cast(NavigableString, grey.previous).strip())
        d, t = grey.text[1:-1].split()
        dt = datetime.combine(parse_nl_date(d), time.fromisoformat(t))
        return amount, dt

    def extract_raw_travel_history(self) -> list[RawTransaction]:
        return [RawTransaction.from_soup(transaction)
                for transaction in self.soup.find_all(
                    'tr', class_='known-transaction')]


@dataclass
class OvTransaction:
    type: str
    date: date
    mode_of_transportation: Optional[str]
    place: str
    amount: Optional[Decimal]

    @classmethod
    def ride(cls,
            check_in: RawTransaction,
            check_out: RawTransaction) -> OvTransaction:
        amount: Optional[Decimal] = None
        if check_out.fare is not None:
            assert check_in.amount is not None
            assert check_out.amount is not None
            assert -check_out.fare == check_in.amount + check_out.amount
            assert check_out.reference == (check_in.type, check_in.place)
            amount = -check_out.fare
        place = ' → '.join(f'{t.place} ({t.time:%H:%M})'
                           for t in (check_in, check_out))
        return OvTransaction(
                type='ride',
                date=check_in.date,
                mode_of_transportation=check_in.mode_of_transportation,
                place=place,
                amount=amount,
                )

    @classmethod
    def unbalanced_check_in(cls,
                            check_in: RawTransaction) -> OvTransaction:
        return OvTransaction(
                type='ride',
                date=check_in.date,
                mode_of_transportation=check_in.mode_of_transportation,
                place=f'{check_in.place} ({check_in.time:%H:%M}),'
                      ' missing check-out',
                amount=check_in.amount,
                )

    @classmethod
    def recharge(cls, transaction: RawTransaction) -> OvTransaction:
        assert transaction.mode_of_transportation is None
        assert transaction.amount is not None
        return OvTransaction(
                type='recharge',
                date=transaction.date,
                mode_of_transportation=None,
                place='',
                amount=transaction.amount,
                )

    @classmethod
    def balance_return(cls, transaction: RawTransaction) -> OvTransaction:
        assert transaction.mode_of_transportation is None
        assert transaction.amount is not None
        return OvTransaction(
                type='balance return',
                date=transaction.date,
                mode_of_transportation=None,
                place='',
                amount=-transaction.amount,
                )


@dataclass
class RawTransaction:
    date: date
    type: str
    mode_of_transportation: Optional[str]
    time: time
    place: str
    meta: dict[str, str]
    lines: list[NavigableString]
    amount: Optional[Decimal]
    amount_description: Optional[str]
    fare: Optional[Decimal]

    @property
    def reference(self) -> Optional[tuple[str, str]]:
        check_in = self.meta.get('Check-in')
        if check_in is not None:
            return 'Check-in', check_in
        else:
            return None

    @classmethod
    def from_soup(cls, transaction: BeautifulSoup) -> RawTransaction:
        datum, omschrijving, ritprijs, declareren = transaction.find_all('td')
        d = parse_nl_date(datum.text.lstrip().partition(' ')[0])
        b = omschrijving.find('b')
        type_ = b.text.strip()
        n = b.next_sibling
        assert n.name != 'br'
        mode_of_transportation: Optional[str] = ' '.join(n.split())
        if not mode_of_transportation:
            mode_of_transportation = None
        n = n.next_sibling
        assert n.name == 'br'
        n = n.next_sibling
        t, _, rest_of_time_line = n.lstrip().partition('\xa0\xa0')
        place = ' '.join(rest_of_time_line.split())
        lines = []
        while n.next_sibling is not None:
            n = n.next_sibling
            assert n.name == 'br'
            n = n.next_sibling
            lines.append(n)
        meta = {}
        to_delete = []
        for i, line in enumerate(lines):
            if ':' in line:
                key, _, value = line.partition(':')
                meta[key.strip()] = value.strip()
                to_delete.append(i)
        for i in reversed(to_delete):
            del lines[i]
        if not lines:
            amount: Optional[Decimal] = None
            amount_description: Optional[str] = None
        else:
            assert len(lines) == 1
            m = re.search(r'(€ -? ?\d+,\d\d)(.*)', lines[-1], flags=re.DOTALL)
            assert m is not None
            amount = parse_amount(m.group(1))
            amount_description = ' '.join(m.group(2).split())
            if not amount_description:
                amount_description = None
        p = ritprijs.text.strip()
        price = parse_amount(p) if p else None
        return cls(
            date=d,
            type=type_,
            mode_of_transportation=mode_of_transportation,
            time=time.fromisoformat(t),
            place=place,
            meta=meta,
            lines=lines,
            amount=amount,
            amount_description=amount_description,
            fare=price,
        )


def travel_history_to_bank_statement(
        transactions: list[OvTransaction],
        accounts: dict[str, str]) -> BankStatement:
    def convert_transaction(transaction: OvTransaction,
                            ) -> Optional[Transaction]:
        # TODO: That looks like a nice case for structural pattern matching
        #       once we depend on Python 3.10.
        if transaction.type == 'recharge':
            account = accounts['recharge']
        elif transaction.type == 'balance return':
            account = accounts['recharge']
        elif transaction.mode_of_transportation is None:
            raise RuntimeError('Expected mode_of_transportation for transaction'
                               f' {transaction}.')
        elif transaction.amount is None:
            # Don't create ledger transactions for itineraries that
            # were payed with a monthly subscription.
            return None
        elif transaction.mode_of_transportation.startswith('Trein'):
            account = accounts['train ticket']
        elif transaction.mode_of_transportation.startswith('Bus'):
            account = accounts['bus ticket']
        else:
            raise RuntimeError('Unknown mode of transportation'
                               f' "{transaction.mode_of_transportation}" in'
                               f' transaction {transaction}.')
        assert transaction.amount is not None
        description = 'OV-Chipkaart |'
        if transaction.mode_of_transportation is None:
            description += ' ' + transaction.type
        else:
            description += ' ' + transaction.mode_of_transportation
        if transaction.place:
            description += ' | ' + transaction.place
        return Transaction(
                account=accounts['assets'],
                description=description,
                operation_date=transaction.date,
                value_date=None,
                amount=transaction.amount,
                currency='€',
                external_account=account,
                )

    return BankStatement(
            transactions=[t for t in (convert_transaction(t)
                                      for t in transactions)
                          if t is not None],
            )


def parse_nl_date(d: str) -> date:
    """Parse Dutch "reverse ISO" date of form DD-MM-YYYY."""
    day, month, year = d.split('-')
    return date(year=int(year), month=int(month), day=int(day))


def parse_amount(s: str) -> Decimal:
    """Parse amount of form '€ 12,34' or '€ - 12,34'."""
    return Decimal(s.removeprefix('€ ').replace(',', '.').replace(' ', ''))


def last_day_of_month(d: date) -> date:
    d = d.replace(day=28)
    one_day = timedelta(days=1)
    while d.month == (d + one_day).month:
        d += one_day
    return d
