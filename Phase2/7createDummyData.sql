-- Active: 1787569567255@@10.8.0.1@5432@buechertausch

-- Alle Zeitangaben sind relativ zu now() (x_days_ago), damit Abfragen wie "überfällig" oder
-- Monatsberichte immer Treffer liefern. Die Daten halten sich an die Regeln der Funktionen:
-- kein Benutzer leiht sein eigenes Exemplar, pro Exemplar höchstens eine angenommene oder laufende
-- Ausleihe, bewerten darf nur der Ausleiher nach der Rückgabe.

START TRANSACTION;
-- Truncate, alle Daten werden gelöscht, Struktur bleibt
TRUNCATE "Users", "CollectionAdresses", "Authors", "Books", "BookAuthors", "Book_copy", "Lents", "Shipping",
        "Reviews", "Messages", "UserAdresses", "Protocols",
        "CollectionTimeWindows"
RESTART IDENTITY CASCADE;

INSERT INTO "Users" (username, first_name, last_name, email, password_hash, created_at, updated_at)
SELECT v.username, v.first_name, v.last_name, v.email, v.password_hash,
       now() - v.registered_days_ago * interval '1 day',
       now() - v.registered_days_ago * interval '1 day'
FROM (VALUES
    ('Alice',     'Alice',     'Doe',        'alice@example.com',     'hashed_password_1',  400),
    ('Bob',       'Bob',       'Smith',      'bob@example.com',       'hashed_password_2',  390),
    ('Charlie',   'Charlie',   'Johnson',    'charlie@example.com',   'hashed_password_3',  380),
    ('Olaf',      'Olaf',      'Williams',   'olaf@example.com',      'hashed_password_4',  370),
    ('Olad',      'Olad',      'Brown',      'olad@example.com',      'hashed_password_5',  360),
    ('Kimberly',  'Kimberly',  'Davis',      'kimberly@example.com',  'hashed_password_6',  350),
    ('Irmtraut',  'Irmtraut',  'Ostermeier', 'irmtraut@example.com',  'hashed_password_7',  340),
    ('Heidi',     'Heidi',     'Klipp',      'heidi@example.com',     'hashed_password_8',  330),
    ('Hildegard', 'Hildegard', 'Müller',     'hildegard@example.com', 'hashed_password_9',  320),
    ('Jürgen',    'Jürgen',    'Schmidt',    'juergen@example.com',   'hashed_password_10', 310)
) AS v(username, first_name, last_name, email, password_hash, registered_days_ago)
ORDER BY v.registered_days_ago DESC; -- user_ids in der Reihenfolge der Liste

-- Heidi hat vor 7 Tagen ihre E-Mail-Adresse geändert (update_user im Protokoll)
UPDATE "Users" SET updated_at = now() - interval '7 days' WHERE username = 'Heidi';

INSERT INTO "CollectionAdresses" (user_id, streetname, streetnummber, city, "state", postal_code, country, latitude, longitude, created_at, updated_at)
SELECT u.user_id, v.streetname, v.street_number, v.city, v.state, v.postal_code, v.country, v.latitude, v.longitude, u.created_at, u.created_at
FROM (VALUES
    ('Alice',   'Steingasse', '1', 'Kassel', 'HE', '62701', 'DE',              39.799000,  -89.644000),
    ('Bob',     'Buchenweg',  '17', 'Kassel',  'HE', '10001', 'DE',             40.750600,  -73.997200),
    ('Charlie', 'Berliner Straße',  '5', 'Kassel',      'HE', '07001', 'DE',        40.580400,  -74.284600),
    ('Olaf',    'Reistergasse', '17', 'Kassel',  'HE', '66002', 'DE',           39.563100,  -95.121600),
    ('Olad',    'Ackerstraße','5', 'Kassel',   'HE', '90001', 'DE',              33.973100, -118.247900),
    ('Kimberly','Am Brunnen','7', 'Kassel','HE', '64015', 'DE',               39.017000,  -94.281600),
    ('Irmtraut','Am Gilsebach','11', 'Kassel',  'HE', '97035', 'DE',            45.420700, -122.724300),
    ('Heidi',   'Schreckstraße','13', 'Kassel',   'HE', '07002', 'DE',           40.668700,  -74.114300),
    ('Hildegard','Blumenhainer Str.','17','Kassel','HE','91950','DE',        32.678100, -117.099200),
    ('Jürgen',  'Musterstraße','1', 'Kassel','HE', '19019', 'DE',             39.952600,  -75.165200)
) AS v(username, streetname, street_number, city, state, postal_code, country, latitude, longitude)
JOIN "Users" u ON u.username = v.username
WHERE NOT EXISTS (
    SELECT 1 FROM "CollectionAdresses" c WHERE c.user_id = u.user_id
)
ORDER BY u.user_id; -- sonst vergibt der JOIN die address_ids in beliebiger Reihenfolge (CollectionTimeWindows nutzt feste IDs)

