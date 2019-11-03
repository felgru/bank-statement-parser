# SPDX-FileCopyrightText: 2019 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from typing import List

from transaction import Transaction

class AccountMapper:
    def __init__(self, xdg_dirs):
        conf_file = xdg_dirs['config'] + '/account_mappings.py'
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
                    'Transaction': Transaction,
                    }
                exec(f, parse_globals)
                if 'rules' not in parse_globals:
                    raise Error(f'{self.conf_file} didn\'t contain any rules.')
                self.rules = parse_globals['rules']

    def map_transactions(self, transactions: List[Transaction]):
        for t in transactions:
            for r in self.rules:
                account = r(t)
                if account is not None:
                    t.external_account = account
                    break
