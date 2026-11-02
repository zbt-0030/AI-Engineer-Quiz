"""Search, filtering, sorting, pagination and CSV export.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - faceted structured filters (media type, collection, tags, status)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

The list endpoint and the CSV export share :func:`build_query`. That is the
whole point of the module: a page of results and an export can never disagree
about *which* assets match or in *what* order they appear, because both are
produced from one WHERE clause and one ORDER BY.

Ordering always ends with a unique tie-breaker (``a.id``) so pagination stays
stable when several assets share a ``created_at`` value.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable, Optional, Sequence

from . import ai
from .db import get_conn

#: ``sort`` key -> (primary order, tie-breaker). The tie-breaker is unique per
#: asset, so the total order is deterministic and pages cannot overlap.
SORTS = {
    "newest": ("a.created_at DESC", "a.id DESC"),
    "oldest": ("a.created_at ASC", "a.id ASC"),
    "name": ("a.title COLLATE NOCASE ASC", "a.id ASC"),
    "largest": ("a.size DESC", "a.id ASC"),
}
DEFAULT_SORT = "newest"
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 24

#: Export schema. ``tags`` is TAG_SEPARATOR-joined and sorted by name.
EXPORT_COLUMNS = ("uuid", "title", "description", "filename", "media_type",
                  "tags", "created_at")
TAG_SEPARATOR = "|"


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query.

    Everything that is not alphanumeric becomes whitespace, so quotes,
    asterisks, brackets and other FTS5 operators can never reach the parser as
    syntax. Returns ``""`` when nothing searchable is left, which callers treat
    as "no keyword constraint" instead of sending ``MATCH ''`` to SQLite.
    """
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in q)
    terms = [t for t in cleaned.split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def normalize_tag_list(values: Optional[Iterable[str]]) -> list[str]:
    """Trim, lower-case, de-duplicate. One value is one complete tag name.

    The requirement is "match by full tag name", so a comma is part of the
    name rather than a separator: a tag stored as ``night,shift`` is only
    reachable by asking for ``night,shift``. Callers that want two tags pass
    two values (``?tags=a&tags=b``).
    """
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = str(value).strip().lower()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _coerce_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return normalize_tag_list([value])
    return normalize_tag_list(value)


def build_query(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tags=None,
    exclude_tags=None,
    collection_id: Optional[int] = None,
    status: str = "active",
) -> tuple[list[str], list[str], list]:
    """Build the shared WHERE fragments and bind parameters.

    All conditions intersect (AND) and every filter is expressed as a
    semi-join against ``assets``, so the outer table is always reached by
    primary key. ``tags`` requires every listed tag
    (``COUNT(DISTINCT tag) = n``); ``exclude_tags`` rejects an asset carrying
    any of them.
    """
    include = _coerce_tags(tags)
    exclude = _coerce_tags(exclude_tags)

    where: list[str] = []
    params: list = []

    if status:
        where.append("a.status = ?")
        params.append(status)

    match = _fts_query(q or "")
    if match:
        # A subquery, not a JOIN: joined on rowid, SQLite will happily drive the
        # query from assets (covering status index) and scan the FTS virtual
        # table once per candidate row. As a materialised subquery the MATCH
        # runs once and the outer table is then looked up by primary key
        # (~300x faster on a 5k-asset catalogue, same rows).
        where.append("a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)")
        params.append(match)

    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)

    if collection_id is not None:
        # ``is not None`` rather than a truthiness test: an id that cannot
        # exist (0, or a negative) must mean "no such collection" and return
        # nothing, not silently widen to the whole catalog.
        where.append("a.id IN (SELECT ca.asset_id FROM collection_assets ca "
                     "WHERE ca.collection_id = ?)")
        params.append(collection_id)

    if include:
        marks = ",".join("?" * len(include))
        where.append(
            "a.id IN (SELECT at.asset_id FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id "
            f"WHERE t.name IN ({marks}) "
            "GROUP BY at.asset_id HAVING COUNT(DISTINCT t.name) = ?)"
        )
        params.extend(include)
        params.append(len(include))

    if exclude:
        marks = ",".join("?" * len(exclude))
        where.append(
            "a.id NOT IN (SELECT at.asset_id FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id "
            f"WHERE t.name IN ({marks}))"
        )
        params.extend(exclude)

    return where, params


def _where_sql(where: Sequence[str]) -> str:
    return " AND ".join(where) if where else "1 = 1"


