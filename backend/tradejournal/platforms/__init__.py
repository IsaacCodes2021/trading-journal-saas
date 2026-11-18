"""Readers for platforms whose exports are not Tradovate's Performance report (that one is `parser.py`).

Each reader turns a platform's file into the engine's plain shapes (`models.Execution` for fills), and never raises on a
bad row: it reports the row by line number and keeps going, like `parser.py`.
"""
