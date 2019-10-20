from collections import namedtuple

Transaction = namedtuple('Transaction', 'type description operation_date'
                                        ' value_date amount sub_total')

Balance = namedtuple('Balance', 'balance date')
