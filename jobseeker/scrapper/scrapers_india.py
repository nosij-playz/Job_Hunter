"""India-focused scrapers: Naukri (via Playwright), Cutshort, Instahyre."""
import asyncio
import re
from playwright.async_api import async_playwright

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobHunter/1.0"}

# South India cities — search keywords Naukri understands
SOUTH_CITIES = [
    "kochi", "cochin", "thiruvananthapuram", "trivandrum",
    "kozhikode", "calicut", "thrissur", "kollam",
    "bangalore", "bengaluru", "chennai", "coimbatore",
    "hyderabad", "mysore", "mangalore", "madurai",
    "trichy", "vijayawada", "visakhapatnam", "puducherry",
]


def _strip_html(html):
    return re.sub(r"<[^>]+>", " ", html or "").strip()


def _norm(job, source, cfg):
    base = {
        "source": source, "company": None, "title": None, "location": "",
        "remote": 0, "salary_min": None, "salary_max": None, "currency": "INR",
        "url": None, "apply_url": None, "posted_date": None, "description": "",
        "contact_email": None, "contact_phone": None, "contact_page": None,
        "priority": 90,
    }
    base.update(job)
    return base


# ─────────────────────────────────────────────────────────────
# NAUKRI — Playwright (public search results)
# ─────────────────────────────────────────────────────────────
async def _naukri_search(page, keyword, city, max_results=40):
    """Search Naukri for a keyword in a city. Returns raw job dicts."""
    kw = keyword.lower().replace(" ", "-")
    ct = city.lower().replace(" ", "-")
    url = f"https://www.naukri.com/{kw}-jobs-in-{ct}?k={keyword.replace(' ', '%20')}&l={city.replace(' ', '%20')}&experience=0"

    jobs = []
    try:
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        cards = await page.query_selector_all("article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple")
        for c in cards[:max_results]:
            try:
                title_el = await c.query_selector("a.title, a.jobTitle, h2 a")
                company_el = await c.query_selector("a.comp-name, a.subTitle, .companyInfo a")
                loc_el = await c.query_selector(".locWdth, .location, .loc span")
                exp_el = await c.query_selector(".expwdth, .experience, .exp span")
                desc_el = await c.query_selector(".job-desc, .jobDescription, .job-description")
                sal_el = await c.query_selector(".sal, .salary, .sal-wrap span")

                title = (await title_el.inner_text()).strip() if title_el else ""
                company = (await company_el.inner_text()).strip() if company_el else ""
                location = (await loc_el.inner_text()).strip() if loc_el else city
                exp = (await exp_el.inner_text()).strip() if exp_el else ""
                desc = (await desc_el.inner_text()).strip() if desc_el else ""
                sal = (await sal_el.inner_text()).strip() if sal_el else ""
                href = await title_el.get_attribute("href") if title_el else None

                if not title or not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.naukri.com" + href

                jobs.append({
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": href,
                    "apply_url": href,
                    "description": f"{desc} Experience: {exp}. Salary: {sal}".strip(),
                    "currency": "INR",
                    "remote": int("remote" in location.lower() or "work from home" in location.lower()),
                })
            except Exception:
                continue
    except Exception as e:
        print(f"      [naukri] search fail {keyword}@{city}: {str(e)[:80]}")
    return jobs


async def naukri_async(cfg):
    """Playwright-driven Naukri scraper — South India focused."""
    keywords = [
        "fresher software engineer", "graduate software engineer",
        "junior python developer", "junior ai engineer",
        "machine learning fresher", "data scientist fresher",
        "full stack fresher", "backend developer fresher",
    ]
    cities = ["kochi", "thiruvananthapuram", "bangalore", "chennai", "coimbatore", "hyderabad"]

    all_jobs = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        for kw in keywords:
            for city in cities[:4]:
                try:
                    found = await _naukri_search(page, kw, city, max_results=25)
                    for j in found:
                        all_jobs.append(_norm(j, f"naukri:{city}", cfg))
                    if found:
                        print(f"      [naukri] {kw} @ {city}: {len(found)}")
                except Exception as e:
                    print(f"      [naukri] error {kw}@{city}: {str(e)[:80]}")
                await asyncio.sleep(2)

        await browser.close()
    return all_jobs


def naukri(cfg):
    """Sync wrapper for main.py."""
    try:
        return asyncio.run(naukri_async(cfg))
    except RuntimeError:
        return []