INSERT INTO "Authors" (name)
SELECT v.name
FROM (VALUES
    ('J.K. Rowling'), ('Walt Disney'), ('George Orwell'), ('Jane Austen'),
    ('Albert Camus'), ('Aldous Huxley'), ('J.R.R. Tolkien'), ('Ray Bradbury'),
    ('Herman Melville'), ('Leo Tolstoy'), ('Terry Pratchett'), ('Neil Gaiman')
) AS v(name)
WHERE NOT EXISTS (
    SELECT 1 FROM "Authors" a WHERE a.name = v.name
);

-- added_days_ago: wann der Buchtitel eingetragen wurde (das Exemplar wird gleichzeitig angelegt)
INSERT INTO "Books" (title, published_date, genre, language, created_at, updated_at)
SELECT v.title, v.published_date::date, v.genre::genre, v.language::book_language,
       now() - v.added_days_ago * interval '1 day',
       now() - v.added_days_ago * interval '1 day'
FROM (VALUES
    ('Harry Potter und der Stein der Weisen', '1997-06-26', 'Fantasy',         'german',  300),
    ('Lustiges Taschenbuch Nr. 234',          '2000-01-01', 'Komödie',         'german',  280),
    ('1984',                                  '1949-06-08', 'Science-Fiction', 'english', 270),
    ('Animal Farm',                           '1945-08-17', 'Roman',           'english', 260),
    ('Pride and Prejudice',                   '1813-01-28', 'Liebesroman',     'english', 290),
    ('Der Mythos des Sisyphos',               '1942-01-01', 'Philosophie',     'german',  250),
    ('Schöne neue Welt',                      '1932-08-30', 'Science-Fiction', 'german',  240),
    ('Der Hobbit',                            '1937-09-21', 'Fantasy',         'german',  230),
    ('Fahrenheit 451',                        '1953-10-19', 'Science-Fiction', 'german',  220),
    ('Moby-Dick',                             '1851-11-14', 'Roman',           'german',  210),
    ('Krieg und Frieden',                     '1869-01-01', 'Roman',           'german',  200),
    ('Ein gutes Omen',                        '1990-05-01', 'Fantasy',         'german',  5)
) AS v(title, published_date, genre, language, added_days_ago)
WHERE NOT EXISTS (
    SELECT 1 FROM "Books" b WHERE b.title = v.title
)
ORDER BY v.added_days_ago DESC;

-- n:m: "Ein gutes Omen" hat zwei Autoren, George Orwell hat zwei Bücher
INSERT INTO "BookAuthors" (book_id, author_id)
SELECT b.book_id, a.author_id
FROM (VALUES
    ('Harry Potter und der Stein der Weisen', 'J.K. Rowling'),
    ('Lustiges Taschenbuch Nr. 234',          'Walt Disney'),
    ('1984',                                  'George Orwell'),
    ('Animal Farm',                           'George Orwell'),
    ('Pride and Prejudice',                   'Jane Austen'),
    ('Der Mythos des Sisyphos',               'Albert Camus'),
    ('Schöne neue Welt',                      'Aldous Huxley'),
    ('Der Hobbit',                            'J.R.R. Tolkien'),
    ('Fahrenheit 451',                        'Ray Bradbury'),
    ('Moby-Dick',                             'Herman Melville'),
    ('Krieg und Frieden',                     'Leo Tolstoy'),
    ('Ein gutes Omen',                        'Terry Pratchett'),
    ('Ein gutes Omen',                        'Neil Gaiman')
) AS v(title, author_name)
JOIN "Books" b ON b.title = v.title
JOIN "Authors" a ON a.name = v.author_name
ON CONFLICT DO NOTHING;

