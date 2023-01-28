#!/usr/bin/python3

# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import re
import sys
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup  # type: ignore
import requests

from downloaders import downloaders
from downloaders.autoloader import Downloaders, Website
from downloaders.downloader import authenticate_interactively


def last_day_of_month(d: date) -> date:
    d = d.replace(day=28)
    one_day = timedelta(days=1)
    while d.month == (d + one_day).month:
        d += one_day
    return d


def create_argument_parser(downloaders: Downloaders) -> argparse.ArgumentParser:
    aparser = argparse.ArgumentParser(
            description='Download transactions from a website.')

    subparsers = aparser.add_subparsers(title='websites',
                                        help='website to download from')
    for title, website in sorted(downloaders.items()):
        subparser = subparsers.add_parser(title)
        website.downloader.instantiate_argparser(subparser)
        subparser.set_defaults(website=website)

    return aparser


if __name__ == '__main__':
    aparser = create_argument_parser(downloaders)

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

    website: Website = args.website

    if len(website.authenticators) != 1:
        raise NotImplementedError(
                'Selecting from multiple Authenticators not implemented, yet.')
    Authenticator = website.authenticators[0]
    downloader = authenticate_interactively(Authenticator)
    config = downloader.config_type().load(args.rules)

    d = start_date
    while d < end_date:
        bank_statement = downloader.download(
                config=config,
                start_date=d,
                end_date=min(last_day_of_month(d), end_date),
                )
        if args.dry_run:
            bank_statement.write_ledger(sys.stdout)
        else:
            ledger_file = Path(
                    f'{d:%Y}/{d:%m}/{downloader.config_type().name}.hledger')
            ledger_file.parent.mkdir(parents=True, exist_ok=True)
            with open(ledger_file, 'w') as f:
                bank_statement.write_ledger(f)
        if d.month < 12:
            d = d.replace(month=d.month+1, day=1)
        else:
            d = d.replace(year=d.year+1, month=1, day=1)
    downloader.print_current_balance()
