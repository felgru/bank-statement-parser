# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta
from decimal import Decimal
import re

from transaction_sanitation import TransactionCleanerRule as Rule

def is_interest_calculation(t):
    return t.type == 'Zinsabrechnung'

def clean_interest_calculation(t):
    description = t.metadata['raw_description'].split('\n')
    block_comment = '\n'.join(l for l in description[1:])
    metadata = dict(t.metadata)
    metadata['block_comment'] = block_comment
    description = f'{t.type} {description[0]}'
    return description, metadata

def is_time_deposit(t):
    return t.type.startswith('Zinsen Festz.') \
           or t.type.startswith('Ausz.Festzins')

def clean_time_deposit(t):
    description = t.type
    account_number = t.metadata['raw_description']
    assert '\n' not in account_number
    metadata = dict(t.metadata)
    metadata['account_number'] = account_number
    metadata['raw_description'] = description + '\n' + account_number
    return description, metadata

def is_giro_transfer(t):
    return t.type == 'Überweisung'

def clean_giro_transfer(t):
    lines = t.metadata['raw_description'].split('\n')
    assert len(lines) >= 2
    description = f'{lines[1]} | {" ".join(lines[2:])}'
    reference = lines[0]
    metadata = dict(t.metadata)
    metadata['block_comment'] = reference
    return description, metadata

rules = [
        Rule(is_interest_calculation, clean_interest_calculation, field=('description', 'metadata')),
        Rule(is_time_deposit, clean_time_deposit, field=('description', 'metadata')),
        Rule(is_giro_transfer, clean_giro_transfer, field=('description', 'metadata')),
        ]
