<!--
SPDX-FileCopyrightText: 2019–2021 Felix Gruber <felgru@posteo.net>

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Bank Statement Parser

> scripts to import bank statement PDFs into hledger files

Supported banks:

* BNP Paribas
* ING.de
* ING.fr
* Mercedes-Benz Bank
* VTB Direktbank

Not actual banks, but bank statement-like files:

* PayPal csv reports
* PayFit payslips
* Buygues Télécom payslips

## Usage

To parse a single bank statement PDF you can use the `parse-bank-statement.py`
script. For bulk imports you can use the `import-bank-statements.py` script
that tries to parse all bank statements found in
`~/accounting/bank_statements/incoming/<name_of_bank>`. For each bank statement
file it creates a corresponding hledger file in `~/accounting/bank_statements`.

To parse PDF files the bank statement parser uses `pdftotext`, which in Debian
is part of the `poppler-utils` package.

## Configuration of automatic account mappings

The transactions on bank statements parsed by the scripts
`parse-bank-statment.py` and `import-bank-statement.py` can be automatically
cleaned up (e.g. to prettify the subject line) and assigned to the right
accounts.

To this end, the scripts optionally read two Python files from the directory
`$XDG_CONFIG_HOME/bank-statement-parser/<name_of_bank>` (where
`$XDG_CONFIG_HOME` defaults to `$HOME/.config` if unset).

### Transaction cleaning rules

The first Python file is `cleaning_rules.py`, which has to contain a variable
`rules` containing a list of `Rule`s that are applied one after one to each
transaction on the bank statement. `Rule`, here, is a class that is implicitly
imported into `cleaning_rules.py`. You can create a `Rule` as
`Rule(predicate, clean)` where `predicate` is a function taking a
`Transaction` as its argument an returning a `bool` that is `True` if the
`clean` function should be applied to the transaction. `clean` on the other
hand also takes a `Transaction` as its argument, but is expected to return a
modified transaction description. For more complicated cleaning rules, `Rule`
accepts an optional argument `field` with which you can specify that the
`clean` function returns another field than the `Transaction`'s description.

See `parsers/banks/cleaning_rules/ing_fr.py` for some examples of already
built-in cleaning rules.

### Account mapping rules

The second Python file to transform a bank statement's `Transaction`s is the
file `account_mappings.py`. As the name suggests, its purpose is to assign
accounts to the `Transaction`s. To this end, it contains a variable `rules`
that contains a list of functions that take a `Transaction` as argument and
return a `str` specifying the account to apply to the transaction or `None`
if the rule doesn't know which account to assign. The rules are then applied
in the given order to each `Transaction` until the first non-`None` result
is encountered which is then assigned as the external account of the
`Transaction`.

As a very simplistic example, you could have the following
`account_mappings.py` with a single rule that applies the account
`income:salary` to transactions with the word `salary` in their
description:

```python
def salary(t):
    if 'salary' in t.description:
        return 'income:salary'

rules = [salary]
```
Here, we've used the fact that Python functions implicitly return `None`
when reaching their end without encountering a `return` statement. Normally
this should be considered a bad coding style, as it might not be clear if
`None` is really the expected return value or if we simply forgot a `return`
statement at the end of the function. In the specific case of our account
mapping rules, we do however always expect a `return None` if the rule does
not match. The normal consideration, that a missing return statement at the
end of the function might be a code smell is thus not really justified here.
Therefore, the implicit return of `None` could be used to keep the mapping
rules short.

### Tips for writing your own cleaning and account mapping rules

To make use of all properties of a `Transaction` object in your cleaning or
mapping rules, you can take a look at its definition in `transaction.py`.
The most important properties are

* `description`: The subject line of the transaction
* `account`: The bank account to which the bank statement belongs
* `external_account`: the other side of the transaction; this is what is
  filled by your account mapping rules
* `amount`
* `currency`
* `operation_date`: date of the transaction
* `value_date`: date when the transaction changes the balance of your account
* `external_value_date`: value date of the external account

Additionally, each `Transaction` contains a `dict` named `metadata` which
can be filled by your cleaning rules with arbitrary metadata that can then
be used in your account mappings.

During development or debugging of cleaning and mapping rules it might be
useful to see the internal representation of `Transaction`s. To this end,
`parse-bank-statement.py` has the option `--raw` which prints the internal
representation of the `Transaction` objects instead of formatting them for
hledger.

## License

These programs are licensed under the GPL version 3 or (at your option)
any later version.

The text of the GPL version 3 can be found in the LICENSES directory.
