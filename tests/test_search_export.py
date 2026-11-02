"""Tests for combined filtering, stable pagination, CSV export and edit/refresh.

The fixtures build their own catalog inside dedicated collections, so the
assertions are not affected by rows other test modules create in the shared
temporary database.

Data is inserted directly (assets + FTS row + tags) instead of going through
``ingest``: that keeps the created_at values exactly equal where the pagination
test needs them equal, and avoids generating JPEGs for every case.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
import uuid

import pytest

os.environ.setdefault("KEEPSTACK_DATA_DIR", tempfile.mkdtemp(prefix="keepstack-search-"))
os.environ["KEEPSTACK_AI_ENABLED"] = "false"
os.environ["KEEPSTACK_OCR_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from keepstack import search  # noqa: E402
from keepstack.app import app  # noqa: E402
from keepstack.db import get_conn, init_db  # noqa: E402

init_db()   # tests that touch the DB directly must not depend on fixture order

SHARED_TS = "2026-01-01T00:00:00+00:00"   # same format the app writes


def _collection(conn, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO collections (uuid, name, kind, created_at) VALUES (?,?, 'folder', ?)",
        (str(uuid.uuid4()), name, SHARED_TS),
    )
    return cur.lastrowid


def _asset(conn, *, title, created_at, tags=(), collection_id=None,
           status="active", description="", media_type="image", filename=None):
    """Insert one asset plus its FTS row, tags and collection membership."""
    uid = str(uuid.uuid4())
    filename = filename or title.lower().replace(" ", "_")[:40] + ".jpg"
    cur = conn.execute(
        """INSERT INTO assets (uuid, sha256, filename, mime_type, media_type, ext,
                               size, title, description, status, storage_key,
                               created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, uuid.uuid4().hex, filename, "image/jpeg", media_type, "jpg",
         1024, title, description, status, "blobs/" + uid, created_at, created_at),
    )
    asset_id = cur.lastrowid
    conn.execute(
        """INSERT INTO asset_fts (rowid, title, description, alt_text, filename,
                                  tags, metadata_text, custom_text)
           VALUES (?,?,?,?,?,?,?,?)""",
        (asset_id, title, description, "", filename, " ".join(tags), "", ""),
    )
    for name in tags:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) VALUES (?,?, 'manual')",
            (asset_id, tag_id),
        )
    if collection_id:
        conn.execute(
            "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) "
            "VALUES (?,?,?)",
            (collection_id, asset_id, created_at),
        )
    return {"id": asset_id, "uuid": uid, "title": title}


