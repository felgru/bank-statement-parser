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

rules = [
        Rule(is_interest_calculation, clean_interest_calculation, field=('description', 'metadata')),
        ]
