"""Seed a reproducible demo catalog for the search / filter / export task.

    python scripts/seed_demo_catalog.py

Adds one collection, "Harbor Campaign 2026", with 30 assets and four tag
combinations, so the acceptance scenario

    keyword "harbor" AND tags approved, campaign AND NOT tag internal

returns 14 rows spread over several pages. Created timestamps are fixed and
deliberately contain ties, which is what makes the pagination behaviour
observable by hand.

Run it against a throw-away data directory, e.g.

    KEEPSTACK_DATA_DIR=/tmp/keepstack-demo python scripts/seed_demo_catalog.py
"""
from __future__ import annotations

import io
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from keepstack import ingest  # noqa: E402
from keepstack.auth import hash_password  # noqa: E402
from keepstack.audit import now_iso  # noqa: E402
from keepstack.db import get_conn, init_db  # noqa: E402

COLLECTION = "Harbor Campaign 2026"

# (title, tags) — 14 rows match the acceptance filter, the rest exercise the
# include-all / exclude-any / keyword boundaries.
SPEC = [
    ("Harbor at dawn", ["approved", "campaign"]),
    ("Harbor at dusk", ["approved", "campaign"]),
    ("Harbor crane lift", ["approved", "campaign"]),
    ("Harbor pilot boat", ["approved", "campaign"]),
    ("Harbor warehouse", ["approved", "campaign"]),
    ("Harbor customs office", ["approved", "campaign"]),
    ("Harbor tugboat", ["approved", "campaign"]),
    ("Harbor breakwater", ["approved", "campaign"]),
    ("Harbor lighthouse", ["approved", "campaign"]),
    ("Harbor ferry terminal", ["approved", "campaign"]),
    ("Harbor grain silo", ["approved", "campaign"]),
    ("Harbor bridge survey", ["approved", "campaign"]),
    ("Harbor night shift", ["approved", "campaign"]),
    ("Harbor aerial view", ["approved", "campaign"]),
    ("Harbor budget memo", ["approved", "campaign", "internal"]),
    ("Harbor safety audit", ["approved", "campaign", "internal"]),
    ("Harbor contractor list", ["approved", "campaign", "internal"]),
    ("Harbor incident report", ["approved", "campaign", "internal"]),
    ("Harbor approved only A", ["approved"]),
    ("Harbor approved only B", ["approved"]),
    ("Harbor approved only C", ["approved"]),
    ("Harbor approved only D", ["approved"]),
    ("Harbor campaign only A", ["campaign"]),
    ("Harbor campaign only B", ["campaign"]),
    ("Skyline poster (demo)", ["approved", "campaign"]),
    ("Transit banner (demo)", ["approved", "campaign"]),
    ("Coastal survey (demo)", ["approved", "campaign"]),
    ("Watershed diagram (demo)", ["approved", "campaign"]),
    ("Reading room (demo)", ["approved", "campaign"]),
    ("Logo mark (demo)", ["approved", "campaign"]),
]

# Fixed timestamps; every third asset shares a timestamp with its predecessor so
# ordering ties are guaranteed to exist.
TIMESTAMPS = [f"2026-03-{(i // 2) + 1:02d}T{(i % 3) * 4 + 8:02d}:00:00Z" for i in range(len(SPEC))]

PALETTE = [(94, 132, 247), (70, 192, 138), (242, 96, 122),
           (220, 160, 60), (150, 110, 220), (60, 180, 200)]


def _jpeg(idx: int, label: str) -> bytes:
    w, h = 900, 600
    img = Image.new("RGB", (w, h), (22, 26, 36))
    draw = ImageDraw.Draw(img)
    rand = random.Random(idx)
    for _ in range(5):
        x0, y0 = rand.randint(-80, w), rand.randint(-80, h)
        r = rand.randint(120, 380)
        draw.ellipse([x0, y0, x0 + r, y0 + r], fill=PALETTE[idx % len(PALETTE)])
    draw.rectangle([0, h - 90, w, h], fill=(0, 0, 0))
    draw.text((20, h - 60), label[:60], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def main() -> None:
    init_db()
    conn = get_conn()
    if not conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        conn.execute(
            "INSERT INTO users (username, email, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('admin','admin@example.org','Administrator',?, 'admin', 1, ?)",
            (hash_password("admin"), now_iso()),
        )
        conn.commit()
    admin = dict(conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone())

    row = conn.execute("SELECT id FROM collections WHERE name = ?", (COLLECTION,)).fetchone()
    if row:
        collection_id = row["id"]
        print(f"Collection {COLLECTION!r} already exists (id={collection_id}); adding only new assets.")
    else:
        import uuid as uuidlib
        collection_id = conn.execute(
            "INSERT INTO collections (uuid, name, description, kind, created_by, created_at) "
            "VALUES (?,?,?,'folder',?,?)",
            (str(uuidlib.uuid4()), COLLECTION,
             "Demo set for the search / filter / export task", admin["id"], now_iso()),
        ).lastrowid
        conn.commit()

    created = 0
    for idx, (title, tags) in enumerate(SPEC):
        existing = conn.execute(
            "SELECT 1 FROM assets WHERE title = ? AND status = 'active'", (title,)
        ).fetchone()
        if existing:
            continue
        asset = ingest.ingest_stream(
            io.BytesIO(_jpeg(idx, title)),
            f"{title.lower().replace(' ', '_')}.jpg",
            user=admin, run_ai=False, title=title,
        )
        conn.execute("UPDATE assets SET created_at = ?, updated_at = ? WHERE id = ?",
                     (TIMESTAMPS[idx], TIMESTAMPS[idx], asset["id"]))
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()["id"]
            conn.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) VALUES (?,?, 'manual')",
                (asset["id"], tag_id),
            )
        conn.execute(
            "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?,?,?)",
            (collection_id, asset["id"], TIMESTAMPS[idx]),
        )
        conn.commit()
        ingest.reindex_fts(asset["id"])
        created += 1

    total = conn.execute(
        "SELECT COUNT(*) c FROM collection_assets WHERE collection_id = ?", (collection_id,)
    ).fetchone()["c"]
    print(f"Seeded {created} new assets into {COLLECTION!r} ({total} total in the collection).")
    print('Acceptance scenario -> keyword "harbor" + tags approved, campaign, exclude internal: 14 rows.')


if __name__ == "__main__":
    main()
