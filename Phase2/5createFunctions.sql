
-- Borrower requests a book copy. Returns the ID of the new lent.
CREATE OR REPLACE FUNCTION create_book_request(p_book_copie_id bigint, p_user_id bigint, p_shipping boolean DEFAULT false)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_owner_id bigint;
    v_shipping_availability shipping_availability;
    v_max_lent_duration integer;
    v_lent_id bigint;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM "Users"
        WHERE user_id = p_user_id
          AND is_active
    ) THEN
        RAISE EXCEPTION 'User with ID % does not exist or is not active.', p_user_id;
    END IF;

    -- Owner = user of the collection address
    -- FOR UPDATE locks the copy: simultaneous requests for the same copy run one after another
    SELECT ca.user_id, bc.shipping_availability, bc.max_lent_duration
    INTO v_owner_id, v_shipping_availability, v_max_lent_duration
    FROM "Book_copy" bc
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    WHERE bc.book_copie_id = p_book_copie_id
    FOR UPDATE OF bc;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Book copy with ID % does not exist.', p_book_copie_id;
    END IF;

    IF v_owner_id = p_user_id THEN
        RAISE EXCEPTION 'User with ID % cannot lend their own book copy with ID %.', p_user_id, p_book_copie_id;
    END IF;

    IF p_shipping AND v_shipping_availability IS DISTINCT FROM 'available' THEN
        RAISE EXCEPTION 'Book copy with ID % can only be picked up, shipping is not available.', p_book_copie_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM "Lents"
        WHERE book_copie_id = p_book_copie_id
          AND lent_status IN ('accepted', 'active')
    ) THEN
        RAISE EXCEPTION 'Book copy with ID % is currently lent out.', p_book_copie_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM "Lents"
        WHERE book_copie_id = p_book_copie_id
          AND lent_to = p_user_id
          AND lent_status = 'requested'
    ) THEN
        RAISE EXCEPTION 'User with ID % has already requested book copy with ID %.', p_user_id, p_book_copie_id;
    END IF;

    -- Dates are provisional, hand_over_book sets them again when the copy is handed over
    INSERT INTO "Lents" (lent_status, book_copie_id, lent_to, shipping_requested, lent_startdate, lent_enddate)
    VALUES ('requested', p_book_copie_id, p_user_id, p_shipping, now(), now() + make_interval(days => v_max_lent_duration))
    RETURNING lent_id INTO v_lent_id;

    INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details)
    VALUES (p_book_copie_id, p_user_id, 'create_request', format('lent_id %s', v_lent_id));

    RETURN v_lent_id;
END;
$$;


-- Owner accepts a request. The copy is then reserved for the borrower.
CREATE OR REPLACE FUNCTION accept_book_request(p_lent_id bigint, p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_book_copie_id bigint;
BEGIN
    -- Check and status change in one statement, so nothing can change in between
    UPDATE "Lents" l
    SET lent_status = 'accepted'
    FROM "Book_copy" bc
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    WHERE l.lent_id = p_lent_id
      AND l.lent_status = 'requested'
      AND bc.book_copie_id = l.book_copie_id
      AND ca.user_id = p_user_id -- only the owner can accept
    RETURNING l.book_copie_id INTO v_book_copie_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lent request with ID % does not exist, is not in requested status or the copy does not belong to user with ID %.', p_lent_id, p_user_id;
    END IF;

    INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details)
    VALUES (v_book_copie_id, p_user_id, 'accept_request', format('lent_id %s', p_lent_id));
EXCEPTION
    -- Index lents_one_open_per_copy: another request for this copy is already accepted or active.
    -- Also applies if two owners. Sessions accept at the same time end up here.
    WHEN unique_violation THEN
        RAISE EXCEPTION 'The book copy of lent request % is already lent out.', p_lent_id;
END;
$$;


