import os
import sys
from pathlib import Path
import sqlite3
import json
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_multi_platform_ratings import fetch_mdblist_ratings, asdict
from migrations.002_add_ratings_to_title_basic import extract_rating_fields
from models.library import update_title_ratings

def dump_all_library_ratings(output_path: str = "data/ratings.json", update_db: bool = True):
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect("library.db")
    cur = conn.cursor()
    
    # Query all movies and series (31 titles)
    titles = cur.execute(
        "SELECT title_id, title_type, primary_title, start_year "
        "FROM title_basic "
        "WHERE title_type IN ('movie', 'series') "
        "ORDER BY title_type, title_id"
    ).fetchall()
    conn.close()
    
    print(f"Total titles to fetch: {len(titles)} (Movies & Series)")
    
    results = {}
    success_count = 0
    fail_count = 0
    db_updated_count = 0
    start_total = time.perf_counter()
    
    for i, (tid, ttype, name, year) in enumerate(titles, 1):
        t0 = time.perf_counter()
        summary = fetch_mdblist_ratings(tid)
        elapsed = (time.perf_counter() - t0) * 1000
        
        summary_dict = asdict(summary)
        summary_dict["library_type"] = ttype
        summary_dict["library_primary_title"] = name
        summary_dict["library_start_year"] = year
        
        results[tid] = summary_dict
        
        if summary.status == "success":
            success_count += 1
            if update_db:
                fields = extract_rating_fields(summary_dict)
                if update_title_ratings("library.db", tid, fields):
                    db_updated_count += 1
            ratings_count = len(summary.platform_ratings)
            print(f"[{i:02d}/{len(titles)}] [OK]   {ttype.upper():<6} {tid} - {name} ({year}): {ratings_count} platforms, score={summary.score} ({elapsed:.0f}ms)")
        else:
            fail_count += 1
            print(f"[{i:02d}/{len(titles)}] [FAIL] {ttype.upper():<6} {tid} - {name} ({year}): status={summary.status} ({elapsed:.0f}ms)")
        
        # Friendly rate limiting buffer
        time.sleep(0.2)
        
    total_elapsed = time.perf_counter() - start_total
    
    payload = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_titles": len(titles),
            "success_count": success_count,
            "failed_count": fail_count,
            "db_updated_count": db_updated_count,
            "total_time_seconds": round(total_elapsed, 2),
            "source": "MDBList API",
        },
        "ratings": results
    }
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    print("\n" + "=" * 60)
    print(f"Dump complete! Saved to {out_file.resolve()}")
    print(f"Total: {len(titles)}, Success: {success_count}, DB Updated: {db_updated_count}, Failed: {fail_count}, Time: {total_elapsed:.2f}s")
    print("=" * 60)

if __name__ == "__main__":
    dump_all_library_ratings("data/ratings.json", update_db=True)

