"""Additional public job sources — no auth, no rate limits."""
import re
import requests
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobHunter/2.0"}


def _strip_html(html):
    return re.sub(r"<[^>]+>", " ", html or "").strip()


# ─── Himalayas ─────────────────────────────────────────────
def himalayas(cfg):
    try:
        r = requests.get("https://himalayas.app/jobs/api?limit=100", headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"      [himalayas] {str(e)[:60]}")
        return []
    jobs = []
    for j in data.get("jobs", []):
        url = j.get("applicationLink") or j.get("url")
        if not url:
            continue
        jobs.append({
            "company": j.get("companyName"),
            "title": j.get("title"),
            "location": ", ".join(j.get("locationRestrictions") or ["Remote"]),
            "remote": 1,
            "url": url, "apply_url": url,
            "posted_date": j.get("pubDate"),
            "description": _strip_html(j.get("description")),
        })
    return jobs


# ─── Working Nomads ────────────────────────────────────────
def working_nomads(cfg):
    try:
        r = requests.get("https://www.workingnomads.com/api/exposed_jobs/", headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"      [working_nomads] {str(e)[:60]}")
        return []
    return [{
        "company": j.get("company_name"),
        "title": j.get("title"),
        "location": j.get("location") or "Remote",
        "remote": 1,
        "url": j.get("url"), "apply_url": j.get("url"),
        "posted_date": j.get("pub_date"),
        "description": _strip_html(j.get("description")),
    } for j in data if j.get("url")]


# ─── WeWorkRemotely (RSS) ──────────────────────────────────
def weworkremotely(cfg):
    feeds = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    ]
    jobs = []
    for feed in feeds:
        try:
            r = requests.get(feed, headers=UA, timeout=30)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                pub_el = item.find("pubDate")
                if title_el is None or link_el is None:
                    continue
                raw = title_el.text or ""
                company, title = (raw.split(":", 1) + [""])[:2] if ":" in raw else ("", raw)
                jobs.append({
                    "company": company.strip(),
                    "title": title.strip(),
                    "location": "Remote",
                    "remote": 1,
                    "url": link_el.text, "apply_url": link_el.text,
                    "posted_date": pub_el.text if pub_el is not None else None,
                    "description": _strip_html(desc_el.text if desc_el is not None else ""),
                })
        except Exception:
            continue
    return jobs


# ─── Jobicy ────────────────────────────────────────────────
def jobicy(cfg):
    try:
        r = requests.get("https://jobicy.com/api/v2/remote-jobs?count=50", headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"      [jobicy] {str(e)[:60]}")
        return []
    return [{
        "company": j.get("companyName"),
        "title": j.get("jobTitle"),
        "location": j.get("jobGeo") or "Remote",
        "remote": 1,
        "url": j.get("url"), "apply_url": j.get("url"),
        "posted_date": j.get("pubDate"),
        "description": _strip_html(j.get("jobExcerpt") or j.get("jobDescription") or ""),
    } for j in data.get("jobs", []) if j.get("url")]


# ─── HN Who Is Hiring ──────────────────────────────────────
def hn_whoishiring(cfg):
    try:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date"
            "?query=Ask%20HN%20Who%20is%20hiring&tags=story&hitsPerPage=5",
            headers=UA, timeout=30,
        )
        hits = r.json().get("hits", [])
        if not hits:
            return []
        story_id = hits[0].get("objectID")
        r2 = requests.get(
            f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=300",
            headers=UA, timeout=30,
        )
        jobs = []
        for h in r2.json().get("hits", []):
            text = _strip_html(h.get("comment_text") or "")
            if len(text) < 100:
                continue
            first = text.split("\n")[0][:180]
            jobs.append({
                "company": "HN",
                "title": first,
                "location": "", "remote": 1,
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "apply_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "posted_date": h.get("created_at"),
                "description": text,
            })
        return jobs
    except Exception as e:
        print(f"      [hn] {str(e)[:60]}")
        return []


# ─── Recruitee ─────────────────────────────────────────────
def recruitee(slug, cfg):
    try:
        r = requests.get(f"https://{slug}.recruitee.com/api/offers/", headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("offers", []):
        url = j.get("careers_url") or j.get("url")
        if not url:
            continue
        jobs.append({
            "company": slug,
            "title": j.get("title"),
            "location": j.get("location") or "",
            "remote": int(j.get("remote", False)),
            "url": url, "apply_url": url,
            "posted_date": j.get("published_at"),
            "description": _strip_html(j.get("description") or ""),
        })
    return jobs


# ─── SmartRecruiters ───────────────────────────────────────
def smartrecruiters(slug, cfg):
    try:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
            headers=UA, timeout=20,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location", {})
        location = f"{loc.get('city','')}, {loc.get('country','')}".strip(", ")
        jobs.append({
            "company": slug,
            "title": j.get("name"),
            "location": location,
            "remote": int(loc.get("remote", False)),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
            "apply_url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
            "posted_date": j.get("releasedDate"),
            "description": "",
        })
    return jobs
