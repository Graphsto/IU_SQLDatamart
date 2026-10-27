
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('user', 'moderator', 'admin');
    CREATE TYPE lent_status AS ENUM ('requested', 'accepted', 'rejected', 'active', 'returned');
    CREATE TYPE shipping_status AS ENUM ('pending', 'shipped', 'delivered');
    CREATE TYPE protocol_type AS ENUM ('lending', 'returning', 'create_book', 'update_book', 'delete_book', 'create_user', 'update_user', 'delete_user', 'create_request', 'accept_request', 'reject_request', 'send_message', 'read_message', 'create_review');
    CREATE TYPE shipping_availability AS ENUM ('available', 'not_available');
    CREATE TYPE book_condition AS ENUM ('new', 'like_new', 'good', 'fair', 'poor');
    CREATE TYPE book_language AS ENUM ('english', 'german', 'french', 'spanish', 'italian', 'other');
    CREATE TYPE genre AS ENUM ('Fiktion', 'Roman', 'Romantik', 'Drama', 'Komödie', 'Horror',
        'Science-Fiction', 'Fantasy', 'Mystery', 'Liebesroman',
        'Thriller', 'Biografie', 'Geschichte', 'Kinderbücher',
        'Jugendbücher', 'Ratgeber', 'Gesundheit', 'Reisen',
        'Kochen', 'Kunst', 'Fotografie', 'Lyrik',
        'Religion', 'Philosophie');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