-- Owner rejects a request.
CREATE OR REPLACE FUNCTION reject_book_request(p_lent_id bigint, p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_book_copie_id bigint;
BEGIN
    UPDATE "Lents" l
    SET lent_status = 'rejected'
    FROM "Book_copy" bc
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    WHERE l.lent_id = p_lent_id
      AND l.lent_status = 'requested'
      AND bc.book_copie_id = l.book_copie_id
      AND ca.user_id = p_user_id -- only the owner can reject
    RETURNING l.book_copie_id INTO v_book_copie_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lent request with ID % does not exist, is not in requested status or the copy does not belong to user with ID %.', p_lent_id, p_user_id;
    END IF;

    INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details)
    VALUES (v_book_copie_id, p_user_id, 'reject_request', format('lent_id %s', p_lent_id));
END;
$$;


-- Owner confirms that the copy was picked up or shipped. The lending period starts now.
CREATE OR REPLACE FUNCTION hand_over_book(p_lent_id bigint, p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_book_copie_id bigint;
BEGIN
    UPDATE "Lents" l
    SET lent_status = 'active',
        lent_startdate = now(),
        lent_enddate = now() + make_interval(days => bc.max_lent_duration)
    FROM "Book_copy" bc
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    WHERE l.lent_id = p_lent_id
      AND l.lent_status = 'accepted'
      AND bc.book_copie_id = l.book_copie_id
      AND ca.user_id = p_user_id -- only the owner can hand over
    RETURNING l.book_copie_id INTO v_book_copie_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lent with ID % does not exist, is not in accepted status or the copy does not belong to user with ID %.', p_lent_id, p_user_id;
    END IF;

    INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details)
    VALUES (v_book_copie_id, p_user_id, 'lending', format('lent_id %s', p_lent_id));
END;
$$;


-- Owner confirms that the copy was returned.
CREATE OR REPLACE FUNCTION return_book(p_lent_id bigint, p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_book_copie_id bigint;
BEGIN
    UPDATE "Lents" l
    SET lent_status = 'returned',
        return_date = now()
    FROM "Book_copy" bc
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    WHERE l.lent_id = p_lent_id
      AND l.lent_status = 'active'
      AND bc.book_copie_id = l.book_copie_id
      AND ca.user_id = p_user_id -- only the owner can confirm the return
    RETURNING l.book_copie_id INTO v_book_copie_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lent with ID % does not exist, is not in active status or the copy does not belong to user with ID %.', p_lent_id, p_user_id;
    END IF;

    INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details)
    VALUES (v_book_copie_id, p_user_id, 'returning', format('lent_id %s', p_lent_id));
END;
$$;


-- Borrower reviews a returned lent. Returns the ID of the new review.
CREATE OR REPLACE FUNCTION write_review(p_lent_id bigint, p_user_id bigint, p_rating integer, p_comment text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_book_copie_id bigint;
    v_review_id bigint;
BEGIN
    -- Only the borrower can review, and only after the return
    SELECT book_copie_id INTO v_book_copie_id
    FROM "Lents"
    WHERE lent_id = p_lent_id
      AND lent_to = p_user_id
      AND lent_status = 'returned';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lent with ID % does not exist, is not returned yet or was not borrowed by user with ID %.', p_lent_id, p_user_id;
    END IF;

    -- rating between 1 and 5 is checked by the table
    INSERT INTO "Reviews" (lent_id, rating, comment)
    VALUES (p_lent_id, p_rating, p_comment)
    RETURNING review_id INTO v_review_id;

    INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details)
    VALUES (v_book_copie_id, p_user_id, 'create_review', format('lent_id %s, review_id %s', p_lent_id, v_review_id));

    RETURN v_review_id;
EXCEPTION
    -- Reviews.lent_id is UNIQUE
    WHEN unique_violation THEN
        RAISE EXCEPTION 'Lent with ID % has already been reviewed.', p_lent_id;
END;
$$;


