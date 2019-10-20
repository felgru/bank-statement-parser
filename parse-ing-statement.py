#!/usr/bin/python3

import argparse

from pdf_parser import PdfParser

aparser = argparse.ArgumentParser(
        description='parse an ING.fr account statement PDF')
aparser.add_argument('-o', metavar='OUTFILE', dest='outfile', default=None,
                     help='write to OUTFILE instead of stdout')
aparser.add_argument('pdf', action='store',
                     help='PDF file of the account statement')

args = aparser.parse_args()

assert args.pdf.endswith('.pdf')
transactions_parser = PdfParser(args.pdf)
bank_statement = transactions_parser.parse()
bank_statement.write_ledger()
