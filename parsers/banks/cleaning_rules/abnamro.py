# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from transaction_sanitation import TransactionCleanerRule as Rule

def add_name_to_description(t):
    return t.metadata['Naam'] + ' | ' + t.description

rules = [
        Rule(lambda t: 'Naam' in t.metadata, add_name_to_description),
        ]
