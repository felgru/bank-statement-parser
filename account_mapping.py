# SPDX-FileCopyrightText: 2019–2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from transaction import AnyTransaction, MultiTransaction, Transaction

class AccountMapper:
    def __init__(self, xdg_dirs: dict[str, Path]):
        conf_file: Optional[Path]
        conf_file = xdg_dirs['config'] / 'account_mappings.py'
        if not conf_file.exists():
            conf_file = None
        self.conf_file = conf_file
        self._read_rules()

    def _read_rules(self) -> None:
        if self.conf_file is None:
            self.rules: list[Callable[[AnyTransaction], str]] = []
        else:
            with open(self.conf_file, 'r') as f:
                content = f.read()
                parse_globals: dict[str, Any] = {
                    'Transaction': Transaction,
                    }
                exec(compile(content, self.conf_file, 'exec'), parse_globals)
                if 'rules' not in parse_globals:
                    raise RuntimeError(
                            f'{self.conf_file} didn\'t contain any rules.')
                self.rules = parse_globals['rules']

    def map_transactions(self, transactions: list[AnyTransaction]) -> None:
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
                                    -> Iterator[tuple[int, Transaction]]:
    for i, posting in enumerate(mt.postings):
        if posting.account is None:
            yield (i, Transaction(account='split from MultiTransaction',
                                  description=posting.comment or mt.description,
                                  operation_date=mt.date,
                                  value_date=None,
                                  amount=-posting.amount,
                                  currency=posting.currency,
                                  external_value_date=posting.date,
                                  metadata=mt.metadata))