-- Ein Exemplar pro Buchtitel ("Animal Farm" und "Ein gutes Omen" haben keins).
-- Besitzer = Benutzer der Abholadresse
INSERT INTO "Book_copy" (book_id, condition, max_lent_duration, collection_address_id, shipping_availability, shipping_cost, created_at, updated_at)
SELECT b.book_id, v.condition::book_condition, v.max_lent_duration, c.address_id, v.shipping_availability::shipping_availability, v.shipping_cost,
       b.created_at, b.created_at
FROM (VALUES
    ('Harry Potter und der Stein der Weisen', 'Alice',     'good',     30, 'available',     5.00),
    ('Lustiges Taschenbuch Nr. 234',          'Bob',       'good',     15, 'not_available', NULL),
    ('1984',                                  'Charlie',   'like_new', 45, 'available',     3.50),
    ('Pride and Prejudice',                   'Olaf',      'good',     60, 'not_available', NULL),
    ('Der Mythos des Sisyphos',               'Olad',      'fair',     20, 'available',     4.00),
    ('Schöne neue Welt',                      'Kimberly',  'good',     30, 'not_available', NULL),
    ('Der Hobbit',                            'Irmtraut',  'like_new', 50, 'available',     6.00),
    ('Fahrenheit 451',                        'Heidi',     'good',     25, 'not_available', NULL),
    ('Moby-Dick',                             'Hildegard', 'good',     40, 'available',     7.00),
    ('Krieg und Frieden',                     'Jürgen',    'poor',     35, 'not_available', NULL)
) AS v(title, owner_username, condition, max_lent_duration, shipping_availability, shipping_cost)
JOIN "Books" b ON b.title = v.title
JOIN "Users" o ON o.username = v.owner_username
JOIN "CollectionAdresses" c ON c.user_id = o.user_id
WHERE NOT EXISTS (
    SELECT 1 FROM "Book_copy" bc WHERE bc.book_id = b.book_id
)
ORDER BY b.book_id;

-- Der Verleiher wird nicht gespeichert, er ergibt sich aus der Abholadresse des Exemplars.
-- Anfrage = 2 Tage vor Start, Enddatum = Start + max_lent_duration des Exemplars.
-- Bei requested/accepted/rejected ist das Startdatum vorläufig (hand_over_book setzt es neu).
-- Versand nur bei Exemplaren mit shipping_availability = 'available'.
INSERT INTO "Lents" (book_copie_id, lent_to, lent_status,
                     shipping_requested, lent_requesttimestamp, lent_startdate, lent_enddate, return_date)
SELECT bc.book_copie_id,
       borrower.user_id,
       v.lent_status::lent_status,
       v.shipping_requested,
       now() - (v.started_days_ago + 2) * interval '1 day',
       now() - v.started_days_ago * interval '1 day',
       now() - v.started_days_ago * interval '1 day' + bc.max_lent_duration * interval '1 day',
       now() - v.returned_days_ago * interval '1 day'