@pytest.fixture(scope="module")
def catalog():
    init_db()
    conn = get_conn()
    suffix = uuid.uuid4().hex[:8]

    # --- filtering / ordering / export fixture -----------------------------
    flt = _collection(conn, f"Harbor campaign {suffix}")
    a1 = _asset(conn, title=f"Harbor at dawn {suffix}", created_at="2026-02-01T09:00:00+00:00",
                tags=["approved", "campaign"], collection_id=flt)
    a2 = _asset(conn, title=f"Harbor at dusk {suffix}", created_at="2026-02-02T09:00:00+00:00",
                tags=["approved", "campaign"], collection_id=flt)
    a3 = _asset(conn, title=f"Harbor crane {suffix}", created_at="2026-02-03T09:00:00+00:00",
                tags=["approved", "campaign", "internal"], collection_id=flt)
    a4 = _asset(conn, title=f"Harbor survey {suffix}", created_at="2026-02-04T09:00:00+00:00",
                tags=["approved"], collection_id=flt)
    a5 = _asset(conn, title=f"Harbor notes {suffix}", created_at="2026-02-05T09:00:00+00:00",
                tags=["campaign"], collection_id=flt)
    a6 = _asset(conn, title=f"Harbor internal memo {suffix}", created_at="2026-02-06T09:00:00+00:00",
                tags=["approved", "campaign", "internal"], collection_id=flt)
    a7 = _asset(conn, title=f"Unrelated poster {suffix}", created_at="2026-02-07T09:00:00+00:00",
                tags=["approved", "campaign"], collection_id=flt)
    a8 = _asset(conn, title=f"Harbor archived {suffix}", created_at="2026-02-08T09:00:00+00:00",
                tags=["approved", "campaign"], collection_id=flt, status="deleted")

    # --- pagination fixture: seven assets sharing one created_at -----------
    pg = _collection(conn, f"Pagination {suffix}")
    pg_assets = [
        _asset(conn, title=f"Tied asset {i} {suffix}", created_at=SHARED_TS,
               tags=["tied"], collection_id=pg)
        for i in range(7)
    ]

    # --- CSV quoting fixture ----------------------------------------------
    qc = _collection(conn, f"Quoting {suffix}")
    tricky = _asset(
        conn, title=f'Dawn, "harbor" {suffix}', created_at="2026-03-01T09:00:00+00:00",
        tags=["approved", "campaign"], collection_id=qc,
        description="He said \"hi\", then left\nsecond line 港口",
    )
    # --- i18n fixture: Chinese text and a tag containing a space ----------
    i18n = _collection(conn, f"I18n {suffix}")
    cjk = _asset(conn, title=f"港口航拍 {suffix}", created_at="2026-04-01T09:00:00+00:00",
                 tags=["潮汐记录", "tidal maps"], collection_id=i18n,
                 description="上海港 潮汐 记录文档")

    # --- precision fixture: same second, different fraction ---------------
    prec = _collection(conn, f"Precision {suffix}")
    early = _asset(conn, title=f"Fraction early {suffix}",
                   created_at="2026-05-01T09:00:00+00:00", collection_id=prec)
    late = _asset(conn, title=f"Fraction late {suffix}",
                  created_at="2026-05-01T09:00:00.500000+00:00", collection_id=prec)
    conn.commit()

    yield {
        "suffix": suffix,
        "flt": flt, "pg": pg, "qc": qc, "i18n": i18n, "prec": prec,
        "a1": a1, "a2": a2, "a3": a3, "a4": a4, "a5": a5, "a6": a6, "a7": a7, "a8": a8,
        "pg_assets": pg_assets, "tricky": tricky, "cjk": cjk,
        "early": early, "late": late,
    }


def _uuids(result) -> list[str]:
    return [item["uuid"] for item in result["items"]]


def _export_rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# 1. combined filtering
# ---------------------------------------------------------------------------
def test_combined_filters_intersect(catalog):
    res = search.search(q="harbor", tags=["approved", "campaign"],
                        exclude_tags=["internal"], collection_id=catalog["flt"],
                        sort="oldest", limit=50)
    assert res["total"] == 2
    assert _uuids(res) == [catalog["a1"]["uuid"], catalog["a2"]["uuid"]]
    assert res["filters"]["tags"] == ["approved", "campaign"]


def test_include_tags_require_all_of_them(catalog):
    only_approved = search.search(q="harbor", tags=["approved"],
                                  collection_id=catalog["flt"], limit=50)
    assert {i["uuid"] for i in only_approved["items"]} == {
        catalog["a1"]["uuid"], catalog["a2"]["uuid"],
        catalog["a3"]["uuid"], catalog["a4"]["uuid"], catalog["a6"]["uuid"],
    }

    both = search.search(q="harbor", tags=["approved", "campaign"],
                         collection_id=catalog["flt"], limit=50)
    assert {i["uuid"] for i in both["items"]} == {
        catalog["a1"]["uuid"], catalog["a2"]["uuid"],
        catalog["a3"]["uuid"], catalog["a6"]["uuid"],
    }


def test_exclude_tag_rejects_any_match(catalog):
    res = search.search(q="harbor", exclude_tags=["campaign"],
                        collection_id=catalog["flt"], limit=50)
    found = {i["uuid"] for i in res["items"]}
    assert found == {catalog["a4"]["uuid"]}      # only the approved-only asset survives
    assert catalog["a5"]["uuid"] not in found


