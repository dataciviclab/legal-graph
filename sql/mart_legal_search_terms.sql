-- mart_legal_search_terms.sql — indice lessicale per `legal_search`
--
-- Una riga per (id, termine) sugli atti navigabili da `legal_search`
-- (stessi tipi della view `act`: niente emendamenti, sommari, votazioni,
-- persone). Il termine è lo **stem conservativo** — minuscolo, accenti
-- rimossi, vocale finale tolta se la parola è più lunga di 4 caratteri —
-- la stessa regola di `legal_graph.mcp.textfold._term_stem`.
--
-- Il peso BM25 è precalcolato qui (k1=1.2, b=0.75) e include il titolo
-- pesato 3x: `legal_search` somma i pesi dei termini della query, senza
-- ricalcolare nulla a ogni chiamata. `ORDER BY term` è un contratto di
-- performance: il filtro `term IN (...)` usa le statistiche parquet.
--
-- Il testo viene da `italia-corpus/testi` (clean GCS), lo stesso artifact
-- che `legal_text` serve su richiesta: una sola copia del corpus.
-- I link markdown e gli URL non sono testo dell'atto e vengono rimossi
-- prima del conteggio (evita di indicizzare `VIGENZA` e nomi di file).
--
-- Stopword: sono indicizzate, non filtrate qui. Il consumer le toglie dalla
-- query (`textfold._STOP`), quindi non vengono mai interrogate e il loro
-- idf≈0 non entra mai nel punteggio. Sono 713.743 righe su 7.209.185
-- (9,9%): il costo è accettato per non duplicare la lista in due linguaggi
-- (SQL + Python) e per non rischiare che le due copie divergano. L'unica
-- fonte della lista resta il codice.

WITH docs AS (
    SELECT id, title_folded
    FROM mart_legal_search_keys
    WHERE tipo NOT IN (
        'EMENDAMENTO', 'SOMMARIO', 'RISOLUZIONE', 'VOTAZIONE',
        'DEPUTATO', 'SENATORE', 'GIUDICE', 'PNRR'
    )
),

-- Una sola espressione per atto: la più lunga (di norma la VIGENZA
-- consolidata). Senza questa scelta ORIGINALE + VIGENZA conterebbero
-- due volte lo stesso testo e gonfierebbero tf.
body AS (
    SELECT id, testo
    FROM (
        SELECT
            urn AS id,
            testo,
            ROW_NUMBER() OVER (
                PARTITION BY urn ORDER BY length(testo) DESC
            ) AS _rn
        FROM read_parquet('{support.testi.path}')
        WHERE NULLIF(urn, '') IS NOT NULL
    )
    WHERE _rn = 1
),

body_clean AS (
    SELECT
        b.id,
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    translate(
                        lower(b.testo),
                        'àáâãäåèéêëìíîïòóôõöùúûüñç',
                        'aaaaaaeeeeiiiiooooouuuunc'
                    ),
                    '\]\([^)]*\)', ' ', 'g'
                ),
                'https?://[^ ]+', ' ', 'g'
            ),
            '&[a-z]+;', ' ', 'g'
        ) AS testo
    FROM body b
    JOIN docs d ON d.id = b.id
),

title_terms AS (
    SELECT id, UNNEST(regexp_extract_all(title_folded, '[a-z]+')) AS term
    FROM docs
),

body_terms AS (
    SELECT id, UNNEST(regexp_extract_all(testo, '[a-z]+')) AS term
    FROM body_clean
),

-- Il titolo pesa 3x: un atto che nomina il tema nel titolo è più
-- probabilmente il tema, ma il peso non basta a battere un testo che
-- lo tratta centinaia di volte (vedi eval temi 2026-11).
fields AS (
    SELECT id, term, COUNT(*) * 3.0 AS w
    FROM title_terms
    WHERE length(term) >= 3
    GROUP BY 1, 2
    UNION ALL
    SELECT id, term, COUNT(*) AS w
    FROM body_terms
    WHERE length(term) >= 3
    GROUP BY 1, 2
),

-- Stem conservativo: la stessa espressione è in `textfold._term_stem`.
stemmed AS (
    SELECT
        id,
        CASE
            WHEN length(term) > 4 AND right(term, 1) IN ('a', 'e', 'i', 'o')
                THEN left(term, length(term) - 1)
            ELSE term
        END AS term,
        w
    FROM fields
),

tf AS (
    SELECT id, term, SUM(w) AS tf
    FROM stemmed
    GROUP BY 1, 2
),

doc_len AS (
    SELECT id, SUM(w) AS dl
    FROM stemmed
    GROUP BY 1
),

df AS (
    SELECT term, COUNT(*) AS df
    FROM tf
    GROUP BY 1
),

stats AS (
    SELECT (SELECT COUNT(*) FROM docs) AS n, AVG(dl) AS avgdl
    FROM doc_len
)

SELECT
    t.id,
    t.term,
    LN(1 + (s.n - d.df + 0.5) / (d.df + 0.5))
        * (t.tf * 2.2)
        / (t.tf + 1.2 * (0.25 + 0.75 * l.dl / s.avgdl)) AS weight
FROM tf t
JOIN df d USING (term)
JOIN doc_len l USING (id)
CROSS JOIN stats s
ORDER BY t.term, t.id