FROM (VALUES
    -- Frühere Ausleihen (alle zurückgegeben und bewertet)
    ('Pride and Prejudice',                   'Alice',     'returned',  false, 200,  150),
    ('Krieg und Frieden',                     'Charlie',   'returned',  false, 160,  128),
    ('Harry Potter und der Stein der Weisen', 'Charlie',   'returned',  false, 150,  125),
    ('1984',                                  'Heidi',     'returned',  false, 140,  100),
    ('Moby-Dick',                             'Olad',      'returned',  false, 130,  95),
    ('Der Hobbit',                            'Olaf',      'returned',  false, 120,  75),
    ('Fahrenheit 451',                        'Hildegard', 'returned',  false, 110,  90),
    ('Lustiges Taschenbuch Nr. 234',          'Kimberly',  'returned',  false, 100,  88),
    ('Der Mythos des Sisyphos',               'Bob',       'returned',  false, 80,   65),
    ('Schöne neue Welt',                      'Jürgen',    'returned',  false, 70,   45),
    -- Aktuelle Ausleihen und Anfragen
    ('Pride and Prejudice',                   'Charlie',   'returned',  false, 90,   35),
    ('Harry Potter und der Stein der Weisen', 'Bob',       'returned',  true,  60,   32),
    ('1984',                                  'Olaf',      'active',    true,  50,   NULL), -- überfällig
    ('Fahrenheit 451',                        'Irmtraut',  'returned',  false, 40,   16),
    ('Schöne neue Welt',                      'Olad',      'rejected',  false, 20,   NULL),
    ('Moby-Dick',                             'Jürgen',    'active',    true,  20,   NULL),
    ('Lustiges Taschenbuch Nr. 234',          'Alice',     'active',    false, 10,   NULL),
    ('Der Hobbit',                            'Heidi',     'active',    true,  5,    NULL),
    ('Der Mythos des Sisyphos',               'Kimberly',  'accepted',  true,  0,    NULL), -- angenommen, Versand wird vorbereitet
    ('Krieg und Frieden',                     'Hildegard', 'accepted',  false, 0,    NULL), -- angenommen, noch nicht abgeholt
    ('Fahrenheit 451',                        'Alice',     'requested', false, 0,    NULL)  -- offene Anfrage
) AS v(title, borrower_username, lent_status, shipping_requested, started_days_ago, returned_days_ago)
JOIN "Books" b ON b.title = v.title
JOIN "Book_copy" bc ON bc.book_id = b.book_id
JOIN "Users" borrower ON borrower.username = v.borrower_username
WHERE NOT EXISTS (
    SELECT 1 FROM "Lents" l WHERE l.book_copie_id = bc.book_copie_id AND l.lent_to = borrower.user_id
)
ORDER BY v.started_days_ago DESC; -- lent_ids in zeitlicher Reihenfolge

-- Nur für Ausleihen mit shipping_requested = true, angelegt bei der Übergabe (lent_startdate)
INSERT INTO "Shipping" (lent_id, shipping_status, tracking_number, created_at, updated_at)
SELECT l.lent_id, v.shipping_status::shipping_status, v.tracking_number, l.lent_startdate, l.lent_startdate
FROM (VALUES
    ('Harry Potter und der Stein der Weisen', 'Bob',      'delivered', 'DHL-00340434161094042557'),
    ('1984',                                  'Olaf',     'delivered', 'DHL-00340434161094042558'),
    ('Der Mythos des Sisyphos',               'Kimberly', 'pending',   NULL),
    ('Der Hobbit',                            'Heidi',    'shipped',   'DHL-00340434161094042559'),
    ('Moby-Dick',                             'Jürgen',   'delivered', 'HERMES-H1001234567890')
) AS v(title, borrower_username, shipping_status, tracking_number)
JOIN "Books" b ON b.title = v.title
JOIN "Book_copy" bc ON bc.book_id = b.book_id
JOIN "Users" borrower ON borrower.username = v.borrower_username
JOIN "Lents" l ON l.book_copie_id = bc.book_copie_id AND l.lent_to = borrower.user_id AND l.shipping_requested
WHERE NOT EXISTS (
    SELECT 1 FROM "Shipping" s WHERE s.lent_id = l.lent_id
);

-- Eine Bewertung gehört zu einer Ausleihe: nur der Ausleiher, nur nach der Rückgabe (wie in write_review).
-- Geschrieben 2 Tage nach der Rückgabe.
INSERT INTO "Reviews" (lent_id, rating, comment, created_at, updated_at)
SELECT l.lent_id, v.rating, v.comment, l.return_date + interval '2 days', l.return_date + interval '2 days'
FROM (VALUES
    ('Harry Potter und der Stein der Weisen', 'Charlie',   5, 'Great book!'),
    ('Harry Potter und der Stein der Weisen', 'Bob',       4, 'Nice start of the series.'),
    ('Lustiges Taschenbuch Nr. 234',          'Kimberly',  4, 'Funny and entertaining.'),
    ('1984',                                  'Heidi',     5, 'A thought-provoking classic.'),
    ('Pride and Prejudice',                   'Alice',     4, 'A timeless romance.'),
    ('Pride and Prejudice',                   'Charlie',   3, 'A bit slow in the middle.'),
    ('Der Mythos des Sisyphos',               'Bob',       3, 'Interesting philosophical ideas.'),
    ('Schöne neue Welt',                      'Jürgen',    4, 'A chilling dystopia.'),
    ('Der Hobbit',                            'Olaf',      5, 'An epic adventure.'),
    ('Fahrenheit 451',                        'Hildegard', 4, 'A cautionary tale about censorship.'),
    ('Fahrenheit 451',                        'Irmtraut',  5, 'Short but powerful.'),
    ('Moby-Dick',                             'Olad',      3, 'A challenging but rewarding read.'),
    ('Krieg und Frieden',                     'Charlie',   5, 'A masterpiece of literature.')
) AS v(title, reviewer_username, rating, comment)
JOIN "Books" b ON b.title = v.title
JOIN "Book_copy" bc ON bc.book_id = b.book_id
JOIN "Users" r ON r.username = v.reviewer_username
JOIN "Lents" l ON l.book_copie_id = bc.book_copie_id AND l.lent_to = r.user_id AND l.lent_status = 'returned'
WHERE NOT EXISTS (
    SELECT 1 FROM "Reviews" rev WHERE rev.lent_id = l.lent_id
)
ORDER BY l.return_date;

