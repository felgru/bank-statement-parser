# SPDX-FileCopyrightText: 2019–2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import re

from transaction_sanitation import TransactionCleanerRule as Rule

transaction_id_pattern = re.compile(r' \(transaction id: .+?\)$')

def remove_transaction_id_from_description(t):
    m = transaction_id_pattern.search(t.description)
    return t.description[:m.start()]

rules = [
        Rule(lambda t: bool(transaction_id_pattern.search(t.description)),
             remove_transaction_id_from_description),
        ]
