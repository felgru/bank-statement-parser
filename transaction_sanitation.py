# SPDX-FileCopyrightText: 2019–2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path
from typing import Any, Callable, Optional, Union

from transaction import AnyTransaction, BaseTransaction, MultiTransaction, Transaction

class TransactionCleaner:
    def __init__(self, xdg_dirs: dict[str, Path], builtin_rules=None):
        conf_file: Optional[Path]
        conf_file = xdg_dirs['config'] / 'account_mappings.py'
        if not conf_file.exists():
            conf_file = None
        self.conf_file = conf_file
        self._read_rules()
        if builtin_rules is not None:
            self.rules[0:0] = builtin_rules

    def _read_rules(self):
        if self.conf_file is None:
            self.rules = []
        else:
            with open(self.conf_file, 'r') as f:
                f = f.read()
                parse_globals = {
                    'Rule': TransactionCleanerRule,
                    'ToMultiRule': ToMultiTransactionRule,
                    'Transaction': Transaction,
                    **globals(),
                    }
                exec(compile(f, self.conf_file, 'exec'), parse_globals)
                if 'rules' not in parse_globals:
                    raise Error(f'{self.conf_file} didn\'t contain any rules.')
                self.rules = parse_globals['rules']

    def clean(self, transaction: AnyTransaction) -> AnyTransaction:
        for r in self.rules:
            if r.applies_to(transaction):
                transaction = r.clean(transaction)
        return transaction

class TransactionCleanerRule:
    def __init__(self, condition: Callable[[BaseTransaction], bool],
                 cleaner: Callable[[BaseTransaction], Any],
                 field: Union[str, tuple[str, ...]] = 'description'):
        self.condition: Callable[[BaseTransaction], bool] = condition
        self.cleaner: Callable[[BaseTransaction], Any] = cleaner
        self.field = field

    def applies_to(self, transaction: BaseTransaction) -> bool:
        return self.condition(transaction)

    def clean(self, t: BaseTransaction) -> BaseTransaction:
        return t.change_property(self.field, self.cleaner)

class ToMultiTransactionRule:
    def __init__(self, condition: Callable[[Transaction], bool],
                 cleaner: Callable[[Transaction], MultiTransaction]):
        self.condition = condition
        self.cleaner = cleaner

    def applies_to(self, transaction: AnyTransaction) -> bool:
        return (isinstance(transaction, Transaction)
                and self.condition(transaction))

    def clean(self, t: Transaction) -> MultiTransaction:
        return self.cleaner(t)
