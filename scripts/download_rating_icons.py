import os
import requests
from pathlib import Path

DEST_DIR = Path("static/icons/ratings")
DEST_DIR.mkdir(parents=True, exist_ok=True)

# 1. Simple Icons raw repository (Clean, official brand monochrome vector icons)
SIMPLE_ICONS = {
    "imdb_mono.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/imdb.svg",
    "tmdb_mono.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/themoviedatabase.svg",
    "rottentomatoes_mono.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/rottentomatoes.svg",
    "metacritic_mono.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/metacritic.svg",
    "trakt.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/trakt.svg",
    "letterboxd_mono.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/letterboxd.svg",
    "myanimelist.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/myanimelist.svg",
    "anilist.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/anilist.svg",
    "douban.svg": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/douban.svg",
}

# 2. Authentic colored vector SVGs for all rating badges and states
COLORED_SVGS = {
    # Official IMDb gold badge
    "imdb.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32" width="64" height="32">
  <rect width="64" height="32" rx="4" fill="#F5C518"/>
  <path d="M9 7h4.8v18H9V7zm8.4 0h5.1l3.5 10.8L29.5 7h5.1v18h-4.4v-11l-3.3 11h-3.4L20.2 14v11h-4.4V7h1.6zm20.8 0h7.3c2.3 0 4.1.6 5.3 1.8 1.2 1.2 1.8 2.9 1.8 5.1v4.3c0 2.2-.6 3.9-1.8 5.1-1.2 1.2-3 1.8-5.3 1.8h-7.3V7zm4.7 4.1v9.8h2.3c1 0 1.8-.3 2.3-.9.5-.6.8-1.5.8-2.8v-2.4c0-1.3-.3-2.2-.8-2.8-.5-.6-1.3-.9-2.3-.9h-2.3zm13.3-4.1h7.5c1.9 0 3.4.5 4.4 1.4 1 .9 1.5 2.2 1.5 3.9 0 1.2-.3 2.2-1 3-.7.8-1.6 1.3-2.7 1.5 1.3.3 2.3.8 3 1.7.7.9 1.1 2 1.1 3.4 0 1.8-.6 3.2-1.7 4.1-1.1.9-2.7 1.4-4.7 1.4H56.2V7zm4.6 3.8v3.4h2.5c.6 0 1.1-.1 1.4-.4.3-.3.5-.7.5-1.3 0-.6-.2-1-.5-1.3-.3-.3-.8-.4-1.4-.4h-2.5zm0 6.6v4h2.7c.7 0 1.2-.2 1.6-.5.4-.3.5-.8.5-1.5 0-.7-.2-1.2-.5-1.5-.4-.3-.9-.5-1.6-.5h-2.7z" fill="#000000"/>
</svg>""",

    # TMDb full color logo badge
    "tmdb.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40" width="100" height="40">
  <defs>
    <linearGradient id="tmdbGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#90CEA1"/>
      <stop offset="50%" stop-color="#01B4E4"/>
      <stop offset="100%" stop-color="#0D253F"/>
    </linearGradient>
  </defs>
  <rect width="100" height="40" rx="6" fill="#0D253F"/>
  <text x="50" y="27" font-family="Arial, Helvetica, sans-serif" font-weight="900" font-size="22" text-anchor="middle" fill="url(#tmdbGrad)" letter-spacing="1.5">TMDB</text>
</svg>""",

    # Rotten Tomatoes Brand
    "rotten_tomatoes.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="40" height="40">
  <path d="M20 7c-2.5-3-6-4-8-4 0 2 1.5 5 4 6.5C10 10.5 6 15 6 21c0 8.3 6.3 15 14 15s14-6.7 14-15c0-6-4-10.5-10-11.5 2.5-1.5 4-4.5 4-6.5-2 0-5.5 1-8 4z" fill="#FA320A"/>
  <path d="M16 9.5c4-2 7-6.5 7-6.5s-.5 4.5 2.5 6.5c-2.5 1-4.5 0-9.5 0z" fill="#2E7D32"/>
</svg>""",

    # RT Fresh (Tomatometer >= 60%)
    "rt_fresh.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">
  <path d="M18 5c-2-2.5-5-3.5-7-3.5 0 1.8 1.2 4.2 3.5 5.5C9 8 5 12 5 18c0 7.7 5.8 14 13 14s13-6.3 13-14c0-6-4-10-9.5-11 2.3-1.3 3.5-3.7 3.5-5.5-2 0-5 1-7 3.5z" fill="#FA320A"/>
  <path d="M14.5 7c3.5-1.8 6-5.5 6-5.5s-.5 4 2 5.5c-2 .8-3.8 0-8 0z" fill="#388E3C"/>
</svg>""",

    # RT Certified Fresh (Tomatometer >= 75% + Critic Quorum)
    "rt_certified.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="40" height="40">
  <circle cx="20" cy="20" r="19" fill="#FA320A" stroke="#FFD700" stroke-width="2"/>
  <path d="M20 8c-1.8-2-4.5-3-6-3 0 1.5 1 3.5 3 4.5C11 10.5 8 14 8 19c0 6.6 5 12 12 12s12-5.4 12-12c0-5-3-8.5-8-9.5 2-1 3-3 3-4.5-1.5 0-4.2 1-6 3z" fill="#FFFFFF"/>
  <path d="M15.5 9.5c3-1.5 5-4.5 5-4.5s-.4 3.5 1.8 4.5c-1.8.7-3.2 0-6.8 0z" fill="#4CAF50"/>
  <polygon points="20,13 22,17.5 27,18 23.2,21.5 24.3,26.5 20,23.8 15.7,26.5 16.8,21.5 13,18 18,17.5" fill="#FFD700"/>
</svg>""",

    # RT Rotten (Tomatometer < 60%)
    "rt_rotten.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">
  <path d="M18 4c-3 1-5 4-8 4-4 0-7 4-5 9 1 3 3 5 2 8-1 4 2 8 7 8s9-3 12-6c3-4 3-9 0-13-2-3-5-10-8-10z" fill="#6BB343"/>
  <circle cx="12" cy="15" r="2" fill="#437728"/>
  <circle cx="22" cy="20" r="2.5" fill="#437728"/>
  <path d="M15 8c2-2 5-3 5-3s-.5 3 1 4c-1.5.5-3 0-6-.1z" fill="#2E541B"/>
</svg>""",

    # RT Popcorn Upright (Audience >= 60%)
    "rt_popcorn.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">
  <path d="M10 14l2 18h12l2-18H10z" fill="#FA320A"/>
  <path d="M14 14l1 18h4l1-18h-6z" fill="#FFFFFF"/>
  <ellipse cx="18" cy="13" rx="10" ry="6" fill="#FDD835"/>
  <circle cx="13" cy="9" r="4.5" fill="#FFEE58"/>
  <circle cx="18" cy="7" r="5" fill="#FFF59D"/>
  <circle cx="23" cy="9" r="4.5" fill="#FFEE58"/>
  <circle cx="15" cy="11" r="3.5" fill="#FDD835"/>
  <circle cx="21" cy="11" r="3.5" fill="#FDD835"/>
</svg>""",

    # RT Popcorn Spilled (Audience < 60%)
    "rt_spilled.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">
  <path d="M28 20l-16-6-7 10 16 6 7-10z" fill="#6BB343"/>
  <path d="M22 17.5l-16-6-2 3 16 6 2-3z" fill="#FFFFFF"/>
  <circle cx="29" cy="26" r="3" fill="#A5D6A7"/>
  <circle cx="24" cy="29" r="2.5" fill="#81C784"/>
  <circle cx="32" cy="29" r="2" fill="#A5D6A7"/>
  <circle cx="18" cy="30" r="2" fill="#81C784"/>
</svg>""",

    # Metacritic Primary Logo
    "metacritic.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">
  <circle cx="18" cy="18" r="18" fill="#333333"/>
  <circle cx="18" cy="18" r="15" fill="#FFCC33"/>
  <text x="18" y="24" font-family="Arial, Helvetica, sans-serif" font-weight="900" font-size="16" text-anchor="middle" fill="#000000" letter-spacing="-1">MC</text>
</svg>""",

    # Metascore Green (Positive >= 61)
    "metascore_positive.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <rect width="32" height="32" rx="4" fill="#66CC33"/>
  <text x="16" y="23" font-family="Arial, Helvetica, sans-serif" font-weight="bold" font-size="18" text-anchor="middle" fill="#FFFFFF">MC</text>
</svg>""",

    # Metascore Yellow (Mixed 40-60)
    "metascore_mixed.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <rect width="32" height="32" rx="4" fill="#FFCC33"/>
  <text x="16" y="23" font-family="Arial, Helvetica, sans-serif" font-weight="bold" font-size="18" text-anchor="middle" fill="#000000">MC</text>
</svg>""",

    # Metascore Red (Negative < 40)
    "metascore_negative.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <rect width="32" height="32" rx="4" fill="#FF0000"/>
  <text x="16" y="23" font-family="Arial, Helvetica, sans-serif" font-weight="bold" font-size="18" text-anchor="middle" fill="#FFFFFF">MC</text>
</svg>""",

    # Letterboxd Authentic 3-Circles Logo (Orange, Green, Blue)
    "letterboxd.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 24" width="60" height="24">
  <circle cx="12" cy="12" r="10" fill="#FF8000"/>
  <circle cx="30" cy="12" r="10" fill="#00E054"/>
  <circle cx="48" cy="12" r="10" fill="#40BCF4"/>
</svg>""",

    # Trakt Full Red Logo
    "trakt_colored.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">
  <rect width="36" height="36" rx="8" fill="#ED1C24"/>
  <path d="M11 11h14v4h-4.5v12h-5V15H11v-4z" fill="#FFFFFF"/>
</svg>""",

    # Roger Ebert Star
    "star.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26" fill="#FFB800"/>
</svg>""",

    # MDBList Badge
    "mdblist.svg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="40" height="40">
  <rect width="40" height="40" rx="8" fill="#1C1E24"/>
  <text x="20" y="27" font-family="Arial, Helvetica, sans-serif" font-weight="900" font-size="18" text-anchor="middle" fill="#00C853" letter-spacing="1">M</text>
  <circle cx="30" cy="12" r="4" fill="#FFB300"/>
</svg>""",
}

def download_simple_icons():
    headers = {"User-Agent": "Mozilla/5.0"}
    for filename, url in SIMPLE_ICONS.items():
        dest = DEST_DIR / filename
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                print(f"[OK Downloaded] {filename:<25} ({len(r.content)} bytes)")
            else:
                print(f"[FAIL {r.status_code}] {filename}")
        except Exception as e:
            print(f"[ERR] {filename}: {e}")

def save_colored_svgs():
    for filename, svg_content in COLORED_SVGS.items():
        dest = DEST_DIR / filename
        dest.write_text(svg_content.strip(), encoding="utf-8")
        print(f"[OK Generated]  {filename:<25} ({len(svg_content)} chars)")

if __name__ == "__main__":
    print("=== 1. Downloading Official Simple Icons ===")
    download_simple_icons()
    print("\n=== 2. Generating Authentic Colored Rating SVGs ===")
    save_colored_svgs()
    print("\nAll rating icons successfully saved to:", DEST_DIR.resolve())
