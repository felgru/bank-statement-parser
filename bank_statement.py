from transaction import Balance, Transaction

class BankStatement:
    def __init__(self, transactions, old_balance, new_balance):
        self.transactions = transactions
        self.old_balance = old_balance
        self.new_balance = new_balance

    def write_ledger(self):
        print('; old balance on {}: €{}\n'.format(self.old_balance.date,
                                                  self.old_balance.balance))
        for transaction in self.transactions:
            self.write_ledger_transaction(transaction)
            print()
        print('; new balance on {}: €{}'.format(self.new_balance.date,
                                                self.new_balance.balance))

    def write_ledger_transaction(self, t):
        print(f'{t.operation_date} {t.description}')
        value_date = f' ; date:{t.value_date}' if t.value_date is not None \
                     else ''
        print(f'    assets::bank::ING.fr  €{t.amount}{value_date}')
        print('    TODO::assign_account')
