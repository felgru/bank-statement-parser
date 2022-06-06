# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal

from .abnamro import DescriptionParser


def test_parsing_bea_transaction() -> None:
    description = ["BEA, Betaalpas",
                   "My example store,PAS123",
                   "NR:123ABC   01.01.22/12.23",
                   "LOCATION"]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


def test_parsing_bea_transaction_with_currency_exchange() -> None:
    description = ["BEA, Google Pay",
                   "My example store,PAS123",
                   "NR:123ABC   01.01.22/12.23",
                   "DRNIS,Land: HR",
                   "HRK 44,00 1EUR=7,4074074 HRK",
                   "ECB Koers=7,5620027 OPSLAG 2,09%",
                   "KOSTEN •0,15 ACHTERAF BEREKEND"]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'DRNIS,Land: HR'
    assert m['foreign_amount'] == Decimal('44.00')
    assert m['foreign_currency'] == 'HRK'
    assert m['exchange_rate'] == Decimal('7.4074074')
    assert m['ecb_exchange_rate'] == Decimal('7.5620027')
    assert m['surcharge'] == Decimal('0.0209')
    assert m['costs'] == Decimal('0.15')


def test_parsing_gea_transaction() -> None:
    description = ["GEA, Betaalpas",
                   "Geldmaat Visstraat 54,PAS123",
                   "NR:123456   01.01.22/12.23"]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("10.00"),
            )
    assert transaction.description \
            == 'Withdrawal Betaalpas, Geldmaat Visstraat 54'
    m = transaction.metadata
    assert m['transaction_type'] == 'GEA'
    assert m['address'] == 'Geldmaat Visstraat 54'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123456'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
