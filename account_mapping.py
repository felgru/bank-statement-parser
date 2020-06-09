# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from typing import Iterator, List, Tuple

from transaction import MultiTransaction, Transaction

class AccountMapper:
    def __init__(self, xdg_dirs):
        conf_file = xdg_dirs['config'] + '/account_mappings.py'
        if not os.path.exists(conf_file):
            conf_file = None
        self.conf_file = conf_file
        self._read_rules()

    def _read_rules(self):
        if self.conf_file is None:
            self.rules = []
        else:
            with open(self.conf_file, 'r') as f:
                f = f.read()
                parse_globals = {
                    'Transaction': Transaction,
                    }
                exec(f, parse_globals)
                if 'rules' not in parse_globals:
                    raise Error(f'{self.conf_file} didn\'t contain any rules.')
                self.rules = parse_globals['rules']

    def map_transactions(self, transactions: List[Transaction]) -> None:
        for t in transactions:
            if isinstance(t, MultiTransaction):
                self._map_multitransaction(t)
            else:
                self._map_transaction(t)

    def _map_multitransaction(self, mt: MultiTransaction) -> None:
        for i, t in extract_unmapped_transactions(mt):
            self._map_transaction(t)
            mt.postings[i].account = t.external_account

    def _map_transaction(self, t: Transaction) -> None:
        for r in self.rules:
            account = r(t)
            if account is not None:
                t.external_account = account
                return

def extract_unmapped_transactions(mt: MultiTransaction) \
                                    -> Iterator[Tuple[int, Transaction]]:
    transactions = []
    for i, posting in enumerate(mt.postings):
        if posting.account is None:
            yield (i, Transaction(account=None,
                                  description=posting.comment or mt.description,
                                  operation_date=mt.date,
                                  value_date=None,
                                  amount=-posting.amount,
                                  currency=posting.currency,
                                  external_value_date=posting.date,
                                  metadata=mt.metadata))
    return transactions
