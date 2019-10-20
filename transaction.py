from copy import copy
from collections import namedtuple

class Transaction:
    def __init__(self, type, description, operation_date, value_date, amount,
                 sub_total):
        self.type = type
        self.description = description
        self.operation_date = operation_date
        self.value_date = value_date
        self.amount = amount
        self.debit, self.credit = sub_total

    def change_property(self, prop: str, f):
        if prop in ('amount', 'sub_total'):
            raise Error('Cannot change amount or sub_total of a transaction')
        res = copy(self)
        setattr(res, prop, f(self))
        return res

    def __repr__(self):
        s = self
        return (f'Transaction(type={s.type!r}, description={s.description!r}, '
                f'operation_date={s.operation_date!r}, '
                f'value_date={s.value_date!r}, amount={s.amount!r}, '
                f'sub_total=({s.debit!r}, {s.credit!r})')

Balance = namedtuple('Balance', 'balance date')