INSERT INTO "Messages" (sender_id, receiver_id, content, sent_at, read_status)
SELECT s.user_id, r.user_id, v.content, now() - v.sent_hours_ago * interval '1 hour', v.read_status
FROM (VALUES
    ('Alice',     'Bob',       'Hi Bob, I am interested in borrowing your book.',     72, true),
    ('Bob',       'Alice',     'Sure, Alice! When would you like to pick it up?',     70, true),
    ('Charlie',   'Olaf',      'Hey Olaf, can I borrow your book next week?',         50, true),
    ('Olaf',      'Charlie',   'Of course, Charlie! Let me know the day.',            48, true),
    ('Olad',      'Kimberly',  'Hi Kimberly, I would like to borrow your book.',      30, true),
    ('Kimberly',  'Olad',      'Sorry Olad, the book is not available right now.',   29, false),
    ('Irmtraut',  'Heidi',     'Hey Heidi, can I borrow your book this weekend?',     20, true),
    ('Heidi',     'Irmtraut',  'Of course, Irmtraut! Let me know the day.',           18, false),
    ('Hildegard', 'Jürgen',    'Hi Jürgen, I am interested in borrowing your book.',  3,  true),
    ('Jürgen',    'Hildegard', 'Sure, Hildegard! When would you like to pick it up?', 1,  false)
) AS v(sender_username, receiver_username, content, sent_hours_ago, read_status)
JOIN "Users" s ON s.username = v.sender_username
JOIN "Users" r ON r.username = v.receiver_username
WHERE NOT EXISTS (
    SELECT 1 FROM "Messages" m WHERE m.sender_id = s.user_id AND m.receiver_id = r.user_id AND m.content = v.content
);

INSERT INTO "UserAdresses" (user_id, street_address, city, state, postal_code, country, created_at, updated_at)
SELECT u.user_id, v.street_address, v.city, v.state, v.postal_code, v.country, u.created_at, u.created_at
FROM (VALUES
    ('Alice',     'Steingasse', 'Kassel', 'HE', '62701', 'DE'),
    ('Bob',       'Buchenweg',      'Kassel',    'HE', '10001', 'DE'),
    ('Charlie',   'Eichenweg',      'Kassel',        'HE', '07001', 'DE'),
    ('Olaf',      'Fichtenweg',     'Kassel',    'HE', '66002', 'DE'),
    ('Olad',      'Zedernweg',    'Kassel',     'HE', '90001', 'DE'),
    ('Kimberly',  'Birkenweg',    'Kassel',  'HE', '64015', 'DE'),
    ('Irmtraut',  'Ahornweg',    'Kassel',    'HE', '97035', 'DE'),
    ('Heidi',     'Walnussweg',   'Kassel',     'HE', '07002', 'DE'),
    ('Hildegard', 'Kastanienweg', 'Kassel', 'HE', '91950', 'DE'),
    ('Jürgen',    'Fichtenweg',   'Kassel',  'HE', '19019', 'DE')
) AS v(username, street_address, city, state, postal_code, country)
JOIN "Users" u ON u.username = v.username
WHERE NOT EXISTS (
    SELECT 1 FROM "UserAdresses" ua WHERE ua.user_id = u.user_id AND ua.street_address = v.street_address
);

