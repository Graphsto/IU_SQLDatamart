Hallo Jakob,

für die nächste Phase würde ich dir empfehlen, noch ein paar Punkte zu überarbeiten:

    Räumliche Suche: Die App soll Bücher in der näheren Umgebung anzeigen können. In deinen Abholadressen fehlen bisher geografische Koordinaten. Ergänze beispielsweise Breiten- und Längengrad und überlege, wie du die Umkreissuche in PostgreSQL umsetzen möchtest.
    -> Erledigt

    Ausleihstatus: Du verwendest lent_status, lent_status_active und status_request_accepted. Hier werden teilweise dieselben Informationen mehrfach gespeichert. Überlege, ob ein einzelner Status mit klar definierten Übergängen ausreicht, damit keine widersprüchlichen Zustände entstehen.
    -> erledigt

    Ausleihvorgänge: Überprüfe, wie du sicherstellst, dass ein Exemplar nicht gleichzeitig mehrfach ausgeliehen werden kann. Außerdem sollte lent_from immer mit dem tatsächlichen Besitzer des Exemplars übereinstimmen.
    -> Erledigt

    Abholzeitfenster: Deine Zeitfenster sind nach Wochentagen organisiert. Überlege, wie du konkrete Abholtermine zuordnen möchtest und wie du Überschneidungen zwischen Zeitfenstern verhinderst.
    -> Erledigt

    Bewertungen: Aktuell kann jeder Benutzer ein Buch einmal bewerten, unabhängig davon, ob er es ausgeliehen hat. Eine Verknüpfung mit dem Ausleihvorgang wäre sinnvoll, damit nur tatsächliche Entleiher eine Bewertung abgeben können.
    -> Erledigt

    Buchdaten: Autoren werden derzeit als Textfeld gespeichert. Überlege, ob eine eigene AUTOR-Tabelle mit einer n zu BOOKS sinnvoll wäre, insbesondere für Bücher mit mehreren Autoren.
    -> Erledigt, Autoren haben ihren eigenen Table

    Datenintegrität: Ergänze Prüfungen für positive Leihdauern, gültige Ausleihzeiträume und Versandkosten. Achte auch darauf, dass Versand nur ausgewählt werden kann, wenn der Besitzer diese Option anbietet.
    -> Prüfungen waren bereits vorhanden

    ERM und Datenwörterbuch: Gleiche die beiden Dateien nochmals ab. Beispielsweise unterscheiden sich die Bezeichnungen und Datentypen bei Books, Book_copy und Lents. Das ERM sollte genau die Struktur zeigen, die du später implementierst.
    -> Erledigt

    Formale Anforderungen: Deine Planungs-PDF umfasst sechs Seiten. Achte auf die Vorgabe von maximal vier Seiten und konzentriere dich auf die geforderten Inhalte.
    -> Die Aufgabenstellung fordert 4 Seiten Inhalt. Meine Arbeit beeinhaltet 4 Seiten Text + 1 Seite Titelblatt + 1 Seite Bilder (zusammengerechnet). Ergibt 4 Seiten Inhalt. Kein Anpassungsbedarf.

Wenn du diese Punkte vor der Implementierung berücksichtigst, bist du für Phase 2 gut vorbereitet.