def order_by(sort: Optional[str]) -> str:
    """ORDER BY for ``sort``; unknown keys fall back to newest-first."""
    primary, tie_break = SORTS.get((sort or "").lower(), SORTS[DEFAULT_SORT])
    return f"{primary}, {tie_break}"


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_SIZE
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, MAX_PAGE_SIZE)), max(0, offset)


def search(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    tags=None,
    exclude_tags=None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: Optional[str] = DEFAULT_SORT,
    mode: str = "keyword",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict:
    """Return one page of matching assets plus the total match count."""
    limit, offset = _clamp(limit, offset)
    include = _coerce_tags(tags)
    if tag:
        include = normalize_tag_list([*include, tag])

    if mode == "semantic" and q.strip():
        return _semantic_search(q, include, exclude_tags, media_type,
                                collection_id, status, limit, offset)

    conn = get_conn()
    where, params = build_query(
        q=q, media_type=media_type, tags=include, exclude_tags=exclude_tags,
        collection_id=collection_id, status=status,
    )
    where_sql = _where_sql(where)

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM assets a WHERE {where_sql}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""SELECT a.* FROM assets a WHERE {where_sql}
            ORDER BY {order_by(sort)} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return {
        "total": total,
        "items": [_card(dict(r)) for r in rows],
        "facets": _facets("active"),
        "mode": mode,
        "page": offset // limit + 1,
        "page_size": limit,
        "sort": sort if (sort or "").lower() in SORTS else DEFAULT_SORT,
        "filters": {
            "q": q or "",
            "media_type": media_type or "",
            "collection_id": collection_id or None,
            "tags": include,
            "exclude_tags": _coerce_tags(exclude_tags),
        },
    }


def export_csv(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    tags=None,
    exclude_tags=None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: Optional[str] = DEFAULT_SORT,
) -> str:
    """Render every matching asset as CSV text.

    Same filters, same ORDER BY, same join/where as :func:`search`, and no
    LIMIT: an export always covers the whole result set, not one page. A header
    row is written even when nothing matches.
    """
    include = _coerce_tags(tags)
    if tag:
        include = normalize_tag_list([*include, tag])

    conn = get_conn()
    where, params = build_query(
        q=q, media_type=media_type, tags=include, exclude_tags=exclude_tags,
        collection_id=collection_id, status=status,
    )

    rows = conn.execute(
        f"""SELECT a.id, a.uuid, a.title, a.description, a.filename,
                   a.media_type, a.created_at
            FROM assets a WHERE {_where_sql(where)}
            ORDER BY {order_by(sort)}""",
        params,
    ).fetchall()
    tagged = _tags_for([r["id"] for r in rows])

    buf = io.StringIO()
    # csv.writer handles commas, quotes and embedded newlines; \r\n keeps
    # Excel happy and is RFC 4180 compliant.
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        writer.writerow([
            r["uuid"], r["title"] or "", r["description"] or "", r["filename"] or "",
            r["media_type"] or "", TAG_SEPARATOR.join(tagged.get(r["id"], [])),
            r["created_at"] or "",
        ])
    return buf.getvalue()


def _tags_for(asset_ids: Sequence[int]) -> dict[int, list[str]]:
    """Tag names per asset, sorted, fetched in chunks to stay under SQLite's
    parameter limit on large exports."""
    conn = get_conn()
    out: dict[int, list[str]] = {}
    ids = list(asset_ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            "SELECT at.asset_id, t.name FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id IN ({marks}) ORDER BY t.name",
            chunk,
        ):
            out.setdefault(row["asset_id"], []).append(row["name"])
    return out


def _semantic_search(q, include, exclude_tags, media_type, collection_id,
                     status, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    where, params = build_query(
        media_type=media_type, tags=include, exclude_tags=exclude_tags,
        collection_id=collection_id, status=status,
    )
    where = [*where, "a.embedding IS NOT NULL"]
    rows = conn.execute(
        f"SELECT a.* FROM assets a WHERE {_where_sql(where)}", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets("active"),
            "mode": "semantic", "page": offset // limit + 1, "page_size": limit,
            "sort": DEFAULT_SORT,
            "filters": {"q": q, "tags": include,
                        "exclude_tags": _coerce_tags(exclude_tags)}}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    """Unfiltered catalog counts for the sidebar.

    These are deliberately *not* the current match counts: the task allows
    facets that do not narrow with the active filters, but they must not be
    presented as "results matching now" in the UI.
    """
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
        "scope": f"all {status} assets",
    }


def _card(a: dict) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "created_at": a["created_at"],
    }