-- Das Protokoll wird aus den Daten oben abgeleitet, so wie es die Funktionen schreiben würden.
-- user_id = wer gehandelt hat: Ausleiher bei Anfrage und Bewertung,
-- Besitzer bei Annahme, Ablehnung, Übergabe (lending) und Rückgabe (returning).
-- Annahme/Ablehnung: 1 Tag nach der Anfrage.
WITH lent_owner AS (
    SELECT l.*, c.user_id AS owner_id
    FROM "Lents" l
    JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
    JOIN "CollectionAdresses" c ON c.address_id = bc.collection_address_id
)
INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details, created_at)
SELECT e.book_copie_id, e.user_id, e.protocol_type, e.details, e.created_at
FROM (
    SELECT NULL::bigint AS book_copie_id, u.user_id, 'create_user'::protocol_type AS protocol_type,
           format('User %s registered', u.username) AS details, u.created_at
    FROM "Users" u
    UNION ALL
    SELECT NULL, u.user_id, 'update_user', 'Email address changed', u.updated_at
    FROM "Users" u
    WHERE u.updated_at > u.created_at
    UNION ALL
    SELECT bc.book_copie_id, c.user_id, 'create_book', format('Copy of %s added', b.title), bc.created_at
    FROM "Book_copy" bc
    JOIN "Books" b ON b.book_id = bc.book_id
    JOIN "CollectionAdresses" c ON c.address_id = bc.collection_address_id
    UNION ALL
    SELECT lo.book_copie_id, lo.lent_to, 'create_request', format('lent_id %s', lo.lent_id), lo.lent_requesttimestamp
    FROM lent_owner lo
    UNION ALL
    SELECT lo.book_copie_id, lo.owner_id, 'accept_request', format('lent_id %s', lo.lent_id), lo.lent_requesttimestamp + interval '1 day'
    FROM lent_owner lo
    WHERE lo.lent_status IN ('accepted', 'active', 'returned')
    UNION ALL
    SELECT lo.book_copie_id, lo.owner_id, 'reject_request', format('lent_id %s', lo.lent_id), lo.lent_requesttimestamp + interval '1 day'
    FROM lent_owner lo
    WHERE lo.lent_status = 'rejected'
    UNION ALL
    SELECT lo.book_copie_id, lo.owner_id, 'lending', format('lent_id %s', lo.lent_id), lo.lent_startdate
    FROM lent_owner lo
    WHERE lo.lent_status IN ('active', 'returned')
    UNION ALL
    SELECT lo.book_copie_id, lo.owner_id, 'returning', format('lent_id %s', lo.lent_id), lo.return_date
    FROM lent_owner lo
    WHERE lo.lent_status = 'returned'
    UNION ALL
    SELECT lo.book_copie_id, lo.lent_to, 'create_review', format('lent_id %s, review_id %s', lo.lent_id, r.review_id), r.created_at
    FROM "Reviews" r
    JOIN lent_owner lo ON lo.lent_id = r.lent_id
) AS e
ORDER BY e.created_at; -- protocol_ids in zeitlicher Reihenfolge

-- weekday: 0 = Sonntag ... 6 = Samstag
INSERT INTO "CollectionTimeWindows" (address_id, weekday, start_time, end_time)
SELECT c.address_id, v.weekday, v.start_time::time, v.end_time::time
FROM (VALUES
    (1,  1, '09:00', '12:00'),
    (1,  6, '10:00', '12:00'),
    (2,  2, '13:00', '16:00'),
    (2,  4, '17:00', '19:00'),
    (3,  3, '10:00', '14:00'),
    (4,  4, '11:00', '15:00'),
    (5,  5, '08:00', '12:00'),
    (6,  6, '14:00', '18:00'),
    (7,  0, '10:00', '13:00'),
    (8,  1, '09:30', '12:30'),
    (9,  2, '13:30', '16:30'),
    (10, 3, '10:30', '14:30'),
    (10, 5, '16:00', '18:00')
) AS v(address_id, weekday, start_time, end_time)
JOIN "CollectionAdresses" c ON c.address_id = v.address_id
WHERE NOT EXISTS (
    SELECT 1 FROM "CollectionTimeWindows" ctw
    WHERE ctw.address_id = c.address_id AND ctw.weekday = v.weekday AND ctw.start_time = v.start_time::time
);

COMMIT;
