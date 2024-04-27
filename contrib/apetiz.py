#!/usr/bin/python3

# SPDX-FileCopyrightText: 2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import contextlib
import datetime
from getpass import getpass
import json
import sys
import statistics
from typing import Literal, Optional, TextIO, TypedDict, Union

from dateutil import parser as dateparser
from dateutil import tz
from dateutil.relativedelta import *
import requests


class ApetizWebsite:

    account_api_url = 'https://moncompte.apetiz.com/NIT/'

    def __init__(self, s: requests.Session):
        self.s = s
        self.user = self._get_user()

    @classmethod
    def login(cls) -> ApetizWebsite:
        # TODO: The login seems to be implemented via Oauth
        # TODO: retry password and username on login failure
        username = input('Apetiz username: ')
        password = getpass()
        s = requests.Session()
        # TODO: create correct payload and find correct login_url
        payload = {
                '_username': username,
                '_password': password,
                }
        login_url = ''  # TODO
        r = s.post(login_url, data=payload)
        r.raise_for_status()
        return cls(s)

    @classmethod
    def with_authorization_token(cls, token: str) -> ApetizWebsite:
        s = requests.Session()
        s.headers.update({'Authorization': f'Bearer {token}'})
        return cls(s)

    def _get_user(self) -> UserJSON:
        r = self.s.get(self.account_api_url + 'user/v1/users/me')
        r.raise_for_status()
        return r.json()

    @property
    def holder(self) -> str:
        return self.user['holders'][0]['id']

    @property
    def employee(self) -> str:
        return self.user['employee']['id']

    def get_transactions(self,
                         from_date: datetime.date,
                         to_date: datetime.date) -> TransactionsJSON:
        url = self.account_api_url + f'holder/v1/holders/{self.holder}/' \
              + 'accounts/main/transactions'
        r = self.s.get(url, params={
            'fromDate': from_date.isoformat(),
            'toDate': to_date.isoformat(),
            'sort': 'dateTransaction,desc',
            'includeSecondaryHolderTransactions': 'true',
            })
        r.raise_for_status()
        return r.json()

    def get_accounts(self) -> AccountsJSON:
        url = self.account_api_url + f'holder/v1/holders/{self.holder}/accounts'
        r = self.s.get(url)
        r.raise_for_status()
        return r.json()

    def get_employee_information(self) -> dict:
        url = self.account_api_url + 'employee/v1/employees/' + self.employee
        r = self.s.get(url)
        r.raise_for_status()
        return r.json()

    def get_main_card(self) -> MainCardJSON:
        url = self.account_api_url \
              + f'holder/v1/holders/{self.holder}/cards/main'
        r = self.s.get(url)
        r.raise_for_status()
        return r.json()


class UserJSON(TypedDict):
    id: str
    active: str  # "ACTIVE"
    subscriberId: str
    employee: EmployeeJSON
    holders: list[UserHolderJSON]
    login: str
    person: PersonJSON
    profiles: list[ProfileJSON]
    secretQuestions: list[SecretQuestionJSON]


class TargetStateJSON(TypedDict):
    id: str


class EmployeeJSON(TypedDict):
    id: str


class UserHolderJSON(TypedDict):
    id: str
    productId: str  # "APZ"


class PersonJSON(TypedDict):
    lastName: str
    firstName: str
    title: str


class ProfileJSON(TypedDict):
    applicationId: str  # "SiteAPZ"
    applicationName: str  # "Site Apetiz"
    type: str  # "EMPLOYEE"
    id: str  # "BENEFICIAIRE_APZ_PROFIL_2"
    targetStates: list[TargetStateJSON]


class SecretQuestionJSON(TypedDict):
    id: str
    label: str


class TransactionsJSON(TypedDict):
    items: list[TransactionItemJSON]


class PaymentJSON(TypedDict, total=False):
    type: Literal["PAYMENT"]
    amount: float  # TODO: I should probably parse this as Decimal
    supplementaryAmount: float  # TODO: I should probably parse this as Decimal
    label: str
    dateTime: str  # ISO date time, like "2020-02-02T12:34:56Z"
    status: Literal["IN_PROGRESS", "CLOSED"]
    creditDebitIndicator: Literal["D", "C"]
    readingMode: Literal["CONTACT", "CONTACTLESS"]
    authorizationNumber: str
    merchant: MerchantJSON  # does not seem to exist when status is IN_PROGRESS


