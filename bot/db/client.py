import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DBClient:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS quote_records (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at       TEXT    NOT NULL,
                quote_number     TEXT    NOT NULL,
                customer_name    TEXT    NOT NULL,
                resin_label      TEXT    NOT NULL,
                body_count       INTEGER NOT NULL,
                material_cost    INTEGER NOT NULL,
                processing_fee   INTEGER NOT NULL,
                auto_discount    TEXT    NOT NULL,
                manual_discount  TEXT    NOT NULL,
                subtotal         INTEGER NOT NULL,
                final_total      INTEGER NOT NULL,
                order_status     TEXT    NOT NULL,
                decision         TEXT    NOT NULL,
                synced_at        TEXT    DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS customer_records (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at        TEXT    NOT NULL,
                quote_number      TEXT    NOT NULL,
                customer_name     TEXT    NOT NULL,
                drive_folder_url  TEXT    NOT NULL,
                final_total       INTEGER NOT NULL,
                pdf_url           TEXT    NOT NULL,
                synced_at         TEXT    DEFAULT NULL
            );
        """)
        self._conn.commit()

    def insert_quote_record(
        self,
        quote_number: str,
        customer_name: str,
        resin_label: str,
        body_count: int,
        material_cost: int,
        processing_fee: int,
        auto_discount: str,
        manual_discount: str,
        subtotal: int,
        final_total: int,
        order_status: str,
        decision: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO quote_records
                (created_at, quote_number, customer_name, resin_label, body_count,
                 material_cost, processing_fee, auto_discount, manual_discount,
                 subtotal, final_total, order_status, decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_now(), quote_number, customer_name, resin_label, body_count,
                material_cost, processing_fee, auto_discount, manual_discount,
                subtotal, final_total, order_status, decision,
            ),
        )
        self._conn.commit()

    def insert_customer_record(
        self,
        quote_number: str,
        customer_name: str,
        drive_folder_url: str,
        final_total: int,
        pdf_url: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO customer_records
                (created_at, quote_number, customer_name, drive_folder_url, final_total, pdf_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_utc_now(), quote_number, customer_name, drive_folder_url, final_total, pdf_url),
        )
        self._conn.commit()

    def get_unsynced_quote_records(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT * FROM quote_records WHERE synced_at IS NULL ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unsynced_customer_records(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT * FROM customer_records WHERE synced_at IS NULL ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_quote_record_synced(self, record_id: int) -> None:
        self._conn.execute(
            "UPDATE quote_records SET synced_at = ? WHERE id = ?",
            (_utc_now(), record_id),
        )
        self._conn.commit()

    def mark_customer_record_synced(self, record_id: int) -> None:
        self._conn.execute(
            "UPDATE customer_records SET synced_at = ? WHERE id = ?",
            (_utc_now(), record_id),
        )
        self._conn.commit()
