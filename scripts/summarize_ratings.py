import json

with open("data/ratings.json", "r", encoding="utf-8") as f:
    data = json.load(f)

meta = data["metadata"]
ratings = data["ratings"]
movies = [v for v in ratings.values() if v.get("library_type") == "movie"]
series = [v for v in ratings.values() if v.get("library_type") == "series"]

print(f"Total Movies: {len(movies)}, Total Series: {len(series)}")
print("\n=== 电影 (Movies) 抓取结果 ===")
for m in sorted(movies, key=lambda x: x.get("score") or 0, reverse=True):
    imdb_score = next((r["score_display"] for r in m["platform_ratings"] if r["platform_key"] == "imdb"), "--")
    rt_score = next((r["score_display"] for r in m["platform_ratings"] if r["platform_key"] == "tomatoes_critic"), "--")
    mc_score = next((r["score_display"] for r in m["platform_ratings"] if r["platform_key"] == "metacritic_critic"), "--")
    lb_score = next((r["score_display"] for r in m["platform_ratings"] if r["platform_key"] == "letterboxd"), "--")
    print(f"[{m['score']:>2}/100] {m['imdb_id']} | {m['title']} ({m['year']}) | IMDb: {imdb_score} | RT: {rt_score} | MC: {mc_score} | LB: {lb_score} | 平台数: {len(m['platform_ratings'])}")

print("\n=== 剧集 (Series) 抓取结果 ===")
for s in sorted(series, key=lambda x: x.get("score") or 0, reverse=True):
    imdb_score = next((r["score_display"] for r in s["platform_ratings"] if r["platform_key"] == "imdb"), "--")
    rt_score = next((r["score_display"] for r in s["platform_ratings"] if r["platform_key"] == "tomatoes_critic"), "--")
    mc_score = next((r["score_display"] for r in s["platform_ratings"] if r["platform_key"] == "metacritic_critic"), "--")
    trakt_score = next((r["score_display"] for r in s["platform_ratings"] if r["platform_key"] == "trakt"), "--")
    print(f"[{s['score']:>2}/100] {s['imdb_id']} | {s['title']} ({s['year']}) | IMDb: {imdb_score} | RT: {rt_score} | MC: {mc_score} | Trakt: {trakt_score} | 平台数: {len(s['platform_ratings'])}")
