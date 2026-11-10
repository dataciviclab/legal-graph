"""Contratto degli allegati AKN (#94): codici con il testo negli <attachments>."""

from legal_graph import legal_text as lt


def test_sezione_doc_estrae_la_sezione():
    md = "# Allegati - X\n\n## A-art. 1\n\ntesto uno\n\n## A-art. 2\n\ntesto due\n"
    assert lt._sezione_doc(md, "A-art. 1") == "testo uno"
    assert lt._sezione_doc(md, "A-art. 2") == "testo due"
    assert lt._sezione_doc(md, "A-art. 3") is None


def test_match_articolo_non_confonde_1_con_10():
    docs = [
        {"doc_id": "a", "doc_name": "X-art. 1"},
        {"doc_id": "b", "doc_name": "X-art. 10"},
    ]
    assert [d["doc_id"] for d in lt.match_articolo(docs, 1)] == ["a"]
    assert [d["doc_id"] for d in lt.match_articolo(docs, 10)] == ["b"]


def test_is_shell_ratio():
    # Codice civile: body 3.081, allegati 1.289.587 → guscio
    assert lt.is_shell(1_289_587, 3_081) is True
    # D.Lgs 36/2023: body 824.357, allegati 855.506 → non guscio
    assert lt.is_shell(855_506, 824_357) is False


def _docs():
    return [
        {"doc_id": "a", "doc_name": "Preleggi-art. 1", "chars": 184, "rel_path": "allegati/x.md"},
        {"doc_id": "b", "doc_name": "CODICE-art. 1", "chars": 440, "rel_path": "allegati/x.md"},
    ]


def test_articolo_ambiguo_restituisce_candidati(monkeypatch):
    monkeypatch.setattr(lt, "attachment_docs", lambda urn: _docs())
    r = lt.fetch_attachment_text(urn="u", articolo=1)
    assert r["ambiguous"] is True
    assert r["attachments"]["count"] == 2
    assert {d["doc_id"] for d in r["attachments"]["docs"]} == {"a", "b"}


def test_articolo_unico_serve_il_doc(monkeypatch):
    docs = [{"doc_id": "b", "doc_name": "CODICE-art. 2043", "chars": 188, "rel_path": "allegati/x.md"}]
    monkeypatch.setattr(lt, "attachment_docs", lambda urn: docs)
    monkeypatch.setattr(
        lt, "_serve_doc", lambda urn, target, max_chars: {"doc_id": target["doc_id"], "text": "art"}
    )
    r = lt.fetch_attachment_text(urn="u", articolo=2043, max_chars=1000)
    assert r["doc_id"] == "b" and r["text"] == "art"


def test_doc_inesistente(monkeypatch):
    monkeypatch.setattr(lt, "attachment_docs", lambda urn: _docs())
    r = lt.fetch_attachment_text(urn="u", doc="nope")
    assert "error" in r and r["attachments"]["count"] == 2


def test_doc_accetta_anche_doc_name(monkeypatch):
    monkeypatch.setattr(lt, "attachment_docs", lambda urn: _docs())
    monkeypatch.setattr(
        lt, "_serve_doc", lambda urn, target, max_chars: {"doc_id": target["doc_id"], "text": "ok"}
    )
    r = lt.fetch_attachment_text(urn="u", doc="Preleggi-art. 1")
    assert r["doc_id"] == "a" and r["text"] == "ok"


def test_cache_per_file_una_sola_fetch(monkeypatch, tmp_path):
    """Due doc dello stesso file → un solo download (la chiave è l'URL)."""
    monkeypatch.setattr(lt, "TEXT_CACHE", tmp_path)
    calls: list[str] = []

    class _Resp:
        ok = True
        text = "# Allegati - X\n\n## A-art. 1\n\ntesto\n"

    class _Result:
        is_ok = True
        response = _Resp()

    class _Http:
        def get(self, url):
            calls.append(url)
            return _Result()

    monkeypatch.setattr(lt, "_get_http", lambda: _Http())
    url = "https://example.test/allegati/x.md"
    a, _ = lt._fetch_url(url, ttl=3600)
    b, _ = lt._fetch_url(url, ttl=3600)
    assert a == b
    assert len(calls) == 1


def test_cache_ttl_scaduto_riscarica(monkeypatch, tmp_path):
    monkeypatch.setattr(lt, "TEXT_CACHE", tmp_path)
    calls: list[str] = []

    class _Resp:
        ok = True
        text = "# Allegati\n"

    class _Result:
        is_ok = True
        response = _Resp()

    class _Http:
        def get(self, url):
            calls.append(url)
            return _Result()

    monkeypatch.setattr(lt, "_get_http", lambda: _Http())
    url = "https://example.test/allegati/y.md"
    lt._fetch_url(url, ttl=60)
    path = lt._cache_path(url)
    import os

    old = path.stat().st_mtime - 7200
    os.utime(path, (old, old))
    lt._fetch_url(url, ttl=60)
    assert len(calls) == 2


def test_articolo_stringa_normalizzata():
    docs = [{"doc_id": "a", "doc_name": "CODICE-art. 2043"}]
    assert lt.match_articolo(docs, "Art. 2043") == docs
    assert lt.match_articolo(docs, "art.2043.") == docs