class MerchantJSON(TypedDict):
    id: str
    name: str
    category: list[MerchantCategoryJSON]


class MerchantCategoryJSON(TypedDict):
    id: str
    label: str
    type: Literal["NIT"]


class LoadingJSON(TypedDict):
    type: Literal["LOADING"]
    amount: float  # TODO: I should probably parse this as Decimal
    prepaidAmount: float  # TODO: I should probably parse this as Decimal
    supplementaryAmount: float  # TODO: I should probably parse this as Decimal
    label: str  # "CHARGEMENT MILLESIME <YEAR>"
    dateTime: str  # ISO date time, like "2020-02-02T12:34:56Z"
    status: Literal["IN_PROGRESS", "CLOSED"]
    creditDebitIndicator: Literal["D", "C"]


TransactionItemJSON = Union[PaymentJSON, LoadingJSON]


class AccountsJSON(TypedDict):
    items: list[AccountItemJSON]
    additionalInformation: AdditionalAccountInformationJSON


class AccountItemJSON(TypedDict):
    type: str  # "Vintage"
    id: str  # year
    realTimeBalance: AmountJSON
    financialBalance: AmountJSON
    useEndDate: str  # ISO date
    exchangeStartDate: str  # ISO date
    exchangeEndDate: str  # ISO date


class AmountJSON(TypedDict):
    amount: float  # TODO: I should probably parse this as Decimal
    currency: str  # "EUR"


class AdditionalAccountInformationJSON(TypedDict):
    totalSpendingAvailable: AmountJSON
    dailySpendingAvailable: AmountJSON
    dailySpendingUsableToday: bool
    dailySpendingLimit: AmountJSON
    totalSpendingAvailableVintage: AmountJSON
    dailySpendingAvailableVintage: AmountJSON
    totalSpendingAvailableSuplementary: AmountJSON
    dailySpendingAvailableSuplementary: AmountJSON


class MainCardJSON(TypedDict):
    holder: CardHolderJSON
    id: str
    cardNumber: str
    product: str  # "Apetiz"
    status: str  # "ACTIVATED"
    expirationDate: str  # year-month
    canBeBlocked: bool
    canBeUnblocked: bool


class CardHolderJSON(TypedDict):
    id: str
    title: str
    firstName: str
    lastName: str


def login_to_apetiz() -> ApetizWebsite:
    print('You need to log in manually to Apetiz in your browser and copy'
          ' the Authorization Bearer token.')
    print('In Firefox you can do this by going to the login page')
    print('  https://moncompte.apetiz.com/')
    print('entering your username and password and before clicking on'
          ' the button to continue, pressing F12 to open the developer tools.')
    print('When you now continue the login, you can click in the Network tab'
          ' on any `json` Type request and look in its request headers for'
          ' the `Authorization:` field.')
    print('This field has the form')
    print('  Authorization: Bearer <long_random_string>')
    print('where <long_random_string> is the bearer token that you have to'
          ' copy here.')
    token = input('Authorization bearer token: ')
    return ApetizWebsite.with_authorization_token(token)


def download_main(args: argparse.Namespace) -> None:
    apetiz = login_to_apetiz()
    last = datetime.date.today()
    one_year_ago = last - relativedelta(years=1)
    transactions = apetiz.get_transactions(one_year_ago, last)
    all_transactions = transactions['items']
    while transactions['items']:
        last = one_year_ago - relativedelta(days=1)
        one_year_ago = last - relativedelta(years=1)
        transactions = apetiz.get_transactions(one_year_ago, last)
        all_transactions.extend(transactions['items'])

    outfile: contextlib.AbstractContextManager[TextIO]
    if args.output == '-':
        outfile = contextlib.nullcontext(sys.stdout)
    else:
        outfile = open(args.output, 'w')
    with outfile as f:
        json.dump(all_transactions, f)


def load_json_transactions(json_file: str) -> list[TransactionItemJSON]:
    infile: contextlib.AbstractContextManager[TextIO]
    if json_file == '-':
        infile = contextlib.nullcontext(sys.stdin)
    else:
        infile = open(json_file)
    with infile as f:
        return json.load(f)


