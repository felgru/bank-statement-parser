# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from pathlib import Path
from pprint import pformat
import re
import sys
from typing import cast, Literal, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString, Tag
import requests

from bank_statement import BankStatement
from transaction import Transaction
from .downloader import (
    Downloader,
    GenericDownloaderConfig,
    PasswordAuthenticator,
)


class NederlandseSpoorwegenConfig(GenericDownloaderConfig):
    name = 'ns'
    display_name = 'Nederlandse Spoorwegen'
    DEFAULT_ACCOUNTS = {
        'balancing': 'assets:balancing:NS',  # Used for invoices.
        'assets': 'assets:OV-Chipkaart',  # Used for "Reizen op Saldo".
        'recharge': 'assets:balancing:OV-Chipkaart',
        'train ticket': 'expenses:transportation:train',
    }


class NederlandseSpoorwegenDownloader(Downloader[NederlandseSpoorwegenConfig]):
    def __init__(self, api: NederlandseSpoorwegenApi):
        self.api = api

    def download(self,
                 config: NederlandseSpoorwegenConfig,
                 **kwargs) -> BankStatement:
        try:
            start_date: date = kwargs.pop('start_date')
        except KeyError:
            raise RuntimeError(f'{self.__class__.__name__}.download is'
                               ' missing the start_date argument.')
        try:
            end_date: date = kwargs.pop('end_date')
        except KeyError:
            raise RuntimeError(f'{self.__class__.__name__}.download is'
                               ' missing the end_date argument.')
        if kwargs:
            raise RuntimeError(
                    f'Unknown keyword arguments: {", ".join(kwargs.keys())}')
        cards = self.api.get_ov_chipkaarts()
        assert len(cards) == 1  # TODO: Handle more than one OV-Chipkaart
        card_number = cards[0]['ovcpNumber']

        self.print_next_invoice()

        transactions = self.api.get_transactions_of_ovcp_in_date_range(
                card_number, start_date=start_date, end_date=end_date)

        return travel_history_to_bank_statement(
                transactions,
                config.accounts)

    def print_next_invoice(self) -> None:
        next_invoice = self.api.get_next_invoice_cost_overview()
        dt = date.fromisoformat(next_invoice['plannedInvoiceDate'])
        total = Decimal('0.00')
        items = []
        for invoice_item in next_invoice['costCategories']:
            amount = parse_amount(invoice_item['amount'])
            total += amount
            items.append(f'{amount:>6} €  {invoice_item["type"]}')
        print(f'Your next invoice will be for {total} € on {dt}:\n'
              + "\n".join(items)
              + f'\n{"-" * max(map(len, items))}\n{total:>6} €  TOTAL',
              file=sys.stderr)


