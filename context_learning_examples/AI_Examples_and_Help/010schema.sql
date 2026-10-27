-- =====================================================================
--  Beispiel-Schema im Postgres-Stil
--  Idempotent: kann beliebig oft neu eingespielt werden, ohne zu brechen.
--  Aufruf z.B.:  psql -h 10.8.0.1 -U app -d appdb -v ON_ERROR_STOP=1 -f 010_schema.sql
-- =====================================================================

-- Alles in EINER Transaktion. Postgres kann DDL transaktional -- wenn
-- irgendwo unten ein Fehler auftritt, ist der komplette Stand wieder wie
-- vorher. In MySQL nicht möglich (implizites COMMIT bei jedem DDL).
BEGIN;

-- ---------------------------------------------------------------------
-- 1) Extensions
-- ---------------------------------------------------------------------
-- citext = case-insensitiver TEXT. Bildet das MySQL-Verhalten nach, das
-- du gewohnt bist ('Foo@Bar.de' = 'foo@bar.de').
CREATE EXTENSION IF NOT EXISTS citext;

-- pg_trgm = Trigram-Suche, ermöglicht schnelle LIKE '%...%'-Abfragen.
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ---------------------------------------------------------------------
-- 2) Schema
-- ---------------------------------------------------------------------
-- Statt "mehrere Datenbanken" wie in MySQL: mehrere Schemas in EINER DB.
-- Vorteil: Joins über Schemagrenzen hinweg funktionieren ganz normal.
CREATE SCHEMA IF NOT EXISTS app;

SET search_path TO app, public;


-- ---------------------------------------------------------------------
-- 3) Eigene Typen
-- ---------------------------------------------------------------------
-- CREATE TYPE kennt kein IF NOT EXISTS -> DO-Block als Guard.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'user_status' AND n.nspname = 'app'
    ) THEN
        CREATE TYPE app.user_status AS ENUM ('pending', 'active', 'suspended');
    END IF;
END
$$;

-- Neue Werte später anhängen (auch idempotent, seit PG 12):
--   ALTER TYPE app.user_status ADD VALUE IF NOT EXISTS 'archived';


-- ---------------------------------------------------------------------
-- 4) Hilfsfunktion + Trigger für updated_at
-- ---------------------------------------------------------------------
-- MySQL hat "ON UPDATE CURRENT_TIMESTAMP" direkt in der Spaltendefinition.
-- Postgres macht das über einen Trigger -- einmal schreiben, überall nutzen.
CREATE OR REPLACE FUNCTION app.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


-- ---------------------------------------------------------------------
-- 5) Tabellen
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS app.users (
    -- Moderner Ersatz für AUTO_INCREMENT / SERIAL.
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- citext statt VARCHAR: Vergleiche ignorieren Groß-/Kleinschreibung.
    -- Kein VARCHAR(255)-Reflex nötig, TEXT ist in Postgres gleich schnell.
    email         citext      NOT NULL UNIQUE,
    display_name  text        NOT NULL,
    password_hash text        NOT NULL,

    -- Echter Boolean, nicht TINYINT(1).
    is_admin      boolean     NOT NULL DEFAULT false,

    status        app.user_status NOT NULL DEFAULT 'pending',

    -- JSONB: binär gespeichert, indexierbar, mit reichhaltigen Operatoren.
    settings      jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- IMMER timestamptz, nie timestamp. Speichert intern UTC und rechnet
    -- bei der Ausgabe in die Session-Zeitzone um -> keine DST-Bugs.
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,          -- NULL = nicht gelöscht (Soft Delete)

    -- Benannte CHECK-Constraints: der Name taucht in Fehlermeldungen auf,
    -- die API kann darauf sauber reagieren.
    CONSTRAINT users_display_name_not_blank
        CHECK (length(btrim(display_name)) > 0),
    CONSTRAINT users_email_looks_valid
        CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
);

COMMENT ON TABLE  app.users            IS 'Benutzerkonten der Anwendung';
COMMENT ON COLUMN app.users.deleted_at IS 'Soft Delete: NULL = aktiv';


CREATE TABLE IF NOT EXISTS app.projects (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    owner_id    bigint      NOT NULL
                REFERENCES app.users(id) ON DELETE CASCADE,

    name        text        NOT NULL,
    slug        text        NOT NULL,

    -- Array als echter Spaltentyp -- keine Kommaliste, keine Join-Tabelle
    -- für einfache Fälle nötig.
    tags        text[]      NOT NULL DEFAULT '{}',

    is_public   boolean     NOT NULL DEFAULT false,

    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    -- Slug nur pro Benutzer eindeutig.
    CONSTRAINT projects_owner_slug_unique UNIQUE (owner_id, slug)
);


