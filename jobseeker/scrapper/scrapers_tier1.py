"""Direct scrapers for FAANG / big-tech career portals.

These companies typically do not use Greenhouse/Lever/Ashby and need their own
public endpoints or public search APIs.
"""
import re
import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _strip(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


# ─── Amazon Jobs ──────────────────────────────────────────
def amazon_jobs(keyword="software engineer", country="IND", max_results=50):
    """amazon.jobs public search API."""
    url = "https://www.amazon.jobs/en/search.json"
    params = {
        "radius": "24km",
        "facets[]": "location",
        "offset": 0,
        "result_limit": min(max_results, 100),
        "sort": "recent",
        "country[]": country,
        "base_query": keyword,
    }
    try:
        r = requests.get(url, params=params, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    for j in data.get("jobs", []):
        path = j.get("job_path") or j.get("jobPath") or ""
        url_value = f"https://www.amazon.jobs{path}" if path.startswith("/") else path
        out.append({
            "company": "Amazon",
            "title": _strip(j.get("title")),
            "location": _strip(j.get("location")),
            "url": url_value,
            "apply_url": url_value,
            "posted_date": j.get("posted_date"),
            "description": _strip(j.get("description_short", "")),
            "source": "amazon-direct",
        })
    return out


# ─── Microsoft Careers ────────────────────────────────────
def microsoft_jobs(keyword="software engineer", location="India", max_results=50):
    """Microsoft careers public search endpoint."""
    url = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
    params = {
        "q": keyword,
        "lc": location,
        "l": "en_us",
        "pg": 1,
        "pgSz": min(max_results, 20),
        "o": "Relevance",
        "flt": "true",
    }
    try:
        r = requests.get(url, params=params, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    for j in data.get("operationResult", {}).get("result", {}).get("jobs", []):
        job_id = j.get("jobId")
        if not job_id:
            continue
        loc = j.get("properties", {}).get("primaryLocation", "")
        url_value = f"https://jobs.careers.microsoft.com/global/en/job/{job_id}"
        out.append({
            "company": "Microsoft",
            "title": _strip(j.get("title")),
            "location": _strip(loc),
            "url": url_value,
            "apply_url": url_value,
            "posted_date": j.get("properties", {}).get("postedDate"),
            "description": _strip(j.get("properties", {}).get("description", "")),
            "source": "microsoft-direct",
        })
    return out


# ─── Google Careers ───────────────────────────────────────
def google_jobs(keyword="software engineer", location="India", max_results=50):
    """Google careers public job listing endpoint."""
    url = "https://careers.google.com/api/v3/search/"
    params = {
        "q": keyword,
        "location": location,
        "num": min(max_results, 20),
        "page": 1,
    }
    try:
        r = requests.get(url, params=params, headers=UA, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    for j in data.get("jobs", []):
        job_id = j.get("id")
        if not job_id:
            continue
        locs = j.get("locations") or []
        loc = ", ".join(locs)
        url_value = f"https://www.google.com/about/careers/applications/jobs/results/{job_id}"
        out.append({
            "company": "Google",
            "title": _strip(j.get("title")),
            "location": _strip(loc),
            "url": url_value,
            "apply_url": url_value,
            "posted_date": j.get("publish_date"),
            "description": "",
            "source": "google-direct",
        })
    return out


# ─── Apple Jobs ───────────────────────────────────────────
def apple_jobs(keyword="software engineer", max_results=50):
    """Apple careers public search API."""
    url = "https://jobs.apple.com/api/role/search"
    payload = {
        "query": keyword,
        "filters": {"postingpostLocation": ["postLocation-INDC"]},
        "page": 1,
        "locale": "en-in",
        "sort": "newest",
    }
    headers = {**UA, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    for j in data.get("searchResults", [])[:max_results]:
        job_id = j.get("positionId")
        if not job_id:
            continue
        out.append({
            "company": "Apple",
            "title": _strip(j.get("postingTitle")),
            "location": _strip(j.get("locationName", "")),
            "url": f"https://jobs.apple.com/en-in/details/{job_id}",
            "apply_url": f"https://jobs.apple.com/en-in/details/{job_id}",
            "posted_date": j.get("postingDate"),
            "description": "",
            "source": "apple-direct",
        })
    return out


def fetch_tier1(cfg):
    """Config-driven entrypoint."""
    t1 = cfg["sources"].get("tier1", {})
    if not t1.get("enabled"):
        return []
    keywords = t1.get("keywords", ["software engineer", "software development engineer", "machine learning"])
    cap = t1.get("max_results_per_keyword", 30)
    out = []
    if t1.get("amazon", True):
        for kw in keywords[:2]:
            print(f"      [tier1] amazon.jobs '{kw}'...")
            out += amazon_jobs(kw, "IND", cap)
    if t1.get("microsoft", True):
        for kw in keywords[:2]:
            print(f"      [tier1] microsoft '{kw}'...")
            out += microsoft_jobs(kw, "India", cap)
    if t1.get("google", True):
        for kw in keywords[:2]:
            print(f"      [tier1] google '{kw}'...")
            out += google_jobs(kw, "India", cap)
    if t1.get("apple", True):
        for kw in keywords[:1]:
            print(f"      [tier1] apple '{kw}'...")
            out += apple_jobs(kw, cap)
    return out
