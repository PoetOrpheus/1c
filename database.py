"""SQLite persistence for the cutting calculator."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


class Database:
    TABLES = {"materials", "cut_types", "extras"}

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self._seed()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                thickness REAL NOT NULL DEFAULT 0,
                width REAL NOT NULL,
                height REAL NOT NULL,
                price_sheet REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS cut_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price_per_cut REAL NOT NULL DEFAULT 0,
                setup_fee REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS extras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                unit TEXT NOT NULL DEFAULT 'шт.',
                price REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                customer TEXT NOT NULL DEFAULT '',
                total REAL NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_created_at
                ON orders(created_at DESC);
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def _seed(self) -> None:
        if self.connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0:
            self.connection.executemany(
                """INSERT INTO materials
                   (name, thickness, width, height, price_sheet, active)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                [
                    ("ЛДСП Белый", 16, 2800, 2070, 3850),
                    ("МДФ Шлифованный", 16, 2800, 2070, 4650),
                    ("Фанера ФК 2/2", 12, 1525, 1525, 2900),
                    ("Оргстекло прозрачное", 4, 2050, 3050, 7200),
                ],
            )
        if self.connection.execute("SELECT COUNT(*) FROM cut_types").fetchone()[0] == 0:
            self.connection.executemany(
                """INSERT INTO cut_types
                   (name, price_per_cut, setup_fee, active)
                   VALUES (?, ?, ?, 1)""",
                [
                    ("Прямолинейный распил", 80, 300),
                    ("Чистовой распил", 130, 500),
                    ("Лазерная резка", 220, 800),
                ],
            )
        if self.connection.execute("SELECT COUNT(*) FROM extras").fetchone()[0] == 0:
            self.connection.executemany(
                "INSERT INTO extras (name, unit, price, active) VALUES (?, ?, ?, 1)",
                [
                    ("Кромление", "пог. м", 120),
                    ("Сверление отверстия", "шт.", 35),
                    ("Упаковка", "шт.", 250),
                    ("Доставка", "рейс", 1500),
                ],
            )
        self.connection.commit()

    def list_records(self, table: str, active_only: bool = False) -> list[dict]:
        self._check_table(table)
        where = " WHERE active = 1" if active_only else ""
        rows = self.connection.execute(
            f"SELECT * FROM {table}{where} ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]

    def insert_record(self, table: str, values: dict) -> int:
        self._check_table(table)
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        cursor = self.connection.execute(
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
            [values[column] for column in columns],
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_record(self, table: str, record_id: int, values: dict) -> None:
        self._check_table(table)
        assignments = ", ".join(f"{column} = ?" for column in values)
        self.connection.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            [*values.values(), record_id],
        )
        self.connection.commit()

    def delete_record(self, table: str, record_id: int) -> None:
        self._check_table(table)
        self.connection.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        self.connection.commit()

    def _check_table(self, table: str) -> None:
        if table not in self.TABLES:
            raise ValueError("Недопустимая таблица")

    def next_order_number(self) -> str:
        prefix = datetime.now().strftime("ЗК-%Y%m%d-")
        row = self.connection.execute(
            "SELECT number FROM orders WHERE number LIKE ? ORDER BY id DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        sequence = 1
        if row:
            try:
                sequence = int(row["number"].rsplit("-", 1)[1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}{sequence:04d}"

    def save_order(self, payload: dict) -> int:
        cursor = self.connection.execute(
            """INSERT INTO orders (number, created_at, customer, total, payload)
               VALUES (?, ?, ?, ?, ?)""",
            (
                payload["number"],
                payload["created_at"],
                payload.get("customer", ""),
                payload["totals"]["grand"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_orders(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id, number, created_at, customer, total FROM orders ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_order(self, order_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT payload FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def update_order(self, order_id: int, payload: dict) -> None:
        self.connection.execute(
            "UPDATE orders SET number=?, created_at=?, customer=?, total=?, payload=? WHERE id=?",
            (payload["number"], payload["created_at"], payload.get("customer", ""),
             payload["totals"]["grand"], json.dumps(payload, ensure_ascii=False), order_id),
        )
        self.connection.commit()

    def get_state(self, key: str):
        row = self.connection.execute("SELECT payload FROM app_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, key: str, payload) -> None:
        self.connection.execute(
            "INSERT INTO app_state(key,payload) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            (key, json.dumps(payload, ensure_ascii=False)),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
