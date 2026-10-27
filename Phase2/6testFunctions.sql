-- Active: 1787569567255@@10.8.0.1@5432@buechertausch
-- call write_review

-- 1. Write Review without an existing Lent
SELECT write_review(p_lent_id => 1, p_user_id => 1, p_rating => 5, p_comment => 'Great book!');
-- 2. Lent exists, but the user is not the borrower
SELECT write_review(p_lent_id => 2, p_user_id => 1, p_rating => 4, p_comment => 'Interesting read.');

-- 3. Create Lent and then write a review
SELECT create_book_request(p_book_copie_id => 2, p_user_id => 3, p_shipping => false);
SELECT accept_book_request(p_lent_id => 23, p_user_id => 4);  -- Alice (Besitzerin) nimmt an
SELECT hand_over_book(p_lent_id => 23, p_user_id => 4);       -- Alice übergibt
SELECT return_book(p_lent_id => 23, p_user_id => 4);          -- Alice bestätigt die Rückgabe
SELECT write_review(p_lent_id => 23, p_user_id => 3, p_rating => 5, p_comment => 'Great book!');  -- Charlie bewertet

--------------------------

-- Create Collection Time Window
SELECT create_collection_time_window(p_address_id => 1, p_user_id => 1, p_weekday => 2, p_start_time => '10:00:00', p_end_time => '12:00:00');

-- Attempt to create overlapping Collection Time Window
SELECT create_collection_time_window(p_address_id => 1, p_user_id => 1, p_weekday => 2, p_start_time => '11:00:00', p_end_time => '13:00:00');

---------------------------

-- Generate Monthly Report (Text)
SELECT generate_monthly_report(p_year => 2026, p_month => 9);

-- Generate Monthly Report (JSON)
SELECT generate_monthly_report_json(p_year => 2026, p_month => 9);

---------------------------

-- Book must e returned (Check if a book is overdue)
SELECT book_must_be_returned(p_lent_id => 23);

--------------------------
-- Test Full User Stats and User-related Functions
SELECT getFullUserStats(p_user_id => 2);

SELECT getAllBooksbyUser(p_user_id => 2);
SELECT getAllLentsbyUser(p_user_id => 2);
