"""All scrapers — config-driven, public APIs only."""
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobHunter/1.0"}


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _strip_html(html):
    return re.sub(r"<[^>]+>", " ", html or "").strip()


def _company_priority(company, source, cfg):
    overrides = cfg.get("company_priority", {}).get("overrides", {})
    default = cfg.get("company_priority", {}).get("default", 50)
    if not company:
        return default
    slug = re.sub(r"[^a-z0-9]", "", company.lower())
    if slug in overrides:
        return overrides[slug]
    if ":" in source:
        src_slug = re.sub(r"[^a-z0-9]", "", source.split(":", 1)[1].lower())
        if src_slug in overrides:
            return overrides[src_slug]
    if len(slug) <= 12 and not any(
        k in slug for k in ("inc", "corp", "group", "systems", "solutions", "technologies")
    ):
        return default + 10
    return default


def _location_priority(location, remote, cfg):
    lp = cfg.get("location_priority", {})
    loc = (location or "").lower()
    for tier in ("tier_1_core", "tier_2_south", "tier_3_india_remote", "tier_4_global_remote"):
        block = lp.get(tier)
        if not block:
            continue
        for city in block.get("cities", []):
            if city in loc:
                return block.get("priority", 50)
    if remote:
        # Global remote: below India-remote tier
        return lp.get("tier_4_global_remote", {}).get("priority", 55)
    # Truly unknown location → deprioritize hard
    return 20


def _norm(job, source, cfg):
    base = {
        "source": source, "company": None, "title": None, "location": "",
        "remote": 0, "salary_min": None, "salary_max": None, "currency": None,
        "url": None, "apply_url": None, "posted_date": None, "description": "",
        "contact_email": None, "contact_phone": None, "contact_page": None,
    }
    base.update(job)
    company_p = _company_priority(base.get("company"), source, cfg)
    location_p = _location_priority(base.get("location"), base.get("remote"), cfg)

    # Source bonus: Indian boards beat global remote boards
    source_bonus = 0
    if source in ("kerala", "metro"):
        source_bonus = 20
    elif source.startswith("naukri"):
        source_bonus = 15
    elif source.startswith("cutshort") or source.startswith("instahyre"):
        source_bonus = 12
    elif source.startswith("linkedin"):
        source_bonus = 10
    elif source in ("arbeitnow", "remotive", "remoteok", "remoteok-fresher"):
        source_bonus = -5

    # 80% location, 20% company, + source bonus
    base["priority"] = int(0.8 * location_p + 0.2 * company_p + source_bonus)
    base["priority"] = max(0, min(130, base["priority"]))
    return base


# ─────────────────────────────────────────────────────────────
# CONFIG-DRIVEN FILTERS
# ─────────────────────────────────────────────────────────────
def _title_in_whitelist(title, cfg):
    if not title:
        return False
    wl = cfg["filters"]["role_whitelist"]
    t = " " + title.lower() + " "
    return any(k in t for k in wl)


def _title_not_senior(title, cfg):
    if not title:
        return False
    t = " " + title.lower() + " "
    reject = cfg["filters"]["seniority_reject"]
    boost = cfg["filters"]["seniority_boost"]
    has_senior = any(k in t for k in reject)
    has_fresher = any(k in t for k in boost)
    if has_senior and not has_fresher:
        return False
    return True


def _location_ok(location, remote, cfg):
    if remote:
        return True
    ok = [l.lower() for l in cfg["filters"]["location_whitelist"]]
    loc = (location or "").lower()
    if not ok:
        return True
    return any(l in loc for l in ok)


