# SPDX-FileCopyrightText: 2022–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from transaction_sanitation import TransactionCleanerRule as Rule


def add_name_to_description(t):
    return t.metadata['Naam'] + ' | ' + t.description


def parse_payment_provider(t):
    meta = t.metadata
    store = meta['store']
    payment_provider, star, store_ = store.partition('*')
    if not star:
        if store.startswith('CCV '):
            payment_provider = 'CCV'
            store_ = store.removeprefix('CCV ')
        else:
            return store, meta
    if payment_provider in {'CCV', 'Zettle_', 'PAY.nl'}:
        meta = dict(meta)
        store = store_
        meta['store'] = store
        meta['payment_provider'] = payment_provider
    return store, meta


rules = [
        Rule(lambda t: 'Naam' in t.metadata, add_name_to_description),
        Rule(lambda t: t.metadata['transaction_type'] == 'BEA',
             parse_payment_provider,
             ('description', 'metadata')),
        ]
