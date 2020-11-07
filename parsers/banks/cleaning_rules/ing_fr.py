# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
from decimal import Decimal
import re

from transaction_sanitation import TransactionCleanerRule as Rule

def parse_transaction_type(t):
    if t.description.startswith('INTERET'):
        return dict(type='INTERET')
    elif t.description.startswith('VIREMENT '):
        return dict(type='VIREMENT INTERNE')
    else:
        raise RuntimeError('Unable to guess transaction type of:', t.description)

def is_sepa_direct_debit(t):
    return (t.type == 'PRELEVEMENT TIP'
            and t.description.startswith('Prlv Sepa '))

sepa_direct_debit_pattern = re.compile(r'PRLV SEPA (.*?):\s+([A-Z]{2}\S+)\s+(.*)',
                                       flags=re.MULTILINE | re.DOTALL)

def clean_sepa_direct_debit(t):
    m = sepa_direct_debit_pattern.match(t.metadata['raw_description'])
    prefix = 'prlv SEPA '
    recipient = m.group(1).strip().title()
    # Creditor Identifier (CI)
    # https://www.sepaforcorporates.com/sepa-direct-debits/sepa-creditor-identifier/
    # https://www.europeanpaymentscouncil.eu/sites/default/files/kb/file/2019-09/EPC262-08%20v7.0%20Creditor%20Identifier%20Overview_0.pdf
    creditor_identifier = m.group(2)
    # Unique Mandate Reference (UMR)
    # https://de.wikipedia.org/wiki/Mandatsreferenz
    mandate_reference = m.group(3).replace('\n', '')
    description = prefix + recipient
    block_comment = t.metadata.get('block_comment', '')
    if block_comment:
        block_comment += '\n'
    block_comment += f'SEPA CI:  {creditor_identifier}\n' \
                     f'SEPA UMR: {mandate_reference}'
    metadata = dict(**t.metadata,
                    sepa_creditor_identifier=creditor_identifier,
                    sepa_mandate_reference=mandate_reference,
                    block_comment=block_comment,
                    )
    return description, metadata

def is_sepa_giro_transfer(t):
    return (t.type == 'VIREMENT EXTERNE'
            and t.description.startswith('Virement Sepa'))

sepa_pattern = re.compile(r'Virement Sepa (Recu|Emis Vers) +(.*)')
sepa_emis_pattern = re.compile(r'VIREMENT SEPA EMIS VERS\s*(\S+)',
                              flags=re.MULTILINE)

def clean_sepa_giro_transfer(t):
    m = sepa_pattern.match(t.description)
    direction = m.group(1).lower()
    if direction == 'recu':
        direction = 'reçu'
    rest = m.group(2)
    if direction == 'emis vers':
        m = sepa_emis_pattern.match(t.metadata['raw_description'])
        account = m.group(1)
        rest = account + rest[rest.find(' '):]
    return f'Virement SEPA {direction} {rest}'

def is_card_transaction(t):
    return t.type.startswith('CARTE ')

value_date_pattern = re.compile(r'(.*?) (\d{2}\/\d{2}\/\d{4}) (.*)')

def is_card_transaction_with_date(t):
    return (is_card_transaction(t)
            and bool(value_date_pattern.match(t.description)))

def move_date(t):
    m = value_date_pattern.match(t.description)
    prefix = m.group(1)
    if prefix == 'Carte' or prefix == 'Paiement Par Carte':
        prefix = 'Paiement par carte'
    description = prefix + ': ' + m.group(3)
    value_date = parse_date(m.group(2))
    return description, value_date

def parse_date(d: str) -> date:
    """ parse a date in "dd/mm/yyyy" format """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    return date(year, month, day)

foreign_card_pattern = re.compile(r'Paiement par carte: (\d+\.?\d*) ([A-Za-z]{3})'
                                  r' Cours (\d+\.\d+) (.*)')

def is_foreign_card_transaction(t):
    return (is_card_transaction(t)
            and bool(foreign_card_pattern.match(t.description)))

def format_foreign_card_transaction(t):
    m = foreign_card_pattern.match(t.description)
    foreign_currency = m.group(2).upper()
    exchange_rate = Decimal(m.group(3))
    foreign_amount = Decimal(m.group(1).replace(',', '.'))
    if foreign_currency == t.currency.replace('€', 'EUR'):
        # Apparently buying in a foreign country in EUR still
        # formats the transaction as a foreign currency transaction
        # on the ING.fr bank statement pdf.
        assert exchange_rate == 1
        assert foreign_amount == -t.amount
        description = f'Paiement par carte: {m.group(4)}'
        metadata = t.metadata
    else:
        description = (f'Paiement par carte: {m.group(1)} {foreign_currency}'
                       f' cours {m.group(3)} {m.group(4)}')
        metadata = dict(t.metadata,
                exchange_rate=exchange_rate,
                foreign_amount=Decimal(m.group(1).replace(',', '.')),
                foreign_currency=foreign_currency,
                )
    return (description, metadata)

checkings_rules = [
        Rule(lambda _: True, lambda t: t.description.title()),
        Rule(is_sepa_direct_debit, clean_sepa_direct_debit,
             field=('description', 'metadata')),
        Rule(is_sepa_giro_transfer, clean_sepa_giro_transfer),
        Rule(is_card_transaction_with_date, move_date,
             field=('description', 'external_value_date')),
        Rule(is_foreign_card_transaction, format_foreign_card_transaction,
             field=('description', 'metadata')),
        ]

ldd_rules = [
        Rule(lambda _: True, parse_transaction_type, field=('metadata')),
        Rule(lambda _: True, lambda t: t.description.title()),
        Rule(is_sepa_giro_transfer, clean_sepa_giro_transfer),
        ]
