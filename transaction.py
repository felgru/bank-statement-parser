from copy import copy
from collections import namedtuple

class Transaction:
    def __init__(self, type, description, operation_date, value_date, amount,
                 external_account=None, external_value_date=None):
        self.type = type
        self.description = description
        self.operation_date = operation_date
        self.value_date = value_date
        self.external_value_date = external_value_date
        self.amount = amount
        self.external_account = external_account

    def change_property(self, prop: str, f):
        if prop in ('amount', 'sub_total'):
            raise Error('Cannot change amount or sub_total of a transaction')
        res = copy(self)
        setattr(res, prop, f(self))
        return res

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
        return (f'Transaction(type={s.type!r}, description={s.description!r}, '
                f'operation_date={s.operation_date!r}, '
                f'value_date={s.value_date!r}, amount={s.amount!r}'
                f'{ext_account}{ext_date})')

Balance = namedtuple('Balance', 'balance date')