def travel_history_to_bank_statement(transactions: dict,
                                     accounts: dict[str, str],
                                     ) -> BankStatement:
    transformed_transactions = []
    last_rhino: dict | None = None
    for transaction in transactions['transactions']:
        type_ = transaction['type']
        if type_ == 'JOURNEY':
            product_code = transaction['product']['code']
            product_name = transaction['product']['name']
            if transaction['source'] == 'RHINO':
                # For "Reizen op Rekening" this can be ignored as it is a
                # duplicate of the 'HB' transaction which has more information.
                # For "Reizen op Saldo" this is the only transaction.
                if product_code != '2000':
                    # product_name != 'Reizen op Saldo'
                    last_rhino = transaction
                    continue
                assert last_rhino is None
                amount = -parse_amount(transaction['amount'])
                journey = transaction['journey']
                departure = journey['departure']
                departure_time = parse_timestamp_as_local(
                        departure['timestamp'])
                departure_station = departure['station']['name']
                arrival = journey['arrival']
                arrival_time = parse_timestamp_as_local(arrival['timestamp'])
                arrival_station = arrival['station']['name']
                carrier = journey['carrier']['name']

                if carrier == 'NS':
                    account = accounts['train ticket']
                else:
                    raise ValueError(f'Unknown carrier {carrier}.')

                description = (
                        f'OV-Chipkaart | {product_name} {carrier} | '
                        f'{departure_station} ({departure_time:%H:%M})'
                        f' → {arrival_station} ({arrival_time:%H:%M})'
                )

                tariff = transaction['tariff']

                transformed_transactions.append(Transaction(
                    account=accounts['assets'],
                    description=description,
                    operation_date=departure_time.date(),
                    value_date=None,
                    amount=amount,
                    currency='€',
                    external_account=account,
                    metadata={
                        'id': transaction['id'],
                        'type': type_,
                        'product_name': product_name,
                        'product_code': product_code,
                        'carrier': carrier,
                        'departure_station': departure_station,
                        'depearture_time': departure_time,
                        'arrival_station': arrival_station,
                        'depearture_time': arrival_time,
                        # newCardBalance does not exist in all Reizen op Saldo
                        # transactions.
                        **({'new_card_balance':
                                parse_amount(transaction['newCardBalance'])}
                           if 'newCardBalance' in transaction else {}),
                        'initial_fee':
                            parse_amount(transaction['initialFee']),
                        'tariff': tariff['type'],
                    },
                    ))
                continue
            elif transaction['source'] != 'HB':
                raise ValueError(
                        'Unexpected JOURNEY source '
                        f'{transaction["source"]} in\n'
                        f'{pformat(transaction, sort_dicts=False)}')
            amount = -parse_amount(transaction['amount'])
            journey = transaction['journey']
            assert last_rhino is not None
            assert (last_rhino['journey']['departure']['eventSequenceId']
                    == journey['departure']['eventSequenceId'])
            assert (transaction['productTemplateCode'].lstrip('0')
                    == last_rhino['productCode'])
            departure = journey['departure']
            departure_time = parse_timestamp_as_local(departure['timestamp'])
            departure_station = departure['station']['name']
            arrival = journey['arrival']
            arrival_time = parse_timestamp_as_local(arrival['timestamp'])
            arrival_station = arrival['station']['name']
            carrier = journey['carrier']['name']
            tariff = last_rhino['tariff']

            if product_name == 'Treinreizen':
                account = accounts['train ticket']
            else:
                raise ValueError(f'Unknown product {product_name}.')

            description = (
                    f'OV-Chipkaart | {product_name} {carrier} | '
                    f'{departure_station} ({departure_time:%H:%M})'
                    f' → {arrival_station} ({arrival_time:%H:%M})'
            )

            transformed_transactions.append(Transaction(
                account=accounts['balancing'],
                description=description,
                operation_date=departure_time.date(),
                value_date=None,
                amount=amount,
                currency='€',
                external_account=account,
                metadata={
                    'id': transaction['id'],
                    'type': type_,
                    'product_name': product_name,
                    'product_code': transaction['product']['code'],
                    'carrier': carrier,
                    'departure_station': departure_station,
                    'depearture_time': departure_time,
                    'arrival_station': arrival_station,
                    'depearture_time': arrival_time,
                    'travel_class': journey['travelClass'],
                    'tariff': tariff['type'],
                },
                ))
            last_rhino = None
        elif type_ == 'CUSTOMER_SERVICE':
            timestamp = parse_timestamp_as_local(transaction['timestamp'])
            amount = -parse_amount(transaction['amount'])
            transformed_transactions.append(Transaction(
                account=accounts['balancing'],
                description='OV-Chipkaart | ' + transaction['description'],
                operation_date=timestamp.date(),
                value_date=None,
                amount=amount,
                currency='€',
                external_account=None,  # Better to let the user fill it in
                                        # manually.
                metadata={
                    'id': transaction['id'],
                    'type': type_,
                    'timestamp': timestamp,
                    'ovcp': transaction['ovcp'],
                },
                ))
        elif type_ == 'PRODUCT_SALE':
            # Loading subscription to OV-Chipkaart.
            # I can probably ignore that, since it is not associated to a
            # payment.
            continue
        elif type_ == 'BALANCE_TOPUP':
            timestamp = parse_timestamp_as_local(transaction['timestamp'])
            transformed_transactions.append(Transaction(
                account=accounts['assets'],
                description='OV-Chipkaart | recharge',
                operation_date=timestamp.date(),
                value_date=None,
                amount=parse_amount(transaction['amount']),
                currency='€',
                external_account=accounts['recharge'],
                metadata={
                    'id': transaction['id'],
                    'type': type_,
                    'timestamp': timestamp,
                    'new_card_balance':
                        parse_amount(transaction['newCardBalance']),
                    'automatic': transaction['automaticTopUp'],
                    'station': transaction['station']['name'],
                },
                ))
        else:
            raise ValueError(
                    f'Unknown transaction type {type_} in \n'
                    f'{pformat(transaction, sort_dicts=False)}')

    assert last_rhino is None
    return BankStatement(
            transactions=reversed(transformed_transactions),
            )


