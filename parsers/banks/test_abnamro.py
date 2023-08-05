# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal

from transaction import MultiTransaction
from transaction_sanitation import TransactionCleaner
from .abnamro import (
    AbnAmroConfig,
    AbnAmroPdfParser,
    AbnAmroTsvRow,
    AbnAmroTsvRowParser,
    DescriptionParser,
)

DEFAULT_ACCOUNTS = AbnAmroConfig.DEFAULT_ACCOUNTS


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
                               accounts=DEFAULT_ACCOUNTS)
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
                               accounts=DEFAULT_ACCOUNTS)
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
                               accounts=DEFAULT_ACCOUNTS)
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
                               accounts=DEFAULT_ACCOUNTS)
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
                               accounts=DEFAULT_ACCOUNTS)
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


def test_parsing_bea_transaction_with_ccv_prefix() -> None:
    description = ["BEA, Betaalpas",
                   "CCV My example store,PAS123",
                   "NR:123ABC   01.01.22/12.23",
                   "LOCATION"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    cleaner = TransactionCleaner(AbnAmroPdfParser.cleaning_rules)
    transaction = cleaner.clean(transaction)
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['payment_provider'] == 'CCV'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


def test_parsing_bea_transaction_with_ccv_prefix2() -> None:
    description = ["BEA, Betaalpas",
                   "CCV*My example store,PAS123",
                   "NR:123ABC   01.01.22/12.23",
                   "LOCATION"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    cleaner = TransactionCleaner(AbnAmroPdfParser.cleaning_rules)
    transaction = cleaner.clean(transaction)
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['payment_provider'] == 'CCV'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


def test_parsing_bea_transaction_with_zettle_prefix() -> None:
    description = ["BEA, Betaalpas",
                   "Zettle_*My example store,PAS123",
                   "NR:123ABC   01.01.22/12.23",
                   "LOCATION"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    cleaner = TransactionCleaner(AbnAmroPdfParser.cleaning_rules)
    transaction = cleaner.clean(transaction)
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['payment_provider'] == 'Zettle_'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


def test_parsing_bea_transaction_with_pay_nl_prefix() -> None:
    description = ["BEA, Betaalpas",
                   "PAY.nl*My example store,PAS123",
                   "NR:123ABC   01.01.22/12.23",
                   "LOCATION"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 1, 1),
            value_date=date(2022, 1, 1),
            amount=Decimal("1.23"),
            )
    cleaner = TransactionCleaner(AbnAmroPdfParser.cleaning_rules)
    transaction = cleaner.clean(transaction)
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['payment_provider'] == 'PAY.nl'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '123ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


def test_parsing_bea_transaction_with_comma_after_nr() -> None:
    """Test new BEA format.

    It seems the BEA format changed in Nov 2022 again and
    now contains a comma after the NR: field and uses a colon instead
    of a dot as the seperator between hour and minute.
    """
    description = ["BEA, Betaalpas",
                   "My example store,PAS123",
                   "NR:123ABC, 01.01.22/12:23",
                   "LOCATION"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
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
                               accounts=DEFAULT_ACCOUNTS)
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


def test_parsing_bea_terugboeking() -> None:
    description = ["BEA, Betaalpas",
                   "My example store,PAS123",
                   "NR:123ABC, 01.01.22/12:23",
                   "LOCATION",
                   "TERUGBOEKING BEA-TRANSACTIE"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
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
    assert m['block_comment'] == 'Terugboeking BEA-transactie'


def test_parsing_gea_transaction() -> None:
    description = ["GEA, Betaalpas",
                   "Geldmaat Visstraat 54,PAS123",
                   "NR:123456   01.01.22/12.23"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
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


def test_parsing_new_gea_transaction() -> None:
    """Test new GEA format.

    It seems the GEA format changed in Nov 2022 again and
    now contains a comma after the NR: field and uses a colon instead
    of a dot as the seperator between hour and minute.
    """
    description = ["GEA, Betaalpas",
                   "Geldmaat Visstraat 54,PAS123",
                   "NR:123456, 01.01.22/12:23"]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
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
                               accounts=DEFAULT_ACCOUNTS)
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


def test_parsing_interest() -> None:
    description = ["INTEREST",
                   "CREDIT INTEREST",
                   "period 01.04.2022 - 30.06.2022",
                   "for interest rates please",
                   "visit www.abnamro.nl/interest",
                   "see your interest note for more",
                   "information",
                   ]

    parser = DescriptionParser(currency='EUR',
                               accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(
            description=description,
            bookdate=date(2022, 6, 30),
            value_date=date(2022, 6, 30),
            amount=Decimal("1.23"),
            )
    interest_type = "CREDIT INTEREST"
    assert transaction.description == \
            f"CREDIT INTEREST 2022-04-01 to 2022-06-30"
    m = transaction.metadata
    assert m['transaction_type'] == "INTEREST"
    assert m['interest_type'] == interest_type
    assert m['period_start'] == date(2022, 4, 1)
    assert m['period_end'] == date(2022, 6, 30)
    assert m['block_comment'] == "\n".join([
        "for interest rates please",
        "visit www.abnamro.nl/interest",
        "see your interest note for more",
        "information",
    ])


def test_tsv_parsing_sepa_incasso_transaction() -> None:
    parser = AbnAmroTsvRowParser(accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(AbnAmroTsvRow(
        account='123456789',
        currency='EUR',
        date1=date(2022, 1, 1),
        balance_before=Decimal('1234.56'),
        balance_after=Decimal('1230.00'),
        date2=date(2022, 1, 1),
        amount=Decimal('-4.56'),
        rest='/TRTP/SEPA Incasso algemeen doorlopend/CSID/NL12345678'
             '/NAME/SOME SHOP/MARF/AB-12345678-9/'
             'IBAN/NL11ABNA1234567890  /BIC/ABNANL2A'
             '/EREF/abcde-1234567890'
    ))
    assert transaction.description == 'SOME SHOP | abcde-1234567890'
    m = transaction.metadata
    assert m['transaction_type'] == 'SEPA Incasso algemeen doorlopend'
    # assert m['store'] == 'SOME SHOP'
    assert m['TRTP'] == 'SEPA Incasso algemeen doorlopend'
    assert m['CSID'] == 'NL12345678'
    assert m['NAME'] == 'SOME SHOP'
    assert m['MARF'] == 'AB-12345678-9'
    assert m['IBAN'] == 'NL11ABNA1234567890'
    assert m['BIC'] == 'ABNANL2A'
    assert m['EREF'] == 'abcde-1234567890'


def test_tsv_parsing_old_bea_transaction() -> None:
    parser = AbnAmroTsvRowParser(accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(AbnAmroTsvRow(
        account='123456789',
        currency='EUR',
        date1=date(2022, 1, 1),
        balance_before=Decimal('1234.56'),
        balance_after=Decimal('1230.00'),
        date2=date(2022, 1, 1),
        amount=Decimal('-4.56'),
        rest='BEA   NR:12345ABC   01.01.22/12.23 '
             'My example store,PAS123       '
             'LOCATION                                                        '
    ))
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '12345ABC'
    assert m['date'] == date(2022, 1, 1)
    assert m['time'] == '12:23'
    assert m['location'] == 'LOCATION'


def test_tsv_parsing_foreign_bea() -> None:
    parser = AbnAmroTsvRowParser(accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(AbnAmroTsvRow(
        account='123456789',
        currency='EUR',
        date1=date(2023, 1, 1),
        balance_before=Decimal('1234.56'),
        balance_after=Decimal('1230.00'),
        date2=date(2023, 1, 1),
        amount=Decimal('-4.56'),
        rest='BEA, Betaalpas                   '
             'My example store,PAS123         '
             'NR:12345678, 01.01.23/12:34      '
             'Berlin, Land: DEU               '))
    assert transaction.description == 'My example store'
    m = transaction.metadata
    assert m['transaction_type'] == 'BEA'
    assert m['store'] == 'My example store'
    assert m['pas_nr'] == '123'
    assert m['NR'] == '12345678'
    assert m['date'] == date(2023, 1, 1)
    assert m['time'] == '12:34'
    assert m['location'] == 'Berlin, Land: DEU'


def test_tsv_parsing_old_banking_fees() -> None:
    parser = AbnAmroTsvRowParser(accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(AbnAmroTsvRow(
        account='123456789',
        currency='EUR',
        date1=date(2023, 6, 15),
        balance_before=Decimal('1234.56'),
        balance_after=Decimal('1230.21'),
        date2=date(2023, 6, 15),
        amount=Decimal('-4.35'),
        rest='ABN AMRO Bank N.V.               '
             'Account                     2,95'
             'Debit card                  1,40'
             '                                 '))
    omschrijving = "ABN AMRO Bank N.V. | Banking fees"
    assert transaction.description == omschrijving
    m = transaction.metadata
    assert m['transaction_type'] == "banking fees"
    transaction.transaction_date == date(2023, 6, 15)
    assert isinstance(transaction, MultiTransaction)
    assert transaction.is_balanced()


def test_tsv_parsing_new_banking_fees() -> None:
    parser = AbnAmroTsvRowParser(accounts=DEFAULT_ACCOUNTS)
    transaction = parser.parse(AbnAmroTsvRow(
        account='123456789',
        currency='EUR',
        date1=date(2023, 7, 15),
        balance_before=Decimal('1234.56'),
        balance_after=Decimal('1230.21'),
        date2=date(2023, 7, 15),
        amount=Decimal('-4.35'),
        rest='ABN AMRO Bank N.V.               '
             'Basic Package               2,95'
             'Debit card                  1,40'
             '                                 '))
    omschrijving = "ABN AMRO Bank N.V. | Banking fees"
    assert transaction.description == omschrijving
    m = transaction.metadata
    assert m['transaction_type'] == "banking fees"
    transaction.transaction_date == date(2023, 7, 15)
    assert isinstance(transaction, MultiTransaction)
    assert transaction.is_balanced()
