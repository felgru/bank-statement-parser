#!/usr/bin/python3

# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from getpass import getpass
from pathlib import Path
import re
import sys
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup  # type: ignore
import requests

sys.path.append(str(Path(__file__).resolve().parent.parent))

from bank_statement import BankStatement
from transaction import Transaction


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
        url = cls.BASE_URL + soup.find('meta')['content'].partition('=')[2]
        res = requests.get(url)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        res = s.post(
                form['action'],
                data={i['name']: i['value']
                      for i in form.find_all('input')
                      if i.has_attr('name')},
                )
        res.raise_for_status()
        # Now we're at the actual login page with username and password fields.
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        session_data_key = form.find(lambda tag:
                tag.name == 'input' and tag['name'] =='sessionDataKey'
        )['value']
        url = urljoin(urlparse(res.url)._replace(query='').geturl(),
                      form['action'])
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
        res = s.post(
                form['action'],
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
        transactions = []
        next_transaction: list[RawTransaction] = []
        for transaction in raw_transactions:
            if transaction.type == 'Saldo automatisch opgeladen':
                assert not next_transaction
                transactions.append(OvTransaction.recharge(transaction))
            elif transaction.type == 'Check-in':
                next_transaction.append(transaction)
            elif transaction.type == 'Check-uit':
                assert len(next_transaction) == 1
                assert next_transaction[0].type == 'Check-in'
                transactions.append(OvTransaction.ride(next_transaction[0],
                                                       transaction))
                next_transaction.clear()
            else:
                raise RuntimeError(f'Unknown transaction type {transaction.type!r}.')
        assert not next_transaction
        return transactions


@dataclass
class HistoryPage:
    soup: BeautifulSoup

    @classmethod
    def from_html(cls, html: str) -> HistoryPage:
        soup = BeautifulSoup(html, 'html.parser')
        return cls(soup=soup)

    def num_pages(self) -> int:
        pagination = self.soup.find('div', class_='transaction-pagination')
        buttons = pagination.find_all('button', attrs={'name': 'pagenumber'})
        return int(buttons[-1]['value'])

    @property
    def current_balance(self) -> tuple[Decimal, datetime]:
        info = self.soup.find('table', id='card-info-table')
        label = info.find('td', class_='table-label')
        assert label.text == 'Saldo'
        td = label.find_next_sibling('td')
        grey = td.find('span', class_='grey')
        amount = parse_amount(grey.previous.strip())
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
    amount: Decimal

    @classmethod
    def ride(cls,
            check_in: RawTransaction,
            check_out: RawTransaction) -> OvTransaction:
        assert check_out.fare is not None
        assert -check_out.fare == check_in.amount + check_out.amount
        assert check_out.reference == (check_in.type, check_in.place)
        place = ' → '.join(f'{t.place} ({t.time:%H:%M})'
                           for t in (check_in, check_out))
        return OvTransaction(
                type='ride',
                date=check_in.date,
                mode_of_transportation=check_in.mode_of_transportation,
                place=place,
                amount=-check_out.fare,
                )

    @classmethod
    def recharge(cls, transaction: RawTransaction) -> OvTransaction:
        assert transaction.mode_of_transportation is None
        return OvTransaction(
                type='recharge',
                date=transaction.date,
                mode_of_transportation=None,
                place='',
                amount=transaction.amount,
                )


@dataclass
class RawTransaction:
    date: date
    type: str
    mode_of_transportation: Optional[str]
    time: time
    place: str
    reference: Optional[tuple[str, str]]
    amount: Decimal
    amount_description: Optional[str]
    fare: Optional[Decimal]

    @classmethod
    def from_soup(cls, transaction: BeautifulSoup) -> RawTransaction:
        datum, omschrijving, ritprijs, declareren = transaction.find_all('td')
        d = parse_nl_date(datum.text.lstrip().partition(' ')[0])
        b = omschrijving.find('b')
        n = b.next_sibling
        assert n.name != 'br'
        mode_of_transportation: Optional[str] = ' '.join(n.split())
        if not mode_of_transportation:
            mode_of_transportation = None
        n = n.next_sibling
        assert n.name == 'br'
        n = n.next_sibling
        t, *rest = n.split()
        place = ' '.join(rest)
        n = n.next_sibling
        assert n.name == 'br'
        n = n.next_sibling
        reference: Optional[tuple[str, str]] = None
        if ':' in n:
            reference = tuple(s.strip()
                              for s in n.partition(':')[::2])  # type: ignore
            n = n.next_sibling
            assert n.name == 'br'
            n = n.next_sibling
        m = re.search(r'(€ -? ?\d+,\d\d)(.*)', n, flags=re.DOTALL)
        assert m is not None
        amount = parse_amount(m.group(1))
        amount_description: Optional[str] = ' '.join(m.group(2).split())
        if not amount_description:
            amount_description = None
        assert n.next_sibling is None
        p = ritprijs.text.strip()
        price = parse_amount(p) if p else None
        return cls(
            date=d,
            type=b.text.strip(),
            mode_of_transportation=mode_of_transportation,
            time=time.fromisoformat(t),
            place=place,
            reference=reference,
            amount=amount,
            amount_description=amount_description,
            fare=price,
        )


def travel_history_to_bank_statement(
        transactions: list[OvTransaction],
        recharge_account: str) -> BankStatement:
    def convert_transaction(transaction: OvTransaction) -> Transaction:
        # TODO: That looks like a nice case for structural pattern matching
        #       once we depend on Python 3.10.
        if transaction.type == 'recharge':
            account = recharge_account
        elif transaction.mode_of_transportation is None:
            raise RuntimeError('Expected mode_of_transportation for transaction'
                               f' {transaction}.')
        elif transaction.mode_of_transportation.startswith('Trein'):
            account = 'expenses:transportation:train'
        elif transaction.mode_of_transportation.startswith('Bus'):
            account = 'expenses:transportation:bus'
        else:
            raise RuntimeError('Unknown mode of transportation'
                               f' "{transaction.mode_of_transportation}" in'
                               f' transaction {transaction}.')
        description = 'OV-Chipkaart |'
        if transaction.mode_of_transportation is None:
            description += ' ' + transaction.type
        else:
            description += ' ' + transaction.mode_of_transportation
        if transaction.place:
            description += ' | ' + transaction.place
        return Transaction(
                account='assets:OV-Chipkaart',
                description=description,
                operation_date=transaction.date,
                value_date=None,
                amount=transaction.amount,
                currency='€',
                external_account=account,
                )

    return BankStatement(
            account='assets:OV-Chipkaart',
            transactions=[convert_transaction(t) for t in transactions],
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


if __name__ == '__main__':
    aparser = argparse.ArgumentParser(
            description='Download OV-Chipkaart history.')
    aparser.add_argument('--start-date', default=None,
            help='start date of download in ISO format'
                 ' (default: beginning of last month)')
    aparser.add_argument('--end-date', default=None,
            help='end date of download in ISO format'
                 ' (default: end of last month)')
    aparser.add_argument('--balancing-account',
            default='assets:balancing:OV-Chipkaart',
            help='balancing account for rechargin OV-Chipkaart')
    aparser.add_argument('--dry-run', '-n',
            dest='dry_run',
            action='store_true',
            help='print downloaded history to stdout instead of writing it'
                 ' to hledger files.')

    args = aparser.parse_args()
    if args.start_date is None:
        start_date = date.today().replace(day=1)
        if start_date.month == 1:
            start_date = start_date.replace(year=start_date.year-1, month=12)
        else:
            start_date = start_date.replace(month=start_date.month-1)
    else:
        start_date = date.fromisoformat(args.start_date)
    if args.end_date is None:
        end_date = date.today()
        if end_date.month == 1:
            end_date = end_date.replace(year=end_date.year-1, month=12)
        else:
            end_date = end_date.replace(month=end_date.month-1)
        end_date = last_day_of_month(end_date)
    else:
        end_date = date.fromisoformat(args.end_date)

    username = input('Username: ')
    password = getpass('Password: ')
    api = MijnOvChipkaartWebsite.login(username, password)
    card_id = api.get_card_number()

    d = start_date
    while d < end_date:
        transactions = api.transactions_for_card(card_id, d,
                                                 min(last_day_of_month(d),
                                                     end_date))
        bank_statement = travel_history_to_bank_statement(
                transactions,
                args.balancing_account)
        if args.dry_run:
            bank_statement.write_ledger(sys.stdout)
        else:
            with open(f'{d:%Y}/{d:%m}/ov-chipkaart.hledger', 'w') as f:
                bank_statement.write_ledger(f)
        if d.month < 12:
            d = d.replace(month=d.month+1, day=1)
        else:
            d = d.replace(year=d.year+1, month=1, day=1)

    balance, dt = api.current_balance(card_id)
    print(f'Your current balance on {dt:%Y-%m-%d %H:%M} is {balance} €')