CREATE TABLE IF NOT EXISTS app.events (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id  bigint      REFERENCES app.projects(id) ON DELETE SET NULL,
    kind        text        NOT NULL,

    -- Halbstrukturierte Payload, pro Event-Typ unterschiedlich.
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Eigener Typ für IP-Adressen: validiert beim INSERT und kann
    -- Subnetz-Vergleiche (payload <<= '10.8.0.0/24').
    source_ip   inet,

    occurred_at timestamptz NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------------------
-- 6) Spalten nachträglich hinzufügen (Migrationsmuster)
-- ---------------------------------------------------------------------
-- Beim nächsten Feature einfach hier unten anhängen -- durch
-- IF NOT EXISTS bleibt die Datei beliebig oft ausführbar.
ALTER TABLE app.users
    ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

-- Constraints kennen kein IF NOT EXISTS -> ebenfalls per Guard:
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'projects_name_not_blank'
          AND conrelid = 'app.projects'::regclass
    ) THEN
        ALTER TABLE app.projects
            ADD CONSTRAINT projects_name_not_blank
            CHECK (length(btrim(name)) > 0);
    END IF;
END
$$;


-- ---------------------------------------------------------------------
-- 7) Trigger
-- ---------------------------------------------------------------------
-- CREATE TRIGGER kennt kein IF NOT EXISTS -> erst droppen, dann anlegen.
DROP TRIGGER IF EXISTS users_set_updated_at ON app.users;
CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON app.users
    FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

DROP TRIGGER IF EXISTS projects_set_updated_at ON app.projects;
CREATE TRIGGER projects_set_updated_at
    BEFORE UPDATE ON app.projects
    FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();


-- ---------------------------------------------------------------------
-- 8) Indizes
-- ---------------------------------------------------------------------

-- Partieller Index: enthält nur die nicht-gelöschten Zeilen. Kleiner,
-- schneller, und genau das, wonach die App normalerweise filtert.
CREATE INDEX IF NOT EXISTS users_active_idx
    ON app.users (created_at DESC)
    WHERE deleted_at IS NULL;

-- Fremdschlüssel bekommen in Postgres KEINEN automatischen Index
-- (anders als in MySQL/InnoDB). Muss man selbst anlegen.
CREATE INDEX IF NOT EXISTS projects_owner_id_idx
    ON app.projects (owner_id);

-- Expression-Index: macht z.B. WHERE lower(name) = '...' schnell.
CREATE INDEX IF NOT EXISTS projects_name_lower_idx
    ON app.projects (lower(name));

-- GIN auf Array: WHERE tags @> ARRAY['docker']
CREATE INDEX IF NOT EXISTS projects_tags_gin_idx
    ON app.projects USING gin (tags);

-- GIN auf JSONB: WHERE payload @> '{"level":"error"}'
-- jsonb_path_ops ist kleiner und schneller, kann dafür nur @>.
CREATE INDEX IF NOT EXISTS events_payload_gin_idx
    ON app.events USING gin (payload jsonb_path_ops);

-- Trigram-Index für Substring-Suche: WHERE name ILIKE '%wire%'
CREATE INDEX IF NOT EXISTS projects_name_trgm_idx
    ON app.projects USING gin (name gin_trgm_ops);

-- BRIN statt B-Tree für sehr große, chronologisch wachsende Tabellen.
-- Winzig (ein paar KB statt hunderte MB), dafür nur für Bereichsabfragen.
CREATE INDEX IF NOT EXISTS events_occurred_at_brin_idx
    ON app.events USING brin (occurred_at);


-- ---------------------------------------------------------------------
-- 9) Rolle für die API (least privilege)
-- ---------------------------------------------------------------------
-- Die API sollte nicht als Superuser arbeiten.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api') THEN
        -- Passwort danach separat setzen, nicht im versionierten Schema:
        --   ALTER ROLE api WITH PASSWORD '...';
        CREATE ROLE api LOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA app TO api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO api;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO api;

-- Gilt auch für Tabellen, die erst in Zukunft angelegt werden:
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO api;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT USAGE ON SEQUENCES TO api;


COMMIT;