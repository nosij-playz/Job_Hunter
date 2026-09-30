"""Metro-city focused scraper for public company career pages."""
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
PHONE_INTL = re.compile(r"\+\d{1,3}[\s\-]?\d{6,12}")
EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CAREER_PATHS = [
    "/careers", "/careers/", "/jobs", "/jobs/", "/career", "/work-with-us",
    "/join-us", "/company/careers", "/about/careers",
]


def _clean(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _extract_contacts(html):
    soup = BeautifulSoup(html, "html.parser")
    phones = set()
    for link in soup.select('a[href^="tel:"]'):
        raw = link.get("href", "")[4:]
        if 10 <= len(re.sub(r"\D", "", raw)) <= 15:
            phones.add(raw.strip())
    text = soup.get_text(" ", strip=True)
    phones.update(PHONE_IN.findall(text)[:10])
    phones.update(PHONE_INTL.findall(text)[:10])
    emails = set(EMAIL.findall(text))

    def rank(email):
        value = email.lower()
        return 0 if any(k in value for k in ("hr@", "careers@", "talent@", "jobs@", "recruit", "hiring@")) else 1

    return list(phones)[:3], sorted(emails, key=rank)[:3]


def _fetch_careers(company):
    base = company.get("website", "").rstrip("/")
    if not base:
        return None, None
    for path in CAREER_PATHS:
        try:
            response = requests.get(base + path, headers=UA, timeout=8, allow_redirects=True)
            if response.status_code == 200 and len(response.text) > 500:
                return response.url, response.text
        except requests.RequestException:
            continue
    return None, None


def _extract_jobs_from_page(html, company_name, city, base_url):
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = _clean(link.get_text())
        if not 4 <= len(text) <= 140:
            continue
        if not any(k in href.lower() for k in ("/job", "/jobs/", "/opening", "/position", "/career")):
            continue
        if href.startswith("/"):
            href = base_url.rstrip("/") + href
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        jobs.append({
            "company": company_name, "title": text, "location": city,
            "url": href, "apply_url": href, "description": "",
        })
    return jobs


def fetch_metro(cfg):
    source = cfg["sources"].get("metro", {})
    if not source.get("enabled"):
        return []
    cities = source.get("cities", [])
    companies = source.get("companies", [])
    output = []
    for city in cities:
        print(f"      [metro] {city.title()}: scanning {len(companies)} companies...")
        for company in companies:
            if city not in company.get("cities", []):
                continue
            name = company.get("name", "")
            url, html = _fetch_careers(company)
            if not html:
                continue
            phones, emails = _extract_contacts(html)
            for job in _extract_jobs_from_page(html, name, city, url):
                job.update({
                    "contact_phone": phones[0] if phones else None,
                    "contact_email": emails[0] if emails else None,
                    "contact_page": url,
                    "source": "metro",
                })
                output.append(job)
            if phones or emails:
                print(f"      [metro] {name} @ {city}: {len(phones)} phones, {len(emails)} emails")
    return output
