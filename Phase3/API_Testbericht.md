# API-Testbericht: Trägt das Datenbankdesign eine echte Anwendung?

Stand: 03.11.2026 · Datenbank: PostgreSQL 17.11 (`Phase2/*.sql`) · API: [`api/`](../api) (Python 3.12, FastAPI, psycopg 3)

Zur Entstehung und zum Rahmen des Backends siehe [`api/note.md`](../api/note.md).

## 1. Ergebnis in Kürze

**Gesamturteil.** Das Datenbankdesign trägt eine vollständige Anwendung: Alle 13 Tabellen, 3 Views und 13 Funktionen
ließen sich hinter 51 Endpunkten verwenden, ohne dass eine SQL-Datei geändert werden musste. Der Kern, der Ausleih-Workflow
`requested → accepted → active → returned` mit Doppelverleih-Schutz, Protokoll und Besitzerprüfung in den Funktionen,
hat alle Prüfungen bestanden, auch bei gleichzeitigen Anfragen. Die Schwächen liegen weniger im Datenmodell als in dem, was
darum herum fehlt: Eindeutigkeits- und Prüfregeln, eine Berechtigungsschicht in der Datenbank, unterscheidbare Fehlercodes
und ein Init-Skript, das einmal sauber durchläuft. Die API konnte jede dieser Lücken umgehen, aber die Umgehungen (Sperren,
Fehlerklassifizierung, Autorisierung) gehören auf Dauer in die Datenbank.