class NederlandseSpoorwegenAuthenticator(
        PasswordAuthenticator[NederlandseSpoorwegenDownloader]):
    def login(self) -> NederlandseSpoorwegenDownloader:
        api = NederlandseSpoorwegenApi.login(self.username, self.password)
        return NederlandseSpoorwegenDownloader(api)


class NederlandseSpoorwegenApi:
    OAUTH_BASE = 'https://loginapi.ns.nl/oauth'
    API_BASE = 'https://gateway.apiportal.ns.nl'
    CARDS_API = f'{API_BASE}/mijnns-card-coupling-api/cards'
    OMNI_TRANSACTION_API = f'{API_BASE}/omni-transaction-api'
    OMNI_OVCP_API = f'{API_BASE}/omni-ovcp-api/ovcp'

    def __init__(self, session: requests.Session, auth_headers: dict):
        """Initialize Nederlandse Spoorwegen website API.

        session is expected to contain session cookies cookies and
        auth_headers is expected to contain the bearer token.

        Use the `login` class method to create a `NederlandseSpoorwegenApi`
        for given login credentials.
        """
        self.session = session
        self.authorization_headers = auth_headers

    @classmethod
    def login(cls, username: str, password: str) -> 'NederlandseSpoorwegenApi':
        """Create an API object with username and password."""
        s = requests.Session()
        res = s.get(f'{cls.OAUTH_BASE}/authorize'
                    '?scope=read'
                    '&response_type=code'
                    '&client_id=mijnns-productie'
                    '&state=/dashboard'
                    '&redirect_uri=https://www.ns.nl/mijnns')
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        assert isinstance(form, Tag)
        res = s.post(
                cast(str, form['action']),
                data={i['name']: i['value']
                      for i in form.find_all('input')
                      if i.has_attr('name')},
                )
        res.raise_for_status()
        # Now we're at the actual login page with username and password fields.
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form', id='loginForm')
        assert isinstance(form, Tag)
        token = form.find(lambda tag: tag.name == 'input'
                                      and tag['name'] == 'csrfToken')
        assert isinstance(token, Tag)
        res = s.post(
                urljoin(res.url, cast(str, form['action'])),
                data={'csrfToken': token['value'],
                      'email': username,
                      'password': password,
                      },
                )
        res.raise_for_status()
        assert res.history[1].url == 'https://login.ns.nl/sessions/new'
        assert res.url.startswith('https://www.ns.nl/mijnns?')
        query_string = urlparse(res.url).query
        # assuming that the first part of the query string is 'code='.
        code, _, _ = query_string.removeprefix('code=').partition('&')
        api = cls.authorize(s, code)
        return api

    @classmethod
    def get_bearer_token(cls, session: requests.Session, code: str) -> str:
        """Obtain a bearer token during login.

        This token is required to use the API. To obtain it, you need the
        random code that is generated during login.
        """
        # When logging in with Firefox, get_nsr_json is contacted, but it
        # seems to have no purpose.
        # nsr = cls.get_nsr_json(session)
        res = session.post(
                f'{cls.OAUTH_BASE}/token',
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': 'https://www.ns.nl/mijnns',
                    'client_id': 'mijnns-productie',
                },
                auth=('mijnns-productie',
                      # This auth token seems to be hard-coded.
                      'EE8B6D07CBAA839A44781F935C7F7AC8AD73F20BA1F9DE57BBA0DAC5558AD41A'),
                )
        res.raise_for_status()
        return res.json()['access_token']

    @classmethod
    def authorize(cls, session: requests.Session, code: str,
                  ) -> 'NederlandseSpoorwegenApi':
        token = cls.get_bearer_token(session, code)
        headers = {
            "Authorization": f'Bearer {token}',
            "Ocp-Apim-Subscription-Key": "e7f5ede34e384a3f9a87e75cce58c5ed",
        }
        res = session.get(cls.API_BASE
                          + '/omni-authorization-api/token/authorize',
                          headers=headers)
        try:
            res.raise_for_status()
        except Exception as e:
            breakpoint()
        if res.text != '"OK"':
            raise RuntimeError(
                    f'API authorization failed with status {res.text}.')
        return cls(session, headers)

    @classmethod
    def get_nsr_json(cls, session: requests.Session) -> dict:
        """Get NSR JSON.

        This returns a dict with the following entries:
        * nsr: The nsr from get_customer_details as a str.
        * emailAddress
        * personId: A UUID.
        """
        res = session.get('https://www.ns.nl/gssp/rest/user/getNSRJson')
        res.raise_for_status()
        m = re.search(r"var nsr = '(.*?})';", res.text)
        assert m is not None
        return json.loads(m.group(1))

    def get_user_details(self) -> dict:
        """Get user details.

        This returns a dict with the following entries:
        * email
        * authenticationLevel: An int. 1 in my test.
        * name: same as nsr.
        * nsr: The nsr from get_customer_details as a str.
        * userId: A UUID; same as personId from get_nsr_json.
        * nsrValidationLevel: The str 'VALIDATED'.
        * authorities: A list of dicts with entry 'authority'.
        * attributes: In my test this was an empty dict.
        """
        res = self.session.get(self.API_BASE
                               + '/omni-authorization-api/token/user-details',
                               headers=self.authorization_headers)
        res.raise_for_status()
        # Result contains: email, authenticationLevel, name, nsr, userId,
        #                  nsrValidationLevel, authorities, attributes.
        return res.json()

    def get_customer_details(self) -> dict:
        """Get customer details.

        This returns a dict with the following entries:
        * voornaam
        * achternaam
        * nsr: This seems to be an id for the customer. (int)
        * initialen
        * geslacht
        * geboortedatum: Birthdate in ISO 8601 format.
        * postcode
        * huisnummer: Careful, this was an int in my test, but I guess
                      that this can be a str if you have a suffix like '42 A'.
        * straat
        * plaats
        * landcode
        * email
        * telefoonnummers: A list of dicts with entries
           * nummer: The phone number as a str.
           * type: 'PRV' for "private", I guess. There are probably other
                   possible types.
        """
        res = self.session.get(self.API_BASE
                               + '/omni-customer-api/klant/V1/',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_ov_chipkaarts(self) -> dict:
        """Get OV-Chipkaarts.

        This returns a list of dicts with the following entries:
        * type: The str 'ovcp'.
        * couplingId: Some hexadecimal string.
        * coupledAt: Date and time in ISO 8601 format; time is all 0 UTC.
        * expirationDate: Date and time in ISO 8601 format; time is all 0 UTC.
        * ovcpNumber: A str containing the number printed on the card.
        """
        res = self.session.get(self.CARDS_API + '?type=ovcp',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_ovcp_cards(self) -> dict:
        """Get OV-Chipkaarts (including products).

        This is mostly more verbose than get_ov_chipkaarts() as the result
        contains information about subscription products loaded onto the card.
        On the other hand it does not contain the couplingId and coupledAt
        date.

        This returns a list of dicts with the following entries:
        * ovcpNumber: A str containing the number printed on the card.
        * expirationDate: Date in ISO 8601 format.
        * cardHolderName
        products: A list of dicts containing the following entries:
            * type: Type of subscription, e.g. 'FLEX'.
            * description: A long name for the product, meant to display
                           to the user.
            * orderId
            * startDate: Date in ISO 8601 format.
            * endDate: Date in ISO 8601 format or None.
            * iban
            * isContractHolder: A bool.
            * route: None in my test.
            * storage: None in my test.
            * paymentFrequency: 'MONTHLY'
            * price: A dict containing the following entries:
               * amount: An int containing the amount in Euro cents.
               * period: 'MONTH'
            * travelClass: None in my test.
            * contractDuration: 'CONTINUOUS'
            * buildingBlocks: A list of str representing the conditions of
               the product, e.g.
               * 'NSFLEX'
               * 'WKNDKORT'
               * 'DALKORT'
               * 'SPITSVOL'
        * addOns: An empty list in my test.
        """
        res = self.session.get(self.API_BASE + '/mijnns-product-api/ovcp-cards',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_subscriptions_on_ov_chipkaart(self, card: str) -> dict:
        """Get subscriptions on OV-Chipkaart with given number.

        Arguments:
        * card: The card number of an OV-Chipkaart linked to your NS account.

        TODO: It seems that the API could accept multiple card numbers at once.

        This returns a list of dicts with the following entries:
        * ovcpNumber: A str containing the OV-Chipkaart card number.
        * subscriptionType: 'FLEX'
        """
        res = self.session.post(
                self.API_BASE + '/omni-ovcp-api/ovcp/subscription/type',
                json=[card],
                headers=self.authorization_headers,
                )
        res.raise_for_status()
        return res.json()

    def get_last_transactions_of_ovcp(self, card: str, *,
                                      offset: int = 0,
                                      limit: int = 15,
                                      source: str | None = None,
                                      ) -> dict:
        """Get last transactions of OV-Chipkaart with given number.

        See structure of returned data in documentation of
        `get_transactions_of_ovcp`.
        """
        return self.get_transactions_of_ovcp(
                card,
                offset=offset,
                limit=limit,
                source=source)

    def get_transactions_of_ovcp_in_date_range(self, card: str,
                                               start_date: date,
                                               end_date: date,
                                               *,
                                               source: str | None = None,
                                               ) -> dict:
        """Get last transactions of OV-Chipkaart with given number.

        It seems that you can go back at most 1.5 years with the start_date.

        See structure of returned data in documentation of
        `get_transactions_of_ovcp`.
        """
        return self.get_transactions_of_ovcp(
                card,
                start_date=start_date,
                end_date=end_date,
                source=source)

    def get_transactions_of_ovcp(self, card: str, *,
                                 offset: int | None = None,
                                 limit: int | None = None,
                                 start_date: date | None = None,
                                 end_date: date | None = None,
                                 source: str | None = None,
                                 ) -> dict:
        """Get transaction of OV-Chipkaart with given number.

        This returns a list of dicts with the following entries:
        * transactions: A list of dicts with the following entries:
          * id
          * type: A str, e.g. 'JOURNEY'.
          * timestamp: An ISO 8601 formatted date and time.
          * source: A str, e.g. 'RHINO', or 'HB'.
          * invoiced: bool
          * typeCode[HB]: A str.
        * failedSources: An empty list in my test.

        Type 'JOURNEY' contains the following additional entries:
          * description[HB]: A user-readable str.
          * extendedDescription[HB]: A user-readable str.
          * amount: Amount in Euro cents as int.
          * amountVat[OPTIONAL]: Amount VAT in Euro cents as int.
          * newCardBalance[RHINO;if travelling on saldo]: Amount in
              Euro cents as int.
          * product: A dict containing the following entries:
            * code: A str containing an alphanumerical code, same as productCode.
            * name: A user-readable description.
          * journey: A dict containing the following entries:
            * travelClass[OPTIONAL]: e.g. 'SECOND'
            * departure/arrival: Each of the two contains a dict with entries:
              * timestamp: An ISO 8601 formatted date and time.
              * station: A dict containing:
                * name: User-readable name of the station.
                * varCode: A str containing a numeric code.
                           Padded with zeroes when source is 'HB'.
              * eventSequenceId[OPTIONAL]: An int. Matches between
                'RHINO' and 'HB' journeys to identify check-in/out events.
            * carrier: A dict containing:
              * code: A str containing a numeric code, e.g. 4 for NS.
                      Padded with zeroes when source is 'HB'.
              * name: A user-readable name for the carrier.
          * tariff[RHINO]: A dict containing:
            * type
            * unitsAtStart: An int.
            * unitsAtEnd: An int.
          * initialFee[RHINO]: An int; seems to correspond to OV-Chipkaart
              "instaptarief" in Euro cents.
          * tripGroupCode[HB]: A str containing a numeric code.
          * productTemplateCode[HB]: A str containing a numeric code,
             padded with zeroes. This seems to be the productCode of the
             corresponding RHINO transaction (modulo zero padding).
          * classChange[HB]: A bool.
          * appliedTarifUnits: An int.
          * productCode: A str containing a numerical code.

        Type 'CUSTOMER_SERVICE' contains the following additional entries:
          * description: A user-readable str.
          * extendedDescription[HB]: A user-readable str.
          * amount: Amount in Euro cents as int.
          * ovcp: A str containing the OV-Chipkaart number.

        Type 'PRODUCT_SALE' contains the following additional entries:
          * product: A dict containing the following entries:
            * code: A str containing an alphanumerical code, same as productCode.
            * name: A user-readable description.
          * product: A dict containing:
            * code: A str containing an alphanumerical code, same as productCode.
            * name: A user-readable description.
          * location: A dict containing:
            * name: A user-readable str.
            * varCode: A str containing a numeric code.
          * vendor: A dict containing:
            * name: A user-readable str.
          * refund: A bool.
          * productCode: A str containing a numerical code.

        Type 'BALANCE_TOPUP' contains the following additional entries:
          * newCardBalance: Amount in Euro cents as int.
          * automaticTopUp: A bool.
          * station: A dict containing the following entries:
            * name
            * varCode: A str containing a numeric code, same as used in
                       JOURNEYs for departure/arrival station.
        """
        # TODO: How do I know how many pages there are?
        query = []
        if offset is not None:
            query.append(f'offset={offset}')
        if limit is not None:
            query.append(f'limit={limit}')
        local_tz = ZoneInfo('Europe/Amsterdam')
        if start_date is not None:
            # Create a time string like '2022-11-03T23:00:00.000Z'.
            dt = datetime.combine(start_date,
                                  time(0, 0, tzinfo=local_tz)) \
                         .astimezone(ZoneInfo('UTC')) \
                         .replace(tzinfo=None) \
                         .isoformat(timespec='milliseconds') + 'Z'
            query.append(f'startDate={dt}')
        if end_date is not None:
            # Create a time string like '2022-11-11T22:59:59.999Z'.
            dt = datetime.combine(end_date,
                                  time(23, 59, 59, 999000, tzinfo=local_tz)) \
                         .astimezone(ZoneInfo('UTC')) \
                         .replace(tzinfo=None) \
                         .isoformat(timespec='milliseconds') + 'Z'
            query.append(f'endDate={dt}')
        if source is not None:
            query += f'&transactionSource={source}'
        res = self.session.get(
                self.OMNI_TRANSACTION_API + f'/ovcp/{card}/transaction'
                + f'?{"&".join(query)}',
                headers=self.authorization_headers,
                )
        res.raise_for_status()
        return res.json()

    def put_note_on_transaction_of_ovcp(self, card: str,
                                        transaction_id: str,
                                        note: str,
                                        ) -> bool:
        """Store note in given transaction of OV-Chipkaart with given number.

        This note appears in the 'opmerking' column on exports.

        Arguments:
        card: OV-Chipkaart card number.
        transaction_id: The id of the 'HB' transaction you want to store
                        a note in.
        note: The note to store.

        Returns whether the note was stored successfully.
        """
        res = self.session.put(
                self.OMNI_TRANSACTION_API + f'/ovcp/{card}/transaction',
                headers=self.authorization_headers,
                json={'notes': note,
                      'transactionId': transaction_id,
                      },
                )
        res.raise_for_status()
        response = res.text
        if response == 'true':
            return True
        elif response == 'false':
            return False
        else:
            raise ValueError(f'Unexpected return value {response}.')

    def export_transactions_of_ovcp(
            self, card: str,
            transaction_ids: list[str],
            mime: Literal['application/pdf', 'text/csv'] = 'application/pdf',
            *,
            start_date: date,
            end_date: date,
    ) -> bytes:
        """Export transaction of OV-Chipkaart with given number.

        Arguments:
        transaction_ids: A list of 'HB' transaction ids to include in
                         the export.
        mime: MIME type of the export format, either 'application/pdf',
              or 'text/csv'.

        Keyword-only arguments:
        start_date
        end_date

        Returns the export as a byte string.
        """
        query = []
        local_tz = ZoneInfo('Europe/Amsterdam')
        if start_date is not None:
            # Create a time string like '2022-11-03T23:00:00.000Z'.
            dt = datetime.combine(start_date,
                                  time(0, 0, tzinfo=local_tz)) \
                         .astimezone(ZoneInfo('UTC')) \
                         .replace(tzinfo=None) \
                         .isoformat(timespec='milliseconds') + 'Z'
            query.append(f'startDate={dt}')
        if end_date is not None:
            # Create a time string like '2022-11-11T22:59:59.999Z'.
            dt = datetime.combine(end_date,
                                  time(23, 59, 59, 999000, tzinfo=local_tz)) \
                         .astimezone(ZoneInfo('UTC')) \
                         .replace(tzinfo=None) \
                         .isoformat(timespec='milliseconds') + 'Z'
            query.append(f'endDate={dt}')
        headers = dict(self.authorization_headers,
                       **{'Accept': mime})
        res = self.session.post(
                self.OMNI_TRANSACTION_API + f'/ovcp/{card}/transaction/export'
                + '?{"&".join(query)}',
                headers=headers,
                json=[{'transactionId': tid} for tid in transaction_ids],
                )
        res.raise_for_status()
        return res.content

    def get_ovcp_contract_info(self, card: str) -> dict:
        """Get OV-Chipkaart contract info.

        This returns a list of dicts with the following entries:
        * cardNo: A str contraining the OV-Chipkaart card number.
        * price: An int containing the monthly fee in Euro cents.
        * flexProductName: A user-readable name.
        * startDate: An ISO 8601 date.
        * orderId: A str containing a numerical id.
        * eligibleForChangeDate: An ISO 8601 date, from which on you
          can change the product.
        * productType: 'NSFLEX'
        * ibanNumber: A str containing the IBAN of the bank account that
           will be charged with the subscription.
        * buildingBlocks: A list of str representing the conditions of
           the product, e.g.
           * 'NSFLEX'
           * 'WKNDKORT'
           * 'DALKORT'
           * 'SPITSVOL'
        * changeAllowed: A bool.
        * travelOptions: A list of dicts containing:
          * type
          * value: A bool.
        * isContractHolder: A bool.
        """
        res = self.session.get(self.OMNI_OVCP_API + f'/{card}/contract-info',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_extended_ovcp_contract_info(self, card: str) -> dict:
        """Get extended OV-Chipkaart contract info.

        This seems to return exactly the same information as
        get_ovcp_contract_info().

        This returns a list of dicts with the following entries:
        * cardNo: A str contraining the OV-Chipkaart card number.
        * price: An int containing the monthly fee in Euro cents.
        * flexProductName: A user-readable name.
        * startDate: An ISO 8601 date.
        * orderId: A str containing a numerical id.
        * eligibleForChangeDate: An ISO 8601 date, from which on you
          can change the product.
        * productType: 'NSFLEX'
        * ibanNumber: A str containing the IBAN of the bank account that
           will be charged with the subscription.
        * buildingBlocks: A list of str representing the conditions of
           the product, e.g.
           * 'NSFLEX'
           * 'WKNDKORT'
           * 'DALKORT'
           * 'SPITSVOL'
        * changeAllowed: A bool.
        * travelOptions: A list of dicts containing:
          * type
          * value: A bool.
        * isContractHolder: A bool.
        """
        res = self.session.get(self.OMNI_OVCP_API
                               + f'/{card}/contract-info-extended',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_card_type_info(self, card: str) -> dict:
        """Get OV-Chipkaart card type info.

        This returns a dict containing:
        * cardType: A str, "Pcard" in my test.
        """
        res = self.session.get(self.OMNI_OVCP_API + f'/{card}/card-type-info',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_next_invoice_cost_overview(self) -> dict:
        """Get next invoice cost overview.

        This returns a list of dicts with the following entries:
        * plannedInvoiceDate: An ISO 8601 date.
        * costCategories: A list of dicts containing:
          * type: e.g. 'SUBSCRIPTIONS', or 'REST'.
          * amount: An int representing the amount in Euro cents.
        """
        res = self.session.get(self.API_BASE
                               + '/omni-invoice-api/next-invoice-cost-overview',
                               headers=self.authorization_headers)
        res.raise_for_status()
        return res.json()

    def get_tariefpunt(self, cd: int) -> dict:
        """Get information about a station.

        Arguments:
        cd: id of station. Found under varCode in transactions.
            ex.: 206 for Eindhoven.

        This returns a list of dicts with the following entries:
        * tariefpunt: A list of dicts containing:
          * reisassistentie: A bool.
          * cd: An int.
          * naamKort: A user-readable str.
          * finCd: An int.
          * geoInfo: A dict containing:
            * xCoordinaat: An int.
            * naderenRadius: An int.
            * yCoordinaat: An int. (probably coordinates in some projection)
            * latitude: float
            * radius: An int.
            * longitude: float
          * vervoerder: A list of dicts containing:
            * cdVervoerder: An int.
          * toegankelijkheid: A bool.
          * naamPublicatie: A dict containing:
            * kort: A short name that might miss some vowels.
            * middel: Same as naamKort.
            * lang: The full name, same as naam.
          * naam: A user-readable str.
          * uicCdOverstapIce: An int.
          * uicCdOverstapIcberlijn: An int.
          * uicCd: An int.
          * uicCdOverstapIcbrussel: An int.
          * uicCdKort: An int.
          * verkorting: A short (2 or 3 letters?) code.
          * cdLand: e.g. 'NL'
          * euroEvaCd: An int.
          * typeStation: An int.
          * begindatumTariefpunt: An ISO 8601 date.
        * returncode: An int, 0 in my test.
        * distributienummer: An int.
        """
        today = date.today()
        # This API endpoint uses another key than the rest.
        headers = dict(self.authorization_headers)
        headers['Ocp-Apim-Subscription-Key'] \
                = '53a4eb3aca3a42539d51147e583f4ffc'
        res = self.session.get(self.API_BASE
                               + '/mrp-tariefpunten-vns/tariefpunten/vns/v1'
                               + f'?datum={today:%Y%m%d}&cd={cd}',
                               headers=headers)
        res.raise_for_status()
        return res.json()


def parse_amount(a: str) -> Decimal:
    return (Decimal(a) / 100).quantize(Decimal('0.01'))


def parse_timestamp_as_local(timestamp: str) -> datetime:
    """Parse UTC timestamp as local Dutch datetime.

    The timestamp is assumed to be of ISO 8601 format with Z suffix, like
    '2022-12-23T12:59:12.000Z'.
    """
    local_tz = ZoneInfo('Europe/Amsterdam')
    # TODO: With Python 3.11, fromisoformat should be able to parse
    #       the Z suffix.
    timestamp = timestamp.removesuffix('Z') + '+00:00'
    return datetime.fromisoformat(timestamp).astimezone(local_tz)
