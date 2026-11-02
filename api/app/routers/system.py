from fastapi import APIRouter

from ..config import APP_VERSION, get_settings
from ..db import Db
from ..deps import DbDep
from ..enums import DB_ENUMS
from ..responses import errs
from ..schemas import ContractCheck, ContractReport, DatabaseInfo, EnumValues, Health, Link, RootInfo

router = APIRouter(tags=["System"])

WARNING = (
    "Test-API: Die Identität kommt ungeprüft aus dem Header X-User-Id. "
    "Nicht ohne echte Authentifizierung betreiben und nicht ins Internet stellen."
)


@router.get("/", response_model=RootInfo, summary="Startseite mit Links")
def root() -> RootInfo:
    """Zeigt die wichtigsten Adressen und den Sicherheitshinweis."""
    s = get_settings()
    return RootInfo(
        name="Bücherbörse Test-API",
        version=APP_VERSION,
        warning=WARNING,
        links=[
            Link(name="Dashboard (Swagger UI: Doku + Testumgebung)", url=s.docs_public_url),
            Link(name="OpenAPI-Spezifikation", url=f"{s.api_public_url}/openapi.json"),
            Link(name="Health", url=f"{s.api_public_url}/health"),
        ],
    )


@router.get(
    "/health",
    response_model=Health,
    summary="Gesundheitscheck (inkl. Datenbank)",
    responses=errs(503),
)
def health(db: DbDep) -> Health:
    """Prüft die Verbindung zur Datenbank. Antwortet 503, solange die Datenbank nicht bereit ist."""
    row = db.one(
        "SELECT current_user AS user, current_database() AS database, "
        "split_part(current_setting('server_version'), ' ', 1) AS version"
    )
    assert row is not None
    return Health(status="ok", auth="dev-header", database=DatabaseInfo(**row))


def enum_labels(db: Db) -> dict[str, list[str]]:
    rows = db.all(
        """
        SELECT t.typname::text AS name, array_agg(e.enumlabel::text ORDER BY e.enumsortorder) AS labels
        FROM pg_type t
        JOIN pg_enum e ON e.enumtypid = t.oid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
        GROUP BY t.typname
        ORDER BY t.typname
        """
    )
    return {r["name"]: r["labels"] for r in rows}


@router.get("/meta/enums", response_model=EnumValues, summary="Enum-Typen der Datenbank")
def list_enums(db: DbDep) -> EnumValues:
    """Liest die Werte der ENUM-Typen aus `pg_enum` (Phase2/3createEnum.sql), in Definitionsreihenfolge."""
    return EnumValues(enums=enum_labels(db))


EXPECTED_TABLES = {
    "Users": ("SELECT", "INSERT", "UPDATE"),
    "CollectionAdresses": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "UserAdresses": ("SELECT", "INSERT", "DELETE"),
    "Authors": ("SELECT", "INSERT"),
    "Books": ("SELECT", "INSERT"),
    "BookAuthors": ("SELECT", "INSERT"),
    "Book_copy": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "Lents": ("SELECT",),
    "Shipping": ("SELECT", "INSERT", "UPDATE"),
    "Reviews": ("SELECT",),
    "Messages": ("SELECT", "INSERT", "UPDATE"),
    "Protocols": ("SELECT", "INSERT"),
    "CollectionTimeWindows": ("SELECT", "DELETE"),
}
EXPECTED_VIEWS = ("available_books", "overdue_lents", "fullbookinfo")
EXPECTED_FUNCTIONS = (
    "accept_book_request(p_lent_id bigint, p_user_id bigint)",
    "book_must_be_returned(p_lent_id bigint)",
    "create_book_request(p_book_copie_id bigint, p_user_id bigint, p_shipping boolean)",
    "create_collection_time_window(p_address_id bigint, p_user_id bigint, p_weekday integer, "
    "p_start_time time without time zone, p_end_time time without time zone)",
    "generate_monthly_report(p_year integer, p_month integer)",
    "generate_monthly_report_json(p_year integer, p_month integer)",
    "getallbooksbyuser(p_user_id bigint)",
    "getalllentsbyuser(p_user_id bigint)",
    "getfulluserstats(p_user_id bigint)",
    "hand_over_book(p_lent_id bigint, p_user_id bigint)",
    "reject_book_request(p_lent_id bigint, p_user_id bigint)",
    "return_book(p_lent_id bigint, p_user_id bigint)",
    "write_review(p_lent_id bigint, p_user_id bigint, p_rating integer, p_comment text)",
)


def contract_checks(db: Db) -> list[ContractCheck]:
    checks: list[ContractCheck] = []

    def add(name: str, missing: list[str]) -> None:
        checks.append(ContractCheck(name=name, ok=not missing, detail=", ".join(missing) or None))

    missing_tables = [
        t for t in EXPECTED_TABLES if db.scalar("SELECT to_regclass(%s) IS NOT NULL", (f'public."{t}"',)) is not True
    ]
    add("Tabellen vorhanden", missing_tables)

    missing_views = [
        v for v in EXPECTED_VIEWS if db.scalar("SELECT to_regclass(%s) IS NOT NULL", (f"public.{v}",)) is not True
    ]
    add("Views vorhanden", missing_views)

    present = {
        r["sig"]
        for r in db.all(
            """
            SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS sig
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.prokind = 'f'
            """
        )
    }
    add("Funktionen mit erwarteter Signatur", [f for f in EXPECTED_FUNCTIONS if f not in present])

    db_enums = enum_labels(db)
    wrong = [
        name
        for name, py_enum in DB_ENUMS.items()
        if db_enums.get(name) != [member.value for member in py_enum]
    ]
    add("Enum-Werte identisch", wrong)

    missing_privs: list[str] = []
    for table, privileges in EXPECTED_TABLES.items():
        for privilege in privileges:
            if not db.scalar("SELECT has_table_privilege(current_user, %s, %s)", (f'public."{table}"', privilege)):
                missing_privs.append(f"{table}:{privilege}")
    for view in EXPECTED_VIEWS:
        if not db.scalar("SELECT has_table_privilege(current_user, %s, 'SELECT')", (f"public.{view}",)):
            missing_privs.append(f"{view}:SELECT")
    add("Rechte des API-Benutzers", missing_privs)
    return checks


@router.get(
    "/meta/contract",
    response_model=ContractReport,
    summary="Vertrag API <-> Datenbank prüfen",
)
def check_contract(db: DbDep) -> ContractReport:
    """Soll/Ist-Abgleich: Existieren alle Tabellen, Views und Funktionen, die die API benutzt? Stimmen die
    Enum-Werte? Hat der API-Benutzer die nötigen Rechte? Ein Fehler hier bedeutet: Das Datenbankdesign hat sich
    geändert, ohne dass die API angepasst wurde (oder umgekehrt)."""
    checks = contract_checks(db)
    return ContractReport(ok=all(c.ok for c in checks), checks=checks)