| Kennzahl | Wert |
|---|---|
| Endpunkte | 51 in 8 Gruppen ([Anhang A](#anhang-a-alle-endpunkte)) |
| Genutzte Datenbankobjekte | 13 von 13 Tabellen, 3 von 3 Views, 13 von 13 Funktionen (per Test abgesichert, [Abschnitt 3](#3-abdeckung)) |
| Tests | 236: 233 bestanden, 3 erwartete Fehlschläge (`xfail`, siehe F1 und F5) |
| Davon mit echten Commits und parallelen Anfragen | 18 |
| Geänderte SQL- oder Phase-Dateien | 0 (SHA-256 von 20 Dateien vor und nach der Arbeit identisch) |
| Befunde | 15: 2 hoch, 7 mittel, 6 niedrig ([Abschnitt 5](#5-befunde)) |
| Browsertest des Dashboards (Chrome) | 22 von 22 Prüfungen auf frischem Stack, 15 von 15 auf dem laufenden Stack (nur lesend) |
| Abhängigkeiten, Geheimnisse | `pip-audit`: keine bekannten Schwachstellen; `gitleaks`: keine Funde |

Schweregrade: **hoch** = verhindert den Betrieb oder kann Daten bzw. Rechte gefährden, **mittel** = führt zu falschem oder
unklarem Verhalten und muss von der Anwendung ausgeglichen werden, **niedrig** = Hygiene, Dokumentation, Risiko ohne
gemessene Auswirkung.

## 2. Rahmen und Vorgehen

* **SQL unangetastet.** `Phase1/`, `Phase2/`, `docker/` und der `db`-Service in `docker-compose.yml` sind unverändert
  (SHA-256-Vergleich von 20 Dateien, Zeilenvergleich des `db`-Blocks; `git status` zeigt nur `README.md`,
  `docker-compose.yml` und `example.env` als geändert und `api/` als neu). Der laufende Datenbank-Container wurde beim
  Start der API weder neu erzeugt noch neu gestartet (gleiche Container-ID und Startzeit vor und nach `up`).
* **Wie die API auf die Datenbank zugreift.** Nur als `app_user` (die für die Anwendung vorgesehene Rolle), mit reinem SQL,
  ohne ORM. Views und Funktionen werden benutzt, wo es sie gibt; alles andere läuft über die Tabellen.
* **Anmeldung.** Nur zum Testen: Der Aufrufer kommt ungeprüft aus dem Header `X-User-Id`, die Rolle lässt sich mit
  `X-Test-Role` überschreiben (alle Seed-Benutzer haben die Rolle `user`). API und Dashboard sind nur auf `127.0.0.1`
  erreichbar.
* **Zwei Testebenen, immer gegen eine Wegwerf-Datenbank** (`api/docker-compose.test.yml`: eigenes Compose-Projekt, eigene
  Datenbank aus demselben Image, keine veröffentlichten Ports). Die Entwicklungsdatenbank wird nie beschrieben.
  * *Ebene 1* (218 Tests): Jeder Test läuft in einer Transaktion, die am Ende zurückgerollt wird; jede Anweisung bekommt einen
    eigenen Savepoint, damit erwartete Datenbankfehler die Transaktion nicht vergiften.
  * *Ebene 2* (18 Tests, Marker `commit`): echter Verbindungspool, echte Commits, parallele Anfragen aus mehreren Threads,
    Sperren, Wiederherstellung nach getöteten Verbindungen, Start ohne Datenbank.
  * Jede Testsitzung lädt zuerst das Seed-Skript `Phase2/7createDummyData.sql` in die Wegwerf-Datenbank (nur mit
    ausdrücklichem Opt-in `ALLOW_TEST_DB_RESET=1`, der nur im Test-Compose gesetzt ist). So sind Wiederholungen
    unabhängig voneinander, IDs sind deterministisch und die relativen Seed-Datumsangaben sind frisch.
* **Tests mit Biss.** Der Test für die Löschsperre wurde per Mutation geprüft: Ohne `FOR UPDATE` im Löschpfad schlagen
  der Race-Test und der deterministische Sperr-Test fehl. Der Race-Test zeigte dabei echten Datenverlust: `(201, 204)`,
  also eine gerade angelegte Anfrage, die per `CASCADE` zusammen mit dem Exemplar gelöscht wurde (siehe F8).
* **Dashboard.** Swagger UI (`swaggerapi/swagger-ui:v5.34.1`) lädt die OpenAPI-Spezifikation der API im Browser und ist
  zugleich Dokumentation und Testumgebung (Authorize, "Try it out" für alle Methoden). Getestet mit Chrome (Playwright,
  Skript nicht Teil des Repos): Spezifikation lädt, 51 Operationen und 8 Gruppen sichtbar, Authorize inkl. Benutzerwechsel
  und Persistenz nach Reload, je ein Schreibzugriff mit `POST`, `PATCH`, `PUT` und `DELETE` (CORS-Preflight), von
  `localhost` und von `127.0.0.1` aus, alle Netzwerkziele nur `localhost`.

## 3. Abdeckung

Jede Tabelle, View und Funktion wird von mindestens einem Endpunkt benutzt, und jeder Endpunkt wird von mindestens einem
Test aufgerufen. Beides ist ausführbar abgesichert statt nur behauptet:
`test_every_table_view_and_function_of_the_database_is_used_by_the_api` gleicht den Katalog der Datenbank mit dem Quelltext
der API ab, und eine Middleware im Testlauf protokolliert die aufgerufenen Routen; mit `REQUIRE_FULL_COVERAGE=1` (im
Test-Compose gesetzt) schlägt der Lauf fehl, wenn eine Operation der OpenAPI-Spezifikation nie aufgerufen wurde
([`test_zz_coverage.py`](../api/tests/test_zz_coverage.py)).

| Datenbankobjekt | Endpunkte | Wichtigste Tests |
|---|---|---|
| **Tabelle** `Users` | `POST /users`, `GET/PATCH/DELETE /users/me`, `GET /users`, `GET/PATCH /users/{id}` | [`test_users.py`](../api/tests/test_users.py) |
| `CollectionAdresses` | `GET/POST /collection-addresses`, `PATCH/DELETE /collection-addresses/{id}` | [`test_addresses.py`](../api/tests/test_addresses.py) |
| `CollectionTimeWindows` | `GET/POST /collection-addresses/{id}/time-windows`, `DELETE /time-windows/{id}` | `test_addresses.py`, `test_db_design.py` |
| `UserAdresses` | `GET/POST /user-addresses`, `DELETE /user-addresses/{id}`, `GET /lents/{id}` | `test_addresses.py`, `test_lents.py` |
| `Authors`, `Books`, `BookAuthors` | `GET/POST /authors`, `GET /books`, `GET /books/{id}`, `POST /copies` | [`test_catalog.py`](../api/tests/test_catalog.py), [`test_copies.py`](../api/tests/test_copies.py) |
| `Book_copy` | `POST/PATCH/DELETE /copies`, `GET /copies/{id}` | `test_copies.py`, `test_commit.py` |
| `Lents` | `POST/GET /lents`, `GET /lents/{id}`, `accept`, `reject`, `hand-over`, `return` | [`test_lents.py`](../api/tests/test_lents.py) |
| `Shipping` | `PUT /lents/{id}/shipping`, `GET /lents/{id}` | `test_lents.py`, `test_commit.py` |
| `Reviews` | `POST /lents/{id}/review`, `GET /books/{id}/reviews` | `test_lents.py`, `test_catalog.py` |
| `Messages` | `POST/GET /messages`, `POST /messages/{id}/read` | [`test_messages.py`](../api/tests/test_messages.py) |
| `Protocols` | `GET /protocols`; geschrieben von allen Aktionen ohne Funktion | [`test_reports.py`](../api/tests/test_reports.py), `test_users.py` |
| **View** `available_books` | `GET /copies`, `GET /books`, `GET /books/{id}`, Feld `available` | `test_copies.py`, `test_catalog.py` |
| `fullBookInfo` | `GET /copies/{id}` | `test_copies.py` |
| `overdue_lents` | `GET /reports/overdue-lents` | `test_reports.py` |
| **Funktion** `create_book_request` | `POST /lents` | `test_lents.py` |
| `accept_book_request`, `reject_book_request`, `hand_over_book`, `return_book` | `POST /lents/{id}/accept`, `reject`, `hand-over`, `return` | `test_lents.py`, `test_commit.py` |
| `write_review` | `POST /lents/{id}/review` | `test_lents.py` |
| `create_collection_time_window` | `POST /collection-addresses/{id}/time-windows` | `test_addresses.py` |
| `book_must_be_returned` | `GET /lents/{id}/overdue` | `test_lents.py`, `test_reports.py` |
| `generate_monthly_report`, `generate_monthly_report_json` | `GET /reports/monthly`, `GET /reports/monthly/json` | `test_reports.py` |
| `getFullUserStats` | `GET /users/{id}/stats` | `test_users.py`, `test_lents.py` |
| `getAllBooksbyUser` | `GET /users/{id}/books` | `test_users.py` |
| `getAllLentsbyUser` | `GET /lents`, `GET /users/{id}/lents` | `test_users.py`, `test_lents.py` |

## 4. Was gut geplant war

Diese Regeln erzwingt die Datenbank selbst. Sie haben jeder Prüfung standgehalten (Tests `test_positive_*` in
[`test_db_design.py`](../api/tests/test_db_design.py) und die genannten Tests).

| Stärke | Beleg |
|---|---|
| **Doppelverleih-Schutz.** Der partielle Unique-Index `lents_one_open_per_copy` und die Funktionen verhindern, dass ein Exemplar zweimal angenommen oder verliehen wird. Mehrere offene *Anfragen* bleiben erlaubt. | `test_positive_one_open_lent_per_copy`; bei zwei gleichzeitigen Annahmen gewinnt in 6 von 6 Runden genau eine (`test_two_acceptances_for_one_copy_have_exactly_one_winner`) |
| **Atomare Statuswechsel.** Jede Funktion prüft Status und Besitzer und ändert den Status in einer einzigen Anweisung, das Protokoll wird in derselben Transaktion geschrieben. | Lebenszyklus-Test mit Protokollreihenfolge `create_request, accept_request, lending, returning, create_review`; eine gescheiterte Anfrage hinterlässt weder Ausleihe noch Protokolleintrag |
| **Besitzerprüfung in den Funktionen** als zweite Linie neben der API. | `test_f4_one_message_covers_missing_row_wrong_state_and_wrong_owner` |
| **Zeitfenster ohne Überschneidung** über den Exclusion-Constraint `no_overlapping_window`; das Ende zählt nicht mit (10–12 und 12–14 sind erlaubt). | `test_positive_overlapping_time_windows_are_rejected`, `test_adjacent_time_windows_are_allowed` |
| **Enums als Vertrag.** Alle 8 Enum-Typen der Datenbank sind in der API identisch abgebildet und erscheinen in Swagger als Auswahllisten. | `test_enums_of_the_api_match_the_database`, `GET /meta/contract` |
| **Prüfregeln und Eindeutigkeiten greifen:** Daten und Dauer der Ausleihe, Bewertung 1 bis 5, Koordinaten (beide oder keine), keine Nachricht an sich selbst, eine Bewertung je Ausleihe, eindeutige Sendungsnummer. Die API übersetzt sie in 409/422 mit dem Constraint-Namen. | `test_positive_check_constraints`, `test_positive_one_review_per_lent`, `test_message_to_yourself_is_refused_by_the_check_constraint` |
| **Das Protokoll ist append-only.** `app_user` darf nur lesen und anhängen. | `test_positive_protocols_are_append_only` |
| **Kein DDL für die Anwendung.** `app_user` kann das Schema nicht verändern. | `test_positive_the_api_role_cannot_change_the_schema` |
| **`ON DELETE RESTRICT` schützt Adressen und Bücher**, auf die noch Exemplare verweisen (409 statt Datenverlust). | `test_delete_is_refused_while_copies_use_the_address` |
| **Die vorhandenen Views und Funktionen passen zu den Hauptanwendungsfällen.** Suche in `available_books`, Listen und Statistik brauchten kaum eigenes SQL. | `test_search_*`, `test_stats_call_get_full_user_stats` |
| **Saubere Grundlagen:** durchgängig `timestamptz`, Identity-Primärschlüssel, Fremdschlüssel überall, ICU-Sortierung `de-DE`. | keine Auffälligkeiten in allen Tests |

## 5. Befunde

Alle Empfehlungen sind **nicht umgesetzt** (Vorgabe: SQL bleibt unangetastet). Wo ein Test das gewünschte Verhalten
beschreibt, ist er mit `xfail(strict=True)` markiert: Sobald das SQL korrigiert wird, schlägt der Lauf an und zeigt, dass der
Marker entfernt werden kann.

| ID | Befund | Schwere | Auswirkung auf die API | Was die API tut |
|---|---|---|---|---|
| [F1](#f1-der-init-bricht-ab-user_ranking-gibt-es-nicht) | Init bricht ab (`user_ranking` existiert nicht), View-`REVOKE` greift nie | hoch | API und Tests müssen mit einem Neustart der DB beim ersten Start leben | wartet selbst auf die DB, Pool übersteht Neustarts |
| [F2](#f2-dockerfile-nur-unter-windowsmacos-auffindbar) | Compose verweist auf `docker/Dockerfile`, getrackt ist `docker/dockerfile` | hoch (Linux, CI) | Test-Stack muss den echten Namen benutzen | benutzt `docker/dockerfile` |
| [F3](#f3-berechtigungsmodell-pauschale-rechte-identität-nur-als-parameter) | Pauschale Rechte, Identität nur als Parameter, `password_hash` lesbar | mittel | gesamte Autorisierung liegt in der API | Besitzerprüfung vor jedem Funktionsaufruf, nie `password_hash` lesen |
| [F4](#f4-fehler-nur-als-p0001-mit-freitext) | Fehler nur als `P0001` mit Freitext, mehrere Ursachen in einer Meldung | mittel | HTTP-Status nur per Regex auf englischen Text ableitbar | 8 Textmuster für 16 Meldungen, parametrisiert getestet; Existenz/Besitz vorab prüfen |
| [F5](#f5-fehlende-prüfregeln-auf-book_copy) | Keine CHECKs auf `max_lent_duration` und `shipping_cost` | mittel | Fehler erscheint erst bei der ersten Anfrage | validiert 1-120 Tage und 0-9,99 € |
| [F6](#f6-lücken-im-ausleih-workflow) | `Shipping.lent_id` nicht eindeutig, kein Versand-Anlegen, keine Lieferadresse, kein Storno, Besitzerstatus ignoriert | mittel | mehrere Workarounds | Sperren, eigene Regeln, siehe F6 |
| [F7](#f7-views-keine-koordinaten-personenbezogene-daten-uneinheitliche-filter) | Views ohne Koordinaten, mit personenbezogenen Daten, uneinheitliche Aktiv-Filter | niedrig | zusätzlicher Join, Ausblenden von Feldern | Haversine-Suche, Rollenprüfung |
| [F8](#f8-on-delete-cascade-vernichtet-die-historie) | `ON DELETE CASCADE` vernichtet Ausleihhistorie | mittel | gefährlich beim Löschen und bei Races | kein Hard-Delete von Benutzern, Löschpfad mit Sperre |
| [F9](#f9-updated_at-pflegt-niemand) | `updated_at` wird nicht gepflegt | niedrig | jeder andere Schreiber lässt es veralten | setzt es bei jedem `UPDATE` selbst |
| [F10](#f10-neun-fremdschlüsselspalten-ohne-index) | Neun Fremdschlüsselspalten ohne Index | niedrig | nicht gemessen | keine |
| [F11](#f11-keine-eindeutigkeit-für-autoren-bücher-und-e-mail-adressen) | Keine Eindeutigkeit für Autoren und Bücher, E-Mail nur exakt | mittel | find-or-create mit Sperren nötig | normalisierter Schlüssel und Advisory-Locks |
| [F12](#f12-namens--und-dokumentationsdrift) | Tippfehler im Vertrag, tote Verweise, veraltetes Testskript, Seed-Daten | niedrig | irreführend | Feldnamen 1:1 übernommen |
| [F13](#f13-seed-keine-moderatoren-platzhalter-passwörter) | Seed ohne Moderator/Admin, Platzhalter statt bcrypt | niedrig | Rollen und Login nicht testbar | `X-Test-Role` |
| [F14](#f14-datenbankport-auf-allen-schnittstellen) | Datenbankport 5432 auf allen Schnittstellen | mittel | betrifft nicht die API | API/Dashboard nur auf `127.0.0.1` |
| [F15](#f15-monatsbericht-semantik-und-fehlende-validierung) | Monatsbericht ohne Validierung, unscharfe Semantik | niedrig | falsche Eingaben liefern Nullen | validiert Jahr und Monat |

### F1: Der Init bricht ab, `user_ranking` gibt es nicht

*Schwere: hoch.*

* **Beleg.** Die letzte Zeile von [`Phase2/9grantPrivileges.sql`](../Phase2/9grantPrivileges.sql) lautet
  `REVOKE INSERT, UPDATE, DELETE ON available_books, overdue_lents, user_ranking FROM app_user;`. Eine View `user_ranking`
  gibt es in keiner Datei. [`docker/init.sh`](../docker/init.sh) arbeitet mit `set -e` und `psql -v ON_ERROR_STOP=1` und bricht
  an dieser Zeile ab: `psql:/sql/9grantPrivileges.sql:17: ERROR: relation "user_ranking" does not exist`.
* **Folge 1: Der erste Start endet mit einem Fehler.** Im Test- und im Scratch-Stack zeigt jeder frische Start
  `RestartCount=1`: Erst die Restart-Policy startet den Container neu, der zweite Start überspringt die Initialisierung
  (`Skipping initialization`). Dasselbe Log steht im Entwicklungs-Container. Ohne Restart-Policy (Wegwerf-Container ohne
  Volume) bleibt der Container nach der Initialisierung mit Exit-Code 3 beendet, die Datenbank ist dann nicht nutzbar, etwa
  in einer CI-Pipeline.
* **Folge 2: Das `REVOKE` greift nie.** `app_user` hat auf allen drei Views `INSERT`, `UPDATE` und `DELETE`, obwohl der
  Kommentar der Datei "Views are read only" sagt. In der Praxis ist das ungefährlich, weil keine der drei Views
  automatisch aktualisierbar ist (Test `test_f1_views_cannot_be_written_in_practice_because_they_are_not_updatable`). Auch
  mit korrektem Namen fehlt `fullBookInfo` in der Liste.
* **Was die API tut.** Sie startet unabhängig von der Datenbank, `/health` antwortet 503, bis die Datenbank erreichbar ist
  (`test_the_api_starts_without_a_database_and_answers_503`), und der Pool prüft Verbindungen beim Ausleihen, sodass ein
  Neustart der Datenbank unter laufender API folgenlos bleibt
  (`test_the_pool_survives_killed_connections`; im Container war die API 1,5 s nach `docker restart` der Datenbank wieder
  gesund). Compose benutzt deshalb `depends_on: db: service_started` statt `service_healthy`.
* **Empfehlung.** Letzte Zeile ersetzen durch
  `REVOKE INSERT, UPDATE, DELETE ON available_books, overdue_lents, fullBookInfo FROM app_user;` (oder `user_ranking` anlegen,
  falls geplant). Danach wird der `xfail`-Test `test_f1_desired_views_are_read_only_for_the_api_role` grün.

### F2: Dockerfile nur unter Windows/macOS auffindbar

*Schwere: hoch für Linux und CI.*

* **Beleg.** [`docker-compose.yml`](../docker-compose.yml) verweist im `db`-Service auf `docker/Dockerfile`, im Repository
  heißt die Datei `docker/dockerfile` (`git ls-files docker`). Auf einem case-sensitiven Dateisystem (Linux-Container,
  `git archive HEAD docker`) existiert nur `docker/dockerfile`; `docker/Dockerfile` fehlt. Folge: `docker compose build db`
  scheitert auf Linux (der Linux-Build selbst wurde nicht ausgeführt, das Fehlen der Datei ist geprüft).
* **Was die API tut.** Der Test-Stack verwendet den echten Namen `docker/dockerfile`.
* **Empfehlung.** Datei in `Dockerfile` umbenennen (`git mv`) oder den Verweis in der Compose-Datei anpassen.

### F3: Berechtigungsmodell: pauschale Rechte, Identität nur als Parameter

*Schwere: mittel.*

* **Beleg.** [`9grantPrivileges.sql`](../Phase2/9grantPrivileges.sql) vergibt
  `SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public` an `app_user`, eingeschränkt nur auf `Protocols`. Alle
  Funktionen sind `SECURITY INVOKER` und bekommen den handelnden Benutzer als Parameter (`p_user_id`): Eine Sitzung von
  `app_user` kann jede Aktion im Namen jedes Benutzers ausführen
  (`test_f3_every_function_takes_the_acting_user_as_a_parameter`: `hand_over_book(20, 10)`). `password_hash` ist für
  `app_user` lesbar (`has_column_privilege`), `DELETE` auf `Users` ist erlaubt (siehe F8).
* **Auswirkung.** Die gesamte Autorisierung (wer darf was) liegt in der API; ein Fehler dort ist sofort ein Datenleck, die
  Datenbank ist keine zweite Verteidigungslinie.
* **Was die API tut.** Die Identität kommt nur aus dem Header, IDs (Absender, Besitzer, Ersteller) nie aus dem Body, alle
  Eingabemodelle lehnen unbekannte Felder ab (`role`, `is_active`, `user_id` lassen sich nicht einschleusen), vor jedem
  Funktionsaufruf wird Besitz bzw. Beteiligung geprüft, `password_hash` wird nie selektiert.
* **Empfehlung.** `REVOKE SELECT (password_hash)`, `DELETE` auf `Users`, `Lents`, `Reviews`, `Messages` entziehen;
  mittelfristig Row-Level-Security mit `current_setting('app.user_id')` oder Funktionen, die den Benutzer selbst ermitteln.

### F4: Fehler nur als `P0001` mit Freitext

*Schwere: mittel.*

* **Beleg.** Alle Funktionen melden jedes Problem mit `RAISE EXCEPTION '…'` ohne `USING ERRCODE`: derselbe SQLSTATE `P0001`
  für "Exemplar existiert nicht", "eigenes Exemplar", "bereits verliehen" und "bereits bewertet"
  (`test_f4_all_function_errors_share_one_sqlstate_and_differ_only_in_the_message`). Die Meldung von `accept_book_request`
  fasst drei Ursachen zusammen: `… does not exist, is not in requested status or the copy does not belong to user …`
  (`test_f4_one_message_covers_missing_row_wrong_state_and_wrong_owner`).
* **Auswirkung.** Ein Client kann Fehler nur an englischem Freitext unterscheiden.
* **Was die API tut.** [`app/errors.py`](../api/app/errors.py) leitet den HTTP-Status aus 8 Textmustern ab, die die 16
  `RAISE EXCEPTION`-Meldungen der Funktionen abdecken (jede Meldung ist in `test_contract.py` parametrisiert getestet,
  unbekannte Texte werden zu 409). Weil "nicht gefunden", "falscher Status" und "nicht dein Exemplar" so nicht trennbar
  sind, prüft die API Existenz und Besitz vor dem Funktionsaufruf selbst (404/403) und überlässt der Funktion nur den
  Zustand (409).
* **Empfehlung.** Eigene SQLSTATEs je Ursache (`USING ERRCODE = 'BX404'` usw.), `DETAIL` mit den IDs, eine Meldung je Ursache.

### F5: Fehlende Prüfregeln auf `Book_copy`

*Schwere: mittel.*

* **Beleg.** `Book_copy.max_lent_duration` und `shipping_cost` haben keine CHECKs. Die Datenbank akzeptiert 0, -5 und 500
  Tage sowie -5 € Versandkosten (`test_f5_the_database_accepts_nonsense_lent_durations_and_costs`). Das Tutor-Feedback
  verlangte Prüfungen für positive Leihdauern, gültige Ausleihzeiträume und Versandkosten, die Antwort lautete "Prüfungen
  waren bereits vorhanden". Das stimmt für den Ausleihzeitraum (`lent_dates_valid`, höchstens 120 Tage in `Lents`) und für die
  Versandoption (geprüft in `create_book_request`), aber nicht für die Leihdauer und die Versandkosten am Exemplar.
* **Auswirkung.** Ein Exemplar mit 500 Tagen Leihdauer lässt sich anlegen. Erst die erste Anfrage scheitert mit dem rohen
  Constraint `lent_date_max_duration` der Ausleihe, und der Anfragende kann nichts dagegen tun
  (`test_f5_the_error_for_a_too_long_duration_appears_late_at_the_first_request`).
* **Was die API tut.** Sie begrenzt Leihdauer auf 1 bis 120 Tage und Versandkosten auf 0 bis 9,99 €.
* **Empfehlung.** `CHECK (max_lent_duration BETWEEN 1 AND 120)` und `CHECK (shipping_cost >= 0)`. Die beiden
  `xfail`-Tests `test_f5_desired_*` werden dann grün.

### F6: Lücken im Ausleih-Workflow

*Schwere: mittel.*

| | Befund | Beleg | Was die API tut |
|---|---|---|---|
| a | `Shipping.lent_id` ist nicht eindeutig: mehrere Versandzeilen je Ausleihe sind möglich | `test_f6_shipping_lent_id_is_not_unique` | sperrt die Ausleihe, hält höchstens eine Zeile, bei vorhandenen Duplikaten 409 (`test_parallel_shipping_updates_keep_a_single_row`, `test_a_shipping_update_waits_for_the_lock_on_the_lent`) |
| b | `hand_over_book` legt keinen Versand an, obwohl `shipping_requested` gesetzt ist | `test_f6_hand_over_does_not_create_the_shipping_row` | `PUT /lents/{id}/shipping` legt ihn an und führt `pending → shipped → delivered` mit Rollenregeln |
| c | Eine Ausleihe hat keine Lieferadresse | `test_f6_a_lent_has_no_delivery_address_and_a_request_cannot_be_cancelled` | liefert die jüngste Wohnadresse des Ausleihers, ausdrücklich als veränderlich und nicht als Momentaufnahme gekennzeichnet |
| d | Es gibt keinen Storno-Status: Eine Anfrage lässt sich nicht zurückziehen | derselbe Test (`lent_status` kennt kein `cancelled`) | nicht ausgleichbar, die Lücke bleibt |
| e | `create_book_request` ignoriert, ob der Besitzer aktiv ist, `available_books` filtert ihn | `test_f6_the_request_function_ignores_the_status_of_the_owner` | behandelt das Exemplar dann als nicht vorhanden (404, `test_requests_for_copies_of_deactivated_owners_are_refused_by_the_api`) |
| f | Für Registrierung, Bücher, Nachrichten und Versand gibt es keine Funktionen. Die Protokolleinträge dieser Aktionen entstehen deshalb in der API, die übrigen in den Funktionen | Tests der Protokolltypen `create_user`, `create_book`, `send_message`, `read_message` | schreibt das Protokoll in derselben Transaktion |

* **Empfehlung.** `UNIQUE (lent_id)` auf `Shipping`; `hand_over_book` legt bei `shipping_requested` die Versandzeile an;
  `lent_status` um `cancelled` und eine Funktion zum Zurückziehen erweitern; in `create_book_request` den Besitzerstatus
  prüfen; Lieferadresse mit der Ausleihe verknüpfen.

### F7: Views: keine Koordinaten, personenbezogene Daten, uneinheitliche Filter

*Schwere: niedrig.*

* `available_books` enthält keine Koordinaten, obwohl das Tutor-Feedback eine Umkreissuche verlangte. Die API verbindet
  deshalb zusätzlich `Book_copy` und `CollectionAdresses` und rechnet die Entfernung selbst (Haversine,
  `test_radius_search_uses_the_coordinates_of_the_collection_address`; Beleg: `test_f7_available_books_has_no_coordinates_*`).
* Personenbezogene Daten stehen in `overdue_lents.borrower_email`, `fullBookInfo.user_firstname/user_lastname` und
  `getFullUserStats.email` (`test_f7_views_and_functions_expose_personal_data`). Die API zeigt sie nur Besitzer,
  Betroffenen und Moderatoren/Admins.
* `fullBookInfo` filtert inaktive Besitzer nicht, `available_books` schon
  (`test_f7_full_book_info_does_not_filter_inactive_owners_but_available_books_does`). Die API blendet Exemplare inaktiver
  Besitzer für Dritte aus.
* Eine Bewertungs-/Ranking-View fehlt (`user_ranking` wird nur im `REVOKE` erwähnt, siehe F1). Die API rechnet den
  Durchschnitt je Buch selbst.
* **Empfehlung.** Koordinaten in `available_books` aufnehmen, einheitlicher Aktiv-Filter, Ranking-View anlegen oder die
  Erwähnung entfernen.

### F8: `ON DELETE CASCADE` vernichtet die Historie

*Schwere: mittel.*

* **Beleg.** Das Datenwörterbuch beschreibt es als Absicht: Löscht man einen Datensatz, werden abhängige mitgelöscht.
  `Lents.book_copie_id` → `Book_copy` sowie `Reviews.lent_id` und `Shipping.lent_id` → `Lents` laufen per `CASCADE`, ebenso
  `Lents.lent_to` und `Messages` → `Users`. Ein `DELETE` auf ein Exemplar löscht Ausleihen, Bewertungen und Versand.
  Das Löschen eines Ausleihers löscht dessen Ausleihen, Bewertungen und Nachrichten, das Löschen eines *Besitzers* ist dagegen
  über `RESTRICT` blockiert: asymmetrisch (`test_f8_deleting_a_borrower_wipes_the_lent_history_but_an_owner_cannot_be_deleted`).
  Die Protokolle bleiben, verlieren aber ihre `user_id` (`SET NULL`).
* **Echter Datenverlust im Test.** Wenn der Löschpfad der API das Exemplar nicht sperrt, kann eine gleichzeitig angelegte
  Anfrage zusammen mit dem Exemplar verschwinden (`(201, 204)` im Mutationstest, siehe Abschnitt 2).
* **Was die API tut.** Benutzer werden nur deaktiviert, nie gelöscht (409 bei offenen Ausleihen); ein Exemplar lässt sich nur
  ohne jede Ausleihe löschen, und der Löschpfad sperrt dieselbe Zeile wie `create_book_request`
  (`test_a_new_request_waits_for_the_lock_the_delete_handler_takes`,
  `test_deleting_a_copy_races_with_a_new_request_without_losing_history`).
* **Empfehlung.** Soft-Delete (`deleted_at`) oder `ON DELETE RESTRICT` für `Lents`, `Reviews`, `Shipping`; Löschrechte für
  `app_user` einschränken.

### F9: `updated_at` pflegt niemand

*Schwere: niedrig.* Das Datenwörterbuch beschreibt `updated_at` als "Zeitpunkt der letzten Änderung", aber es gibt keinen
Trigger: Ein `UPDATE` lässt den Wert unverändert (`test_f9_updated_at_is_not_maintained_by_the_database`). Die API setzt ihn bei
jedem `UPDATE` selbst. **Empfehlung:** `BEFORE UPDATE`-Trigger.

### F10: Neun Fremdschlüsselspalten ohne Index

*Schwere: niedrig, nicht gemessen.* Ohne Index (ein partieller Index zählt nicht) sind: `CollectionAdresses.user_id`,
`Lents.book_copie_id`, `Lents.lent_to`, `Messages.sender_id`, `Messages.receiver_id`, `Protocols.book_copie_id`,
`Protocols.user_id`, `Shipping.lent_id`, `UserAdresses.user_id` (`test_f10_foreign_keys_without_a_supporting_index`). Bei
10 Benutzern ist das unsichtbar; bei vielen Daten betrifft es `getAllLentsbyUser`, Nachrichtenlisten und die
`CASCADE`-Löschungen. **Empfehlung:** Indizes anlegen und mit `EXPLAIN` an realistischen Datenmengen prüfen.

### F11: Keine Eindeutigkeit für Autoren, Bücher und E-Mail-Adressen

*Schwere: mittel.* `Authors.name` und `Books` haben keine Eindeutigkeitsregel, `Users.email` ist nur exakt eindeutig:
`ALICE@EXAMPLE.COM` neben `alice@example.com` ist erlaubt (`test_f11_the_schema_does_not_identify_authors_books_or_emails_uniquely`).

* **Was die API tut.** Autoren und Bücher werden per find-or-create über einen normalisierten Schlüssel (Titel plus Menge
  der Autorennamen) gefunden und mit Advisory-Locks serialisiert: parallel angelegte Exemplare desselben Buchs ergeben genau
  ein Buch und einen Autor (`test_parallel_copy_creation_finds_one_book_and_one_author`,
  `test_parallel_author_creation_is_find_or_create`). Mehrere vorhandene Treffer werden gemeldet (409), nicht geraten. Die
  E-Mail wird ohne Beachtung der Schreibweise geprüft; ohne Unique-Index auf `lower(email)` ist diese Prüfung bei zwei
  gleichzeitigen Registrierungen mit unterschiedlicher Schreibweise nicht lückenlos.
* **Empfehlung.** `UNIQUE (lower(email))`, ein Schlüssel für Bücher (z. B. ISBN), Entscheidung über Autoren-Eindeutigkeit.

### F12: Namens- und Dokumentationsdrift

*Schwere: niedrig.*

* **Tippfehler im Vertrag**, die die API 1:1 übernimmt (Rückverfolgbarkeit): `book_copie_id`, `streetnummber`, `Lents`,
  `CollectionAdresses`, `UserAdresses`. Funktionen und Views in gemischter Schreibweise werden von PostgreSQL in Kleinbuchstaben
  gefaltet (`"getFullUserStats"` in Anführungszeichen existiert nicht, `test_f12_mixed_case_function_names_are_folded_to_lower_case`).
* **Tote Verweise:** [`Datenwoerterbuch.txt`](../Phase1/Datenwoerterbuch.txt) nennt `SQL/Phase2/5createTables.sql`,
  `SQL/Phase2/6createFunctions.sql` und `ERM_BookExchange.puml` (tatsächlich `Phase2/4createTables.sql`,
  `5createFunctions.sql`, `Phase1/ERM.puml`); [`1createRolesAndUser.sql`](../Phase2/1createRolesAndUser.sql) nennt
  `8.1grantPrivileges.sql` (tatsächlich `9grantPrivileges.sql`).
* **Veraltetes Testskript:** [`6testFunctions.sql`](../Phase2/6testFunctions.sql) setzt `lent_id => 23` voraus (der Seed hat 21
  Ausleihen, die nächste wäre 22) und nennt Benutzer 4 "Alice (Besitzerin)", im Seed ist es Olaf.
* **Repository-Hygiene:** [`.gitattributes`](../.gitattributes) hat in Zeile 1 einen Kommentar hinter dem Attribut, Git warnt bei
  jedem Aufruf (`# is not a valid attribute name`). [`.dockerignore`](../.dockerignore) nennt drei Dateien, die es nicht gibt
  (`6.1testFunctions.sql`, `9createTestQueries.sql`, `createFramesAndWindow.sql`); deshalb liegen `6testFunctions.sql` und
  `10createTestQueries.sql` im Image unter `/sql`.
* **Seed-Daten:** Alle Abholadressen liegen in "Kassel", haben aber US-Koordinaten und US-Postleitzahlen. Autoren und
  Nachrichten werden im Seed ohne `ORDER BY` eingefügt, ihre IDs folgen deshalb nicht der Reihenfolge der Liste (Tests nutzen
  Namen statt IDs).

### F13: Seed: keine Moderatoren, Platzhalter-Passwörter

*Schwere: niedrig.* Alle 10 Seed-Benutzer haben die Rolle `user` (`test_f2_the_seed_has_no_moderator_and_no_admin`) und
Platzhalter wie `hashed_password_1` statt bcrypt-Hashes, wie das Datenwörterbuch es beschreibt
(`test_f13_the_seed_passwords_are_placeholders_so_a_login_cannot_be_tested`). Rollenprüfungen und ein echter Login sind damit
nicht testbar; die API behilft sich mit `X-Test-Role` und hat bewusst keinen Login. **Empfehlung:** mindestens einen
Moderator und einen Admin in den Seed aufnehmen.

### F14: Datenbankport auf allen Schnittstellen

*Schwere: mittel.* `ports: ["5432:5432"]` im `db`-Service veröffentlicht die Datenbank auf `0.0.0.0` und `[::]`
(`docker ps`), also auch für andere Rechner im Netzwerk, einschließlich des Superusers `postgres`. Das betrifft die
bestehende Compose-Datei und wurde nicht geändert. API und Dashboard sind dagegen nur an `127.0.0.1` gebunden.
**Empfehlung:** `"127.0.0.1:5432:5432"`.

### F15: Monatsbericht: Semantik und fehlende Validierung

*Schwere: niedrig.* `generate_monthly_report(2026, 13)` liefert stillschweigend Nullen statt eines Fehlers. `total_users` zählt
die Neuregistrierungen des Monats, nicht alle Benutzer; `total_active_lents` und `total_overdue_lents` zählen Ausleihen, die
*heute* aktiv bzw. überfällig sind und im Monat angefragt wurden, keine Momentaufnahme des Monats. `EXTRACT(...)` auf den
Spalten verhindert Indexnutzung. Die API validiert Jahr (2000 bis 2100) und Monat (1 bis 12); die Zahlen werden gegen
unabhängiges SQL geprüft (`test_monthly_report_function_and_json_function_agree`).

## 6. Was die API ausgleichen musste

| Lücke | Ausgleich in der API | Datei |
|---|---|---|
| Datenbank vertraut `p_user_id` (F3) | Besitz- und Beteiligungsprüfung vor jedem Funktionsaufruf, IDs nur aus dem Header | [`routers/lents.py`](../api/app/routers/lents.py), [`copies.py`](../api/app/routers/copies.py) |
| Nur `P0001` mit Freitext (F4) | Statusableitung per Textmuster, Existenzprüfung vorab | [`errors.py`](../api/app/errors.py) |
| Keine CHECKs auf `Book_copy` (F5) | Eingabevalidierung | [`schemas.py`](../api/app/schemas.py) |
| `Shipping.lent_id` nicht eindeutig (F6a) | Zeilensperre auf der Ausleihe | `routers/lents.py` |
| Kein Versand-Anlegen, keine Lieferadresse (F6b, c) | `PUT .../shipping`, jüngste Wohnadresse (gekennzeichnet) | `routers/lents.py` |
| Besitzerstatus in `create_book_request` (F6e) | Vorprüfung, 404 | `routers/lents.py` |
| Kein Protokoll für Aktionen ohne Funktion (F6f) | eigene Einträge in derselben Transaktion | alle Router |
| Koordinaten fehlen in `available_books` (F7) | Join und Haversine | [`routers/copies.py`](../api/app/routers/copies.py) |
| Cascade und Races beim Löschen (F8) | Zeilensperre, Prüfung aller Ausleihen, kein Hard-Delete von Benutzern | `routers/copies.py`, [`users.py`](../api/app/routers/users.py) |
| `updated_at` ohne Trigger (F9) | explizit setzen | alle Router |
| Keine Eindeutigkeit (F11) | find-or-create mit normalisiertem Schlüssel und Advisory-Locks | [`routers/catalog.py`](../api/app/routers/catalog.py) |
| Erster Start der DB mit Fehler (F1) | Pool mit Verbindungsprüfung, Start ohne Datenbank, `/health` 503 | [`db.py`](../api/app/db.py) |

## 7. Grenzen dieser Prüfung

* **Keine Last- und Performancemessung.** F10 ist nicht quantifiziert; bei 10 Benutzern und 21 Ausleihen wäre jeder Befund
  zur Geschwindigkeit Spekulation.
* **Kein echter Login** (F13): Passwörter, bcrypt und Sitzungen sind nicht Teil dieser Prüfung.
* **Eine Datenbankversion** (PostgreSQL 17.11, ICU `de-DE`) und ein Betriebssystem (Windows mit Docker Desktop). Das Verhalten
  auf Linux (F2) ist aus dem Dateisystemverhalten abgeleitet, nicht auf einem Linux-Host gebaut.
* **Rollback-Tests und `now()`.** In Ebene 1 steht `now()` für die Dauer des Tests still (Beginn der Transaktion). Aussagen
  über sich ändernde Zeitstempel (`updated_at`) werden deshalb in Ebene 2 mit echten Commits geprüft.
* **Seed-Daten sind relativ** zum Zeitpunkt des Datenbankstarts. Tests, die Fristen betreffen, setzen die Daten selbst.
* **Dashboard nur mit Chrome** geprüft; das Skript ist nicht Teil des Repositorys.
* **Fachliche Fragen**, etwa wie konkrete Abholtermine zugeordnet werden sollen (Tutor-Feedback), sind mit der vorhandenen
  Struktur (wiederkehrende Wochentag-Zeitfenster) nur teilweise beantwortbar; die API zeigt die Fenster, ordnet aber keinen
  Termin zu.

## 8. Reproduzieren

```bash
# Datenbank, API und Dashboard starten (Dashboard: http://localhost:8080)
cp example.env .env
docker compose up -d --build

# Tests (Wegwerf-Stack, berührt die Entwicklungsdatenbank nicht)
docker compose -f api/docker-compose.test.yml run --rm tests
docker compose -f api/docker-compose.test.yml down -v
```

Der Testlauf führt zuerst die Rollback-Tests mit `REQUIRE_FULL_COVERAGE=1` aus (alle Operationen müssen aufgerufen werden),
danach die Tests mit echten Commits.

## Anhang A: Alle Endpunkte

Alle Operationen außer `GET /`, `GET /health`, `GET /meta/*`, `POST /users`, `GET /authors`, `GET /books*` und `GET /copies`
verlangen den Header `X-User-Id`. Berichte, Protokoll und Benutzerliste verlangen zusätzlich die Rolle Moderator/Admin (in
den Tests und im Dashboard per `X-Test-Role`).

| Gruppe | Endpunkt | Zweck |
|---|---|---|
| Adressen | `GET /collection-addresses` | Eigene Abholadressen |
| Adressen | `POST /collection-addresses` | Abholadresse anlegen |
| Adressen | `DELETE /collection-addresses/{address_id}` | Abholadresse löschen |
| Adressen | `PATCH /collection-addresses/{address_id}` | Abholadresse ändern |
| Adressen | `GET /collection-addresses/{address_id}/time-windows` | Abholzeitfenster einer Adresse |
| Adressen | `POST /collection-addresses/{address_id}/time-windows` | Abholzeitfenster anlegen |
| Adressen | `DELETE /time-windows/{window_id}` | Abholzeitfenster löschen |
| Adressen | `GET /user-addresses` | Eigene Wohnadressen |
| Adressen | `POST /user-addresses` | Wohnadresse anlegen |
| Adressen | `DELETE /user-addresses/{address_id}` | Wohnadresse löschen |
| Ausleihen | `GET /lents` | Meine Ausleihen |
| Ausleihen | `POST /lents` | Buch anfragen |
| Ausleihen | `GET /lents/{lent_id}` | Ausleihe im Detail |
| Ausleihen | `POST /lents/{lent_id}/accept` | Anfrage annehmen (Besitzer) |
| Ausleihen | `POST /lents/{lent_id}/hand-over` | Übergabe bestätigen (Besitzer) |
| Ausleihen | `GET /lents/{lent_id}/overdue` | Ist die Rückgabe überfällig? |
| Ausleihen | `POST /lents/{lent_id}/reject` | Anfrage ablehnen (Besitzer) |
| Ausleihen | `POST /lents/{lent_id}/return` | Rückgabe bestätigen (Besitzer) |
| Ausleihen | `POST /lents/{lent_id}/review` | Bewertung schreiben (Ausleiher) |
| Ausleihen | `PUT /lents/{lent_id}/shipping` | Versandstatus setzen |
| Benutzer | `GET /users` | Benutzer suchen (Moderator/Admin) |
| Benutzer | `POST /users` | Registrieren (ohne Passwort) |
| Benutzer | `GET /users/{user_id}` | Benutzer ansehen |
| Benutzer | `PATCH /users/{user_id}` | Rolle oder Status ändern (Admin) |
| Benutzer | `GET /users/{user_id}/books` | Exemplare, die ein Benutzer anbietet |
| Benutzer | `GET /users/{user_id}/lents` | Ausleihen eines Benutzers |
| Benutzer | `GET /users/{user_id}/stats` | Statistik eines Benutzers |
| Benutzer | `DELETE /users/me` | Eigenes Konto deaktivieren |
| Benutzer | `GET /users/me` | Eigenes Profil |
| Benutzer | `PATCH /users/me` | Eigenes Profil ändern |
| Berichte | `GET /protocols` | Ereignisprotokoll |
| Berichte | `GET /reports/monthly` | Monatsbericht |
| Berichte | `GET /reports/monthly/json` | Monatsbericht (JSON-Funktion) |
| Berichte | `GET /reports/overdue-lents` | Überfällige Ausleihen |
| Exemplare | `GET /copies` | Verfügbare Exemplare suchen (mit Umkreissuche) |
| Exemplare | `POST /copies` | Exemplar anbieten |
| Exemplare | `DELETE /copies/{book_copie_id}` | Exemplar löschen |
| Exemplare | `GET /copies/{book_copie_id}` | Exemplar im Detail |
| Exemplare | `PATCH /copies/{book_copie_id}` | Exemplar ändern |
| Katalog | `GET /authors` | Autoren suchen |
| Katalog | `POST /authors` | Autor anlegen oder finden |
| Katalog | `GET /books` | Bücher suchen |
| Katalog | `GET /books/{book_id}` | Buch mit verfügbaren Exemplaren |
| Katalog | `GET /books/{book_id}/reviews` | Bewertungen eines Buchs |
| Nachrichten | `GET /messages` | Meine Nachrichten |
| Nachrichten | `POST /messages` | Nachricht senden |
| Nachrichten | `POST /messages/{message_id}/read` | Als gelesen markieren (Empfänger) |
| System | `GET /` | Startseite mit Links |
| System | `GET /health` | Gesundheitscheck (inkl. Datenbank) |
| System | `GET /meta/contract` | Vertrag API <-> Datenbank prüfen |
| System | `GET /meta/enums` | Enum-Typen der Datenbank |

## Anhang B: Messwerte und Belege

| Messung | Ergebnis |
|---|---|
| Frischer Start des gesamten Stacks (`docker compose up -d --build`, leeres Volume, Images gebaut) | alle drei Container nach 14 bis 17 s bereit, Datenbank dabei einmal neu gestartet (F1) |
| Neustart der Datenbank unter laufender API | `/health` nach 1,5 s wieder 200 |
| Vollständiger Testlauf (Rollback-Tests plus Tests mit Commits) | rund 30 s, wiederholbar auf derselben Datenbank (Reset per Seed-Skript) |
| `pip-audit` (Laufzeit- und Test-Abhängigkeiten, alle gepinnt) | keine bekannten Schwachstellen |
| `gitleaks` über alle neuen und geänderten Dateien | keine Funde |
| Container der API | läuft als Benutzer `appuser` (UID 10001), Quellcode gehört `root` |
| Dashboard auf dem laufenden Stack (nur lesend) | 15 von 15 Prüfungen |
| Dashboard auf frischem Stack (alle Methoden, zwei Ursprünge) | 22 von 22 und 15 von 15 Prüfungen; Preflight für `POST`, `PUT`, `PATCH`, `DELETE` von `localhost` und `127.0.0.1` erlaubt, fremde Ursprünge: 400 |
| Umfang | `api/app`: rund 2 700 Zeilen, `api/tests`: rund 1 900 Zeilen |
