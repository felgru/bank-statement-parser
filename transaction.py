# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from copy import copy
from collections import namedtuple

class Transaction:
    def __init__(self, account, description,
                 operation_date, value_date, amount, currency='€',
                 external_account=None, external_value_date=None,
                 metadata=None):
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

    def change_property(self, prop, f):
        res = copy(self)
        const_properties = ('amount', 'currency', 'sub_total')
        if isinstance(prop, str):
            if prop in const_properties:
                raise Error(f'Cannot change {prop} of a transaction')
            setattr(res, prop, f(self))
        else:
            new_vals = f(self)
            for p, v in zip(prop, new_vals):
                if prop in const_properties:
                    raise Error(f'Cannot change {prop} of a transaction')
                setattr(res, p, v)
        return res

    @property
    def type(self):
        try:
            return self.metadata['type']
        except KeyError:
            raise AttributeError("'{}' object has no attribute 'type'"
                                 .format(self.__class__.__name__))

    def to_multi_transaction(self):
        mt = MultiTransaction(description=self.description,
                              transaction_date=self.operation_date,
                              metadata=self.metadata)
        mt.add_posting(Posting(self.account, self.amount, self.currency,
                               self.value_date))
        mt.add_posting(Posting(self.external_account, -self.amount,
                               self.currency, self.external_value_date))
        return mt

    def format_as_ledger_transaction(self):
        t = self
        result = f'{t.operation_date} {t.description}\n'
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

    def __repr__(self):
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
                f'value_date={s.value_date!r}, amount={s.amount!r}'
                f'{ext_account}{ext_date}{meta})')

class MultiTransaction:
    def __init__(self, description, transaction_date, postings=None,
                 metadata=None):
        self.description = description
        self.date = transaction_date
        if postings is None:
            self.postings = []
        else:
            self.postings = postings
        if metadata is None:
            metadata = {}
        self.metadata = metadata

    def add_posting(self, posting):
        self.postings.append(posting)

    def change_property(self, prop, f):
        res = copy(self)
        if isinstance(prop, str):
            setattr(res, prop, f(self))
        else:
            new_vals = f(self)
            for p, v in zip(prop, new_vals):
                setattr(res, p, v)
        return res

    @property
    def type(self):
        try:
            return self.metadata['type']
        except KeyError:
            raise AttributeError("'{}' object has no attribute 'type'"
                                 .format(self.__class__.__name__))

    def format_as_ledger_transaction(self):
        t = self
        result = f'{t.date} {t.description}\n'
        result += ''.join(p.format_as_ledger_transaction(t.date)
                          for p in t.postings)
        return result

    def __repr__(self):
        s = self
        if s.metadata:
            meta = f', metadata={s.metadata!r}'
        else:
            meta = ''
        return (f'MultiTransaction({s.description}, {s.date},'
                f' {s.postings}{meta})')

class Posting:
    def __init__(self, account, amount, currency='€',
                 posting_date=None, comment=None):
        self.account = account
        self.amount = amount
        self.currency = currency
        self.date = posting_date
        self.comment = comment

    def format_as_ledger_transaction(self, transaction_date):
        t = self
        account = t.account or 'TODO:assign_account'
        if t.amount is None:
            amount = ''
        else:
            amount = f'{t.amount} {t.currency}'
        comments = []
        if t.date is not None and t.date != transaction_date:
            comments.append(f'date:{t.date}')
        if t.comment is not None:
            comments.append(t.comment)
        if comments:
            comments = ' ; ' + ', '.join(comments)
        else:
            comments = ''
        return f'    {account}  {amount}{comments}\n'

    def __repr__(self):
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

Balance = namedtuple('Balance', 'balance date')
