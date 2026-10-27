SELECT * FROM available_books;
SELECT * FROM overdue_lents;
SELECT * FROM fullBookInfo;


SELECT * FROM "Users";

-- Get Book Titel of a book owned by a specific user
SELECT title from "Books"
JOIN "Book_copy" bc ON bc.book_id = "Books".book_id
JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
WHERE ca.user_id = 2;

-- List Messages from a specific user
SELECT * FROM "Messages"
WHERE sender_id = 2;

-- List Messages to a specific user
SELECT * FROM "Messages"
WHERE receiver_id = 2;