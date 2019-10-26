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

    def change_property(self, prop, f):
        res = copy(self)
        if isinstance(prop, str):
            if prop in ('amount', 'sub_total'):
                raise Error('Cannot change amount or sub_total of a transaction')
            setattr(res, prop, f(self))
        else:
            new_vals = f(self)
            for p, v in zip(prop, new_vals):
                setattr(res, p, v)
        return res

    def format_as_ledger_transaction(self, account):
        t = self
        result = f'{t.operation_date} {t.description}\n'
        if t.value_date is not None and t.value_date != t.operation_date:
            value_date = f' ; date:{t.value_date}'
        else:
            value_date = ''
        result += f'    {account}  {t.amount} €{value_date}\n'
        ext_acc = t.external_account or 'TODO::assign_account'
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
        return (f'Transaction(type={s.type!r}, description={s.description!r}, '
                f'operation_date={s.operation_date!r}, '
                f'value_date={s.value_date!r}, amount={s.amount!r}'
                f'{ext_account}{ext_date})')

Balance = namedtuple('Balance', 'balance date')
