"""Dynamic company discovery for ATS and regional company directories.

This keeps a curated top-tier list for speed, while expanding the search surface
with automatically discovered companies from ATS index harvests and Kerala sites.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

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

ROOT = Path(__file__).resolve().parent.parent
DISCOVERED_PATH = ROOT / "data" / "discovered.json"
CC_INDEX = "https://index.commoncrawl.org/CC-MAIN-2024-46-index"


def _default_discovery():
    return {
        "greenhouse": [],
        "lever": [],
        "ashby": [],
        "workable": [],
        "kerala_sites": [],
    }


def _normalize_url(url):
    if not url:
        return ""
    value = str(url).strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def _company_name_from_site(site_url):
    parsed = urlparse(_normalize_url(site_url))
    host = parsed.netloc.lower().replace("www.", "")
    if not host:
        return "Discovered Company"
    label = host.split(".")[0].replace("-", " ").replace("_", " ")
    return label.title() if label else "Discovered Company"


def _cc_query(url_pattern, limit=500, timeout=60):
    """Return a list of crawled URLs matching a pattern from Common Crawl."""
    try:
        response = requests.get(
            CC_INDEX,
            params={
                "url": url_pattern,
                "output": "json",
                "limit": str(limit),
                "filter": "status:200",
            },
            headers=UA,
            timeout=timeout,
        )
        if response.status_code != 200:
            return []

        urls = []
        for line in response.text.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = payload.get("url")
            if value:
                urls.append(value)
        return urls
    except Exception:
        return []


def harvest_greenhouse_slugs(limit=500):
    urls = _cc_query("boards.greenhouse.io/*", limit=limit)
    slugs = set()
    for url in urls:
        match = re.search(r"boards\.greenhouse\.io/([a-zA-Z0-9\-_]+)", url)
        if match:
            slugs.add(match.group(1).lower())
    return sorted(slugs)


def harvest_lever_slugs(limit=500):
    urls = _cc_query("jobs.lever.co/*", limit=limit)
    slugs = set()
    for url in urls:
        match = re.search(r"jobs\.lever\.co/([a-zA-Z0-9\-_]+)", url)
        if match:
            slugs.add(match.group(1).lower())
    return sorted(slugs)


def harvest_ashby_slugs(limit=500):
    urls = _cc_query("jobs.ashbyhq.com/*", limit=limit)
    slugs = set()
    for url in urls:
        match = re.search(r"jobs\.ashbyhq\.com/([a-zA-Z0-9\-_]+)", url)
        if match:
            slugs.add(match.group(1).lower())
    return sorted(slugs)


def harvest_workable_slugs(limit=500):
    urls = _cc_query("apply.workable.com/*", limit=limit)
    slugs = set()
    for url in urls:
        match = re.search(r"apply\.workable\.com/([a-zA-Z0-9\-_]+)", url)
        if match:
            slugs.add(match.group(1).lower())
    return sorted(slugs)


def harvest_infopark():
    sites = set()
    for url in [
        "https://infopark.in/companies",
        "https://infopark.in/companies/",
        "https://infopark.in/company-listing",
    ]:
        try:
            response = requests.get(url, headers=UA, timeout=15)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith("http") and "infopark" not in href.lower():
                    sites.add(href.split("?")[0].rstrip("/"))
            if sites:
                break
        except Exception:
            continue
    return sorted(sites)[:500]


def harvest_technopark():
    sites = set()
    for url in [
        "https://technopark.org/companies",
        "https://technopark.org/company-list",
        "https://technopark.org/",
    ]:
        try:
            response = requests.get(url, headers=UA, timeout=15)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith("http") and "technopark" not in href.lower():
                    sites.add(href.split("?")[0].rstrip("/"))
            if sites:
                break
        except Exception:
            continue
    return sorted(sites)[:500]


def harvest_ksum():
    sites = set()
    for url in [
        "https://startupmission.kerala.gov.in/startups",
        "https://startupmission.kerala.gov.in/",
    ]:
        try:
            response = requests.get(url, headers=UA, timeout=15)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith("http") and "startupmission" not in href.lower():
                    sites.add(href.split("?")[0].rstrip("/"))
        except Exception:
            continue
    return sorted(sites)[:800]


def ddg_search(query, max_results=25):
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=UA,
            timeout=15,
        )
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        urls = []
        for anchor in soup.select("a.result__a")[:max_results]:
            href = anchor.get("href", "")
            match = re.search(r"uddg=([^&]+)", href)
            if match:
                href = unquote(match.group(1))
            urls.append(href)
        return urls
    except Exception:
        return []


def discover_slugs_from_search(city, role="software engineer"):
    found = {"greenhouse": set(), "lever": set(), "ashby": set()}
    queries = [
        f"site:boards.greenhouse.io {role} {city}",
        f"site:jobs.lever.co {role} {city}",
        f"site:jobs.ashbyhq.com {role} {city}",
    ]
    for query in queries:
        for url in ddg_search(query, max_results=20):
            if "boards.greenhouse.io" in url:
                match = re.search(r"boards\.greenhouse\.io/([a-zA-Z0-9\-_]+)", url)
                if match:
                    found["greenhouse"].add(match.group(1).lower())
            elif "jobs.lever.co" in url:
                match = re.search(r"jobs\.lever\.co/([a-zA-Z0-9\-_]+)", url)
                if match:
                    found["lever"].add(match.group(1).lower())
            elif "jobs.ashbyhq.com" in url:
                match = re.search(r"jobs\.ashbyhq\.com/([a-zA-Z0-9\-_]+)", url)
                if match:
                    found["ashby"].add(match.group(1).lower())
        time.sleep(1)
    return {key: sorted(value) for key, value in found.items()}


def load_discovered():
    path = Path(DISCOVERED_PATH)
    if not path.exists():
        return _default_discovery()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        merged = _default_discovery()
        for key, value in merged.items():
            if key in data:
                merged[key] = data[key]
        return merged
    except Exception:
        return _default_discovery()


def save_discovered(data):
    path = Path(DISCOVERED_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def merge_into_config(cfg):
    """Merge discovered ATS slugs and Kerala sites back into the live cfg."""
    discoveries = load_discovered()
    sources = cfg.setdefault("sources", {})

    for key in ("greenhouse", "lever", "ashby", "workable"):
        section = sources.get(key, {})
        if not isinstance(section, dict):
            continue
        existing = set()
        for item in section.get("companies", []):
            if isinstance(item, str):
                existing.add(item.lower())
        new_slugs = set(str(item).lower() for item in discoveries.get(key, []))
        section["companies"] = sorted(existing | new_slugs)
        sources[key] = section

    kerala = sources.get("kerala", {})
    if isinstance(kerala, dict):
        existing = []
        seen = set()
        for item in kerala.get("companies", []):
            if isinstance(item, dict):
                website = (item.get("website") or item.get("name") or "").strip().lower()
                if website:
                    seen.add(website)
                existing.append(item)
        for site in discoveries.get("kerala_sites", []):
            url = _normalize_url(site)
            if not url:
                continue
            key = url.lower()
            if key in seen:
                continue
            existing.append({
                "name": _company_name_from_site(url),
                "website": url,
                "city": "Kerala",
            })
            seen.add(key)
        kerala["companies"] = existing
        sources["kerala"] = kerala

    return cfg


def discover_and_cache(force=False):
    """Harvest new companies and cache them to data/discovered.json."""
    path = Path(DISCOVERED_PATH)
    if not force and path.exists():
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days < 7:
            return load_discovered()

    greenhouse = harvest_greenhouse_slugs(500)
    lever = harvest_lever_slugs(500)
    ashby = harvest_ashby_slugs(500)
    workable = harvest_workable_slugs(500)

    infopark = harvest_infopark()
    technopark = harvest_technopark()
    ksum = harvest_ksum()

    ddg = {"greenhouse": set(), "lever": set(), "ashby": set()}
    for city in ["kochi", "bangalore", "chennai", "trivandrum", "hyderabad"]:
        discovered = discover_slugs_from_search(city)
        for key in ddg:
            ddg[key].update(discovered.get(key, []))

    data = {
        "greenhouse": sorted(set(greenhouse) | ddg["greenhouse"]),
        "lever": sorted(set(lever) | ddg["lever"]),
        "ashby": sorted(set(ashby) | ddg["ashby"]),
        "workable": workable,
        "kerala_sites": sorted(set(infopark) | set(technopark) | set(ksum)),
    }
    save_discovered(data)
    return data
