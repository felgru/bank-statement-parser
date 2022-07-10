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

from downloaders import downloaders
from downloaders.downloader import PasswordAuthenticator


def last_day_of_month(d: date) -> date:
    d = d.replace(day=28)
    one_day = timedelta(days=1)
    while d.month == (d + one_day).month:
        d += one_day
    return d


if __name__ == '__main__':
    aparser = argparse.ArgumentParser(
            description='Download transactions from a website.')
    aparser.add_argument('--start-date', default=None,
            help='start date of download in ISO format'
                 ' (default: beginning of last month)')
    aparser.add_argument('--end-date', default=None,
            help='end date of download in ISO format'
                 ' (default: end of last month)')
    aparser.add_argument('--balancing-account',
            default=None,
            help='balancing account for transactions to your other accounts')
    aparser.add_argument('--dry-run', '-n',
            dest='dry_run',
            action='store_true',
            help='print downloaded history to stdout instead of writing it'
                 ' to hledger files.')
    aparser.add_argument('website', action='store',
            help=f'website to download from ({", ".join(sorted(downloaders))})')

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

    try:
        website = downloaders[args.website]
    except KeyError:
        print(f"Unknown website {args.website}", file=sys.stderr)
        exit(1)

    if len(website.authenticators) != 1:
        raise NotImplementedError(
                'Selecting from multiple Authenticators not implemented, yet.')
    Authenticator = website.authenticators[0]
    assert issubclass(Authenticator, PasswordAuthenticator)

    username = input('Username: ')
    password = getpass('Password: ')
    downloader = Authenticator(username, password).login()

    d = start_date
    while d < end_date:
        bank_statement = downloader.download(
                rules_dir=None,
                start_date=d,
                end_date=min(last_day_of_month(d), end_date),
                balancing_account=args.balancing_account)
        if args.dry_run:
            bank_statement.write_ledger(sys.stdout)
        else:
            with open(f'{d:%Y}/{d:%m}/{downloader.name}.hledger', 'w') as f:
                bank_statement.write_ledger(f)
        if d.month < 12:
            d = d.replace(month=d.month+1, day=1)
        else:
            d = d.replace(year=d.year+1, month=1, day=1)