def json_to_ledger_main(args: argparse.Namespace) -> None:
    all_transactions = load_json_transactions(args.json_file)
    apetiz_account = 'assets:meal_vouchers:apetiz'
    payment_account = 'expenses:food'
    loading_account = 'income:apetiz'
    currency = '€'
    for t1, t2 in zip(all_transactions, all_transactions[1:]):
        assert t1['dateTime'] > t2['dateTime']
    for transaction in reversed(all_transactions):
        type_ = transaction['type']
        if type_ == 'PAYMENT':
            account = payment_account
        elif type_ == 'LOADING':
            account = loading_account
        else:
            raise RuntimeError(f'Unexpected transaction type: {type_!r}')
        indicator = transaction['creditDebitIndicator']
        if indicator == 'D':
            sign = -1
        elif indicator == 'C':
            sign = 1
        else:
            raise RuntimeError(
                    f'Unexpected creditDebitIndicator: {indicator!r}')
        amount = sign * transaction['amount']
        # mypy complaints that LoadingJSON has no key 'merchant'.
        # This is a false positive as the PaymentJSON in the
        # TransactionItemJSON has a 'merchant' key.
        merchant: Optional[MerchantJSON] = \
                transaction.get('merchant')  # type: ignore[assignment]
        if merchant is not None:
            description = merchant['name']
        else:
            description = transaction['label']
        transaction_date = dateparser.isoparse(transaction['dateTime']) \
                                     .date()
        print(f'{transaction_date} {description}')
        print(f'    {apetiz_account}  {amount} {currency}')
        print(f'    {account}  {-amount} {currency}')
        print()


def print_recharges_main(args: argparse.Namespace) -> None:
    all_transactions = load_json_transactions(args.json_file)
    for transaction in reversed(all_transactions):
        if transaction['type'] != 'LOADING':
            continue
        amount = transaction['amount']
        transaction_date = dateparser.isoparse(transaction['dateTime']) \
                                     .date()
        print(f'{transaction_date}: {amount:6.2f} {transaction["label"]}')


def plot_payment_times_main(args: argparse.Namespace) -> None:
    import matplotlib.pyplot as plt  # type: ignore[import-untyped]
    all_transactions = load_json_transactions(args.json_file)
    dates: list[datetime.date] = []
    hours: list[float] = []
    paris_tz = tz.gettz('Europe/Paris')
    for transaction in reversed(all_transactions):
        if transaction['type'] != 'PAYMENT':
            continue
        # While the transaction time has UTC timezone in the JSON data,
        # it looks like it is actually local time.
        transaction_time = dateparser.isoparse(transaction['dateTime'])
                                     #.astimezone(paris_tz)
        dates.append(transaction_time.date())
        time = transaction_time.time()
        hours.append(time.hour + time.minute / 60 + time.second / 3600)
    median_hour = statistics.median(hours)
    plt.plot([dates[0], dates[-1]], [median_hour] * 2, '-')
    plt.plot(dates, hours, '.')
    plt.show()


if __name__ == '__main__':
    aparser = argparse.ArgumentParser(
            description='Download transactions of Apetiz meal voucher')
    subparsers = aparser.add_subparsers()
    parser_download = subparsers.add_parser('download-data',
            help='download transactions from Apetiz website')
    parser_download.add_argument('-o', dest='output', default='-',
                                 help='output file')
    parser_download.set_defaults(main=download_main)
    parser_json_to_ledger = subparsers.add_parser('json-to-ledger',
            help='convert JSON transactions to hledger format')
    parser_json_to_ledger.add_argument(
            'json_file', help='JSON file with transaction data')
    parser_json_to_ledger.set_defaults(main=json_to_ledger_main)
    parser_print_recharges = subparsers.add_parser('recharges',
            help='print date and amount of recharges')
    parser_print_recharges.add_argument(
            'json_file', help='JSON file with transaction data')
    parser_print_recharges.set_defaults(main=print_recharges_main)
    parser_plot_payment_times = subparsers.add_parser('plot-payment-times',
            help='plot times of payments against dates')
    parser_plot_payment_times.add_argument(
            'json_file', help='JSON file with transaction data')
    parser_plot_payment_times.set_defaults(main=plot_payment_times_main)

    args = aparser.parse_args()
    if 'main' not in args:
        print('Missing subcommand', file=sys.stderr)
        aparser.print_usage()
        exit(1)
    args.main(args)
