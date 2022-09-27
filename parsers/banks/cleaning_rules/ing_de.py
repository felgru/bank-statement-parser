# SPDX-FileCopyrightText: 2019, 2021–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, timedelta
from decimal import Decimal
import re

from transaction_sanitation import TransactionCleanerRule as Rule

def is_card_transaction(t):
    return (t.type == 'Lastschrift' and
            re.search(r'\b(NR\d{10}|NR XXXX \d{4}|NR XXXX \d{4} [\d-]+)\b'
                      r'.*\bARN\d+\b',
                      t.description, flags=re.DOTALL))

def parse_card_metadata(t):
    m = re.search(r'(.*)\n(NR\d{10}|NR XXXX \d{4}|NR XXXX \d{4} [\d-]+)'
                  r'\s*(.*?)\s*(\bARN\d+\b)',
                  t.description, flags=re.DOTALL)
    description = m.group(1).replace('\n', ' ')
    metadata = dict(t.metadata)
    metadata.update(dict(
            NR_number = m.group(2),
            ARN_number = m.group(4),
            ))
    rest = m.group(3).replace('\n', ' ')
    m = re.search(r'(.*?) (\w+) (\d\d\.\d\d)(.*)$', rest)
    card_transaction_type = m.group(2)
    metadata['card_transaction_type'] = card_transaction_type
    purchase_date = parse_date_relative_to(m.group(3), t.operation_date)
    rest = m.group(1) + m.group(4)
    if card_transaction_type == 'BARGELDAUSZAHLUNG':
        m = re.search(r'(.*?) \d{6}$', rest)
        metadata.update(parse_location(m.group(1)))
    elif card_transaction_type == 'KAUFUMSATZ':
        m = re.search(r'^(.*) KURS (\d+,\d+) (\d+,\d\d) \d{6}$', rest)
        if m is not None: # Transaction in foreign currency
            metadata.update(parse_location(m.group(1)))
            metadata.update(dict(
                    exchange_rate = Decimal(m.group(2).replace(',', '.')),
                    foreign_amount = parse_amount(m.group(3)),
                    ))
        else:
            m = re.search(r'^(.*?)( \d{6}|)$', rest)
            metadata.update(parse_location(m.group(1)))
    elif card_transaction_type == 'WECHSELKURSGEBUEHR':
        assert rest.endswith('%')
        percentage = Decimal(rest[:-1].replace(',', '.'))
        metadata.update(dict(
                exchange_fee_rate = percentage / 100,
                ))
    else:
        raise RuntimeError('Unknown card transaction type:', card_transaction_type)
    return description, purchase_date, metadata

def parse_location(s: str) -> dict[str, str]:
    m = re.match(r'(.*?) ([A-Z]{2})', s)
    if m is not None:
        return dict(
                location=m.group(1),
                country=m.group(2),
               )
    else:
        return dict(location=s)

def parse_date_relative_to(d: str, ref_d: date) -> date:
    day = int(d[:2])
    month = int(d[3:5])
    year = ref_d.year
    dd = date(year, month, day)
    half_a_year = timedelta(days=356/2)
    diff = dd - ref_d
    if abs(diff) > half_a_year:
        if diff < timedelta(days=0):
            dd = date(year + 1, month, day)
        else:
            dd = date(year - 1, month, day)
    return dd

def parse_amount(a: str) -> Decimal:
    """ parse a decimal amount like -10,00 """
    a = a.replace(',', '.')
    return Decimal(a)

def is_direct_debit(t):
    return (t.type == 'Lastschrift' and
            re.search(r'^Mandat: .*\nReferenz: .*$', t.description,
                      flags=re.MULTILINE))

def parse_direct_debit_metadata(t):
    lines = t.description.split('\n')
    description = []
    metadata = dict(t.metadata)
    key_value_pattern = re.compile('(\w+): (.*)')
    for l in lines:
        m = key_value_pattern.fullmatch(l)
        if m is None:
            description.append(l)
        else:
            metadata[m.group(1)] = m.group(2)
    if len(description) > 1:
        description = description[0] + ' | ' + ' '.join(description[1:])
    else:
        description = description[0]
    return description, metadata

def is_giro_card_transaction(t):
    return (t.type == 'Abbuchung' and
            re.search(r'^Referenz: .*$', t.description,
                      flags=re.MULTILINE))

def parse_giro_card_metadata(t):
    lines = t.description.split('\n')
    description = []
    metadata = dict(t.metadata)
    key_value_pattern = re.compile('(\w+): (.*)')
    for l in lines:
        m = key_value_pattern.fullmatch(l)
        if m is None:
            description.append(l)
        else:
            metadata[m.group(1)] = m.group(2)
    if len(description) > 1:
        description = description[0] + ' | ' + ' '.join(description[1:])
    else:
        description = description[0]
    return description, metadata

def is_standing_order(t):
    return t.type == 'Dauerauftrag/Terminueberw.'

def clean_standing_order_description(t):
    lines = t.description.split('\n')
    metadata = dict(t.metadata)
    metadata['name'] = lines[0]
    description = lines[0] + ' | ' + ' '.join(lines[1:])
    return description, metadata

def is_fee(t):
    return t.type == 'Entgelt'

def is_card_exchange_fee(t):
    return (is_fee(t) and t.description.startswith('VISA ')
            and t.description.find('AUSLANDSEINSATZENTGELT') >= 0)

def parse_card_exchange_fee_metadata(t):
    lines = t.description.split('\n')
    metadata = {'fee_type': 'AUSLANDSEINSATZENTGELT', **t.metadata}
    m = re.match(r'VISA (\d{4} X{4} X{4} \d{3,4}) '
                 r'(\d,\d\d)%AUSLANDSEINSATZENTGELT',
                 lines[-2])
    metadata['card_number'] = m.group(1)
    metadata['exchange_fee_rate'] = Decimal(m.group(2).replace(',', '.'))
    m = re.match(r'\w+ \(.+?\) (ARN\d+)', lines[-1])
    metadata['ARN_number'] = m.group(1)
    description = ' '.join(lines[:-2])
    return description, metadata

def is_giro_transfer(t):
    return t.type == 'Gutschrift' or t.type == 'Ueberweisung'

def clean_giro_transfer_description(t):
    lines = t.description.split('\n')
    description = []
    metadata = dict(t.metadata)
    key_value_pattern = re.compile('(\w+): (.*)')
    for l in lines:
        m = key_value_pattern.fullmatch(l)
        if m is None:
            description.append(l)
        else:
            metadata[m.group(1)] = m.group(2)
    name = description[0]
    metadata['name'] = name
    if len(description) > 1:
        description = name + ' | ' + ' '.join(description[1:])
    else:
        description = name
    return description, metadata

rules = [
        Rule(is_card_transaction, parse_card_metadata, field=('description', 'external_value_date', 'metadata')),
        Rule(is_direct_debit, parse_direct_debit_metadata, field=('description', 'metadata')),
        Rule(is_giro_card_transaction, parse_giro_card_metadata, field=('description', 'metadata')),
        Rule(is_standing_order, clean_standing_order_description, field=('description', 'metadata')),
        Rule(is_card_exchange_fee, parse_card_exchange_fee_metadata, field=('description', 'metadata')),
        Rule(is_giro_transfer, clean_giro_transfer_description, field=('description', 'metadata')),
        ]

def is_interest(t):
    return t.type == 'Zinsertrag'

def create_interest_description(t):
    return t.type

extra_konto_rules = [
        Rule(is_giro_transfer, clean_giro_transfer_description, field=('description', 'metadata')),
        Rule(is_interest, create_interest_description),
        ]
