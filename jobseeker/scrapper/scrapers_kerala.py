"""Kerala-focused scraper for public Infopark and Technopark career pages."""
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
PHONE_IN = re.compile(r"(?:(?:\+|00)91[\s\-]?)?[6-9]\d{9}")
EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CAREER_PATHS = ["/careers", "/careers/", "/jobs", "/jobs/", "/join-us", "/work-with-us"]


def _extract_contacts(html):
    soup = BeautifulSoup(html, "html.parser")
    phones = set()
    for link in soup.select('a[href^="tel:"]'):
        raw = link.get("href", "")[4:]
        if 10 <= len(re.sub(r"\D", "", raw)) <= 15:
            phones.add(raw.strip())
    text = soup.get_text(" ", strip=True)
    phones.update(PHONE_IN.findall(text)[:8])
    emails = set(EMAIL.findall(text))

    def rank(email):
        value = email.lower()
        return 0 if any(k in value for k in ("hr@", "careers@", "talent@", "jobs@", "recruit", "hiring@")) else 1

    return list(phones)[:3], sorted(emails, key=rank)[:3]


def _fetch(url):
    try:
        response = requests.get(url, headers=UA, timeout=8, allow_redirects=True)
        return response.text if response.status_code == 200 else None
    except requests.RequestException:
        return None


def _extract_jobs(html, company, city, base):
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        title = re.sub(r"\s+", " ", link.get_text()).strip()
        if not 4 <= len(title) <= 140:
            continue
        if not any(k in href.lower() for k in ("/job", "/jobs/", "/opening", "/position", "/career")):
            continue
        if href.startswith("/"):
            href = base.rstrip("/") + href
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        jobs.append({"company": company, "title": title, "location": city,
                     "url": href, "apply_url": href, "description": ""})
    return jobs


def fetch_kerala(cfg):
    source = cfg["sources"].get("kerala", {})
    if not source.get("enabled"):
        return []
    output = []
    for company in source.get("companies", []):
        name = company.get("name", "")
        base = company.get("website", "").rstrip("/")
        city = company.get("city", "Kochi")
        if not base:
            continue
        html, used_url = None, base
        for path in CAREER_PATHS:
            html = _fetch(base + path)
            if html and len(html) > 500:
                used_url = base + path
                break
        if not html:
            html = _fetch(base)
        if not html:
            continue
        phones, emails = _extract_contacts(html)
        jobs = _extract_jobs(html, name, city, used_url)
        for job in jobs:
            job.update({
                "contact_phone": company.get("phone") or (phones[0] if phones else None),
                "contact_email": company.get("email") or (emails[0] if emails else None),
                "contact_page": used_url,
                "source": "kerala",
            })
            output.append(job)
        if jobs:
            print(f"      [kerala] {name} @ {city}: {len(jobs)} jobs")
    return output
