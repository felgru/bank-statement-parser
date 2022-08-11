# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Iterable
from copy import copy
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, NamedTuple, Optional, TypeVar, Union

class BaseTransaction(metaclass=ABCMeta):
    description: str
    operation_date: date
    metadata: dict[str, Any]

    @abstractmethod
    def change_property(self,
                        prop: Union[str, Iterable[str]],
                        f: Callable[[BaseTransaction], Any],
                        ) -> BaseTransaction: pass

    @abstractmethod
    def format_as_ledger_transaction(self) -> str: pass

    @abstractmethod
    def __repr__(self) -> str: pass

    @property
    def type(self) -> str:
        try:
            return self.metadata['type']
        except KeyError:
            raise AttributeError("'{}' object has no attribute 'type'"
                                 .format(self.__class__.__name__))

class Transaction(BaseTransaction):
    def __init__(self, account: str, description: str,
                 operation_date: date, value_date: Optional[date],
                 amount: Decimal, currency: str = '€',
                 external_account: Optional[str] = None,
                 external_value_date: Optional[date] = None,
                 metadata: Optional[dict[str, Any]] = None):
        self.account = account
        self.description = description
        self.operation_date = operation_date
        self.value_date = value_date
        self.external_value_date = external_value_date
        self.amount = amount
        self.currency = currency
        self.external_account = external_account
        if metadata is None:
            metadata = {}
        self.metadata = metadata

    # TODO: Overload to handle different types of f
    def change_property(self,
                        prop: Union[str, Iterable[str]],
                        f: Callable[[BaseTransaction], Any],
                        ) -> Transaction:
        res = copy(self)
        const_properties = ('amount', 'currency', 'sub_total')
        if isinstance(prop, str):
            if prop in const_properties:
                raise RuntimeError(f'Cannot change {prop} of a transaction')
            setattr(res, prop, f(self))
        else:
            new_vals = f(self)
            for p, v in zip(prop, new_vals):
                if prop in const_properties:
                    raise RuntimeError(f'Cannot change {prop} of a transaction')
                setattr(res, p, v)
        return res

    def to_multi_transaction(self) -> MultiTransaction:
        mt = MultiTransaction(description=self.description,
                              transaction_date=self.operation_date,
                              metadata=self.metadata)
        mt.add_posting(Posting(self.account, self.amount, self.currency,
                               self.value_date))
        mt.add_posting(Posting(self.external_account, -self.amount,
                               self.currency, self.external_value_date))
        return mt

    def format_as_ledger_transaction(self) -> str:
        t = self
        if '\n' in t.description:
            raise RuntimeError(
                    'Transaction description contains unallowed'
                    f' character "\\n": {t.description!r}')
        comment = t.metadata.get('comment', '')
        if comment:
            comment = ' ; ' + comment
        result = f'{t.operation_date} {t.description}{comment}\n'
        block_comment = t.metadata.get('block_comment')
        if block_comment is not None:
            block_comment = '\n    ; '.join(block_comment.split('\n'))
            result += '    ; ' + block_comment + '\n'
        if t.value_date is not None and t.value_date != t.operation_date:
            value_date = f' ; date:{t.value_date}'
        else:
            value_date = ''
        result += f'    {t.account}  {t.amount} {t.currency}{value_date}\n'
        ext_acc = t.external_account or 'TODO:assign_account'
        if t.external_value_date is None:
            ext_date = ''
        else:
            ext_date = f'  ; date:{t.external_value_date}'
        result += f'    {ext_acc}{ext_date}\n'
        return result

    def __repr__(self) -> str:
        s = self
        if s.external_account is not None:
            ext_account = f', external_account={s.external_account!r}'
        else:
            ext_account = ''
        if s.external_value_date is not None:
            ext_date = f', external_value_date={s.external_value_date!r}'
        else:
            ext_date = ''
        if s.metadata:
            meta = f', metadata={s.metadata!r}'
        else:
            meta = ''
        return (f'Transaction(account={s.account!r}, '
                f'description={s.description!r}, '
                f'operation_date={s.operation_date!r}, '
                f'value_date={s.value_date!r}, amount={s.amount!r}, '
                f'currency={s.currency!r}'
                f'{ext_account}{ext_date}{meta})')

