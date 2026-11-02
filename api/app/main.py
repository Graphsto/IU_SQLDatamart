import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from .config import APP_VERSION, get_settings
from .db import close_pool, open_pool
from .errors import install_handlers
from .routers import addresses, catalog, copies, lents, messages, reports, system, users

log = logging.getLogger("api")

DESCRIPTION = """
Kleine Backend-API über der PostgreSQL-Datenbank der **Bücherbörse** (`buechertausch`). Sie dient als
Praxistest: Trägt das Datenbankdesign (Tabellen, Views, Funktionen, Rechte) eine echte Anwendung?
Die API ändert **kein** SQL und benutzt die Datenbank ausschließlich als `app_user` mit reinem SQL.
Wo sie Lücken im Design findet, umgeht sie diese und dokumentiert sie im Testbericht (`Phase3/API_Testbericht.md`).

> **Nur zum Testen.** Es gibt keinen Login. Der Aufrufer wird ungeprüft über den Header `X-User-Id` bestimmt.

### So testest du hier
1. Oben auf **Authorize** klicken und bei `DevUserId` eine Benutzer-ID eintragen, z. B. `1` (Seed: 1 Alice,
   2 Bob, 3 Charlie, 4 Olaf, 5 Olad, 6 Kimberly, 7 Irmtraut, 8 Heidi, 9 Hildegard, 10 Jürgen).
2. Eine Operation aufklappen, **Try it out**, **Execute**. Die Anfrage geht direkt an diese API.
3. Alle Seed-Benutzer haben die Rolle `user`. Für Moderator-/Admin-Endpunkte (Berichte, Protokoll, Benutzerliste)
   bei der Anfrage den Parameter `X-Test-Role` auf `moderator` oder `admin` setzen.

### Konventionen
* JSON-Feldnamen sind die Spaltennamen der Datenbank, auch mit Tippfehlern (`book_copie_id`, `streetnummber`).
* Statuscodes: `401` Header fehlt/unbekannt, `403` nicht erlaubt, `404` nicht gefunden, `409` Konflikt
  (Status, Eindeutigkeit, Fremdschlüssel, Überschneidung), `422` ungültige Eingabe oder Regelverstoß der Datenbank,
  `503` Datenbank nicht erreichbar.
* Fehlerformat: `{"detail": "...", "sqlstate": "...", "constraint": "..."}` (die letzten beiden nur bei Datenbankfehlern).
* Listen: `limit` (Standard 50, maximal 200) und `offset`.
* Zeitangaben sind ISO-8601 mit Zeitzone.
"""

TAGS = [
    {"name": "System", "description": "Status, Links, Abgleich mit dem Datenbankvertrag."},
    {"name": "Benutzer", "description": "Registrierung, Profil, Statistik (`getFullUserStats`), Ausleihen eines Benutzers."},
    {"name": "Adressen", "description": "Abholadressen, Abholzeitfenster (`create_collection_time_window`) und Wohnadressen."},
    {"name": "Katalog", "description": "Autoren, Bücher und Bewertungen."},
    {
        "name": "Exemplare",
        "description": "Suche in `available_books` (inkl. Umkreissuche), Detail aus `fullBookInfo`, Exemplare anlegen und pflegen.",
    },
    {
        "name": "Ausleihen",
        "description": (
            "Workflow `requested -> accepted -> active -> returned` (oder `rejected`) mit den Funktionen "
            "`create_book_request`, `accept_book_request`, `reject_book_request`, `hand_over_book`, `return_book`, "
            "`write_review`, `book_must_be_returned`, dazu der Versand."
        ),
    },
    {"name": "Nachrichten", "description": "Nachrichten zwischen Benutzern."},
    {
        "name": "Berichte",
        "description": "Monatsbericht, überfällige Ausleihen (`overdue_lents`) und das Protokoll. Nur Moderatoren/Admins.",
    },
]


def _operation_id(route: APIRoute) -> str:
    return route.name


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.warning("Dev-Header-Authentifizierung aktiv (X-User-Id): nur für Tests, nicht öffentlich betreiben.")
    open_pool()
    try:
        yield
    finally:
        close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(
        title="Bücherbörse Test-API",
        version=APP_VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        servers=[{"url": settings.api_public_url, "description": "Diese API (API_PUBLIC_URL)"}],
        docs_url=None,  # FastAPI's own docs page loads assets from a CDN; the dashboard is the swagger-ui container
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
        generate_unique_id_function=_operation_id,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-User-Id", "X-Test-Role"],
        expose_headers=["Location"],
        allow_credentials=False,
        max_age=600,
    )
    install_handlers(app)

    for module in (system, users, addresses, catalog, copies, lents, messages, reports):
        app.include_router(module.router)
    return app


app = create_app()
