CREATE FUNCTION get_film_count(len_from INT, len_to INT)
RETURNS INT LANGUAGE plpgsql
AS $$
DECLARE
   film_count INTEGER;
BEGIN
   SELECT COUNT(*)
   INTO film_count
   FROM film
   WHERE length BETWEEN len_from AND len_to;
   RETURN film_count;
END;
$$;

    --Positional Notation: 

SELECT get_film_count(40, 90);

    --Named Notation: 

SELECT get_film_count(len_from => 40, len_to => 90);

    --Mixed Notation: 

SELECT get_film_count(40, len_to => 90);