"""SME / small-shop India sources — Hasjob, HackerEarth, AngelList."""
import re
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobHunter/3.0"}


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def hasjob(max_results=50):
    """Hasjob.co — Indian startups, small teams. Public list."""
    url = "https://hasjob.co/"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for card in soup.select("article, .job")[:max_results]:
            a = card.select_one("a[href]")
            if not a:
                continue
            href = a.get("href", "")
            if href.startswith("/"):
                href = "https://hasjob.co" + href
            title = _clean(a.get_text())
            company_el = card.select_one(".company, .company-name")
            company = _clean(company_el.get_text()) if company_el else ""
            loc_el = card.select_one(".location")
            loc = _clean(loc_el.get_text()) if loc_el else "India"
            if not title or len(title) < 5:
                continue
            out.append({
                "company": company,
                "title": title,
                "location": loc,
                "url": href,
                "apply_url": href,
                "description": _clean(card.get_text()),
                "source": "hasjob",
            })
        return out
    except Exception as e:
        print(f"      [hasjob] {str(e)[:80]}")
        return []


def hackerearth_jobs(keyword="software", max_results=50):
    """HackerEarth Jobs public listings."""
    url = f"https://www.hackerearth.com/jobs/?q={keyword}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for a in soup.select('a[href*="/jobs/"]')[:max_results]:
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.hackerearth.com" + href
            title = _clean(a.get_text())
            if not title or len(title) < 5:
                continue
            out.append({
                "company": "",
                "title": title,
                "location": "India",
                "url": href,
                "apply_url": href,
                "description": "",
                "source": "hackerearth",
            })
        return out
    except Exception:
        return []


def fetch_sme(cfg):
    sme = cfg["sources"].get("sme", {})
    if not sme.get("enabled"):
        return []
    out = []
    if sme.get("hasjob", True):
        print("      [sme] hasjob.co...")
        out += hasjob(sme.get("max_results", 50))
    if sme.get("hackerearth", True):
        print("      [sme] hackerearth jobs...")
        out += hackerearth_jobs("software", sme.get("max_results", 30))
    return out
