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

To parse a single bank statement PDF you can use the `parse-bank-statement.py`
script. For bulk imports you can use the `import-bank-statements.py` script
that tries to parse all bank statements found in
`~/accounting/bank_statements/incoming/<name_of_bank>`. For each bank statement
file it creates a corresponding hledger file in `~/accounting/bank_statements`.

To parse PDF files the bank statement parser uses `pdftotext`, which in Debian
is part of the `poppler-utils` package.

## License

These programs are licensed under the GPL version 3 or (at your option)
any later version.

The text of the GPL version 3 can be found in the LICENSES directory.
