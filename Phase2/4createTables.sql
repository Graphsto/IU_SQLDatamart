CREATE TABLE IF NOT EXISTS "Users" (
    user_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text NOT NULL UNIQUE,
    first_name text NOT NULL,
    last_name text NOT NULL,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role user_role NOT NULL DEFAULT 'user', -- App permissions
    is_active BOOLEAN NOT NULL DEFAULT true, -- false if Deactivated or banned 
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "CollectionAdresses" (
    address_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES "Users"(user_id) ON DELETE CASCADE,
    streetname text NOT NULL,
    streetnummber text NOT NULL,
    city text NOT NULL,
    "state" text NOT NULL,
    postal_code text NOT NULL, -- Text instead of numbers, because not every country uses only numbers
    country text,
    latitude numeric(8, 6), -- Latitude, 6 After the decimal point
    longitude numeric(9, 6), -- Longitude, 6 After the decimal point
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT latitude_range CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT longitude_range CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT coordinates_complete CHECK ((latitude IS NULL) = (longitude IS NULL)) -- both or none
); -- Collection addresses are separate from user addresses and define the location for book pickup or shipping.

CREATE TABLE IF NOT EXISTS "Authors" (
    author_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL, -- Full name or pen name, e.g. "J.R.R. Tolkien"
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
); -- Authors are separate from books to allow many-to-many relationships (n:m via "BookAuthors")

CREATE TABLE IF NOT EXISTS "Books" (
    book_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL,
    published_date date,
    genre genre, -- genre aus ENUM-Typ
    language book_language,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Zwischentabelle Books <-> Authors
CREATE TABLE IF NOT EXISTS "BookAuthors" (
    book_id bigint NOT NULL REFERENCES "Books"(book_id) ON DELETE CASCADE,
    author_id bigint NOT NULL REFERENCES "Authors"(author_id) ON DELETE RESTRICT,

    PRIMARY KEY (book_id, author_id)
);

CREATE INDEX IF NOT EXISTS book_authors_author_id_idx
    ON "BookAuthors" (author_id);
    -- The PK index starts with book_id and only helps with "authors of a book".
    -- For "all books of an author" and the RESTRICT check when deleting an author, this index is needed.
    -- RESTRICT: Author cannot be deleted if any book still references them.

-- Physical copy of a book offered by a user (Books = title, Book_copy = copy)
CREATE TABLE IF NOT EXISTS "Book_copy" (
    book_copie_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id bigint NOT NULL REFERENCES "Books"(book_id) ON DELETE RESTRICT, -- Book (title) this copy belongs to
    condition book_condition,
    max_lent_duration integer NOT NULL DEFAULT 60, -- Maximum duration (in days) for which the copy can be lent
    collection_address_id bigint NOT NULL REFERENCES "CollectionAdresses"(address_id) ON DELETE RESTRICT, -- Collection address, also defines the owner
    shipping_availability shipping_availability DEFAULT 'not_available', -- Whether the lender offers shipping or not
    shipping_cost numeric(3, 2) DEFAULT 4.00, -- Cost of shipping the copy, optional
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
); -- Physical copies of books are tied to a collection address, which defines the owner and the location for pickup or shipping.

CREATE INDEX IF NOT EXISTS book_copy_book_id_idx
    ON "Book_copy" (book_id);
    -- RESTRICT: Book cannot be deleted if any copies still reference it.

CREATE INDEX IF NOT EXISTS book_copy_collection_address_id_idx
    ON "Book_copy" (collection_address_id);
    -- RESTRICT: Address cannot be deleted if any copies still reference it.


CREATE TABLE IF NOT EXISTS "Lents" (
    lent_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lent_status lent_status NOT NULL, -- requested -> accepted -> active -> returned, or rejected
    book_copie_id bigint NOT NULL REFERENCES "Book_copy"(book_copie_id) ON DELETE CASCADE, -- The physical copy that is lent
    lent_to bigint NOT NULL REFERENCES "Users"(user_id) ON DELETE CASCADE,
    shipping_requested BOOLEAN DEFAULT false, -- Whether the user requested shipping or not
    lent_startDate timestamptz NOT NULL DEFAULT now(),
    lent_enddate timestamptz NOT NULL DEFAULT now() + interval '60 days', -- Date when the book is expected to be returned
    lent_requesttimestamp timestamptz NOT NULL DEFAULT now(), -- Date when the user requested to lend the book
    return_date timestamptz, -- Date when the book was returned

    CONSTRAINT lent_dates_valid CHECK (lent_startDate < lent_enddate),
    CONSTRAINT lent_date_max_duration CHECK (lent_enddate <= lent_startDate + interval '120 days')
);
CREATE UNIQUE INDEX  IF NOT EXISTS lents_one_open_per_copy
    ON "Lents" (book_copie_id)
    WHERE lent_status IN ('accepted', 'active');
    -- Ensure that a book copy can only have one open lent at a time (accepted or active)

CREATE TABLE IF NOT EXISTS "Shipping" (
    shipping_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lent_id bigint NOT NULL REFERENCES "Lents"(lent_id) ON DELETE CASCADE,
    shipping_status shipping_status NOT NULL, -- Status of shipping (pending, shipped, delivered)
    tracking_number text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
); -- The adress for shipping is resolved over the lent_id

CREATE TABLE IF NOT EXISTS "Reviews" (
    review_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lent_id bigint NOT NULL UNIQUE REFERENCES "Lents"(lent_id) ON DELETE CASCADE, -- The lent that this review is associated with, User and Book over Join
    rating integer NOT NULL CHECK (rating >= 1 AND rating <= 5), -- Rating between 1 and 5
    comment text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
); -- Only if a user rented a book, he can write a review



CREATE TABLE IF NOT EXISTS "Messages" (
    message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sender_id bigint NOT NULL REFERENCES "Users"(user_id) ON DELETE CASCADE,
    receiver_id bigint NOT NULL REFERENCES "Users"(user_id) ON DELETE CASCADE,
    content text NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    read_status BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT message_not_self CHECK (sender_id <> receiver_id)
);

CREATE TABLE IF NOT EXISTS "UserAdresses" (
    address_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES "Users"(user_id) ON DELETE CASCADE,
    street_address text NOT NULL,
    city text NOT NULL,
    state text NOT NULL,
    postal_code text NOT NULL,
    country text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
); -- User addresses are separated from the Collection addresses. UserAdresses can be used for the delivery option.

CREATE TABLE IF NOT EXISTS "Protocols" (
    protocol_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Nullable + SET NULL: not every event has a copy/user, and the log survives deletions
    book_copie_id bigint REFERENCES "Book_copy"(book_copie_id) ON DELETE SET NULL,
    user_id bigint REFERENCES "Users"(user_id) ON DELETE SET NULL,
    protocol_type protocol_type NOT NULL, -- lending, returning, create_book...)
    details text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE EXTENSION IF NOT EXISTS btree_gist; -- Extension needed for the exclusion constraint on CollectionTimeWindows
DO $$ BEGIN
    CREATE TYPE timerange AS RANGE (subtype = time);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
CREATE TABLE IF NOT EXISTS "CollectionTimeWindows" (
    window_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    address_id bigint NOT NULL
               REFERENCES "CollectionAdresses"(address_id) ON DELETE CASCADE,
    -- 0 = Sonntag … 6 = Samstag (passt zu EXTRACT(DOW FROM ...))
    weekday    smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),

    start_time time NOT NULL,
    end_time   time NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    -- Ensures no overlapping collection time windows for the same address and weekday.
    CONSTRAINT window_valid CHECK (start_time < end_time),
    CONSTRAINT no_overlapping_window EXCLUDE USING gist (
    address_id WITH =, weekday WITH =,
    timerange(start_time, end_time) WITH &&)
);