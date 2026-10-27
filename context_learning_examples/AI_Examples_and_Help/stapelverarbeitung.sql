CREATE PROCEDURE stapelverarbeitung()
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT id FROM warteschlange ORDER BY id LOOP
    UPDATE warteschlange SET verarbeitet = true WHERE id = r.id;
    IF r.id % 1000 = 0 THEN
      COMMIT;
    END IF;
  END LOOP;
END $$;

--Der klassische Fall ist der Batchlauf mit Zwischen-Commits.
--Ohne sie entstünde eine einzige riesige Transaktion, die VACUUM blockiert und bei einem Fehler nach Stunden alles verwirft: