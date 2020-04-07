# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date
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

sepa_pattern = re.compile(r'Virement Sepa (\S+) (.*)')

def is_sepa_giro_transfer(t):
    return (t.type == 'VIREMENT EXTERNE'
            and sepa_pattern.match(t.description))

def clean_sepa_direct_debit(t):
    prefix = 'prlv SEPA '
    return prefix + t.description[len(prefix):]

def clean_sepa_giro_transfer(t):
    m = sepa_pattern.match(t.description)
    direction = m.group(1).lower()
    if direction == 'recu':
        direction = 'reçu'
    rest = m.group(2)
    if rest.startswith('Vers '):
        rest = 'v' + rest[1:]
    return f'Virement SEPA {direction} {rest}'

def is_card_transaction(t):
    return t.type.startswith('CARTE ')

value_date_pattern = re.compile(r'(.*?) (\d{2}\/\d{2}\/\d{4}) (.*)')

def is_card_transaction_with_date(t):
    return (is_card_transaction(t)
            and bool(value_date_pattern.match(t.description)))

def move_date(t):
    m = value_date_pattern.match(t.description)
    description = m.group(1) + ': ' + m.group(3)
    value_date = parse_date(m.group(2))
    return description, value_date

def parse_date(d: str) -> date:
    """ parse a date in "dd/mm/yyyy" format """
    day = int(d[:2])
    month = int(d[3:5])
    year = int(d[6:])
    return date(year, month, day)

checkings_rules = [
        Rule(lambda _: True, lambda t: t.description.title()),
        Rule(is_sepa_direct_debit, clean_sepa_direct_debit),
        Rule(is_sepa_giro_transfer, clean_sepa_giro_transfer),
        Rule(is_card_transaction_with_date, move_date, field=('description', 'external_value_date')),
        ]

ldd_rules = [
        Rule(lambda _: True, parse_transaction_type, field=('metadata')),
        ]
