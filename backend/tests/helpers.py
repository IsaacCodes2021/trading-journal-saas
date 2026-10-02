"""Shared test helpers: fixture access and small synthetic CSV builders."""

from __future__ import annotations

from pathlib import Path

from tradejournal.parser import ParseResult, parse_performance_csv

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = "01-05-2024-performance.csv"
TZ = "America/Los_Angeles"
HEADER = (
    "symbol,_priceFormat,_priceFormatType,_tickSize,buyFillId,sellFillId,qty,"
    "buyPrice,sellPrice,pnl,boughtTimestamp,soldTimestamp,duration"
)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def parse_fixture(name: str) -> ParseResult:
    return parse_performance_csv(fixture_bytes(name), TZ)


def fixture_lines(name: str, *line_numbers: int) -> bytes:
    """A CSV with the header plus only the given (1-based) lines of a fixture."""
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return make_csv(*(lines[n - 1] for n in line_numbers))


def make_csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


def lines_of(result: ParseResult, groups: list[list[int]]) -> list[list[int]]:
    """Translate index groups into CSV line-number groups for readable assertions."""
    return [[result.pairs[i].line for i in group] for group in groups]
