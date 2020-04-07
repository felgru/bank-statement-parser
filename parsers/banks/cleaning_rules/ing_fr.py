# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

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

checkings_rules = [
        Rule(lambda _: True, lambda t: t.description.title()),
        Rule(is_sepa_direct_debit, clean_sepa_direct_debit),
        Rule(is_sepa_giro_transfer, clean_sepa_giro_transfer),
        ]

ldd_rules = [
        Rule(lambda _: True, parse_transaction_type, field=('metadata')),
        ]
