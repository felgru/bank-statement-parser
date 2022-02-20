# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, Optional, Union

from transaction import BaseTransaction, MultiTransaction, Transaction

class TransactionCleaner:
    def __init__(self,
                 rules_file: Optional[Path],
                 builtin_rules: Optional[Sequence[AnyCleanerRule]] = None):
        conf_file: Optional[Path] = None
        if rules_file is not None and rules_file.exists():
            conf_file = rules_file
        self.conf_file = conf_file
        self._read_rules()
        if builtin_rules is not None:
            self.rules[0:0] = builtin_rules

    def _read_rules(self) -> None:
        if self.conf_file is None:
            self.rules: list[AnyCleanerRule] = []
        else:
            with open(self.conf_file, 'r') as f:
                content = f.read()
                parse_globals: dict[str, Any] = {
                    'Rule': TransactionCleanerRule,
                    'ToMultiRule': ToMultiTransactionRule,
                    'Transaction': Transaction,
                    **globals(),
                    }
                exec(compile(content, self.conf_file, 'exec'), parse_globals)
                if 'rules' not in parse_globals:
                    raise RuntimeError(
                            f'{self.conf_file} didn\'t contain any rules.')
                self.rules = parse_globals['rules']

    def clean(self, transaction: BaseTransaction) -> BaseTransaction:
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

    def applies_to(self, transaction: BaseTransaction) -> bool:
        return (isinstance(transaction, Transaction)
                and self.condition(transaction))

    def clean(self, t: BaseTransaction) -> MultiTransaction:
        assert isinstance(t, Transaction)
        return self.cleaner(t)

AnyCleanerRule = Union[TransactionCleanerRule, ToMultiTransactionRule]