def test_duplicate_tags_do_not_change_results(catalog):
    once = search.search(tags=["approved"], collection_id=catalog["flt"], limit=50)
    twice = search.search(tags=["approved", "approved"], collection_id=catalog["flt"], limit=50)
    assert _uuids(once) == _uuids(twice)
    assert twice["total"] == 6                   # a1-a4, a6, a7; a8 is deleted


def test_same_tag_included_and_excluded_is_empty(catalog):
    res = search.search(tags=["approved"], exclude_tags=["approved"],
                        collection_id=catalog["flt"], limit=50)
    assert res["total"] == 0
    assert res["items"] == []


def test_deleted_assets_never_surface(catalog):
    res = search.search(q="harbor", collection_id=catalog["flt"], status="active", limit=50)
    assert catalog["a8"]["uuid"] not in {i["uuid"] for i in res["items"]}


def test_tags_are_case_insensitive(catalog):
    lower = search.search(tags=["approved"], collection_id=catalog["flt"], limit=50)
    upper = search.search(tags=["APPROVED"], collection_id=catalog["flt"], limit=50)
    assert _uuids(lower) == _uuids(upper)


def test_blank_and_punctuation_keywords_are_not_an_error(catalog):
    # whitespace and symbol-only input mean "no keyword constraint"
    blank = search.search(q="   ", collection_id=catalog["flt"], limit=50)
    punct = search.search(q="!!! *** ((( ))) -", collection_id=catalog["flt"], limit=50)
    assert blank["total"] == punct["total"] == 7  # all active members, 7 of 8 rows

    # symbols mixed with words stay a valid FTS query instead of crashing
    mixed = search.search(q='harbor "quoted" * (paren) -dash',
                          collection_id=catalog["flt"], limit=50)
    assert mixed["total"] == 0


# ---------------------------------------------------------------------------
# 2. sorting and pagination
# ---------------------------------------------------------------------------
def test_sort_is_honoured_with_a_keyword(catalog):
    oldest = search.search(q="harbor", tags=["approved", "campaign"],
                           exclude_tags=["internal"], collection_id=catalog["flt"],
                           sort="oldest", limit=50)
    newest = search.search(q="harbor", tags=["approved", "campaign"],
                           exclude_tags=["internal"], collection_id=catalog["flt"],
                           sort="newest", limit=50)
    assert _uuids(oldest)[0] == catalog["a1"]["uuid"]
    assert _uuids(newest)[0] == catalog["a2"]["uuid"]
    assert _uuids(oldest) == list(reversed(_uuids(newest)))


def test_pagination_is_stable_with_identical_created_at(catalog):
    collected: list[str] = []
    for offset in range(0, 7, 3):
        page = search.search(collection_id=catalog["pg"], sort="newest",
                             limit=3, offset=offset)
        collected.extend(_uuids(page))
    assert len(collected) == 7
    assert len(set(collected)) == 7, "an asset appeared on two pages"
    assert set(collected) == {a["uuid"] for a in catalog["pg_assets"]}

    # the reverse order visits the same set, in reverse, without duplicates
    reversed_pages: list[str] = []
    for offset in range(0, 7, 3):
        page = search.search(collection_id=catalog["pg"], sort="oldest",
                             limit=3, offset=offset)
        reversed_pages.extend(_uuids(page))
    assert reversed_pages == list(reversed(collected))


def test_page_metadata_and_beyond_the_end(catalog):
    page2 = search.search(collection_id=catalog["pg"], limit=3, offset=3)
    assert page2["page"] == 2 and page2["page_size"] == 3 and page2["total"] == 7

    beyond = search.search(collection_id=catalog["pg"], limit=3, offset=99)
    assert beyond["items"] == []
    assert beyond["total"] == 7


def test_limit_is_clamped_to_a_sane_range(catalog):
    assert search.search(collection_id=catalog["pg"], limit=9999)["page_size"] == search.MAX_PAGE_SIZE
    assert search.search(collection_id=catalog["pg"], limit=0)["page_size"] == 1
    assert search.search(collection_id=catalog["pg"], offset=-5)["page"] == 1


