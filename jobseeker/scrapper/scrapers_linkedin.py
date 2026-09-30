"""LinkedIn Jobs scraper — public guest endpoint, no login required.

Endpoints used:
  GET /jobs-guest/jobs/api/seeMoreJobPostings/search   → job cards (HTML fragment)
  GET /jobs-guest/jobs/api/jobPosting/{job_id}          → full job detail

Filter params:
  f_TPR  = date posted (seconds): r3600=1h, r86400=24h, r604800=7d
  f_E    = experience level: 1=intern, 2=entry, 3=associate, 4=mid-senior
  keywords, location, start (offset, 25 per page)
"""
import re
import time
import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def _extract_id(url):
    if not url:
        return None
    m = re.search(r"(\d{8,})", url)
    return m.group(1) if m else None


def _get_with_retry(url, params=None, timeout=20, retries=3):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=UA, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.exceptions.HTTPError(f"status={r.status_code}")
            return r
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
                continue
    raise last_exc


def _parse_cards(html):
    """Parse the HTML fragment LinkedIn returns."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for li in soup.select("li"):
        link_el = li.select_one("a.base-card__full-link, a.base-card__full-link")
        if not link_el:
            continue
        href = link_el.get("href", "")
        job_id = _extract_id(href)
        if not job_id:
            continue

        title_el = li.select_one("h3.base-search-card__title")
        company_el = li.select_one("h4.base-search-card__subtitle a, h4.base-search-card__subtitle")
        loc_el = li.select_one(".job-search-card__location")
        time_el = li.select_one("time")

        jobs.append({
            "job_id": job_id,
            "title": _clean(title_el.get_text() if title_el else ""),
            "company": _clean(company_el.get_text() if company_el else ""),
            "location": _clean(loc_el.get_text() if loc_el else ""),
            "posted_date": time_el.get("datetime") if time_el else None,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}",
            "apply_url": f"https://www.linkedin.com/jobs/view/{job_id}",
        })
    return jobs


def fetch_detail(job_id, timeout=15):
    """Fetch full JD text for one job. Slow — call sparingly."""
    try:
        r = _get_with_retry(DETAIL.format(jid=job_id), timeout=timeout, retries=3)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        desc = soup.select_one(".description__text, .show-more-less-html__markup")
        return _clean(desc.get_text(" ", strip=True) if desc else "")
    except Exception:
        return ""


def search(
    keywords,
    location="India",
    f_tpr="r86400",
    f_e="2,3",
    max_results=75,
    fetch_details=False,
    sleep_between=1.2,
):
    """
    keywords:     "fresher software engineer" etc.
    location:     "India" / "Bangalore" / "Kochi"
    f_tpr:        r3600=1h, r86400=24h, r604800=7d
    f_e:          1=intern, 2=entry, 3=associate, 4=mid-senior, 5=director
    max_results:  cap (each page = 25)
    """
    all_jobs = []
    seen = set()
    start = 0
    pages = (max_results + 24) // 25

    for page in range(pages):
        params = {
            "keywords": keywords,
            "location": location,
            "f_TPR": f_tpr,
            "f_E": f_e,
            "start": str(start),
        }
        try:
            r = _get_with_retry(BASE, params=params, timeout=20, retries=3)
            if r.status_code != 200:
                break
            cards = _parse_cards(r.text)
            if not cards:
                break
            for c in cards:
                if c["job_id"] in seen:
                    continue
                seen.add(c["job_id"])
                all_jobs.append(c)
        except Exception as e:
            print(f"      [linkedin] page {page} fail after retries: {str(e)[:100]}")
            break

        start += 25
        time.sleep(sleep_between)

    if fetch_details:
        for i, j in enumerate(all_jobs):
            if i >= 30:   # cap detail fetches
                break
            j["description"] = fetch_detail(j["job_id"])

    return all_jobs


def fetch_linkedin(cfg):
    """
    Config-driven entrypoint. Called from scrapers.py dispatcher.

    cfg["sources"]["linkedin"] = {
      "enabled": true,
      "searches": [
        {"keywords": "fresher software engineer", "location": "India", "f_tpr": "r86400"},
        ...
      ],
      "max_results_per_search": 75,
      "fetch_details": false
    }
    """
    lc = cfg["sources"].get("linkedin", {})
    if not lc.get("enabled"):
        return []

    searches = lc.get("searches", [])
    cap = lc.get("max_results_per_search", 75)
    fetch_details = lc.get("fetch_details", False)

    out = []
    for s in searches:
        kw = s.get("keywords", "")
        loc = s.get("location", "India")
        tpr = s.get("f_tpr", "r86400")
        fe = s.get("f_e", "2,3")
        print(f"      [linkedin] '{kw}' @ {loc} ({tpr})...")
        jobs = search(
            keywords=kw,
            location=loc,
            f_tpr=tpr,
            f_e=fe,
            max_results=cap,
            fetch_details=fetch_details,
        )
        print(f"      [linkedin] '{kw}' → {len(jobs)} jobs")
        out.extend(jobs)

    return out
