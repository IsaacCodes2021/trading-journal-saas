from decimal import Decimal

import pytest

from tradejournal import accounts, db


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def account(conn):
    """An Apex account charging $4.00 per contract round trip on every symbol."""
    with db.transaction(conn):
        created = accounts.create_account(
            conn, "Apex 07", firm="Apex", account_size=Decimal("50000"), starting_balance=Decimal("50000")
        )
        accounts.set_fee(conn, created.id, "*", Decimal("4.00"))
    return created
