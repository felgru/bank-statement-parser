# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal

from .abnamro import DescriptionParser


def test_parsing_sepa_overboeking() -> None:
    description = ["SEPA Overboeking",
                   "IBAN: NL11ABNA1234567890",
                   "BIC: ABNANL2A",
                   "Naam: J Doe",
                   # Lines are broken after 32 characters, but the pdftotext
                   # extraction does not produce spaces at the end of a line.
                   # Make sure that those spaces are re-added by the parser.
                   "Omschrijving: Keep space at end",
                   "of line"]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    omschrijving = "Keep space at end of line"
    assert transaction.description == omschrijving
    m = transaction.metadata
    assert m['transaction_type'] == "SEPA Overboeking"
    assert m['IBAN'] == "NL11ABNA1234567890"
    assert m['BIC'] == "ABNANL2A"
    assert m['Naam'] == "J Doe"
    assert m['Omschrijving'] == omschrijving


def test_sepa_overboeking_omschrijving_falls_back_to_kenmerk() -> None:
    description = ["SEPA Overboeking",
                   "IBAN: NL11ABNA1234567890",
                   "BIC: ABNANL2A",
                   "Naam: J Doe",
                   "Kenmerk: test"]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    kenmerk = "test"
    assert transaction.description == kenmerk
    m = transaction.metadata
    assert m['transaction_type'] == "SEPA Overboeking"
    assert m['IBAN'] == "NL11ABNA1234567890"
    assert m['BIC'] == "ABNANL2A"
    assert m['Naam'] == "J Doe"
    assert m['Kenmerk'] == kenmerk
    assert 'Omschrijving' not in m


def test_parsing_sepa_overboeking_without_omschrijving_or_kenmerk() -> None:
    description = ["SEPA Overboeking",
                   "IBAN: NL11ABNA1234567890",
                   "BIC: ABNANL2A",
                   "Naam: J Doe",
                   ]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    assert transaction.description == ''
    m = transaction.metadata
    assert m['transaction_type'] == "SEPA Overboeking"
    assert m['IBAN'] == "NL11ABNA1234567890"
    assert m['BIC'] == "ABNANL2A"
    assert m['Naam'] == "J Doe"
    assert 'Omschrijving' not in m


def test_parsing_old_bea_transaction() -> None:
    description = ["BEA   NR:12345ABC   01.01.22/12.23",
                   "My example store,PAS123",
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
    assert m['NR'] == '12345ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


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
    assert m['atm_name'] == 'Geldmaat Visstraat 54'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123456'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'


def test_parsing_gea_with_location() -> None:
    description = ["GEA, Betaalpas",
                   "some foreign bank,PAS123",
                   "NR:123456   01.01.22/12.23",
                   "BERLIN,Land: DE"]

    parser = DescriptionParser(currency='EUR',
                               account='assets:bank:checking:ABN AMRO')
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("10.00"),
            )
    assert transaction.description \
            == 'Withdrawal Betaalpas, some foreign bank'
    m = transaction.metadata
    assert m['transaction_type'] == 'GEA'
    assert m['atm_name'] == 'some foreign bank'
    assert m['location'] == 'BERLIN,Land: DE'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123456'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