# ─────────────────────────────────────────────────────────────
# SOURCE SCRAPERS
# ─────────────────────────────────────────────────────────────
def remoteok(cfg):
    try:
        r = requests.get("https://remoteok.com/api", headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    jobs = []
    for item in data[1:]:
        url = item.get("url") or item.get("apply_url")
        if not url:
            continue
        jobs.append(_norm({
            "company": item.get("company"),
            "title": item.get("position"),
            "location": item.get("location") or "Remote",
            "remote": 1,
            "salary_min": item.get("salary_min"),
            "salary_max": item.get("salary_max"),
            "currency": "USD",
            "url": url,
            "apply_url": item.get("apply_url") or url,
            "posted_date": str(item.get("date")),
            "description": _strip_html(item.get("description")),
        }, "remoteok", cfg))
    return jobs


def remoteok_fresher(cfg):
    try:
        r = requests.get("https://remoteok.com/api", headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    fresher_tags = ["junior", "entry", "graduate", "intern", "associate", "trainee", "fresher"]
    jobs = []
    for item in data[1:]:
        title = (item.get("position") or "").lower()
        tags = [t.lower() for t in (item.get("tags") or [])]
        blob = title + " " + " ".join(tags)
        if not any(f in blob for f in fresher_tags):
            continue
        url = item.get("url") or item.get("apply_url")
        if not url:
            continue
        jobs.append(_norm({
            "company": item.get("company"),
            "title": item.get("position"),
            "location": item.get("location") or "Remote",
            "remote": 1,
            "salary_min": item.get("salary_min"),
            "salary_max": item.get("salary_max"),
            "currency": "USD",
            "url": url,
            "apply_url": item.get("apply_url") or url,
            "posted_date": str(item.get("date")),
            "description": _strip_html(item.get("description")),
        }, "remoteok-fresher", cfg))
    return jobs


def arbeitnow(cfg):
    pages = cfg["sources"]["arbeitnow"].get("pages", 3)
    jobs = []
    for page in range(1, pages + 1):
        try:
            r = requests.get(
                f"https://www.arbeitnow.com/api/job-board-api?page={page}",
                headers=UA, timeout=30,
            )
            if r.status_code != 200:
                break
            for j in r.json().get("data", []):
                url = j.get("url")
                if not url:
                    continue
                jobs.append(_norm({
                    "company": j.get("company_name"),
                    "title": j.get("title"),
                    "location": j.get("location") or "",
                    "remote": int(j.get("remote", False)),
                    "url": url,
                    "apply_url": url,
                    "posted_date": str(j.get("created_at")),
                    "description": _strip_html(j.get("description")),
                }, "arbeitnow", cfg))
        except Exception:
            continue
    return jobs


def remotive(cfg):
    limit = cfg["sources"]["remotive"].get("limit", 100)
    try:
        r = requests.get(
            f"https://remotive.com/api/remote-jobs?limit={limit}",
            headers=UA, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("jobs", []):
        url = j.get("url")
        if not url:
            continue
        jobs.append(_norm({
            "company": j.get("company_name"),
            "title": j.get("title"),
            "location": j.get("candidate_required_location") or "Remote",
            "remote": 1,
            "currency": j.get("salary", ""),
            "url": url,
            "apply_url": url,
            "posted_date": j.get("publication_date"),
            "description": _strip_html(j.get("description")),
        }, "remotive", cfg))
    return jobs


def greenhouse(slug, cfg):
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
            headers=UA, timeout=8,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name", "")
        url = j.get("absolute_url")
        if not url:
            continue
        jobs.append(_norm({
            "company": slug, "title": j.get("title"),
            "location": location,
            "remote": int("remote" in location.lower()),
            "url": url, "apply_url": url,
            "posted_date": j.get("updated_at"),
            "description": _strip_html(j.get("content", "")),
            "contact_page": f"https://boards.greenhouse.io/{slug}",
        }, f"greenhouse:{slug}", cfg))
    return jobs


def lever(slug, cfg):
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            headers=UA, timeout=8,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data:
        location = j.get("categories", {}).get("location", "")
        url = j.get("hostedUrl")
        if not url:
            continue
        jobs.append(_norm({
            "company": slug, "title": j.get("text"),
            "location": location,
            "remote": int("remote" in location.lower()),
            "url": url, "apply_url": j.get("applyUrl") or url,
            "posted_date": str(j.get("createdAt")),
            "description": j.get("descriptionPlain") or "",
            "contact_page": url,
        }, f"lever:{slug}", cfg))
    return jobs


def ashby(slug, cfg):
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            headers=UA, timeout=8,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("jobs", []):
        url = j.get("jobUrl") or j.get("applyUrl")
        if not url:
            continue
        loc = j.get("location") or ""
        jobs.append(_norm({
            "company": slug, "title": j.get("title"),
            "location": loc,
            "remote": int(j.get("isRemote", False) or "remote" in loc.lower()),
            "url": url, "apply_url": j.get("applyUrl") or url,
            "posted_date": j.get("publishedAt"),
            "description": _strip_html(j.get("descriptionHtml") or j.get("descriptionPlain") or ""),
            "contact_page": f"https://jobs.ashbyhq.com/{slug}",
        }, f"ashby:{slug}", cfg))
    return jobs


def workable(slug, cfg):
    try:
        r = requests.get(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            headers=UA, timeout=8,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("results", []):
        url = j.get("url") or f"https://apply.workable.com/{slug}/j/{j.get('shortcode','')}"
        loc = (j.get("location") or {}).get("location_str") or ""
        jobs.append(_norm({
            "company": slug, "title": j.get("title"),
            "location": loc,
            "remote": int(j.get("remote", False) or "remote" in loc.lower()),
            "url": url, "apply_url": url,
            "posted_date": j.get("published_on"),
            "description": _strip_html(j.get("description") or ""),
            "contact_page": f"https://apply.workable.com/{slug}/",
        }, f"workable:{slug}", cfg))
    return jobs


# ─────────────────────────────────────────────────────────────
# DISPATCHER
# ─────────────────────────────────────────────────────────────
def _ats_parallel(fn, slugs, source_name, cfg, workers=20):
    """Run an ATS scraper across many slugs in parallel."""
    if not slugs:
        return []
    jobs = []
    done = 0
    total = len(slugs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, slug, cfg): slug for slug in slugs}
        for fut in as_completed(futures):
            try:
                jobs.extend(fut.result())
            except Exception:
                pass
            done += 1
            if done % 25 == 0 or done == total:
                print(f"      [{source_name}] {done}/{total} slugs → {len(jobs)} jobs")
    return jobs


def discover(cfg):
    src = cfg["sources"]
    all_jobs = []

    if src.get("remoteok", {}).get("enabled"):
        print("      → RemoteOK...")
        all_jobs += remoteok(cfg)
        all_jobs += remoteok_fresher(cfg)

    if src.get("arbeitnow", {}).get("enabled"):
        print("      → Arbeitnow...")
        all_jobs += arbeitnow(cfg)

    if src.get("remotive", {}).get("enabled"):
        print("      → Remotive...")
        all_jobs += remotive(cfg)

    for key, fn, label in (
        ("greenhouse", greenhouse, "greenhouse"),
        ("lever", lever, "lever"),
        ("ashby", ashby, "ashby"),
        ("workable", workable, "workable"),
    ):
        if src.get(key, {}).get("enabled"):
            slugs = src[key].get("companies", [])
            print(f"      → {label.title()} ({len(slugs)}) [parallel]...")
            all_jobs += _ats_parallel(fn, slugs, label, cfg)

    # ── Metro and Kerala career pages ──
    for module_name, function_name, config_key, label in (
        ("jobseeker.scrapper.scrapers_metro", "fetch_metro", "metro", "Metro career pages"),
        ("jobseeker.scrapper.scrapers_kerala", "fetch_kerala", "kerala", "Kerala companies"),
    ):
        try:
            module = __import__(module_name, fromlist=[function_name])
            fetch = getattr(module, function_name)
            if src.get(config_key, {}).get("enabled"):
                print(f"      → {label}...")
                new_jobs = fetch(cfg)
                all_jobs += [_norm(j, j.get("source", config_key), cfg) for j in new_jobs]
                print(f"      [{config_key}] total: {len(new_jobs)}")
        except Exception as exc:
            print(f"      [{config_key}] skipped: {exc}")

    # ── LinkedIn (guest API) ──
    try:
        from jobseeker.scrapper.scrapers_linkedin import fetch_linkedin
        if src.get("linkedin", {}).get("enabled"):
            print("      → LinkedIn (guest API)...")
            li_jobs = fetch_linkedin(cfg)
            for j in li_jobs:
                j["source"] = "linkedin"
            all_jobs += [_norm(j, "linkedin", cfg) for j in li_jobs]
            print(f"      [linkedin] total: {len(li_jobs)}")
    except Exception as e:
        print(f"      [linkedin] skipped: {e}")

    # ── Startups (Cutshort / Instahyre / Wellfound) ──
    try:
        from jobseeker.scrapper.scrapers_startups import fetch_startups
        if src.get("startups", {}).get("enabled"):
            print("      → Startup boards (Cutshort / Instahyre / Wellfound)...")
            st_jobs = fetch_startups(cfg)
            all_jobs += [_norm(j, j.get("source", "startup"), cfg) for j in st_jobs]
            print(f"      [startups] total: {len(st_jobs)}")
    except Exception as e:
        print(f"      [startups] skipped: {e}")

    try:
        from jobseeker.scrapper.scrapers_tier1 import fetch_tier1
        if src.get("tier1", {}).get("enabled"):
            print("      → Tier 1 (FAANG direct APIs)...")
            t1_jobs = fetch_tier1(cfg)
            all_jobs += [_norm(j, j.get("source", "tier1"), cfg) for j in t1_jobs]
            print(f"      [tier1] total: {len(t1_jobs)}")
    except Exception as e:
        print(f"      [tier1] skipped: {e}")

    try:
        from jobseeker.scrapper.scrapers_sme import fetch_sme
        if src.get("sme", {}).get("enabled"):
            print("      → SME boards (Hasjob / HackerEarth)...")
            sm_jobs = fetch_sme(cfg)
            all_jobs += [_norm(j, j.get("source", "sme"), cfg) for j in sm_jobs]
            print(f"      [sme] total: {len(sm_jobs)}")
    except Exception as e:
        print(f"      [sme] skipped: {e}")

    try:
        from jobseeker.scrapper.scrapers_more import (
            himalayas, working_nomads, weworkremotely,
            jobicy, hn_whoishiring, recruitee, smartrecruiters,
        )
        aggregators = (
            ("himalayas", himalayas),
            ("working_nomads", working_nomads),
            ("weworkremotely", weworkremotely),
            ("jobicy", jobicy),
            ("hn_whoishiring", hn_whoishiring),
        )
        for key, fn in aggregators:
            if src.get(key, {}).get("enabled"):
                print(f"      → {key.replace('_', ' ').title()}...")
                all_jobs += [_norm(j, key, cfg) for j in fn(cfg)]
        for key, fn in (("recruitee", recruitee), ("smartrecruiters", smartrecruiters)):
            if src.get(key, {}).get("enabled"):
                slugs = src[key].get("companies", [])
                all_jobs += _ats_parallel(fn, slugs, key, cfg)
    except Exception as e:
        print(f"      [scrapers_more] skipped: {e}")

    print(f"      Raw: {len(all_jobs)}")

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if _title_in_whitelist(j.get("title"), cfg)]
    print(f"      Role filter: {before} → {len(all_jobs)}")

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if _title_not_senior(j.get("title"), cfg)]
    print(f"      Seniority filter: {before} → {len(all_jobs)}")

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if _location_ok(j.get("location"), j.get("remote"), cfg)]
    print(f"      Location whitelist: {before} → {len(all_jobs)}")

    bl = [x.lower() for x in cfg["filters"].get("company_blacklist", [])]
    if bl:
        before = len(all_jobs)
        all_jobs = [j for j in all_jobs
                    if not any(b in (j.get("company") or "").lower() for b in bl)]
        print(f"      Company blacklist: {before} → {len(all_jobs)}")

    tex = cfg["filters"].get("title_exclude_keywords", [])
    if tex:
        before = len(all_jobs)
        all_jobs = [j for j in all_jobs
                    if not any(k in (" " + (j.get("title") or "").lower() + " ") for k in tex)]
        print(f"      Title exclude: {before} → {len(all_jobs)}")

    t1 = sum(1 for j in all_jobs if _location_priority(j.get("location"), j.get("remote"), cfg) >= 100)
    t2 = sum(1 for j in all_jobs if 85 <= _location_priority(j.get("location"), j.get("remote"), cfg) < 100)
    t3 = sum(1 for j in all_jobs if 70 <= _location_priority(j.get("location"), j.get("remote"), cfg) < 85)
    print(f"      Location tiers: T1(S.India core)={t1}  T2(S.India)={t2}  T3(India/remote)={t3}")

    seen, dedup = set(), []
    for j in all_jobs:
        u = j.get("url")
        if u and u not in seen:
            seen.add(u)
            dedup.append(j)

    dedup.sort(key=lambda x: (-(x.get("priority") or 0), x.get("company") or ""))
    return dedup