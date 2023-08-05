# SPDX-FileCopyrightText: 2019–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, Optional, Union

from transaction import BaseTransaction, MultiTransaction, Transaction

class TransactionCleaner:
    def __init__(self,
                 rules: Sequence[AnyCleanerRule]):
        self.rules = list(rules)

    @classmethod
    def from_rules_file(cls, rules_file: Optional[Path]):
        if rules_file is None or not rules_file.exists():
            return cls([])
        with open(rules_file, 'r') as f:
            content = f.read()
            parse_globals: dict[str, Any] = {
                'Rule': TransactionCleanerRule,
                'ToMultiRule': ToMultiTransactionRule,
                'Transaction': Transaction,
                **globals(),
                }
            exec(compile(content, rules_file, 'exec'), parse_globals)
            if 'rules' not in parse_globals:
                raise RuntimeError(
                        f'{rules_file} didn\'t contain any rules.')
            rules = parse_globals['rules']
            return cls(rules)

    def with_builtin_rules(self,
                           builtin_rules: Optional[Sequence[AnyCleanerRule]],
                           ) -> TransactionCleaner:
        rules = list(self.rules)
        if builtin_rules is not None:
            rules[0:0] = builtin_rules
        return TransactionCleaner(rules)

    def clean(self, transaction: BaseTransaction) -> BaseTransaction:
        for r in self.rules:
            try:
                applies = r.applies_to(transaction)
            except Exception as e:
                raise RuntimeError(
                        'Error while trying to check if cleaning rule'
                        f' is applicable to transaction {transaction}.') from e
            if applies:
                try:
                    transaction = r.clean(transaction)
                except Exception as e:
                    import inspect
                    assert e.__traceback__ is not None
                    ef = inspect.getinnerframes(e.__traceback__)[-1]
                    raise RuntimeError(
                        'Error while trying to clean transaction'
                        f' {transaction} '
                        f'in {ef.filename}, line {ef.lineno}:\n'
                        f'{type(e).__name__}: {e}') from e
        return transaction

    def __repr__(self) -> str:
        return (f'<{self.__class__.__name__}(rules={self.rules!r})>')

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

    def __repr__(self) -> str:
        return (f'<{self.__class__.__name__}('
                f'{self.condition.__name__}, {self.cleaner.__name__}, '
                f'{self.field})>')

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

    def __repr__(self) -> str:
        return (f'<{self.__class__.__name__}('
                f'{self.condition.__name__}, {self.cleaner.__name__})>')

AnyCleanerRule = Union[TransactionCleanerRule, ToMultiTransactionRule]