class MultiTransaction(BaseTransaction):
    def __init__(self, description: str, transaction_date: date,
                 postings: Optional[list[Posting]] = None,
                 metadata: Optional[dict[str, Any]] = None):
        self.description = description
        self.date = transaction_date
        if postings is None:
            self.postings = []
        else:
            self.postings = postings
        if metadata is None:
            metadata = {}
        self.metadata = metadata

    def add_posting(self, posting: Posting) -> None:
        self.postings.append(posting)

    # TODO: Overload to handle different types of f
    def change_property(self,
                        prop: Union[str, Iterable[str]],
                        f: Callable[[MultiTransaction], Any],
                        ) -> MultiTransaction:
        res = copy(self)
        if isinstance(prop, str):
            setattr(res, prop, f(self))
        else:
            new_vals = f(self)
            for p, v in zip(prop, new_vals):
                setattr(res, p, v)
        return res

    def format_as_ledger_transaction(self) -> str:
        t = self
        assert('\n' not in t.description)
        comment = t.metadata.get('comment', '')
        if comment:
            comment = ' ; ' + comment
        result = f'{t.date} {t.description}{comment}\n'
        block_comment = t.metadata.get('block_comment')
        if block_comment is not None:
            block_comment = '\n    ; '.join(block_comment.split('\n'))
            result += '    ; ' + block_comment + '\n'
        result += ''.join(p.format_as_ledger_transaction(t.date)
                          for p in t.postings)
        return result

    def is_balanced(self) -> bool:
        without_amount = 0
        amounts: defaultdict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for p in self.postings:
            if p.amount is None:
                without_amount += 1
                continue
            amounts[p.currency] += p.amount
        unbalanced_currencies = sum(1 for a in amounts.values() if a != 0)
        return (unbalanced_currencies == 0 and without_amount == 0) \
               or (unbalanced_currencies <= 1 and without_amount == 1)

    def __repr__(self) -> str:
        s = self
        if s.metadata:
            meta = f', metadata={s.metadata!r}'
        else:
            meta = ''
        return (f'MultiTransaction({s.description}, {s.date},'
                f' {s.postings}{meta})')

class Posting:
    def __init__(self, account: Optional[str], amount: Decimal,
                 currency: str = '€', posting_date: Optional[date] = None,
                 comment: Optional[str] = None, *,
                 conversion_price: Optional[tuple[Decimal, str]] = None):
        self.account = account
        self.amount = amount
        self.currency = currency
        self.date = posting_date
        self.comment = comment
        self.conversion_price = conversion_price

    def format_as_ledger_transaction(self, transaction_date: date) -> str:
        t = self
        account = t.account or 'TODO:assign_account'
        if t.amount is None:
            amount = ''
        else:
            amount = f'{t.amount} {t.currency}'
            if t.conversion_price is not None:
                amount += f' @@ {t.conversion_price[0]}' \
                          f' {t.conversion_price[1]}'
        comments = []
        if t.date is not None and t.date != transaction_date:
            comments.append(f'date:{t.date}')
        if t.comment is not None:
            comments.append(t.comment)
        if comments:
            comments_str = ' ; ' + ', '.join(comments)
        else:
            comments_str = ''
        return f'    {account}  {amount}{comments_str}\n'

    def __repr__(self) -> str:
        s = self
        if s.date is not None:
            date = f', posting_date={s.date!r}'
        else:
            date = ''
        if s.comment is not None:
            comment = f', comment={s.comment!r}'
        else:
            comment = ''
        return (f'Posting({s.account!r}, {s.amount!r}, {s.currency!r}'
                f'{date}{comment})')

class Balance(NamedTuple):
    balance: Decimal
    date: date
