"""Lille hjælper til additive, idempotente skemamigreringer (ALTER TABLE ADD
COLUMN) på en tabel der allerede eksisterer. Samme mønster/begrundelse som
PA SPEAKERS-projektets tilsvarende fil: `CREATE TABLE IF NOT EXISTS` alene
tilføjer IKKE en kolonne retroaktivt til en allerede-eksisterende tabel.
"""
from __future__ import annotations


def add_column_if_missing(conn, table: str, column: str, column_ddl: str) -> None:
    """`conn` er hvad som helst med en `.execute(sql)` -- en sqlite3.Connection
    eller en TursoClient. Tjekker via `PRAGMA table_info` FØR den forsøger
    ALTER, i stedet for at forsøge og fange en "duplicate column"-fejl bagefter
    (upålideligt mod Turso's HTTP-transport - se PA SPEAKERS' tilsvarende fil
    for detaljer)."""
    result = conn.execute(f"PRAGMA table_info({table})")
    rows = result.rows if hasattr(result, "rows") else result.fetchall()
    existing_columns = {row[1] for row in rows}
    if column in existing_columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_ddl}")
