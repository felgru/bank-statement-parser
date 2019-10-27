import json

class BankStatement:
    def __init__(self, account, transactions,
                 old_balance=None, new_balance=None):
        self.account = account
        self.transactions = transactions
        self.old_balance = old_balance
        self.new_balance = new_balance

    def write_ledger(self, outfile):
        if self.old_balance is not None:
            date = self._format_date(self.old_balance.date)
            print('; old balance{}: {} €\n'.format(date,
                                                   self.old_balance.balance),
                  file=outfile)
        for t in self.transactions:
            print(t.format_as_ledger_transaction(self.account), file=outfile)
        if self.new_balance is not None:
            date = self._format_date(self.new_balance.date)
            print('; new balance{}: {} €'.format(date,
                                                 self.new_balance.balance),
                  file=outfile)

    def write_raw(self, outfile):
        if self.old_balance is not None:
            date = self._format_date(self.old_balance.date)
            print('old balance{}: {} €\n'.format(date,
                                                 self.old_balance.balance),
                  file=outfile)
        for transaction in self.transactions:
            print(transaction, file=outfile)
        if self.new_balance is not None:
            date = self._format_date(self.new_balance.date)
            print('new balance{}: {} €'.format(date,
                                               self.new_balance.balance),
                  file=outfile)

    @staticmethod
    def _format_date(d) -> str:
            if d is not None:
                d = f' on {d}'
            else:
                d = ''
            return d

class BankStatementMetadata:
    def __init__(self, start_date, end_date,
                 iban=None, bic=None,
                 account_owner=None, owner_number=None,
                 card_number=None, account_number=None,
                 **extra):
        self.account_owner = account_owner
        self.iban = iban
        self.bic = bic
        self.owner_number = owner_number
        self.card_number = card_number
        self.account_number = account_number
        self.start_date = start_date
        self.end_date = end_date
        self.extra = dict(extra)

    def __getattr__(self, key):
        return self.extra[key]

    def write(self, outfile):
        print(f'account owner: {self.account_owner}', file=outfile)
        print(f'IBAN: {self.iban}', file=outfile)
        print(f'BIC: {self.bic}', file=outfile)
        print(f'owner number: {self.owner_number}', file=outfile)
        print(f'card number: {self.card_number}', file=outfile)
        print(f'account number: {self.account_number}', file=outfile)
        print(f'start date: {self.start_date}', file=outfile)
        print(f'end date: {self.end_date}', file=outfile)
        for key, value in sorted(self.extra.items()):
            print(f'{key}: {value}', file=outfile)

    def write_json(self, outfile):
        data = {s: str(getattr(self, s)) for s in [
                'account_owner', 'iban', 'bic', 'owner_number', 'card_number',
                'account_number', 'start_date', 'end_date']}
        data.update(self.extra)
        print(json.dumps(data), file=outfile)