# ---------------------------------------------------------------------------
# 3. export matches the list
# ---------------------------------------------------------------------------
def test_export_contains_every_match_in_list_order(catalog):
    args = dict(q="harbor", tags=["approved", "campaign"], exclude_tags=["internal"],
                collection_id=catalog["flt"], sort="oldest")
    listed = search.search(**args, limit=1, offset=0)
    second = search.search(**args, limit=1, offset=1)

    rows = _export_rows(search.export_csv(**args))
    assert len(rows) == listed["total"] == 2
    assert [r["uuid"] for r in rows] == _uuids(listed) + _uuids(second)
    assert rows[0]["tags"] == "approved|campaign"
    assert set(rows[0]) == set(search.EXPORT_COLUMNS)


def test_export_writes_header_only_when_nothing_matches(catalog):
    text = search.export_csv(tags=["approved"], exclude_tags=["approved"],
                             collection_id=catalog["flt"])
    lines = [ln for ln in text.split("\r\n") if ln]
    assert lines == [",".join(search.EXPORT_COLUMNS)]


def test_export_quotes_commas_quotes_newlines_and_chinese(catalog):
    text = search.export_csv(collection_id=catalog["qc"])
    rows = _export_rows(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == catalog["tricky"]["title"]
    assert row["description"] == 'He said "hi", then left\nsecond line 港口'


def test_export_honours_media_type_filter(catalog):
    image_only = _export_rows(search.export_csv(collection_id=catalog["flt"], media_type="image"))
    none = _export_rows(search.export_csv(collection_id=catalog["flt"], media_type="video"))
    assert len(image_only) == 7
    assert none == []


def test_keyword_matches_chinese_token_prefix(catalog):
    hit = search.search(q="港口", collection_id=catalog["i18n"])
    assert [i["uuid"] for i in hit["items"]] == [catalog["cjk"]["uuid"]]
    # unicode61 has no CJK word segmentation: a run of ideographs is a single
    # token, so only a prefix of that token matches. That is the tokenizer the
    # brief says to keep, so this is behaviour, not a bug.
    assert search.search(q="航拍", collection_id=catalog["i18n"])["total"] == 0


def test_tag_with_a_space_matches_by_full_name(catalog):
    hit = search.search(tags=["tidal maps"], collection_id=catalog["i18n"])
    assert [i["uuid"] for i in hit["items"]] == [catalog["cjk"]["uuid"]]
    assert search.search(tags=["tidal"], collection_id=catalog["i18n"])["total"] == 0


def test_tag_containing_a_comma_is_one_name_not_two():
    """A comma belongs to the tag name; it is not a separator.

    The write path stores whatever name it is given, so ``night,shift`` is a
    legal tag. Splitting the filter value on commas would make that tag
    impossible to select by its full name.
    """
    conn = get_conn()
    coll = _collection(conn, f"Comma {uuid.uuid4().hex[:8]}")
    _asset(conn, title="Comma tagged", created_at=SHARED_TS,
           tags=["night,shift", "night", "shift"], collection_id=coll)
    conn.commit()

    exact = search.search(tags=["night,shift"], collection_id=coll)
    assert exact["total"] == 1
    # Two separate requests for the parts are not the same thing.
    assert search.search(tags=["night,shift", "shift"], collection_id=coll)["total"] == 1
    # And an HTTP round trip keeps the encoded comma intact.
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        client.headers.update({"Authorization": "Bearer " + login.json()["token"]})
        res = client.get("/api/assets", params={"tags": "night,shift", "collection_id": coll})
    assert res.status_code == 200
    assert res.json()["total"] == 1


def test_unknown_collection_id_returns_nothing(catalog):
    """An id that cannot exist must not silently widen to the whole catalog."""
    assert search.search(collection_id=0)["total"] == 0
    assert search.search(collection_id=-7)["total"] == 0
    assert search.export_csv(collection_id=0).count("\r\n") == 1   # header only


def test_tag_names_are_normalised_to_nfc():
    """``café`` spelled with a combining accent must match the composed form.

    Tags arrive both from metadata parsing and from users typing into the
    filter box; those two can disagree on unicode normal form while looking
    identical, which SQLite treats as different strings.
    """
    assert search.normalize_tag_name("  Café ") == "caf\u00e9"
    assert search.normalize_tag_name("cafe\u0301") == "caf\u00e9"

    conn = get_conn()
    coll = _collection(conn, f"Unicode {uuid.uuid4().hex[:8]}")
    # Stored the way the write path stores it (canonical form).
    _asset(conn, title="Accent tag", created_at=SHARED_TS,
           tags=[search.normalize_tag_name("cafe\u0301")], collection_id=coll)
    conn.commit()

    assert search.search(tags=["caf\u00e9"], collection_id=coll)["total"] == 1
    assert search.search(tags=["cafe\u0301"], collection_id=coll)["total"] == 1

    # The write endpoint canonicalises too, so both spellings collapse to one row.
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        client.headers.update({"Authorization": "Bearer " + login.json()["token"]})
        rows = client.get("/api/assets", params={"collection_id": coll}).json()["items"]
        client.post(f"/api/assets/{rows[0]['uuid']}/tags", json={"name": "Cafe\u0301 R\u00e9sum\u00e9"})
        names = [t["name"] for t in client.get("/api/tags").json()["tags"]]
    assert "caf\u00e9 r\u00e9sum\u00e9" in names
    assert "cafe\u0301 r\u00e9sum\u00e9" not in names


def test_missing_text_fields_come_back_as_empty_strings(catalog):
    """A null title must not reach the client as ``null`` while the export
    prints it as an empty cell."""
    conn = get_conn()
    coll = _collection(conn, f"Nulls {uuid.uuid4().hex[:8]}")
    cur = conn.execute(
        """INSERT INTO assets (uuid, sha256, filename, mime_type, media_type, ext,
                               size, title, description, alt_text, status,
                               storage_key, created_at, updated_at)
           VALUES (?,?,'bare.bin',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'active',?,?,?)""",
        (str(uuid.uuid4()), uuid.uuid4().hex, SHARED_TS, SHARED_TS, SHARED_TS))
    conn.execute("INSERT INTO collection_assets (collection_id, asset_id, added_at) "
                 "VALUES (?,?,?)", (coll, cur.lastrowid, SHARED_TS))
    conn.commit()

    item = search.search(collection_id=coll)["items"][0]
    assert item["title"] == "" and item["media_type"] == "" and item["mime_type"] == ""
    assert item["alt_text"] == "" and item["ext"] == ""
    row = _export_rows(search.export_csv(collection_id=coll))[0]
    assert row["title"] == "" and row["media_type"] == "" and row["tags"] == ""


def test_ordering_is_chronological_across_sub_second_values(catalog):
    asc = search.search(collection_id=catalog["prec"], sort="oldest", limit=10)
    assert _uuids(asc) == [catalog["early"]["uuid"], catalog["late"]["uuid"]]
    desc = search.search(collection_id=catalog["prec"], sort="newest", limit=10)
    assert _uuids(desc) == list(reversed(_uuids(asc)))


def test_export_carries_chinese_fields(catalog):
    rows = _export_rows(search.export_csv(collection_id=catalog["i18n"]))
    assert len(rows) == 1
    assert rows[0]["title"].startswith("港口航拍")
    assert rows[0]["description"] == "上海港 潮汐 记录文档"
    assert rows[0]["tags"] == "tidal maps|潮汐记录"


def test_export_keeps_tags_past_the_first_lookup_chunk():
    """Tags are fetched 500 assets at a time; crossing that boundary must not
    silently drop the tag column for later rows."""
    conn = get_conn()
    coll = _collection(conn, f"Chunk {uuid.uuid4().hex[:8]}")
    total = 505
    for i in range(total):
        _asset(conn, title=f"Chunk asset {i:03d}", created_at="2026-07-01T00:00:00+00:00",
               tags=["chunky"], collection_id=coll)
    conn.commit()

    rows = _export_rows(search.export_csv(collection_id=coll))
    assert len(rows) == total
    assert {r["tags"] for r in rows} == {"chunky"}


# ---------------------------------------------------------------------------
# 4. HTTP layer
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200, login.text
        c.headers.update({"Authorization": "Bearer " + login.json()["token"]})
        yield c


def _query(catalog, **overrides):
    params = {
        "q": "harbor",
        "tags": ["approved", "campaign"],
        "exclude_tags": ["internal"],
        "collection_id": catalog["flt"],
        "sort": "oldest",
        "page_size": 1,
        "page": 1,
    }
    params.update(overrides)
    return params


def test_api_lists_and_pages(catalog, client):
    first = client.get("/api/assets", params=_query(catalog)).json()
    assert first["total"] == 2
    assert len(first["items"]) == 1
    assert first["items"][0]["uuid"] == catalog["a1"]["uuid"]
    assert first["page"] == 1 and first["page_size"] == 1

    second = client.get("/api/assets", params=_query(catalog, page=2)).json()
    assert second["items"][0]["uuid"] == catalog["a2"]["uuid"]

    empty = client.get("/api/assets", params=_query(catalog, page=99)).json()
    assert empty["items"] == [] and empty["total"] == 2


def test_api_rejects_invalid_parameters(catalog, client):
    assert client.get("/api/assets", params=_query(catalog, page=0)).status_code == 422
    assert client.get("/api/assets", params=_query(catalog, page_size=0)).status_code == 422
    assert client.get("/api/assets", params=_query(catalog, page_size=10_000)).status_code == 422
    assert client.get("/api/assets", params=_query(catalog, status="bogus")).status_code == 400
    assert client.get("/api/assets", params=_query(catalog, sort="bogus")).status_code == 400
    assert client.get("/api/assets", params=_query(catalog, mode="bogus")).status_code == 400


def test_export_endpoint_requires_auth(catalog):
    with TestClient(app) as anon:
        res = anon.get("/api/assets/export.csv", params={"collection_id": catalog["flt"]})
        assert res.status_code == 401


def test_export_endpoint_returns_downloadable_utf8_csv(catalog, client):
    res = client.get("/api/assets/export.csv", params=_query(catalog, page=2))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert res.content.startswith(b"\xef\xbb\xbf"), "missing UTF-8 BOM for Excel"

    # page=2 must not shrink the export: it always covers every match
    rows = list(csv.DictReader(io.StringIO(res.content.decode("utf-8-sig"))))
    assert [r["uuid"] for r in rows] == [catalog["a1"]["uuid"], catalog["a2"]["uuid"]]


def test_edit_then_query_and_export_reflect_the_change(catalog, client):
    target = catalog["a4"]
    renamed = f"Harbor survey renamed {catalog['suffix']}"

    patched = client.patch(f"/api/assets/{target['uuid']}",
                           json={"title": renamed, "description": "updated by test"})
    assert patched.status_code == 200

    found = client.get("/api/assets", params={"q": "renamed", "collection_id": catalog["flt"]}).json()
    assert [i["uuid"] for i in found["items"]] == [target["uuid"]]

    added = client.post(f"/api/assets/{target['uuid']}/tags", json={"name": "campaign"})
    assert added.status_code == 200

    both_tags = client.get("/api/assets", params={
        "q": "renamed", "tags": ["approved", "campaign"],
        "collection_id": catalog["flt"],
    }).json()
    assert [i["uuid"] for i in both_tags["items"]] == [target["uuid"]]

    exported = list(csv.DictReader(io.StringIO(
        client.get("/api/assets/export.csv", params={"q": "renamed",
                                                     "collection_id": catalog["flt"]})
        .content.decode("utf-8-sig"))))
    assert [r["title"] for r in exported] == [renamed]
    assert exported[0]["description"] == "updated by test"
    assert exported[0]["tags"] == "approved|campaign"
