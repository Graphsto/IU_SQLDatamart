# IU_SQLDatamart

SQL Datamart Abgae by Jakob Freund

## Datenbank, Test-API und Dashboard starten

```bash
cp example.env .env        # Platzhalter-Passwörter in .env ersetzen
docker compose up -d --build
```

Das startet drei Container:

| Container | Was | Adresse |
|---|---|---|
| `db` | PostgreSQL mit der Datenbank `buechertausch` (aus `Phase2/`) | `localhost:5432` |
| `api` | Test-API (FastAPI) über der Datenbank, Zugriff als `app_user` | http://localhost:8000 |
| `docs` | **Dashboard**: API-Dokumentation und Testumgebung in einem (Swagger UI) | **http://localhost:8080** |

Im Dashboard oben auf **Authorize** klicken, eine Benutzer-ID eintragen (Seed: `1` = Alice … `10` = Jürgen), eine
Operation aufklappen und **Try it out** benutzen. Für Berichte, Protokoll und Benutzerliste bei der Anfrage den
Parameter `X-Test-Role` auf `moderator` oder `admin` setzen (alle Seed-Benutzer haben die Rolle `user`).
Die OpenAPI-Spezifikation liegt unter http://localhost:8000/openapi.json.

> **Nur zum Testen.** Die API kennt keinen Login: der Aufrufer wird ungeprüft über den Header `X-User-Id` bestimmt.
> API und Dashboard sind deshalb nur auf `127.0.0.1` erreichbar (Ports änderbar mit `API_PORT` und `DOCS_PORT` in `.env`).
> Der Datenbank-Port 5432 ist dagegen auf allen Netzwerkschnittstellen veröffentlicht, siehe `docker-compose.yml`.

Beim ersten Start einer leeren Datenbank beendet sich der `db`-Container einmal mit einem Fehler und startet von selbst
neu (`Phase2/9grantPrivileges.sql` verweist auf eine View, die es nicht gibt). Die API wartet darauf selbst; das
Dashboard startet, sobald die API gesund ist. Details stehen im Testbericht.

Stoppen mit `docker compose down`. **`docker compose down -v` löscht das Datenbank-Volume.**

## Tests der API

Die Tests laufen in einem eigenen, wegwerfbaren Stack (eigene Datenbank aus demselben Image, keine veröffentlichten
Ports). Die Entwicklungsdatenbank wird dabei nicht berührt.

```bash
docker compose -f api/docker-compose.test.yml run --rm tests     # Tests ausführen
docker compose -f api/docker-compose.test.yml down -v            # Test-Stack samt Daten entfernen
```

Das Ergebnis der Planungsprüfung (was die API am Datenbankdesign gefunden hat) steht in
[Phase3/API_Testbericht.md](Phase3/API_Testbericht.md).