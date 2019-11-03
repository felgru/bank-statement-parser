# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

from transaction import Transaction

class TransactionCleaner:
    def __init__(self, xdg_dirs):
        conf_file = xdg_dirs['config'] + '/cleaning_rules.py'
        if not os.path.exists(conf_file):
            conf_file = None
        self.conf_file = conf_file
        self._read_rules()

    def _read_rules(self):
        if self.conf_file is None:
            self.rules = []
        else:
            with open(self.conf_file, 'r') as f:
                f = f.read()
                parse_globals = {
                    'Rule': TransactionCleanerRule,
                    'Transaction': Transaction,
                    **globals(),
                    }
                exec(f, parse_globals)
                if 'rules' not in parse_globals:
                    raise Error(f'{self.conf_file} didn\'t contain any rules.')
                self.rules = parse_globals['rules']

    def clean(self, transaction: Transaction) -> Transaction:
        for r in self.rules:
            if r.applies_to(transaction):
                transaction = r.clean(transaction)
        return transaction

class TransactionCleanerRule:
    def __init__(self, condition, cleaner, field='description'):
        self.condition = condition
        self.cleaner = cleaner
        self.field = field

    def applies_to(self, transaction: Transaction) -> bool:
        return self.condition(transaction)

    def clean(self, t: Transaction) -> Transaction:
        return t.change_property(self.field, self.cleaner)
