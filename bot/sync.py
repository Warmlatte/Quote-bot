import logging
from dataclasses import dataclass

from bot.db.client import DBClient
from bot.sheets.client import SheetsClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    synced_quotes: int
    synced_customers: int
    failed_quotes: int
    failed_customers: int


def sync_records(db: DBClient, sheets: SheetsClient) -> SyncResult:
    synced_quotes = 0
    failed_quotes = 0
    synced_customers = 0
    failed_customers = 0

    for record in db.get_unsynced_quote_records():
        try:
            sheets.append_quote_record(
                quote_number=record["quote_number"],
                customer_name=record["customer_name"],
                resin_label=record["resin_label"],
                body_count=record["body_count"],
                material_cost=record["material_cost"],
                processing_fee=record["processing_fee"],
                auto_discount=record["auto_discount"] == "95折",
                manual_discount=record["manual_discount"],
                subtotal=record["subtotal"],
                final_total=record["final_total"],
                order_status=record["order_status"],
                decision=record["decision"],
            )
            db.mark_quote_record_synced(record["id"])
            synced_quotes += 1
        except Exception:
            logger.exception("Failed to sync quote record id=%s", record["id"])
            failed_quotes += 1

    for record in db.get_unsynced_customer_records():
        try:
            sheets.append_customer_record(
                quote_number=record["quote_number"],
                customer_name=record["customer_name"],
                drive_folder_url=record["drive_folder_url"],
                final_total=record["final_total"],
                pdf_url=record["pdf_url"],
            )
            db.mark_customer_record_synced(record["id"])
            synced_customers += 1
        except Exception:
            logger.exception("Failed to sync customer record id=%s", record["id"])
            failed_customers += 1

    return SyncResult(
        synced_quotes=synced_quotes,
        synced_customers=synced_customers,
        failed_quotes=failed_quotes,
        failed_customers=failed_customers,
    )
