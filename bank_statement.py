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
        print(f'{t.operation_date} {t.description}')
        value_date = f' ; date:{t.value_date}' if t.value_date is not None \
                     else ''
        print(f'    assets::bank::ING.fr  €{t.amount}{value_date}',
              file=outfile)
        print('    TODO::assign_account\n',
              file=outfile)

    def write_raw(self, outfile):
        print('old balance on {}: €{}\n'.format(self.old_balance.date,
                                                self.old_balance.balance),
              file=outfile)
        for transaction in self.transactions:
            print(transaction, file=outfile)
        print('new balance on {}: €{}'.format(self.new_balance.date,
                                              self.new_balance.balance),
              file=outfile)
