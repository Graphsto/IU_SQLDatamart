-- Views for the Book Exchange Application.

-- Copies that can be requested by users
CREATE OR REPLACE VIEW available_books AS
SELECT bc.book_copie_id,
       b.book_id,
       b.title,
       string_agg(a.name, ', ' ORDER BY a.name) AS authors,
       b.genre,
       b.language,
       bc.condition,
       bc.max_lent_duration,
       bc.shipping_availability,
       bc.shipping_cost,
       ca.city,
       ca.postal_code,
       u.user_id AS owner_id,
       u.username AS owner
FROM "Book_copy" bc
JOIN "Books" b ON b.book_id = bc.book_id
JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
JOIN "Users" u ON u.user_id = ca.user_id AND u.is_active
LEFT JOIN "BookAuthors" ba ON ba.book_id = b.book_id
LEFT JOIN "Authors" a ON a.author_id = ba.author_id
WHERE NOT EXISTS (
    SELECT 1
    FROM "Lents" l
    WHERE l.book_copie_id = bc.book_copie_id
      AND l.lent_status IN ('accepted', 'active')
)
GROUP BY bc.book_copie_id, b.book_id, ca.address_id, u.user_id;


-- Active lents whose return date has passed
CREATE OR REPLACE VIEW overdue_lents AS
SELECT l.lent_id,
       b.title,
       borrower.user_id AS borrower_id,
       borrower.username AS borrower,
       borrower.email AS borrower_email,
       owner.user_id AS owner_id,
       owner.username AS owner,
       l.lent_enddate,
       now()::date - l.lent_enddate::date AS days_overdue
FROM "Lents" l
JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
JOIN "Books" b ON b.book_id = bc.book_id
JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
JOIN "Users" owner ON owner.user_id = ca.user_id
JOIN "Users" borrower ON borrower.user_id = l.lent_to
WHERE l.lent_status = 'active'
  AND now() > l.lent_enddate;


-- Full Book Information. Authors, Book owner and full Collection Adress
CREATE OR REPLACE VIEW fullBookInfo AS
SELECT bc.book_copie_id, 
       b.book_id,
       b.title,
       string_agg(a.name, ', ' ORDER BY a.name) AS authors,
       b.genre,
       b.language,
       bc.condition,
       bc.max_lent_duration,
       bc.shipping_availability,
       bc.shipping_cost,
       ca.city,
       ca.postal_code,
       u.user_id AS owner_id,
       u.username AS owner,
       u.first_name AS user_firstname,
       u.last_name AS user_lastname
       
FROM "Book_copy" bc
JOIN "Books" b ON b.book_id = bc.book_id
JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
JOIN "Users" u ON u.user_id = ca.user_id
LEFT JOIN "BookAuthors" ba ON ba.book_id = b.book_id
LEFT JOIN "Authors" a ON a.author_id = ba.author_id
GROUP BY bc.book_copie_id, b.book_id, ca.address_id, u.user_id;

