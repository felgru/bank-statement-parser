# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal

from .ing_de import parse_transaction


def test_parsing_visa_card_transaction() -> None:
    description = ("VISA NAME OF SHOP\n"
                   "NR XXXX 1234 SHOP DE KAUFUMSATZ 23.01 123456\n"
                   "ARN12345678901234567890123")
    description, external_value_date, metadata \
            = parse_transaction('Lastschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'VISA NAME OF SHOP'
    assert external_value_date == date(2022, 1, 23)
    assert metadata == {
        'ARN_number': 'ARN12345678901234567890123',
        'NR_number': 'NR XXXX 1234',
        'card_transaction_type': 'KAUFUMSATZ',
        'country': 'DE',
        'location': 'SHOP',
        'type': 'Lastschrift',
    }


def test_parsing_foreign_visa_card_transaction() -> None:
    description = ("VISA NAME OF SHOP\n"
                   "NR1234567890 MONTREAL CA KURS 1,1234567 KAUFUMSATZ 23.01\n"
                   "12,34 123456 ARN12345678901234567890123")
    description, external_value_date, metadata \
            = parse_transaction('Lastschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'VISA NAME OF SHOP'
    assert external_value_date == date(2022, 1, 23)
    assert metadata == {
        'ARN_number': 'ARN12345678901234567890123',
        'NR_number': 'NR1234567890',
        'card_transaction_type': 'KAUFUMSATZ',
        'country': 'CA',
        'exchange_rate': Decimal('1.1234567'),
        'foreign_amount': Decimal('12.34'),
        'location': 'MONTREAL',
        'type': 'Lastschrift',
    }


def test_parsing_foreign_visa_card_exchange_fee_transaction() -> None:
    description = ("VISA NAME OF SHOP\n"
                   "NR1234567890 1,75% WECHSELKURSGEBUEHR 23.01\n"
                   "ARN12345678901234567890123")
    description, external_value_date, metadata \
            = parse_transaction('Lastschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'VISA NAME OF SHOP'
    assert external_value_date == date(2022, 1, 23)
    assert metadata == {
        'ARN_number': 'ARN12345678901234567890123',
        'NR_number': 'NR1234567890',
        'exchange_fee_rate': Decimal('0.0175'),
        'fee_type': 'WECHSELKURSGEBUEHR',
        'type': 'Entgelt',
    }


def test_parsing_cash_withdrawal_transaction() -> None:
    description = ("VISA MY BANK\n"
                   "NR1234567890 CITY BRANCH FR "
                   "BARGELDAUSZAHLUNG 30.01 123456\n"
                   "ARN12345678901234567890123")
    description, external_value_date, metadata \
            = parse_transaction('Lastschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'VISA MY BANK'
    assert external_value_date == date(2022, 1, 30)
    assert metadata == {
        'ARN_number': 'ARN12345678901234567890123',
        'NR_number': 'NR1234567890',
        'card_transaction_type': 'BARGELDAUSZAHLUNG',
        'country': 'FR',
        'location': 'CITY BRANCH',
        'type': 'Lastschrift',
    }


def test_parsing_foreign_cash_withdrawal_transaction() -> None:
    description = ("Bargeldauszahlung VISA Card ATM IN FOREIGN\n"
                   "COUNTRY\n"
                   "NR1234567890 CITY NO KURS 1,2345678 BARGELDAUSZAHLUNG\n"
                   "30.01 12,34 123456 ARN12345678901234567890123")
    description, external_value_date, metadata \
            = parse_transaction('Lastschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'Bargeldauszahlung VISA Card ATM IN FOREIGN COUNTRY'
    assert external_value_date == date(2022, 1, 30)
    assert metadata == {
        'ARN_number': 'ARN12345678901234567890123',
        'NR_number': 'NR1234567890',
        'card_transaction_type': 'BARGELDAUSZAHLUNG',
        'country': 'NO',
        'exchange_rate': Decimal('1.2345678'),
        'foreign_amount': Decimal('12.34'),
        'location': 'CITY',
        'type': 'Lastschrift',
    }


def test_parsing_direct_debit_transaction() -> None:
    description = ("NAME OF SHOP\n"
                   "description line 1\n"
                   "description line 2\n"
                   "Mandat: 1234567890ABC\n"
                   "Referenz: 1234567890123 ABC")
    description, external_value_date, metadata \
            = parse_transaction('Lastschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'NAME OF SHOP | description line 1 description line 2'
    assert external_value_date is None
    assert metadata == {
        'Mandat': '1234567890ABC',
        'Referenz': '1234567890123 ABC',
        'type': 'Lastschrift',
    }


def test_parsing_exchange_fee_transaction() -> None:
    description = ("VISA NAME OF SHOP\n"
                   "VISA 4546 XXXX XXXX 1234 1,75%AUSLANDSEINSATZENTGELT\n"
                   "KREDITKARTE (VISA CARD) ARN12345678901234567890123")
    description, external_value_date, metadata \
            = parse_transaction('Entgelt',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'VISA NAME OF SHOP'
    assert external_value_date is None
    assert metadata == {
        'ARN_number': 'ARN12345678901234567890123',
        'card_number': '4546 XXXX XXXX 1234',
        'exchange_fee_rate': Decimal('0.0175'),
        'fee_type': 'AUSLANDSEINSATZENTGELT',
        'type': 'Entgelt',
    }


def test_parsing_ueberweisung_transaction() -> None:
    description = ("Sender\n"
                   "description1\n"
                   "description2")
    description, external_value_date, metadata \
            = parse_transaction('Gutschrift',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'Sender | description1 description2'
    assert external_value_date is None
    assert metadata == {
        'name': 'Sender',
        'type': 'Gutschrift',
    }


def test_parsing_salary_transaction() -> None:
    description = ("Employer\n"
                   "description\n"
                   "Referenz: 123-456-789")
    description, external_value_date, metadata \
            = parse_transaction('Gehalt/Rente',
                                description,
                                date(2022, 1, 31),
                                )
    assert description == 'Employer | description'
    assert external_value_date is None
    assert metadata == {
        'Referenz': '123-456-789',
        'type': 'Gehalt/Rente',
    }
