"""Playwright auto-applier. Handles Greenhouse + Lever reliably; generic fallback otherwise."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

from jobseeker.core import make_cover_letter


# ─── Field extraction JS ────────────────────────────────────
EXTRACT_JS = """() => {
  const out = [];
  const els = [...document.querySelectorAll('input, textarea, select')];
  els.forEach((el, idx) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return;
    if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;
    const label = el.labels && el.labels[0] ? el.labels[0].innerText.trim() : '';
    out.push({
      idx,
      tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      id: el.id || '',
      name: el.name || '',
      placeholder: el.placeholder || '',
      aria: el.getAttribute('aria-label') || '',
      label: label,
      required: !!el.required,
      options: el.tagName === 'SELECT'
        ? [...el.options].map(o => o.value || o.text)
        : null
    });
  });
  return out;
}"""

FILL_JS = """(p) => {
  const els = [...document.querySelectorAll('input, textarea, select')];
  const el = els[p.idx];
  if (!el) return false;
  if (el.tagName === 'SELECT') {
    const opt = [...el.options].find(o => (o.value||'').toLowerCase() === String(p.value).toLowerCase()
                                       || (o.text||'').toLowerCase() === String(p.value).toLowerCase());
    if (!opt) return false;
    el.value = opt.value;
  } else if (el.type === 'checkbox' || el.type === 'radio') {
    el.checked = !!p.value;
  } else {
    el.focus();
    el.value = p.value;
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
  return true;
}"""


# ─── Build profile → field values mapping ───────────────────
def build_field_map(llm, profile, job, cover_letter, fields):
    profile_flat = {
        "first_name": (profile.get("name") or "").split(" ")[0],
        "last_name": " ".join((profile.get("name") or "").split(" ")[1:]) or "",
        "full_name": profile.get("name") or "",
        "email": profile.get("email") or "",
        "phone": profile.get("phone") or "",
        "location": profile.get("location") or "",
        "linkedin": (profile.get("links") or {}).get("linkedin") or "",
        "github": (profile.get("links") or {}).get("github") or "",
        "portfolio": (profile.get("links") or {}).get("portfolio") or "",
        "summary": profile.get("summary") or "",
        "skills": ", ".join(profile.get("skills", [])[:25]),
        "years_experience": "1",
        "cover_letter": cover_letter,
        "current_company": "Fresher",
        "notice_period": "Immediate",
        "expected_salary": "",
        "authorized_to_work": "Yes",
        "requires_sponsorship": "Yes",
        "how_did_you_hear": "Company careers page",
    }

    prompt = f"""Fill the job application form.

Return strict JSON: {{"<idx>": "<value or null>", ...}}

Rules:
- Return null for: file inputs, hidden, submit buttons, unknown fields, salary negotiation.
- Checkbox/radio: return true or false.
- If field asks "authorized to work" for US role → "Yes" (will require sponsorship).
- If field asks visa/sponsorship → "Yes".
- If field asks cover letter / why this role → use cover_letter (trim to 250 words).
- If field asks "expected salary" → null.
- Fill only what candidate data supports.

CANDIDATE DATA:
{profile_flat}

JOB: {job.get('title')} at {job.get('company')}

FORM FIELDS:
{fields}
"""
    try:
        return llm.gen_json(prompt)
    except Exception:
        return {}


# ─── Submit detector ────────────────────────────────────────
SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Submit application")',
    'button:has-text("Submit Application")',
    'button:has-text("Submit")',
    'button:has-text("Send application")',
    'button:has-text("Apply")',
    '#submit_app',
]


async def find_submit(page):
    for sel in SUBMIT_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return el
        except Exception:
            continue
    return None


# ─── Main apply routine ─────────────────────────────────────
async def apply_one(page, job, profile, llm, cv_abs_path):
    try:
        await page.goto(job["apply_url"], timeout=90000, wait_until="domcontentloaded")
    except Exception as e:
        return False, f"navigation failed: {e}"

    # Wait for form to hydrate
    await page.wait_for_timeout(7000)
    # Some ATS load forms inside an iframe
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            has_form = await frame.evaluate(
                "() => !!document.querySelector('input, textarea, select')"
            )
            if has_form:
                # Use the frame's content for field extraction next
                page = frame
                break
    except Exception:
        pass

    # Scroll to reveal lazy fields
    try:
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight/3)")
            await page.wait_for_timeout(700)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)
    except Exception:
        pass

    cover_letter = make_cover_letter(llm, profile, job)
    fields = await page.evaluate(EXTRACT_JS)
    if not fields:
        return False, "no fields detected"

    mapping = build_field_map(llm, profile, job, cover_letter, fields)

    filled = 0
    for idx_str, value in mapping.items():
        if value in (None, "", [], False):
            continue
        try:
            ok = await page.evaluate(FILL_JS, {"idx": int(idx_str), "value": str(value)})
            if ok:
                filled += 1
        except Exception:
            continue

    # Upload CV
    try:
        file_inputs = await page.query_selector_all('input[type="file"]')
        for fi in file_inputs:
            await fi.set_input_files(cv_abs_path)
            filled += 1
            break
    except Exception:
        pass

    if filled == 0:
        return False, "nothing filled"

    await page.wait_for_timeout(1500)

    submit = await find_submit(page)
    if not submit:
        return False, "no submit button found"

    try:
        await submit.click()
    except Exception as e:
        return False, f"submit click failed: {e}"

    await page.wait_for_timeout(6000)

    body = ""
    try:
        body = (await page.inner_text("body")).lower()
    except Exception:
        pass

    success = ["thank you", "thanks for applying", "application received",
               "application submitted", "we'll be in touch", "we have received",
               "successfully applied", "your application"]
    if any(m in body for m in success):
        return True, "submitted"

    # URL-based check
    if any(x in page.url.lower() for x in ("thank", "success", "confirmation", "applied")):
        return True, "submitted (url)"

    return False, "submit unclear — check manually"


# ─── Runner ─────────────────────────────────────────────────
async def run(jobs, profile, llm, cv_path, store, headless=False):
    cv_abs = str(Path(cv_path).resolve())
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        for i, job in enumerate(jobs, 1):
            print(f"\n[{i}/{len(jobs)}] {job.get('company')} — {job.get('title')}")

            # Fresh context per job — prevents cross-contamination
            ctx = None
            page = None
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                ok, msg = await apply_one(page, job, profile, llm, cv_abs)
            except Exception as e:
                ok, msg = False, f"crash: {str(e)[:120]}"
            finally:
                try:
                    if ctx:
                        await ctx.close()
                except Exception:
                    pass

            status = "applied" if ok else "failed"
            try:
                store.update(job["url"], status=status, notes=msg)
            except Exception:
                pass
            results.append((job.get("company"), job.get("title"), ok, msg))
            print(f"   → {status}: {msg}")

            # If browser died, relaunch
            if not browser.is_connected():
                print("   [recovery] browser died — relaunching...")
                try:
                    browser = await p.chromium.launch(headless=headless)
                except Exception as e:
                    print(f"   [recovery] relaunch failed: {e}")
                    break

            await asyncio.sleep(4)

        try:
            await browser.close()
        except Exception:
            pass

    return results