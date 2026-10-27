-- =====================================================================
--  Beispiel-Queries zum Schema aus 010_schema.sql
--  Zeigt die Stellen, an denen Postgres anders (oder mehr) kann als MySQL.
--  Nicht zum Einspielen gedacht -- zum Ausprobieren in DBeaver.
-- =====================================================================

SET search_path TO app, public;


-- ---------------------------------------------------------------------
-- INSERT ... RETURNING  (gibt es in MySQL nicht)
-- ---------------------------------------------------------------------
-- Kein "SELECT LAST_INSERT_ID()" als zweites Query mehr nötig.
INSERT INTO users (email, display_name, password_hash)
VALUES ('Jakob@Example.com', 'Jakob', 'dummy-hash')
RETURNING id, email, created_at;

-- Funktioniert auch bei UPDATE und DELETE:
UPDATE users
   SET status = 'active'
 WHERE email = 'jakob@example.com'
RETURNING id, status, updated_at;


-- ---------------------------------------------------------------------
-- UPSERT:  ON CONFLICT  statt  ON DUPLICATE KEY UPDATE
-- ---------------------------------------------------------------------
-- Der Konfliktziel-Ausdruck ist explizit -- kein Raten, welcher Unique
-- Key gerade gegriffen hat.
INSERT INTO users (email, display_name, password_hash)
VALUES ('jakob@example.com', 'Jakob (neu)', 'anderer-hash')
ON CONFLICT (email) DO UPDATE
   SET display_name = EXCLUDED.display_name,
       updated_at   = now()
RETURNING id, display_name;

-- Nur einfügen wenn nicht vorhanden, sonst still ignorieren
-- (= INSERT IGNORE):
INSERT INTO users (email, display_name, password_hash)
VALUES ('jakob@example.com', 'egal', 'egal')
ON CONFLICT (email) DO NOTHING;


-- ---------------------------------------------------------------------
-- citext: Groß-/Kleinschreibung
-- ---------------------------------------------------------------------
-- Trifft, weil email vom Typ citext ist:
SELECT id, email FROM users WHERE email = 'JAKOB@EXAMPLE.COM';

-- Bei normalem text müsstest du entweder ILIKE nehmen ...
SELECT id, display_name FROM users WHERE display_name ILIKE 'jakob';
-- ... oder lower() -- dann greift der Expression-Index aus dem Schema:
SELECT id, name FROM projects WHERE lower(name) = 'mein projekt';


-- ---------------------------------------------------------------------
-- Arrays
-- ---------------------------------------------------------------------
INSERT INTO projects (owner_id, name, slug, tags)
VALUES (1, 'VPS Setup', 'vps-setup', ARRAY['docker', 'wireguard', 'postgres'])
RETURNING id, tags;

-- Enthält Tag? (nutzt den GIN-Index)
SELECT name FROM projects WHERE tags @> ARRAY['docker'];

-- Enthält mindestens einen von mehreren?
SELECT name FROM projects WHERE tags && ARRAY['docker', 'nginx'];

-- Array in Zeilen auffalten -- z.B. für eine Tag-Statistik:
SELECT tag, count(*) AS anzahl
  FROM projects, unnest(tags) AS tag
 GROUP BY tag
 ORDER BY anzahl DESC;

-- Tag hinzufügen, ohne Duplikat:
UPDATE projects
   SET tags = array_append(tags, 'ufw')
 WHERE slug = 'vps-setup'
   AND NOT tags @> ARRAY['ufw'];


-- ---------------------------------------------------------------------
-- JSONB
-- ---------------------------------------------------------------------
INSERT INTO events (project_id, kind, payload, source_ip)
VALUES (1, 'deploy', '{"level":"info","service":"api","duration_ms":842}', '10.8.0.2')
RETURNING id;

--  ->   liefert jsonb,   ->>  liefert text
SELECT payload ->> 'service'                     AS service,
       (payload ->> 'duration_ms')::int          AS dauer_ms
  FROM events
 WHERE kind = 'deploy';

-- Containment @> -- das ist der Operator, den der GIN-Index bedient:
SELECT id, occurred_at FROM events
 WHERE payload @> '{"level":"error"}';

-- Schlüssel vorhanden?
SELECT id FROM events WHERE payload ? 'duration_ms';

-- Verschachtelt (Pfad):
SELECT id FROM events WHERE payload #>> '{meta,user,id}' = '42';

-- Teilweise aktualisieren, Rest behalten:
UPDATE users
   SET settings = settings || '{"theme":"dark"}'::jsonb
 WHERE id = 1;

-- Schlüssel entfernen:
UPDATE users SET settings = settings - 'theme' WHERE id = 1;


-- ---------------------------------------------------------------------
-- Zeit / timestamptz
-- ---------------------------------------------------------------------
SELECT now(),                        -- mit Zeitzone
       now() AT TIME ZONE 'Europe/Berlin',
       now() - interval '7 days'  AS vor_einer_woche,
       date_trunc('day', now())   AS heute_00_uhr;

-- Events pro Tag der letzten Woche (nutzt den BRIN-Index):
SELECT date_trunc('day', occurred_at) AS tag, count(*)
  FROM events
 WHERE occurred_at >= now() - interval '7 days'
 GROUP BY tag
 ORDER BY tag;


-- ---------------------------------------------------------------------
-- inet: IP-Adressen
-- ---------------------------------------------------------------------
-- Alle Events aus deinem WireGuard-Netz:
SELECT id, source_ip FROM events WHERE source_ip <<= '10.8.0.0/24';


-- ---------------------------------------------------------------------
-- Sonstige Syntax-Unterschiede
-- ---------------------------------------------------------------------
SELECT display_name || ' <' || email || '>' AS kontakt FROM users;  -- ||  statt CONCAT
SELECT '42'::int + 1;                                               -- ::  statt CAST
SELECT * FROM users ORDER BY id LIMIT 20 OFFSET 10;                 -- nicht LIMIT 10, 20

-- Aggregat als Liste (MySQL: GROUP_CONCAT):
SELECT u.display_name, string_agg(p.name, ', ' ORDER BY p.name) AS projekte
  FROM users u
  JOIN projects p ON p.owner_id = u.id
 GROUP BY u.display_name;

-- Aggregat als JSON -- praktisch, wenn die API direkt verschachteltes
-- JSON ausliefern soll, ohne im Code zusammenzubauen:
SELECT jsonb_build_object(
           'id',       u.id,
           'email',    u.email,
           'projects', coalesce(jsonb_agg(
                           jsonb_build_object('name', p.name, 'tags', p.tags)
                       ) FILTER (WHERE p.id IS NOT NULL), '[]'::jsonb)
       ) AS user_json
  FROM users u
  LEFT JOIN projects p ON p.owner_id = u.id
 GROUP BY u.id, u.email;


-- ---------------------------------------------------------------------
-- Introspektion: die SQL-Entsprechungen zu den psql-Backslash-Befehlen
-- ---------------------------------------------------------------------
--  \dt   Tabellen auflisten
SELECT table_schema, table_name
  FROM information_schema.tables
 WHERE table_schema = 'app'
 ORDER BY table_name;

--  \d+ users   Spalten einer Tabelle
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = 'app' AND table_name = 'users'
 ORDER BY ordinal_position;

--  \di   Indizes
SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'app';

-- Tabellengrößen inkl. Indizes:
SELECT relname AS tabelle,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS groesse
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'app' AND c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC;

-- Prüfen, ob ein Index wirklich benutzt wird (statt EXPLAIN raten):
EXPLAIN ANALYZE
SELECT name FROM projects WHERE tags @> ARRAY['docker'];