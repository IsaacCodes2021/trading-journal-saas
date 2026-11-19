"""Roundturn's own import file, version 1, as a chatbot would write it from another platform's export: the golden file
the Other platform tests share (tests/test_other_platform.py, tests/pg/test_pg_other_platform.py).

Five made-up round turns on 2026-11-18 in New York time (UTC-05:00), one of them three Micro E-mini S&P contracts bought
at different prices (an average that repeats), one in the evening. Gross +$372.50, fees $25.42, net +$347.08.
"""

from __future__ import annotations

HEADER = "roundturn_version,symbol,side,qty,entry_time,entry_price,exit_time,exit_price,gross_pnl,fees,net_pnl"
ROWS = [
    "1,NQZ6,long,2,2026-11-18T09:31:05-05:00,21000.25,2026-11-18T09:33:10-05:00,21010.5,410.00,9.00,401.00",
    "1,NQZ6,short,1,2026-11-18T09:40:00-05:00,21015.00,2026-11-18T09:42:30-05:00,21020.25,-105.00,4.50,-109.50",
    "1,MESZ6,long,3,2026-11-18T10:01:00-05:00,6000.0833333333,2026-11-18T10:05:00-05:00,6001.25,17.50,3.72,13.78",
    "1,CLF7,short,1,2026-11-18T10:30:00-05:00,60.15,2026-11-18T10:45:00-05:00,59.95,200.00,4.00,196.00",
    "1,GCG7,long,1,2026-11-18T18:05:00-05:00,2650.0,2026-11-18T19:10:00-05:00,2648.5,-150.00,4.20,-154.20",
]


def file_of(*rows: str, header: str = HEADER, eol: str = "\r\n") -> bytes:
    return eol.join([header, *rows, ""]).encode()


GOLDEN = file_of(*ROWS)
