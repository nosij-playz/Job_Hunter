"""Excel-first job hunter: deep scan, no auto-apply, sorted output."""
import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

# Ensure both cwd and package parent are on path
PKG_DIR = Path(__file__).parent
ROOT_DIR = PKG_DIR.parent
os.chdir(PKG_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jobseeker.core import (
    load_config, LLM, Store, parse_cv, match_job,
    make_cold_email, extract_contacts,
    brain_screen_titles, quick_fit_score,
)
from jobseeker.scrapper.scrapers import discover
from jobseeker.exporter import export_all


def apply_runtime_tuning(cfg, fast=False):
    llm_cfg = cfg.setdefault("llm", {})
    thresholds = cfg.setdefault("thresholds", {})

    if fast:
        llm_cfg["brain_enabled"] = False
        llm_cfg["batch_screen_size"] = thresholds.get("batch_screen_size_fast", llm_cfg.get("batch_screen_size", 16))
        thresholds["quick_fit_min"] = thresholds.get("quick_fit_min_fast", thresholds.get("quick_fit_min", 35))
        thresholds["max_jobs_to_score"] = thresholds.get("max_jobs_to_score_fast", thresholds.get("max_jobs_to_score", 260))
        thresholds["min_description_length"] = thresholds.get("min_description_length_fast", thresholds.get("min_description_length", 120))
        print("[runtime] Fast mode enabled: smaller scoring pool, no brain screen")
    else:
        llm_cfg["batch_screen_size"] = llm_cfg.get("batch_screen_size", 24)
        thresholds["quick_fit_min"] = thresholds.get("quick_fit_min", 35)
        thresholds["max_jobs_to_score"] = thresholds.get("max_jobs_to_score", 260)
        thresholds["min_description_length"] = thresholds.get("min_description_length", 120)

    return cfg


async def main(fast=False, refresh_discovery=False):
    cfg = load_config()
    cfg = apply_runtime_tuning(cfg, fast=fast)
    print("=" * 60)
    print("JOB HUNTER — Deep Scan → Sorted Excel")
    if fast:
        print("MODE: FAST")
    else:
        print("MODE: FULL")
    print("=" * 60)

    llm = LLM(cfg["llm"]["model"], cfg["llm"]["host"])
    if not llm.health():
        print("[FATAL] Ollama not reachable.")
        return

    store = Store(cfg["output"]["db_path"])

    # ── 1. CV ──
    print("\n[1/5] Parsing CV...")
    profile = parse_cv(llm, cfg["candidate"]["cv_path"], cfg["candidate"]["profile_cache"])
    print(f"      {profile.get('name')} | {len(profile.get('skills', []))} skills")

    # ── 1.5. Dynamic company discovery ──
    try:
        from jobseeker.scrapper.scrapers_discovery import discover_and_cache, merge_into_config
        print("\n[1.5] Dynamic company discovery...")
        discover_and_cache(force=refresh_discovery)
        cfg = merge_into_config(cfg)
        greenhouse_count = len(cfg["sources"].get("greenhouse", {}).get("companies", []))
        lever_count = len(cfg["sources"].get("lever", {}).get("companies", []))
        print(f"      Config now has {greenhouse_count} greenhouse + {lever_count} lever slugs")
    except Exception as e:
        print(f"      [discovery] skipped: {str(e)[:120]}")

    # ── 2. Deep discovery ──
    print("\n[2/5] Deep scan across all sources...")
    print("      (this includes LinkedIn guest API + startup boards + dynamic discovery — may take 2-4 min)")
    jobs = discover(cfg)
    print(f"      Sources done. Raw jobs: {len(jobs)}")

    # Naukri (South India focused) — must run in async context
    try:
        from jobseeker.scrapper.scrapers_india import naukri_async
        print("      → Naukri (South India focused)...")
        naukri_jobs = await naukri_async(cfg)
        print(f"      Naukri added: {len(naukri_jobs)}")
        jobs = naukri_jobs + jobs
    except Exception as e:
        print(f"      [naukri] skipped: {str(e)[:120]}")

    print(f"      After config filters: {len(jobs)}")

    before = len(jobs)
    fresh = [j for j in jobs if not store.exists(j["url"])]
    dupes = before - len(fresh)
    print(f"      New (unscored): {len(fresh)}  |  Duplicates skipped: {dupes}")

    if not fresh:
        print("\n[!] No new jobs. Regenerating Excel from existing DB...")
        export_all(
            store,
            cfg["output"]["applied_xlsx"],
            cfg["output"]["cold_xlsx"],
            cfg["thresholds"]["min_score_to_cold"],
            cfg.get("export_top_n", 1500),
        )
        return

    # ── 3. Brain screen ──
    if cfg["llm"].get("brain_enabled"):
        print(f"\n[3/5] Brain screening {len(fresh)} titles...")
        titles = [j.get("title") or "" for j in fresh]
        keep_idx = brain_screen_titles(llm, titles, cfg["llm"].get("batch_screen_size", 40))
        fresh = [j for i, j in enumerate(fresh) if i in keep_idx]
        print(f"      Brain kept: {len(fresh)}")

    # ── 4. Quick filter + score cap ──
    qmin = cfg["thresholds"].get("quick_fit_min", 30)
    min_desc = cfg["thresholds"].get("min_description_length", 100)
    cap = cfg["thresholds"].get("max_jobs_to_score", 800)

    before = len(fresh)
    fresh = [j for j in fresh if len(j.get("description") or "") >= min_desc]
    print(f"\n[4/5] Description filter: {before} → {len(fresh)}")

    kept = []
    for j in fresh:
        qs = quick_fit_score(profile, j)
        j["_quick"] = qs
        if qs >= qmin:
            kept.append(j)
    fresh = kept
    print(f"      Quick-fit (≥{qmin}): {len(fresh)}")

    if len(fresh) > cap:
        fresh = fresh[:cap]
        print(f"      Capped to top {cap} by priority")

    # ── 5. LLM scoring ──
    print(f"\n[5/5] Scoring {len(fresh)} jobs (this is the slow part)...")
    min_short = cfg["thresholds"]["min_score_to_shortlist"]

    for i, job in enumerate(fresh, 1):
        try:
            result = match_job(llm, profile, job)
        except Exception as e:
            print(f"      [{i}/{len(fresh)}] ERROR: {str(e)[:80]}")
            continue

        job["match_score"] = result.get("score", 0)
        job["matched_skills"] = result.get("matched_skills", [])
        job["missing_skills"] = result.get("missing_skills", [])
        job["reason"] = result.get("reason", "")

        email, phone = extract_contacts(job.get("description", ""))
        if email:
            job["contact_email"] = email
        if phone:
            job["contact_phone"] = phone

        should = result.get("should_apply") and job["match_score"] >= min_short
        job["status"] = "shortlisted" if should else "cold"
        store.insert(job)

        tag = "★" if job["match_score"] >= 80 else " "
        print(f"      [{i}/{len(fresh)}] {tag} {job['match_score']:>3} | "
              f"{job.get('company','?')[:20]:<20} | {job.get('title','?')[:42]}")

    cold = store.cold_candidates(min_score=cfg["thresholds"]["min_score_to_cold"])
    print(f"\nGenerating cold emails for {len(cold)} candidates (score ≥ "
          f"{cfg['thresholds']['min_score_to_cold']})...")
    for j in cold[:30]:
        if j.get("cold_email"):
            continue
        try:
            obj = make_cold_email(llm, profile, j)
            store.update(j["url"], cold_email=json.dumps(obj))
        except Exception:
            pass

    print("\n>>> EXPORTING EXCEL <<<")
    out = export_all(
        store,
        cfg["output"]["applied_xlsx"],
        cfg["thresholds"]["min_score_to_cold"],
        cfg.get("export_top_n", 1500),
    )

    print("\n" + "=" * 60)
    print(f"DONE → {out}")
    print("=" * 60)
    print("\nOpen the Excel file — sheet 'Apply First (80+)' is your top priority.")
    print("Click the 'Apply Now' hyperlink in each row → apply in 30 seconds.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job hunter pipeline")
    parser.add_argument("--fast", action="store_true", help="skip full brain screen and reduce scoring volume")
    parser.add_argument("--refresh-discovery", action="store_true", help="force a fresh ATS/company discovery cache")
    args = parser.parse_args()
    asyncio.run(main(fast=args.fast, refresh_discovery=args.refresh_discovery))