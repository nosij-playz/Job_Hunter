"""LLM client, CV parser, storage, matcher, cover letter, cold email, brain."""
import json
import re
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

import requests
import pdfplumber
from tenacity import retry, stop_after_attempt, wait_exponential


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
def load_config(path="config.json"):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# LLM CLIENT
# ─────────────────────────────────────────────────────────────
class LLM:
    def __init__(self, model, host):
        self.model = model
        self.host = host.rstrip("/")

    def health(self):
        try:
            return requests.get(f"{self.host}/api/tags", timeout=5).status_code == 200
        except Exception:
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def _post(self, payload):
        r = requests.post(f"{self.host}/api/generate", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()["response"]

    def gen(self, prompt, system=None, json_mode=False, temperature=0.2):
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": 8192},
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        return self._post(payload)

    def gen_json(self, prompt, system=None):
        raw = self.gen(prompt, system=system, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
            raise


# ─────────────────────────────────────────────────────────────
# CV PARSER
# ─────────────────────────────────────────────────────────────
def pdf_to_text(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
    return text


def parse_cv(llm, cv_path, cache="data/profile.json"):
    cache_path = Path(cache)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    text = pdf_to_text(cv_path)
    system = "You are a CV parser. Return ONLY valid JSON. No markdown."
    prompt = f"""Parse this CV into JSON with exactly these keys:
{{
  "name": str, "email": str, "phone": str, "location": str,
  "summary": str, "skills": [str],
  "experience": [{{"company": str, "role": str, "duration": str, "bullets": [str]}}],
  "projects": [{{"name": str, "description": str, "tech": [str]}}],
  "education": [{{"institution": str, "degree": str, "year": str, "cgpa": str}}],
  "certifications": [str], "awards": [str],
  "links": {{"github": str, "linkedin": str, "portfolio": str}}
}}
If a field is missing, use empty string or empty list. Do NOT invent data.

CV:
\"\"\"
{text}
\"\"\"
"""
    profile = llm.gen_json(prompt, system=system)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile


def profile_summary(profile):
    skills = ", ".join(profile.get("skills", [])[:35])
    projects = "\n".join(
        f"- {p.get('name')}: {p.get('description','')} [Tech: {', '.join(p.get('tech', []))}]"
        for p in profile.get("projects", [])[:6]
    )
    return f"""Name: {profile.get('name')}
Location: {profile.get('location')}
Summary: {profile.get('summary')}
Skills: {skills}
Education: {profile.get('education')}
Certifications: {', '.join(profile.get('certifications', []))}
Awards: {', '.join(profile.get('awards', []))}
Key Projects:
{projects}
"""


# ─────────────────────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, company TEXT, title TEXT, location TEXT,
    remote INTEGER DEFAULT 0,
    salary_min INTEGER, salary_max INTEGER, currency TEXT,
    url TEXT UNIQUE, apply_url TEXT, posted_date TEXT,
    description TEXT,
    contact_email TEXT, contact_phone TEXT, contact_page TEXT,
    priority INTEGER DEFAULT 50,
    match_score INTEGER,
    matched_skills TEXT, missing_skills TEXT,
    reason TEXT, cover_letter TEXT, cold_email TEXT,
    status TEXT DEFAULT 'new',
    applied_date TEXT, notes TEXT, created_at TEXT
);
"""

WANTED_COLUMNS = {
    "source": "TEXT", "company": "TEXT", "title": "TEXT", "location": "TEXT",
    "remote": "INTEGER DEFAULT 0",
    "salary_min": "INTEGER", "salary_max": "INTEGER", "currency": "TEXT",
    "url": "TEXT", "apply_url": "TEXT", "posted_date": "TEXT", "description": "TEXT",
    "contact_email": "TEXT", "contact_phone": "TEXT", "contact_page": "TEXT",
    "priority": "INTEGER DEFAULT 50",
    "match_score": "INTEGER",
    "matched_skills": "TEXT", "missing_skills": "TEXT",
    "reason": "TEXT", "cover_letter": "TEXT", "cold_email": "TEXT",
    "status": "TEXT DEFAULT 'new'",
    "applied_date": "TEXT", "applied_at": "TEXT",
    "company_phone": "TEXT", "company_email": "TEXT",
    "notes": "TEXT", "created_at": "TEXT",
}


class Store:
    def __init__(self, path="data/jobs.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        cur = self.conn.execute("PRAGMA table_info(jobs)")
        existing = {r["name"] for r in cur.fetchall()}
        for col, typ in WANTED_COLUMNS.items():
            if col not in existing:
                try:
                    self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
                    print(f"[db] added: {col}")
                except Exception as e:
                    print(f"[db] skip {col}: {e}")

    def _db_cols(self):
        cur = self.conn.execute("PRAGMA table_info(jobs)")
        return {r["name"] for r in cur.fetchall()}

    @staticmethod
    def make_id(url):
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def exists(self, url):
        return self.conn.execute("SELECT 1 FROM jobs WHERE url=?", (url,)).fetchone() is not None

    def insert(self, job):
        job = dict(job)
        job.pop("_quick", None)  # transient field
        job["id"] = self.make_id(job["url"])
        job.setdefault("created_at", datetime.utcnow().isoformat())
        for k in ("matched_skills", "missing_skills"):
            if isinstance(job.get(k), list):
                job[k] = json.dumps(job[k])
        db_cols = self._db_cols()
        job = {k: v for k, v in job.items() if k in db_cols}
        cols = list(job.keys())
        ph = ",".join("?" * len(cols))
        sql = f"INSERT OR IGNORE INTO jobs ({','.join(cols)}) VALUES ({ph})"
        self.conn.execute(sql, [job[c] for c in cols])
        self.conn.commit()
        return job["id"]

    def update(self, url, **fields):
        if not fields:
            return
        for k in ("matched_skills", "missing_skills"):
            if k in fields and isinstance(fields[k], list):
                fields[k] = json.dumps(fields[k])
        db_cols = self._db_cols()
        fields = {k: v for k, v in fields.items() if k in db_cols}
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [url]
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE url=?", vals)
        self.conn.commit()

    def shortlist(self, min_score, limit):
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE status='shortlisted' AND match_score>=? "
            "ORDER BY match_score DESC LIMIT ?",
            (min_score, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def cold_candidates(self, min_score=70):
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE status IN ('new','cold') AND match_score>=? "
            "ORDER BY match_score DESC",
            (min_score,),
        )
        return [dict(r) for r in cur.fetchall()]

    def applied(self):
        cur = self.conn.execute(
            "SELECT * FROM jobs WHERE status='applied' ORDER BY applied_date DESC"
        )
        return [dict(r) for r in cur.fetchall()]

    def all(self):
        cur = self.conn.execute("SELECT * FROM jobs ORDER BY match_score DESC")
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# QUICK KEYWORD PRE-SCORE (no LLM)
# ─────────────────────────────────────────────────────────────
CORE_SKILLS = [
    "python", "java", "javascript", "sql", "c++",
    "machine learning", "deep learning", "neural network", "cnn", "gan",
    "tensorflow", "pytorch", "scikit-learn", "sklearn", "xgboost",
    "nlp", "natural language", "llm", "large language", "rag",
    "retrieval augmented", "generative ai", "genai", "agentic",
    "computer vision", "opencv", "object detection", "face recognition",
    "model", "training", "inference", "data science", "data analysis",
    "pandas", "numpy", "matplotlib",
    "flask", "django", "react", "node.js", "nodejs", "rest api", "restful",
    "api", "backend", "frontend", "full stack", "fullstack",
    "mysql", "postgresql", "sqlite", "firebase", "database",
    "docker", "git", "linux", "kubernetes",
    "sap", "abap",
    "software", "developer", "engineer", "programming", "coding",
    "algorithms", "data structures", "oop",
]


def quick_fit_score(profile, job):
    jd = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    if not jd.strip():
        return 0
    hits = sum(1 for skill in CORE_SKILLS if skill in jd)
    return min(100, int(hits * 10))


# ─────────────────────────────────────────────────────────────
# LLM BRAIN — batch title screen
# ─────────────────────────────────────────────────────────────
BRAIN_SCREEN_SYSTEM = (
    "You are a recruiter screening job titles for a fresher candidate. "
    "Return ONLY valid JSON. No commentary."
)


def brain_screen_titles(llm, titles, batch_size=30):
    keep = set()
    for i in range(0, len(titles), batch_size):
        batch = titles[i:i + batch_size]
        indexed = [{"idx": j, "title": t} for j, t in enumerate(batch)]
        prompt = f"""Given these job titles, return ONLY the indexes of roles a
fresher software engineer (0-1 yrs exp, AI/ML + Python + full-stack) could
realistically apply to.

Reject: sales, marketing, support, design, HR, recruiting, finance,
consulting, product management, senior/staff/principal/lead roles.

Accept: any engineering, developer, AI/ML, data, QA, devops, or explicit
fresher/junior/graduate/intern role.

Titles:
{json.dumps(indexed, indent=2)}

Return JSON: {{"keep": [<int>...]}}
"""
        try:
            res = llm.gen_json(prompt, system=BRAIN_SCREEN_SYSTEM)
            for local_idx in res.get("keep", []):
                if isinstance(local_idx, int) and 0 <= local_idx < len(batch):
                    keep.add(i + local_idx)
        except Exception as e:
            print(f"   [brain] batch {i//batch_size} failed: {e} — keeping batch")
            for j in range(len(batch)):
                keep.add(i + j)
    return keep


# ─────────────────────────────────────────────────────────────
# MATCHER — Jison-specific fresher rubric
# ─────────────────────────────────────────────────────────────
MATCH_SYSTEM = (
    "You are a technical recruiter scoring freshers for AI/ML and software roles. "
    "Be honest but calibrated. Return ONLY valid JSON."
)


def match_job(llm, profile, job):
    prompt = f"""Score this fresher candidate against the job.

Return JSON:
{{
  "score": <int 0-100>,
  "matched_skills": [<str>],
  "missing_skills": [<str>],
  "reason": "<one sentence>",
  "should_apply": <true|false>
}}

═══════════════════════════════════════════════════════════
CANDIDATE PROFILE
═══════════════════════════════════════════════════════════
- B.Tech CSE 2026, CGPA 8.25, GATE 2026 qualified
- 4 internships (ML, Full-Stack, Data Science) — remote
- Award-winning project: NeuroWave (SRISHTI 2026 best project)
- AI/ML: Scikit-learn, TensorFlow, PyTorch, XGBoost, LLMs, RAG, GANs, CNN, NLP
- CV: OpenCV, object detection, face recognition
- Agentic AI: SustainAI (multi-agent LLM + CV + REST)
- Full-Stack: Python, Flask, Django, React, Node.js, Firebase, MySQL, PostgreSQL
- SAP: ABAP + SAP Technology Consultant certified
- DevOps: Git, Docker, Linux
- 8 substantial projects

{profile_summary(profile)}

═══════════════════════════════════════════════════════════
SCORING RUBRIC — be strict but generous where it counts
═══════════════════════════════════════════════════════════

90-100 = Role explicitly says "fresher"/"new grad"/"0-2 yrs" AND requires ≥70%
         of Jison's core stack (Python + AI/ML OR full-stack OR SAP-adjacent).

80-89  = Junior/Associate/Engineer-I role. Requires Python + (AI/ML OR
         full-stack). Jison's projects clearly align. AUTO-APPLY.

70-79  = Standard "Software Engineer" / "AI Engineer" / "Data Scientist" with
         no seniority filter, AND JD mentions ≥3 of Jison's skills. Show but
         do NOT auto-apply — flag for cold outreach.

60-69  = Partial match. 2-3 yrs experience asked OR adjacent stack.

<60    = 3+ yrs, senior title, non-engineering, or wrong stack.

HARD DISQUALIFIERS → score ≤ 25, should_apply=false:
- "5+ years" / "8+ years" / "10+ years"
- Senior / Staff / Principal / Lead / Architect / Manager / Director
- Non-engineering titles
- Rust/Swift/Kotlin/Scala/COBOL/embedded-only shops

═══════════════════════════════════════════════════════════
JOB
═══════════════════════════════════════════════════════════
Company: {job.get('company')}
Title: {job.get('title')}
Location: {job.get('location')}
Description:
{(job.get('description') or '')[:3500]}
"""
    return llm.gen_json(prompt, system=MATCH_SYSTEM)


# ─────────────────────────────────────────────────────────────
# COVER LETTER + COLD EMAIL
# ─────────────────────────────────────────────────────────────
def make_cover_letter(llm, profile, job):
    prompt = f"""Write a concise cover letter (150-180 words) for this application.
Tone: confident, specific, no clichés. Reference 2 relevant projects.
Do NOT invent experience. End with a clear line of interest.

CANDIDATE:
{profile_summary(profile)}

JOB:
{job.get('title')} at {job.get('company')}
{(job.get('description') or '')[:1500]}

Return ONLY the letter body."""
    return llm.gen(prompt, temperature=0.4).strip()


def make_cold_email(llm, profile, job):
    prompt = f"""Write a short cold outreach email (80-100 words).
Return JSON: {{"subject": "...", "body": "..."}}
Tone: warm, direct, specific. Reference ONE project that matches the JD.
End with a 15-minute chat ask. No fluff.

CANDIDATE: {profile.get('name')}, {profile.get('summary')}
Best project: {profile.get('projects', [{}])[0].get('name')}

JOB: {job.get('title')} at {job.get('company')}
JD: {(job.get('description') or '')[:700]}
"""
    return llm.gen_json(prompt)


# ─────────────────────────────────────────────────────────────
# CONTACT EXTRACTION
# ─────────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_IN_MOBILE = re.compile(r"(?:(?:\+|00)91[\s\-]?)?[6-9]\d{9}")
PHONE_INTL = re.compile(r"\+\d{1,3}[\s\-]?\d{6,12}")
PHONE_LANDLINE = re.compile(r"(?:(?:\+|00)91[\s\-]?)?\d{2,4}[\s\-]?\d{6,8}")


def extract_contacts(text):
    """Return the best public email and phone found in arbitrary text."""
    if not text:
        return None, None
    emails = EMAIL_RE.findall(text)

    def rank(email):
        value = email.lower()
        if any(k in value for k in ("hr@", "careers@", "talent@", "jobs@", "recruit", "hiring@")):
            return 0
        if "noreply" in value or "no-reply" in value:
            return 2
        return 1

    best_email = sorted(emails, key=rank)[0] if emails else None
    candidates = PHONE_IN_MOBILE.findall(text) + PHONE_INTL.findall(text) + PHONE_LANDLINE.findall(text)
    seen = set()
    best_phone = None
    for phone in candidates:
        digits = re.sub(r"\D", "", phone)
        if 10 <= len(digits) <= 13 and digits not in seen:
            seen.add(digits)
            best_phone = phone.strip()
            break
    return best_email, best_phone