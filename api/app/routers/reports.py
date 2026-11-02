from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query

from ..db import Where
from ..deps import DbDep, PageDep, QueryId, Staff
from ..enums import ProtocolType
from ..responses import errs
from ..schemas import MonthlyReport, OverdueLent, ProtocolOut

router = APIRouter(tags=["Berichte"])

Year = Annotated[int, Query(ge=2000, le=2100, description="Jahr, z. B. 2026.")]
Month = Annotated[int, Query(ge=1, le=12, description="Monat 1-12.")]


@router.get(
    "/reports/monthly",
    response_model=MonthlyReport,
    summary="Monatsbericht",
    responses=errs(401, 403, 422),
)
def monthly_report(user: Staff, db: DbDep, year: Year, month: Month) -> Any:
    """Ruft `generate_monthly_report` auf (Tabellenfunktion, eine Zeile). Gezählt wird nach dem Anlage- bzw.
    Anfragezeitpunkt im angegebenen Monat. Nur Moderatoren/Admins (`X-Test-Role`)."""
    row = db.one("SELECT * FROM generate_monthly_report(%s, %s)", (year, month))
    return {"year": year, "month": month, **(row or {})}


@router.get(
    "/reports/monthly/json",
    response_model=MonthlyReport,
    summary="Monatsbericht (JSON-Funktion)",
    responses=errs(401, 403, 422),
)
def monthly_report_json(user: Staff, db: DbDep, year: Year, month: Month) -> Any:
    """Dasselbe über `generate_monthly_report_json`: die Datenbank liefert das fertige JSON, die API reicht es durch."""
    return db.scalar("SELECT generate_monthly_report_json(%s, %s)", (year, month))


@router.get(
    "/reports/overdue-lents",
    response_model=list[OverdueLent],
    summary="Überfällige Ausleihen",
    responses=errs(401, 403),
)
def overdue_lents(user: Staff, db: DbDep, page: PageDep) -> Any:
    """Liest die View `overdue_lents` (aktive Ausleihen nach dem Rückgabedatum). Enthält E-Mail-Adressen der
    Ausleiher, deshalb nur für Moderatoren/Admins."""
    return db.all(
        "SELECT * FROM overdue_lents ORDER BY days_overdue DESC, lent_id LIMIT %s OFFSET %s",
        (page.limit, page.offset),
    )


@router.get(
    "/protocols",
    response_model=list[ProtocolOut],
    summary="Ereignisprotokoll",
    responses=errs(401, 403),
)
def list_protocols(
    user: Staff,
    db: DbDep,
    page: PageDep,
    protocol_type: ProtocolType | None = None,
    user_id: QueryId | None = None,
    book_copie_id: QueryId | None = None,
    since: Annotated[datetime | None, Query(description="Ab diesem Zeitpunkt (ISO-8601).")] = None,
    until: Annotated[datetime | None, Query(description="Bis (ausschließlich) zu diesem Zeitpunkt.")] = None,
) -> Any:
    """Die Tabelle `Protocols` (Audit-Log, für `app_user` nur lesen und anhängen). Neueste zuerst."""
    where = Where()
    if protocol_type is not None:
        where.add("protocol_type = %(protocol_type)s", protocol_type=protocol_type.value)
    if user_id is not None:
        where.add("user_id = %(user_id)s", user_id=user_id)
    if book_copie_id is not None:
        where.add("book_copie_id = %(book_copie_id)s", book_copie_id=book_copie_id)
    if since is not None:
        where.add("created_at >= %(since)s", since=since)
    if until is not None:
        where.add("created_at < %(until)s", until=until)
    return db.all(
        f"""
        SELECT protocol_id, book_copie_id, user_id, protocol_type, details, created_at
        FROM "Protocols" {where.sql()}
        ORDER BY protocol_id DESC LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**where.params, "limit": page.limit, "offset": page.offset},
    )
