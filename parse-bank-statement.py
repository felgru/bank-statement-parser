#!/usr/bin/python3

import argparse
import sys

from parsers.banks.ing_de import IngDePdfParser
from parsers.banks.ing_fr import IngFrPdfParser
from parsers.banks.bnp_paribas import BnpParibasPdfParser
from parsers.banks.payfit import PayfitPdfParser

banks = {'ing.de': IngDePdfParser,
         'ing.fr': IngFrPdfParser,
         'bnp': BnpParibasPdfParser,
         'payfit': PayfitPdfParser,
        }

aparser = argparse.ArgumentParser(
        description='parse an ING.fr account statement PDF')
aparser.add_argument('-o', metavar='OUTFILE', dest='outfile', default=None,
                     help='write to OUTFILE instead of stdout')
aparser.add_argument('--raw', dest='raw', default=False,
                     action='store_true',
                     help='write raw parsing results (useful when creating filters)')
aparser.add_argument('--meta', dest='meta', default=False,
                     action='store_true',
                     help='parse only metadata')
aparser.add_argument('--json', dest='json', default=False,
                     action='store_true',
                     help='when coupled with meta, write output as JSON dict')
aparser.add_argument('bank', action='store',
                     help='bank to parse from ({})'.format(', '.join(sorted(banks))))
aparser.add_argument('pdf', action='store',
                     help='PDF file of the account statement')

args = aparser.parse_args()
if args.outfile is None:
    args.outfile = sys.stdout
else:
    args.outfile = open(args.outfile, 'w')
Parser = banks[args.bank]

assert args.pdf.endswith('.pdf')
transactions_parser = Parser(args.pdf)
if args.meta:
    metadata = transactions_parser.parse_metadata()
    if args.json:
        metadata.write_json(args.outfile)
    else:
        metadata.write(args.outfile)
else:
    bank_statement = transactions_parser.parse()
    if not args.raw:
        bank_statement.write_ledger(args.outfile)
    else:
        bank_statement.write_raw(args.outfile)
