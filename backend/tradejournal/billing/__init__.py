"""Billing: the connector a payment provider plugs into (the billing design, "Provider connector").

    types.py      the plain frozen values every provider returns, and the errors
    provider.py   the interface (`BillingProvider`), the only thing the rest of the app knows about a provider
    signature.py  the webhook signature: HMAC-SHA256 over "<timestamp>:<raw body>", checked in constant time
    fake.py       `FakeBilling`, in memory, for every test and for local development
    paddle.py     `PaddleBilling`, the real one (docs/design/paddle-integration.md)
    factory.py    which one a configuration asks for

Nothing outside this package imports a provider's own types, so changing provider changes one adapter.
"""
