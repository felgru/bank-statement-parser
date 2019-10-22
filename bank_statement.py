from transaction import Balance, Transaction

class BankStatement:
    def __init__(self, transactions, old_balance, new_balance):
        self.transactions = transactions
        self.old_balance = old_balance
        self.new_balance = new_balance

    def write_ledger(self, outfile):
        print('; old balance on {}: €{}\n'.format(self.old_balance.date,
                                                  self.old_balance.balance),
              file=outfile)
        for transaction in self.transactions:
            self.write_ledger_transaction(transaction, outfile)
        print('; new balance on {}: €{}'.format(self.new_balance.date,
                                                self.new_balance.balance),
              file=outfile)

    def write_ledger_transaction(self, t, outfile):
        print(f'{t.operation_date} {t.description}', file=outfile)
        value_date = f' ; date:{t.value_date}' if t.value_date is not None \
                     else ''
        print(f'    assets::bank::ING.fr  €{t.amount}{value_date}',
              file=outfile)
        ext_acc = t.external_account or 'TODO::assign_account'
        if t.external_value_date is None:
            ext_date = ''
        else:
            ext_date = f'  ; date:{t.external_value_date}'
        print(f'    {ext_acc}{ext_date}\n', file=outfile)

    def write_raw(self, outfile):
        print('old balance on {}: €{}\n'.format(self.old_balance.date,
                                                self.old_balance.balance),
              file=outfile)
        for transaction in self.transactions:
            print(transaction, file=outfile)
        print('new balance on {}: €{}'.format(self.new_balance.date,
                                              self.new_balance.balance),
              file=outfile)

class BankStatementMetadata:
    def __init__(self, account_owner, iban, bic, owner_number, card_number,
                 account_number, start_date, end_date):
        self.account_owner = account_owner
        self.iban = iban
        self.bic = bic
        self.owner_number = owner_number
        self.card_number = card_number
        self.account_number = account_number
        self.start_date = start_date
        self.end_date = end_date

    def write(self, outfile):
        print(f'account owner: {self.account_owner}', file=outfile)
        print(f'IBAN: {self.iban}', file=outfile)
        print(f'BIC: {self.bic}', file=outfile)
        print(f'owner number: {self.owner_number}', file=outfile)
        print(f'card number: {self.card_number}', file=outfile)
        print(f'account number: {self.account_number}', file=outfile)
        print(f'start date: {self.start_date}', file=outfile)
        print(f'end date: {self.end_date}', file=outfile)
