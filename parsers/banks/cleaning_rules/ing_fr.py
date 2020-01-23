# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from transaction_sanitation import TransactionCleanerRule as Rule

def parse_transaction_type(t):
    if t.description.startswith('INTERET'):
        return dict(type='INTERET')
    elif t.description.startswith('VIREMENT '):
        return dict(type='VIREMENT INTERNE')
    else:
        raise RuntimeError('Unable to guess transaction type of:', t.description)

ldd_rules = [
        Rule(lambda _: True, parse_transaction_type, field=('metadata')),
        ]
