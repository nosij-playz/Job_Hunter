"""Startup/SME-focused scrapers — Cutshort, Instahyre, Wellfound, YC.

All use public search pages. No login, no cookies.
"""
import re
import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


# ─── Cutshort ─────────────────────────────────────────────
def cutshort(keyword="software engineer", city="bangalore", max_results=50):
    """Public Cutshort search — Indian startups."""
    url = f"https://cutshort.io/jobs/{city}/{keyword.replace(' ', '-').lower()}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        jobs = []
        for a in soup.select('a[href*="/job/"]')[:max_results]:
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://cutshort.io" + href
            title = _clean(a.get_text())
            if not title or len(title) < 5:
                continue
            jobs.append({
                "title": title,
                "company": "",
                "location": city,
                "url": href,
                "apply_url": href,
                "description": "",
            })
        return jobs
    except Exception as e:
        print(f"      [cutshort] {str(e)[:80]}")
        return []


# ─── Instahyre ────────────────────────────────────────────
def instahyre(keyword="software-engineer", max_results=50):
    """Public Instahyre listing — Indian tech / startup."""
    url = f"https://www.instahyre.com/jobs/?q={keyword.replace(' ', '+')}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        jobs = []
        for a in soup.select('a[href*="/job/"]')[:max_results]:
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.instahyre.com" + href
            title = _clean(a.get_text())
            if not title or len(title) < 5:
                continue
            jobs.append({
                "title": title,
                "company": "",
                "location": "",
                "url": href,
                "apply_url": href,
                "description": "",
            })
        return jobs
    except Exception as e:
        print(f"      [instahyre] {str(e)[:80]}")
        return []


# ─── Wellfound (AngelList) ────────────────────────────────
def wellfound(keyword="software-engineer", max_results=50):
    """Public Wellfound listings — early-stage startups."""
    kw = keyword.lower().replace(" ", "-")
    url = f"https://wellfound.com/role/r/{kw}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        jobs = []
        for a in soup.select('a[href*="/jobs/"]')[:max_results]:
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://wellfound.com" + href
            title = _clean(a.get_text())
            if not title or len(title) < 5 or len(title) > 120:
                continue
            jobs.append({
                "title": title,
                "company": "",
                "location": "Remote",
                "url": href,
                "apply_url": href,
                "description": "",
            })
        return jobs
    except Exception as e:
        print(f"      [wellfound] {str(e)[:80]}")
        return []


# ─── YCombinator Work at a Startup ────────────────────────
def ycombinator(role="software-engineer", max_results=50):
    """Public YC Work at a Startup listing."""
    url = f"https://www.workatastartup.com/jobs?role={role}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        return []
    except Exception:
        return []


def fetch_startups(cfg):
    """Config-driven entrypoint called from scrapers.py."""
    sc = cfg["sources"].get("startups", {})
    if not sc.get("enabled"):
        return []

    out = []
    keywords = sc.get("keywords", ["software engineer", "python developer", "ai engineer"])
    cities = sc.get("cities", ["bangalore", "kochi", "chennai", "hyderabad"])
    cap = sc.get("max_results_per_keyword", 40)

    if sc.get("cutshort", True):
        for kw in keywords:
            for city in cities[:2]:
                print(f"      [cutshort] '{kw}' @ {city}")
                for j in cutshort(kw, city, cap):
                    j["source"] = "cutshort"
                    out.append(j)

    if sc.get("instahyre", True):
        for kw in keywords:
            print(f"      [instahyre] '{kw}'")
            for j in instahyre(kw, cap):
                j["source"] = "instahyre"
                out.append(j)

    if sc.get("wellfound", True):
        for kw in keywords:
            print(f"      [wellfound] '{kw}'")
            for j in wellfound(kw, cap):
                j["source"] = "wellfound"
                out.append(j)

    return out
