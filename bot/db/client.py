import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_TZ_TAIPEI = timezone(timedelta(hours=8))


def _now_taipei() -> str:
    return datetime.now(_TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")


class DBClient:
    def __init__(self, db_path: str) -> None:
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EnvironmentError(
                f"Cannot create database directory '{Path(db_path).parent}': {exc}. "
                "Set DB_PATH to a writable path (e.g. DB_PATH=./quote_bot.db)."
            ) from exc
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate_tables()

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
                synced_at        TEXT    DEFAULT NULL,
                drive_folder_url TEXT    DEFAULT NULL,
                file_details_text TEXT   DEFAULT NULL,
                rejection_reason TEXT    DEFAULT NULL
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

    def _migrate_tables(self) -> None:
        new_columns = [
            "ALTER TABLE quote_records ADD COLUMN drive_folder_url TEXT DEFAULT NULL",
            "ALTER TABLE quote_records ADD COLUMN file_details_text TEXT DEFAULT NULL",
            "ALTER TABLE quote_records ADD COLUMN rejection_reason TEXT DEFAULT NULL",
            "ALTER TABLE quote_records ADD COLUMN shipping_fee INTEGER DEFAULT 0",
            "ALTER TABLE quote_records ADD COLUMN shipping_address TEXT DEFAULT ''",
        ]
        for stmt in new_columns:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
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
        drive_folder_url: str | None = None,
        file_details_text: str | None = None,
        rejection_reason: str | None = None,
        shipping_fee: int = 0,
        shipping_address: str = "",
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO quote_records
                (created_at, quote_number, customer_name, resin_label, body_count,
                 material_cost, processing_fee, auto_discount, manual_discount,
                 subtotal, final_total, order_status, decision,
                 drive_folder_url, file_details_text, rejection_reason,
                 shipping_fee, shipping_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_taipei(), quote_number, customer_name, resin_label, body_count,
                material_cost, processing_fee, auto_discount, manual_discount,
                subtotal, final_total, order_status, decision,
                drive_folder_url, file_details_text, rejection_reason,
                shipping_fee, shipping_address,
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
    ) -> bool:
        """Returns True if inserted; False if non-empty drive_folder_url already exists."""
        if drive_folder_url:
            existing = self._conn.execute(
                "SELECT id FROM customer_records WHERE drive_folder_url = ?",
                (drive_folder_url,),
            ).fetchone()
            if existing:
                return False
        self._conn.execute(
            """
            INSERT INTO customer_records
                (created_at, quote_number, customer_name, drive_folder_url, final_total, pdf_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_now_taipei(), quote_number, customer_name, drive_folder_url, final_total, pdf_url),
        )
        self._conn.commit()
        return True

    def count_accepted_quotes_today(self, date_prefix: str) -> int:
        """Count accepted quote_records whose quote_number starts with trb{date_prefix}."""
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM quote_records WHERE quote_number LIKE ? AND decision = '接受'",
            (f"trb{date_prefix}%",),
        )
        return cursor.fetchone()[0]

    def count_quick_quotes_today(self, date_str: str) -> int:
        """Count quick-decision records for the given date (YYYY-MM-DD in Taipei time)."""
        try:
            # created_at is stored as YYYY/MM/DD HH:MM; convert date_str to match
            date_slash = date_str.replace("-", "/")
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM quote_records WHERE decision = '快速' AND substr(created_at, 1, 10) = ?",
                (date_slash,),
            )
            return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_unsynced_quote_records(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT * FROM quote_records WHERE synced_at IS NULL ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unsynced_accepted_quotes(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT * FROM quote_records WHERE synced_at IS NULL AND decision = '接受' ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unsynced_rejected_quotes(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT * FROM quote_records WHERE synced_at IS NULL AND decision = '拒絕' ORDER BY id"
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
            (_now_taipei(), record_id),
        )
        self._conn.commit()

    def mark_customer_record_synced(self, record_id: int) -> None:
        self._conn.execute(
            "UPDATE customer_records SET synced_at = ? WHERE id = ?",
            (_now_taipei(), record_id),
        )
        self._conn.commit()
