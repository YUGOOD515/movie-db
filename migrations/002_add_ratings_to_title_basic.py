"""Migration 002: Add multi-platform rating columns to title_basic in library.db.

Columns added:
- mdblist_score: INTEGER (0-100)
- mdblist_score_average: INTEGER (0-100)
- rt_critics_score: INTEGER (0-100)
- rt_critics_votes: INTEGER
- rt_audience_score: INTEGER (0-100)
- rt_audience_votes: INTEGER
- metacritic_score: INTEGER (0-100)
- metacritic_votes: INTEGER
- metacritic_user_score: REAL (0.0-10.0)
- metacritic_user_votes: INTEGER
- trakt_score: INTEGER (0-100)
- trakt_votes: INTEGER
- letterboxd_score: REAL (0.0-5.0)
- letterboxd_votes: INTEGER
- roger_ebert_rating: REAL (0.0-4.0)
- myanimelist_score: REAL (0.0-10.0)
- myanimelist_votes: INTEGER
"""

import json
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_DB_PATH = PROJECT_ROOT / "library.db"
RATINGS_JSON_PATH = PROJECT_ROOT / "data" / "ratings.json"

NEW_COLUMNS = [
    ("mdblist_score", "INTEGER"),
    ("mdblist_score_average", "INTEGER"),
    ("rt_critics_score", "INTEGER"),
    ("rt_critics_votes", "INTEGER"),
    ("rt_audience_score", "INTEGER"),
    ("rt_audience_votes", "INTEGER"),
    ("metacritic_score", "INTEGER"),
    ("metacritic_votes", "INTEGER"),
    ("metacritic_user_score", "REAL"),
    ("metacritic_user_votes", "INTEGER"),
    ("trakt_score", "INTEGER"),
    ("trakt_votes", "INTEGER"),
    ("letterboxd_score", "REAL"),
    ("letterboxd_votes", "INTEGER"),
    ("roger_ebert_rating", "REAL"),
    ("myanimelist_score", "REAL"),
    ("myanimelist_votes", "INTEGER"),
]

def migrate_schema(db_path: Path = LIBRARY_DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Get existing columns
    cur.execute("PRAGMA table_info(title_basic)")
    existing_cols = {row[1] for row in cur.fetchall()}
    
    added_cols = []
    for col_name, col_type in NEW_COLUMNS:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE title_basic ADD COLUMN {col_name} {col_type}")
            added_cols.append(col_name)
            
    conn.commit()
    conn.close()
    print(f"Schema migration complete. Added {len(added_cols)} columns: {added_cols}")

def extract_rating_fields(item_dict: dict) -> dict:
    """Extract flat rating fields from TitleRatingsSummary dict."""
    res = {
        "mdblist_score": item_dict.get("score"),
        "mdblist_score_average": item_dict.get("score_average"),
        "rt_critics_score": None,
        "rt_critics_votes": None,
        "rt_audience_score": None,
        "rt_audience_votes": None,
        "metacritic_score": None,
        "metacritic_votes": None,
        "metacritic_user_score": None,
        "metacritic_user_votes": None,
        "trakt_score": None,
        "trakt_votes": None,
        "letterboxd_score": None,
        "letterboxd_votes": None,
        "roger_ebert_rating": None,
        "myanimelist_score": None,
        "myanimelist_votes": None,
    }
    
    platform_ratings = item_dict.get("platform_ratings", [])
    for r in platform_ratings:
        key = r.get("platform_key")
        votes = r.get("votes")
        
        if key == "tomatoes_critic":
            # score_100 or raw percentage
            res["rt_critics_score"] = int(r["score_100"]) if r.get("score_100") is not None else None
            res["rt_critics_votes"] = votes
        elif key == "tomatoes_audience":
            res["rt_audience_score"] = int(r["score_100"]) if r.get("score_100") is not None else None
            res["rt_audience_votes"] = votes
        elif key == "metacritic_critic":
            res["metacritic_score"] = int(r["score_100"]) if r.get("score_100") is not None else None
            res["metacritic_votes"] = votes
        elif key == "metacritic_user":
            res["metacritic_user_score"] = float(r["score_10"]) if r.get("score_10") is not None else None
            res["metacritic_user_votes"] = votes
        elif key == "trakt":
            res["trakt_score"] = int(r["score_100"]) if r.get("score_100") is not None else None
            res["trakt_votes"] = votes
        elif key == "letterboxd":
            # Extract raw 5-point value from score_display like '4.5/5.0'
            disp = r.get("score_display", "")
            if "/" in disp:
                try:
                    res["letterboxd_score"] = float(disp.split("/")[0])
                except Exception:
                    res["letterboxd_score"] = float(r["score_10"]) / 2.0 if r.get("score_10") else None
            res["letterboxd_votes"] = votes
        elif key == "rogerebert":
            disp = r.get("score_display", "")
            if "/" in disp:
                try:
                    res["roger_ebert_rating"] = float(disp.split("/")[0])
                except Exception:
                    pass
        elif key == "myanimelist":
            res["myanimelist_score"] = float(r["score_10"]) if r.get("score_10") is not None else None
            res["myanimelist_votes"] = votes
            
    return res

def populate_ratings(db_path: Path = LIBRARY_DB_PATH, json_path: Path = RATINGS_JSON_PATH):
    if not json_path.is_file():
        print(f"Ratings JSON not found at {json_path}")
        return
        
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    ratings_map = data.get("ratings", {})
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    updated_count = 0
    for tid, item in ratings_map.items():
        if item.get("status") != "success":
            continue
            
        fields = extract_rating_fields(item)
        set_clauses = ", ".join(f"{k} = :{k}" for k in fields.keys())
        params = {"title_id": tid, **fields}
        
        cur.execute(
            f"""
            UPDATE title_basic
               SET {set_clauses},
                   updated_at = CURRENT_TIMESTAMP
             WHERE title_id = :title_id
            """,
            params
        )
        if cur.rowcount > 0:
            updated_count += 1
            
    conn.commit()
    conn.close()
    print(f"Successfully populated ratings for {updated_count} titles in {db_path.name}.")

if __name__ == "__main__":
    migrate_schema()
    populate_ratings()