-- Owner adds a collection time window to their address. Returns the ID of the new time window.
CREATE OR REPLACE FUNCTION create_collection_time_window(p_address_id bigint, p_user_id bigint, p_weekday integer, p_start_time time, p_end_time time)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_window_id bigint;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM "CollectionAdresses"
        WHERE address_id = p_address_id
          AND user_id = p_user_id
    ) THEN
        RAISE EXCEPTION 'Collection address with ID % does not exist or does not belong to user with ID %.', p_address_id, p_user_id;
    END IF;

    -- weekday 0-6 and start_time < end_time are checked by the table
    INSERT INTO "CollectionTimeWindows" (address_id, weekday, start_time, end_time)
    VALUES (p_address_id, p_weekday, p_start_time, p_end_time)
    RETURNING window_id INTO v_window_id;

    RETURN v_window_id;
EXCEPTION
    -- Constraint no_overlapping_window
    WHEN exclusion_violation THEN
        RAISE EXCEPTION 'Time window % - % overlaps with an existing time window of address % on weekday %.', p_start_time, p_end_time, p_address_id, p_weekday;
END;
$$;


-- true = copy is still with the borrower and the return date has passed.
CREATE OR REPLACE FUNCTION book_must_be_returned(p_lent_id bigint)
RETURNS boolean
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_status lent_status;
    v_due_date timestamptz;
BEGIN
    SELECT lent_status, lent_enddate INTO v_status, v_due_date
    FROM "Lents"
    WHERE lent_id = p_lent_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lent with ID % does not exist.', p_lent_id;
    END IF;

    RETURN v_status = 'active' AND now() > v_due_date;
END;
$$;

-- genererate a Report about the Monthly Activity of the Book Exchange Application
CREATE OR REPLACE FUNCTION generate_monthly_report(p_year integer, p_month integer)
RETURNS TABLE (
    total_users bigint,
    total_books bigint,
    total_lents bigint,
    total_reviews bigint,
    total_messages bigint,
    total_active_lents bigint,
    total_overdue_lents bigint
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM "Users" WHERE EXTRACT(YEAR FROM created_at) = p_year AND EXTRACT(MONTH FROM created_at) = p_month) AS total_users,  
        (SELECT COUNT(*) FROM "Books" WHERE EXTRACT(YEAR FROM created_at) = p_year AND EXTRACT(MONTH FROM created_at) = p_month) AS total_books,
        (SELECT COUNT(*) FROM "Lents" WHERE EXTRACT(YEAR FROM lent_requesttimestamp) = p_year AND EXTRACT(MONTH FROM lent_requesttimestamp) = p_month) AS total_lents,
        (SELECT COUNT(*) FROM "Reviews" WHERE EXTRACT(YEAR FROM created_at) = p_year AND EXTRACT(MONTH FROM created_at) = p_month) AS total_reviews,
        (SELECT COUNT(*) FROM "Messages" WHERE EXTRACT(YEAR FROM sent_at) = p_year AND EXTRACT(MONTH FROM sent_at) = p_month) AS total_messages,
        (SELECT COUNT(*) FROM "Lents" WHERE lent_status = 'active' AND EXTRACT(YEAR FROM lent_requesttimestamp) = p_year AND EXTRACT(MONTH FROM lent_requesttimestamp) = p_month) AS total_active_lents,
        (SELECT COUNT(*) FROM "Lents" WHERE lent_status = 'active' AND now() > lent_enddate AND EXTRACT(YEAR FROM lent_requesttimestamp) = p_year AND EXTRACT(MONTH FROM lent_requesttimestamp) = p_month) AS total_overdue_lents;
END;
$$;

-- The same report as JSON
CREATE OR REPLACE FUNCTION generate_monthly_report_json(p_year integer, p_month integer)
RETURNS jsonb
LANGUAGE sql STABLE
AS $$
    SELECT jsonb_build_object('year', p_year, 'month', p_month) || to_jsonb(r)
    FROM generate_monthly_report(p_year, p_month) AS r;
$$;

CREATE OR REPLACE FUNCTION getFullUserStats(p_user_id bigint)
RETURNS TABLE (
    user_id bigint,
    username text,
    email text,
    created_at timestamptz,
    total_books bigint,
    total_lents bigint,
    total_reviews bigint,
    total_messages bigint
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT
        u.user_id,
        u.username,
        u.email,
        u.created_at,
        -- user of the collection address of the copy
        (SELECT COUNT(*) FROM "Book_copy" bc
            JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
            WHERE ca.user_id = u.user_id) AS total_books,
        -- Number for Lent Activities
        (SELECT COUNT(*) FROM "Lents" l
            JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
            JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
            WHERE l.lent_to = u.user_id OR ca.user_id = u.user_id) AS total_lents,
        -- Reviews written by the borrower
        (SELECT COUNT(*) FROM "Reviews" r
            JOIN "Lents" l ON l.lent_id = r.lent_id
            WHERE l.lent_to = u.user_id) AS total_reviews,
        (SELECT COUNT(*) FROM "Messages" WHERE sender_id = u.user_id OR receiver_id = u.user_id) AS total_messages
    FROM "Users" u
    WHERE u.user_id = p_user_id;
END;
$$;

-- return all books the user offers to lent
CREATE OR REPLACE FUNCTION getAllBooksbyUser(p_user_id bigint) 
RETURNS TABLE (
    book_copie_id bigint,
    book_id bigint,
    title text,
    authors text,
    genre genre,
    language book_language,
    condition book_condition,
    max_lent_duration integer,
    shipping_availability shipping_availability ,
    shipping_cost numeric,
    city text,
    postal_code text
)
LANGUAGE sql STABLE
AS $$
    SELECT bc.book_copie_id::bigint,
           b.book_id::bigint,
           b.title::text,
           string_agg(a.name::text, ', ' ORDER BY a.name)::text AS authors,
           b.genre::genre,
           b.language::book_language,
           bc.condition::book_condition,
           bc.max_lent_duration::integer,
           bc.shipping_availability::shipping_availability,
           bc.shipping_cost::numeric,
           ca.city::text,
           ca.postal_code::text
    FROM "Book_copy" bc
    JOIN "Books" b ON b.book_id = bc.book_id
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    LEFT JOIN "BookAuthors" ba ON ba.book_id = b.book_id
    LEFT JOIN "Authors" a ON a.author_id = ba.author_id
    WHERE ca.user_id = p_user_id
    GROUP BY bc.book_copie_id, b.book_id, ca.address_id;
$$;

CREATE OR REPLACE FUNCTION getAllLentsbyUser(p_user_id bigint)
RETURNS TABLE (
    lent_id bigint,
    book_copie_id bigint,
    book_id bigint,
    title text,
    authors text,
    genre genre,
    language book_language,
    condition book_condition,
    max_lent_duration integer,
    shipping_availability shipping_availability,
    shipping_cost numeric,
    city text,
    postal_code text,
    lent_to bigint,
    lent_from bigint,
    lent_status lent_status,
    lent_requesttimestamp timestamptz,
    lent_startdate timestamptz,
    lent_enddate timestamptz,
    return_date timestamptz
)
LANGUAGE sql STABLE
AS $$
    SELECT l.lent_id::bigint,
           bc.book_copie_id::bigint,
           b.book_id::bigint,
           b.title::text,
           string_agg(a.name::text, ', ' ORDER BY a.name)::text AS authors,
           b.genre::genre,
           b.language::book_language,
           bc.condition::book_condition,
           bc.max_lent_duration::integer,
           bc.shipping_availability::shipping_availability,
           bc.shipping_cost::numeric,
           ca.city::text,
           ca.postal_code::text,
           l.lent_to::bigint,
           ca.user_id::bigint AS lent_from,
           l.lent_status::lent_status,
           l.lent_requesttimestamp::timestamptz,
           l.lent_startdate::timestamptz,
           l.lent_enddate::timestamptz,
           l.return_date::timestamptz
    FROM "Lents" l
    JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
    JOIN "Books" b ON b.book_id = bc.book_id
    JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
    LEFT JOIN "BookAuthors" ba ON ba.book_id = b.book_id
    LEFT JOIN "Authors" a ON a.author_id = ba.author_id
    WHERE l.lent_to = p_user_id OR ca.user_id = p_user_id
    GROUP BY l.lent_id, bc.book_copie_id, b.book_id, ca.address_id;
$$;