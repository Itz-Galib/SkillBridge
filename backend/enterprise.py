"""Initial hiring workflow APIs for SkillBridge PRD."""
import json, time, uuid, os, re, secrets, csv, io
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from main import current_user, one, allr, run, audit, QBYID

router = APIRouter(tags=["hiring"])

def role_of(user):
    row = one("SELECT role FROM user_roles WHERE user_id=?", (user["id"],))
    return row["role"] if row else "candidate"

def require_role(user, *roles):
    if role_of(user) not in roles:
        raise HTTPException(403, "This action is not allowed for your account role")

def company_for(user):
    company = one("SELECT * FROM companies WHERE owner_user_id=?", (user["id"],))
    if company:
        return dict(company)
    # Membership lookup is tenant-scoped; company role alone never grants access.
    member = one("""SELECT c.* FROM companies c JOIN company_members m ON m.company_id=c.id
                    WHERE m.user_id=? AND m.status='active' LIMIT 1""", (user["id"],))
    if not member:
        raise HTTPException(409, "Your account is not connected to an organisation yet")
    return dict(member)

class CompanyProfile(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    website: Optional[str] = Field(default=None, max_length=240)
class JobCreate(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    description: str = Field(min_length=20, max_length=8000)
    location: str = Field(default="Remote", max_length=120)
    employment_type: str = Field(default="Full-time", max_length=40)
    skills: List[str] = Field(default_factory=list, max_length=30)
    publish: bool = False
class ShareResults(BaseModel):
    share: bool
class CompanyAssessmentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    requirements: str = Field(min_length=10, max_length=5000)
    questions: List[dict] = Field(default_factory=list, max_length=100)
    job_id: Optional[str] = None


class EducationItem(BaseModel):
    institution: str = Field(min_length=2, max_length=160)
    degree: str = Field(default="", max_length=160)
    field: str = Field(default="", max_length=160)
    start_year: str = Field(default="", max_length=10)
    end_year: str = Field(default="", max_length=10)
    details: str = Field(default="", max_length=1000)

class ExperienceItem(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    title: str = Field(min_length=2, max_length=160)
    employment_type: str = Field(default="", max_length=60)
    start_date: str = Field(default="", max_length=20)
    end_date: str = Field(default="", max_length=20)
    current: bool = False
    description: str = Field(default="", max_length=2000)

class ProjectItem(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)
    technologies: List[str] = Field(default_factory=list, max_length=30)
    url: str = Field(default="", max_length=300)

class CandidateProfileUpdate(BaseModel):
    headline: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=40)
    location: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=3000)
    linkedin: str = Field(default="", max_length=300)
    github: str = Field(default="", max_length=300)
    portfolio: str = Field(default="", max_length=300)
    education: List[EducationItem] = Field(default_factory=list, max_length=12)
    experience: List[ExperienceItem] = Field(default_factory=list, max_length=20)
    projects: List[ProjectItem] = Field(default_factory=list, max_length=20)
    preferences: dict = Field(default_factory=dict)

@router.get("/api/candidate/profile")
def get_candidate_profile(user=Depends(current_user)):
    require_role(user, "candidate")
    row = one("SELECT * FROM candidate_profiles WHERE user_id=?", (user["id"],))
    cv = one("SELECT extracted,confirmed,source,redaction FROM profiles WHERE user_id=?", (user["id"],))
    result = {
        "user": {"name": user["name"], "email": user["email"]},
        "profile": {
            "headline": "", "phone": "", "location": "", "summary": "",
            "linkedin": "", "github": "", "portfolio": "",
            "education": [], "experience": [], "projects": [],
            "preferences": {"job_types": ["Internship", "Full-time"], "work_mode": "Any", "locations": []},
            "updated": None,
        },
        "cv": {"uploaded": bool(cv and cv["extracted"]), "source": cv["source"] if cv else None,
               "skills": json.loads(cv["confirmed"]) if cv and cv["confirmed"] else [],
               "extracted_skills": json.loads(cv["extracted"]) if cv and cv["extracted"] else [],
               "redaction": json.loads(cv["redaction"]) if cv and cv["redaction"] else {}},
    }
    if row:
        p = dict(row)
        for key in ("education", "experience", "projects", "preferences"):
            p[key] = json.loads(p[key] or ("{}" if key == "preferences" else "[]"))
        result["profile"].update({k: p[k] for k in ("headline","phone","location","summary","linkedin","github","portfolio","education","experience","projects","preferences","updated")})
    return result

@router.put("/api/candidate/profile")
def update_candidate_profile(body: CandidateProfileUpdate, user=Depends(current_user)):
    require_role(user, "candidate")
    preferences = body.preferences if isinstance(body.preferences, dict) else {}
    allowed_types = {"Internship", "Full-time", "Part-time", "Contract", "Temporary"}
    allowed_modes = {"Any", "Remote", "Hybrid", "On-site"}
    preferences["job_types"] = [x for x in preferences.get("job_types", []) if x in allowed_types][:5]
    preferences["work_mode"] = preferences.get("work_mode", "Any") if preferences.get("work_mode", "Any") in allowed_modes else "Any"
    preferences["locations"] = [str(x)[:100] for x in preferences.get("locations", [])[:10] if str(x).strip()]
    run("""INSERT INTO candidate_profiles(user_id,headline,phone,location,summary,linkedin,github,portfolio,education,experience,projects,preferences,updated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET headline=excluded.headline,phone=excluded.phone,location=excluded.location,
           summary=excluded.summary,linkedin=excluded.linkedin,github=excluded.github,portfolio=excluded.portfolio,
           education=excluded.education,experience=excluded.experience,projects=excluded.projects,
           preferences=excluded.preferences,updated=excluded.updated""",
        (user["id"],body.headline.strip(),body.phone.strip(),body.location.strip(),body.summary.strip(),
         body.linkedin.strip(),body.github.strip(),body.portfolio.strip(),
         json.dumps([x.model_dump() for x in body.education]),json.dumps([x.model_dump() for x in body.experience]),
         json.dumps([x.model_dump() for x in body.projects]),json.dumps(preferences),time.time()))
    audit(user["id"], "candidate_profile_updated")
    return {"ok": True, "updated": time.time()}

@router.get("/api/enterprise/me")
def enterprise_me(user=Depends(current_user)):
    return {"user":{"id":user["id"],"name":user["name"],"email":user["email"]},"role":role_of(user)}

@router.get("/api/jobs")
def public_jobs():
    rows=allr("SELECT j.id,j.title,j.description,j.location,j.employment_type,j.skills,j.status,j.is_mock,j.created,c.name AS company_name FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status='published' ORDER BY j.created DESC")
    return {"items":[{**dict(r),"skills":json.loads(r["skills"]),"is_mock":bool(r["is_mock"])} for r in rows]}

@router.post("/api/company/profile")
def create_company_profile(body:CompanyProfile,user=Depends(current_user)):
    require_role(user,"company")
    current=one("SELECT id FROM companies WHERE owner_user_id=?",(user["id"],))
    if current:
        run("UPDATE companies SET name=?,website=? WHERE owner_user_id=?",(body.name.strip(),body.website,user["id"]))
    else:
        membership=one("SELECT id FROM company_members WHERE user_id=? AND status='active' LIMIT 1",(user["id"],))
        if membership:
            raise HTTPException(403,"Only the organisation owner can create or edit company identity details")
        run("INSERT INTO companies(id,owner_user_id,name,website,verified,created) VALUES (?,?,?,?,?,?)",(uuid.uuid4().hex,user["id"],body.name.strip(),body.website,0,time.time()))
    audit(user["id"],"company_profile_saved",body.name.strip())
    return {"ok":True,"company":dict(one("SELECT id,name,website,verified FROM companies WHERE owner_user_id=?",(user["id"],)))}

@router.post("/api/jobs")
def create_job(body:JobCreate,user=Depends(current_user)):
    require_role(user,"company"); company=company_for(user); require_company_permission(user, company, "owner", "recruiter"); jid=uuid.uuid4().hex
    if body.publish and not company["verified"]:
        raise HTTPException(403, "Company verification is required before publishing jobs")
    skills=list(dict.fromkeys(s.strip() for s in body.skills if s.strip()))
    run("INSERT INTO jobs(id,company_id,title,description,location,employment_type,skills,status,is_mock,created) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (jid,company["id"],body.title.strip(),body.description.strip(),body.location.strip(),body.employment_type.strip(),json.dumps(skills),"published" if body.publish else "draft",0,time.time()))
    audit(user["id"],"job_created",jid)
    return {"id":jid,"status":"published" if body.publish else "draft"}

@router.get("/api/company/jobs")
def company_jobs(user=Depends(current_user)):
    require_role(user,"company"); company=company_for(user)
    rows=allr("SELECT * FROM jobs WHERE company_id=? ORDER BY created DESC",(company["id"],))
    items=[]
    for row in rows:
        item=dict(row); item["skills"]=json.loads(item["skills"] or "[]"); item["is_mock"]=bool(item["is_mock"])
        detail=one("SELECT details_json,revision,updated FROM job_details WHERE job_id=?",(item["id"],))
        item["details"]=json.loads(detail["details_json"]) if detail else {}
        item["revision"]=detail["revision"] if detail else 1
        items.append(item)
    return {"items":items}

@router.post("/api/jobs/{job_id}/apply")
def apply_to_job(job_id:str,user=Depends(current_user)):
    require_role(user,"candidate")
    if not one("SELECT id FROM jobs WHERE id=? AND status='published'",(job_id,)): raise HTTPException(404,"Published job not found")
    existing=one("SELECT id FROM applications WHERE job_id=? AND candidate_id=?",(job_id,user["id"]))
    if existing: return {"application_id":existing["id"],"status":"already_applied"}
    aid=uuid.uuid4().hex
    run("INSERT INTO applications(id,job_id,candidate_id,status,results_shared,created) VALUES (?,?,?,?,?,?)",(aid,job_id,user["id"],"submitted",0,time.time()))
    audit(user["id"],"job_applied",job_id)
    return {"application_id":aid,"status":"submitted"}

@router.get("/api/applications/mine")
def my_applications(user=Depends(current_user)):
    require_role(user,"candidate")
    rows=allr("SELECT a.id AS application_id,a.status,a.results_shared,a.created,j.id AS job_id,j.title,j.location,j.employment_type,c.name AS company_name FROM applications a JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id WHERE a.candidate_id=? ORDER BY a.created DESC",(user["id"],))
    return {"items":[{**dict(r),"results_shared":bool(r["results_shared"])} for r in rows]}

@router.put("/api/applications/{application_id}/share-results")
def share_results(application_id:str,body:ShareResults,user=Depends(current_user)):
    require_role(user,"candidate")
    if not one("SELECT id FROM applications WHERE id=? AND candidate_id=?",(application_id,user["id"])): raise HTTPException(404,"Application not found")
    run("UPDATE applications SET results_shared=? WHERE id=? AND candidate_id=?",(1 if body.share else 0,application_id,user["id"]))
    audit(user["id"],"application_results_sharing_changed",application_id)
    return {"application_id":application_id,"results_shared":body.share}

@router.get("/api/company/applications")
def company_applications(user=Depends(current_user)):
    require_role(user,"company"); company=company_for(user); require_company_permission(user, company, "owner", "recruiter", "hiring_manager")
    if not company["verified"]:
        raise HTTPException(403, "Company verification is required before accessing applicant records")
    rows=allr("SELECT a.id AS application_id,a.status,a.results_shared,a.created,j.id AS job_id,j.title AS job_title,u.id AS candidate_id,u.name AS candidate_name FROM applications a JOIN jobs j ON j.id=a.job_id JOIN users u ON u.id=a.candidate_id WHERE j.company_id=? ORDER BY a.created DESC",(company["id"],))
    items=[]
    for row in rows:
        item=dict(row); item["results_shared"]=bool(item["results_shared"]); item["latest_assessment"]=None
        if item["results_shared"]:
            att=one("SELECT result,finished FROM attempts WHERE user_id=? AND status='done' ORDER BY finished DESC LIMIT 1",(item["candidate_id"],))
            if att and att["result"]:
                result=json.loads(att["result"]); item["latest_assessment"]={"overall":result.get("overall"),"level":result.get("level"),"trust":result.get("trust"),"finished":att["finished"]}
        items.append(item)
    return {"items":items}

@router.post("/api/company/assessments")
def request_company_assessment(body:CompanyAssessmentCreate,user=Depends(current_user)):
    require_role(user,"company"); company=company_for(user); require_company_permission(user, company, "owner", "recruiter")
    if not company["verified"]:
        raise HTTPException(403, "Company verification is required before requesting assessments")
    if body.job_id and not one("SELECT id FROM jobs WHERE id=? AND company_id=?",(body.job_id,company["id"])): raise HTTPException(404,"Job not found for this company")
    aid=uuid.uuid4().hex
    run("INSERT INTO company_assessments(id,company_id,job_id,title,requirements,questions,status,created) VALUES (?,?,?,?,?,?,?,?)",(aid,company["id"],body.job_id,body.title.strip(),body.requirements.strip(),json.dumps(body.questions),"requested",time.time()))
    audit(user["id"],"company_assessment_requested",aid)
    return {"id":aid,"status":"requested"}

@router.get("/api/admin/overview")
def admin_overview(user=Depends(current_user)):
    require_role(user,"admin")
    counts={"users":one("SELECT COUNT(*) AS n FROM users")["n"],"candidates":one("SELECT COUNT(*) AS n FROM user_roles WHERE role='candidate'")["n"],"companies":one("SELECT COUNT(*) AS n FROM companies")["n"],"jobs":one("SELECT COUNT(*) AS n FROM jobs")["n"],"applications":one("SELECT COUNT(*) AS n FROM applications")["n"],"attempts":one("SELECT COUNT(*) AS n FROM attempts")["n"],"assessment_requests":one("SELECT COUNT(*) AS n FROM company_assessments WHERE status='requested'")["n"]}
    return {"counts":counts,"demo_data":True,"note":"Counts may include explicitly labelled synthetic demo records."}

@router.get("/api/admin/users")
def admin_users(user=Depends(current_user)):
    require_role(user,"admin")
    rows=allr("SELECT u.id,u.name,u.email,COALESCE(r.role,'candidate') AS role,u.created FROM users u LEFT JOIN user_roles r ON r.user_id=u.id ORDER BY u.created DESC LIMIT 500")
    return {"items":[dict(r) for r in rows]}

@router.get("/api/admin/company-assessments")
def admin_company_assessments(user=Depends(current_user)):
    require_role(user,"admin")
    rows=allr("SELECT a.id,a.title,a.requirements,a.questions,a.status,a.created,c.name AS company_name FROM company_assessments a JOIN companies c ON c.id=a.company_id ORDER BY a.created DESC")
    return {"items":[{**dict(r),"questions":json.loads(r["questions"])} for r in rows]}


# ---------------------------------------------------------------------------
# Company / recruiter workspace — PRD v2.0 implementation slice.
# These tables are tenant-scoped and intentionally keep candidate evidence
# separate from recruiter notes and assessment integrity data.
def ensure_company_workspace_schema():
    statements = [
        """CREATE TABLE IF NOT EXISTS company_verification (
            company_id TEXT PRIMARY KEY, legal_name TEXT NOT NULL, country TEXT NOT NULL,
            business_id TEXT NOT NULL DEFAULT '', evidence_note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending_review', decision_reason TEXT NOT NULL DEFAULT '',
            submitted_at REAL NOT NULL, reviewed_at REAL, reviewed_by INTEGER)""",
        """CREATE TABLE IF NOT EXISTS job_details (
            job_id TEXT PRIMARY KEY, details_json TEXT NOT NULL DEFAULT '{}',
            revision INTEGER NOT NULL DEFAULT 1, updated REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS job_revisions (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, revision INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL, actor_id INTEGER NOT NULL, created REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS application_notes (
            id TEXT PRIMARY KEY, application_id TEXT NOT NULL, company_id TEXT NOT NULL,
            author_id INTEGER NOT NULL, body TEXT NOT NULL, created REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS application_history (
            id TEXT PRIMARY KEY, application_id TEXT NOT NULL, company_id TEXT NOT NULL,
            actor_id INTEGER NOT NULL, from_stage TEXT NOT NULL, to_stage TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS company_members (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL, user_id INTEGER,
            email TEXT NOT NULL, company_role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'invited',
            invite_token TEXT NOT NULL, created REAL NOT NULL, UNIQUE(company_id,email))""",
        """CREATE TABLE IF NOT EXISTS phase_two_invites (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL, job_id TEXT NOT NULL,
            application_id TEXT NOT NULL, title TEXT NOT NULL, requirements TEXT NOT NULL,
            config_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending_platform_review', deadline TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, updated REAL NOT NULL)"""
    ]
    for statement in statements:
        run(statement)

ensure_company_workspace_schema()


def company_for_access(user):
    require_role(user, "company")
    company = one("SELECT * FROM companies WHERE owner_user_id=?", (user["id"],))
    if company:
        return dict(company)
    member = one("""SELECT c.* FROM companies c JOIN company_members m ON m.company_id=c.id
                    WHERE m.user_id=? AND m.status='active' LIMIT 1""", (user["id"],))
    if not member:
        raise HTTPException(409, "Your account is not connected to an organisation yet")
    return dict(member)


def company_role_for(user, company):
    if company["owner_user_id"] == user["id"]:
        return "owner"
    member = one("SELECT company_role FROM company_members WHERE company_id=? AND user_id=? AND status='active'",
                 (company["id"], user["id"]))
    return member["company_role"] if member else None


def require_company_permission(user, company, *roles):
    role = company_role_for(user, company)
    if role not in roles:
        raise HTTPException(403, "Your organisation role does not allow this action")
    return role


def company_job(company_id, job_id):
    row = one("SELECT * FROM jobs WHERE id=? AND company_id=?", (job_id, company_id))
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)


class CompanyVerificationInput(BaseModel):
    legal_name: str = Field(min_length=2, max_length=180)
    country: str = Field(min_length=2, max_length=80)
    business_id: str = Field(default="", max_length=100)
    evidence_note: str = Field(min_length=10, max_length=2000)


class CompanyJobInput(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    description: str = Field(min_length=20, max_length=8000)
    location: str = Field(default="Remote", max_length=120)
    employment_type: str = Field(default="Full-time", max_length=40)
    work_mode: str = Field(default="Remote", max_length=30)
    experience_level: str = Field(default="Any", max_length=40)
    salary_min: Optional[int] = Field(default=None, ge=0)
    salary_max: Optional[int] = Field(default=None, ge=0)
    salary_currency: str = Field(default="BDT", max_length=8)
    deadline: str = Field(default="", max_length=30)
    required_skills: List[str] = Field(default_factory=list, max_length=30)
    preferred_skills: List[str] = Field(default_factory=list, max_length=30)
    eligibility: str = Field(default="", max_length=2000)
    hard_requirements: List[str] = Field(default_factory=list, max_length=20)
    match_weights: dict = Field(default_factory=dict)


class CompanyJobStatus(BaseModel):
    status: str
    reason: str = Field(default="", max_length=1000)


class ApplicationStageUpdate(BaseModel):
    stage: str
    reason: str = Field(default="", max_length=1000)


class RecruiterNote(BaseModel):
    body: str = Field(min_length=2, max_length=3000)


class TeamInviteInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    role: str


class TeamRoleUpdate(BaseModel):
    role: str


class PhaseTwoInviteInput(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    requirements: str = Field(min_length=10, max_length=5000)
    syllabus: List[str] = Field(default_factory=list, max_length=40)
    question_types: List[str] = Field(default_factory=list, max_length=8)
    duration_minutes: int = Field(default=60, ge=10, le=360)
    difficulty: dict = Field(default_factory=lambda: {"easy": 30, "medium": 50, "hard": 20})
    rubric: List[dict] = Field(default_factory=list, max_length=30)
    deadline: str = Field(default="", max_length=30)
    allowed_resources: List[str] = Field(default_factory=list, max_length=20)
    attempt_count: int = Field(default=1, ge=1, le=3)
    integrity_mode: str = Field(default="light")
    accommodations: str = Field(default="", max_length=1000)
    credit_cost: int = Field(default=0, ge=0, le=10000)


@router.get("/api/company/workspace")
def company_workspace(user=Depends(current_user)):
    company = company_for_access(user)
    role = company_role_for(user, company)
    jobs = allr("SELECT status,COUNT(*) AS n FROM jobs WHERE company_id=? GROUP BY status", (company["id"],))
    apps = allr("""SELECT a.status,COUNT(*) AS n FROM applications a JOIN jobs j ON j.id=a.job_id
                   WHERE j.company_id=? GROUP BY a.status""", (company["id"],))
    verification = one("SELECT status,decision_reason,submitted_at,reviewed_at FROM company_verification WHERE company_id=?",
                       (company["id"],))
    recent = allr("""SELECT a.id AS application_id,a.status,a.created,j.title AS job_title,u.name AS candidate_name
                     FROM applications a JOIN jobs j ON j.id=a.job_id JOIN users u ON u.id=a.candidate_id
                     WHERE j.company_id=? ORDER BY a.created DESC LIMIT 6""", (company["id"],)) if company["verified"] and role in ("owner", "recruiter", "hiring_manager") else []
    return {
        "company": {"id": company["id"], "name": company["name"], "website": company["website"] or "",
                    "verified": bool(company["verified"]), "role": role},
        "verification": dict(verification) if verification else {"status": "not_submitted", "decision_reason": ""},
        "jobs": {r["status"]: r["n"] for r in jobs},
        "applications": {r["status"]: r["n"] for r in apps},
        "recent_applications": [dict(r) for r in recent],
        "permissions": {"manage_company": role == "owner", "manage_team": role == "owner",
                        "manage_jobs": role in ("owner", "recruiter"), "manage_pipeline": role in ("owner", "recruiter", "hiring_manager"),
                        "view_reports": role in ("owner", "recruiter", "hiring_manager", "viewer")}
    }


@router.get("/api/company/verification")
def get_company_verification(user=Depends(current_user)):
    company = company_for_access(user)
    row = one("SELECT legal_name,country,business_id,evidence_note,status,decision_reason,submitted_at,reviewed_at FROM company_verification WHERE company_id=?",
              (company["id"],))
    return {"company": {"name": company["name"], "website": company["website"] or "", "verified": bool(company["verified"])},
            "verification": dict(row) if row else {"status": "not_submitted"}}


@router.post("/api/company/verification")
def submit_company_verification(body: CompanyVerificationInput, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner")
    now = time.time()
    run("""INSERT INTO company_verification(company_id,legal_name,country,business_id,evidence_note,status,decision_reason,submitted_at,reviewed_at,reviewed_by)
           VALUES (?,?,?,?,?,'pending_review','',?,NULL,NULL)
           ON CONFLICT(company_id) DO UPDATE SET legal_name=excluded.legal_name,country=excluded.country,
           business_id=excluded.business_id,evidence_note=excluded.evidence_note,status='pending_review',
           decision_reason='',submitted_at=excluded.submitted_at,reviewed_at=NULL,reviewed_by=NULL""",
        (company["id"], body.legal_name.strip(), body.country.strip(), body.business_id.strip(), body.evidence_note.strip(), now))
    run("UPDATE companies SET verified=0 WHERE id=?", (company["id"],))
    audit(user["id"], "company_verification_submitted", company["id"])
    return {"status": "pending_review", "message": "Verification request submitted for review."}


@router.post("/api/company/jobs")
def create_company_job(body: CompanyJobInput, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter")
    if body.salary_min is not None and body.salary_max is not None and body.salary_max < body.salary_min:
        raise HTTPException(422, "Salary maximum must be greater than salary minimum")
    jid, now = uuid.uuid4().hex, time.time()
    skills = list(dict.fromkeys([s.strip() for s in body.required_skills + body.preferred_skills if s.strip()]))
    details = body.model_dump()
    run("""INSERT INTO jobs(id,company_id,title,description,location,employment_type,skills,status,is_mock,created)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (jid, company["id"], body.title.strip(), body.description.strip(), body.location.strip(),
         body.employment_type.strip(), json.dumps(skills), "draft", 0, now))
    run("INSERT INTO job_details(job_id,details_json,revision,updated) VALUES (?,?,1,?)", (jid, json.dumps(details), now))
    snapshot = {"title": body.title, "description": body.description, **details, "status": "draft"}
    run("INSERT INTO job_revisions(id,job_id,revision,snapshot_json,actor_id,created) VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, jid, 1, json.dumps(snapshot), user["id"], now))
    audit(user["id"], "company_job_created", jid)
    return {"id": jid, "status": "draft", "revision": 1}


@router.patch("/api/company/jobs/{job_id}")
def update_company_job(job_id: str, body: CompanyJobInput, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter")
    job = company_job(company["id"], job_id)
    if job["status"] not in ("draft", "needs_changes", "paused"):
        raise HTTPException(409, "Only draft, needs-changes or paused jobs can be edited")
    if body.salary_min is not None and body.salary_max is not None and body.salary_max < body.salary_min:
        raise HTTPException(422, "Salary maximum must be greater than salary minimum")
    now = time.time()
    old = one("SELECT revision FROM job_details WHERE job_id=?", (job_id,))
    revision = (old["revision"] if old else 1) + 1
    skills = list(dict.fromkeys([s.strip() for s in body.required_skills + body.preferred_skills if s.strip()]))
    run("UPDATE jobs SET title=?,description=?,location=?,employment_type=?,skills=? WHERE id=? AND company_id=?",
        (body.title.strip(), body.description.strip(), body.location.strip(), body.employment_type.strip(), json.dumps(skills), job_id, company["id"]))
    run("""INSERT INTO job_details(job_id,details_json,revision,updated) VALUES (?,?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET details_json=excluded.details_json,revision=excluded.revision,updated=excluded.updated""",
        (job_id, json.dumps(body.model_dump()), revision, now))
    snapshot = {"title": body.title, "description": body.description, **body.model_dump(), "status": job["status"]}
    run("INSERT INTO job_revisions(id,job_id,revision,snapshot_json,actor_id,created) VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, job_id, revision, json.dumps(snapshot), user["id"], now))
    audit(user["id"], "company_job_updated", job_id)
    return {"id": job_id, "revision": revision, "status": job["status"]}


@router.post("/api/company/jobs/{job_id}/publish")
def publish_company_job(job_id: str, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter")
    job = company_job(company["id"], job_id)
    if not company["verified"]:
        raise HTTPException(403, "Company verification is required before a job can be published")
    if job["status"] not in ("draft", "needs_changes", "paused"):
        raise HTTPException(409, "This job cannot be submitted from its current state")
    direct = os.getenv("ALLOW_DIRECT_JOB_PUBLISH", "false").lower() == "true"
    status = "published" if direct else "pending_review"
    run("UPDATE jobs SET status=? WHERE id=? AND company_id=?", (status, job_id, company["id"]))
    audit(user["id"], "company_job_submitted", job_id)
    return {"id": job_id, "status": status, "direct_publish_policy": direct}


@router.post("/api/company/jobs/{job_id}/status")
def change_company_job_status(job_id: str, body: CompanyJobStatus, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter")
    job = company_job(company["id"], job_id)
    allowed = {"paused", "closed", "draft"}
    if body.status not in allowed:
        raise HTTPException(422, "Status must be paused, closed or draft")
    if body.status == "draft" and job["status"] not in ("paused", "needs_changes"):
        raise HTTPException(409, "Only paused or needs-changes jobs can return to draft")
    run("UPDATE jobs SET status=? WHERE id=? AND company_id=?", (body.status, job_id, company["id"]))
    audit(user["id"], "company_job_status_changed", job_id + ":" + body.status + ":" + body.reason)
    return {"id": job_id, "status": body.status}


@router.get("/api/company/analytics")
def company_analytics(user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter", "hiring_manager", "viewer")
    totals = {
        "jobs": one("SELECT COUNT(*) AS n FROM jobs WHERE company_id=?", (company["id"],))["n"],
        "published_jobs": one("SELECT COUNT(*) AS n FROM jobs WHERE company_id=? AND status='published'", (company["id"],))["n"],
        "applicants": one("""SELECT COUNT(*) AS n FROM applications a JOIN jobs j ON j.id=a.job_id
                             WHERE j.company_id=?""", (company["id"],))["n"],
        "shared_results": one("""SELECT COUNT(*) AS n FROM applications a JOIN jobs j ON j.id=a.job_id
                                 WHERE j.company_id=? AND a.results_shared=1""", (company["id"],))["n"]
    }
    stages = allr("""SELECT a.status,COUNT(*) AS n FROM applications a JOIN jobs j ON j.id=a.job_id
                     WHERE j.company_id=? GROUP BY a.status ORDER BY n DESC""", (company["id"],))
    job_performance = allr("""SELECT j.id,j.title,j.status,COUNT(a.id) AS applicants FROM jobs j
                              LEFT JOIN applications a ON a.job_id=j.id WHERE j.company_id=?
                              GROUP BY j.id,j.title,j.status ORDER BY applicants DESC,j.created DESC""", (company["id"],))
    return {"totals": totals, "stages": [dict(r) for r in stages], "jobs": [dict(r) for r in job_performance]}


@router.get("/api/company/applications/{application_id}")
def company_application_detail(application_id: str, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter", "hiring_manager")
    if not company["verified"]:
        raise HTTPException(403, "Company verification is required before accessing applicant records")
    row = one("""SELECT a.id AS application_id,a.status,a.results_shared,a.created,j.id AS job_id,j.title AS job_title,
                 j.skills AS job_skills,u.id AS candidate_id,u.name AS candidate_name
                 FROM applications a JOIN jobs j ON j.id=a.job_id JOIN users u ON u.id=a.candidate_id
                 WHERE a.id=? AND j.company_id=?""", (application_id, company["id"]))
    if not row:
        raise HTTPException(404, "Application not found")
    item = dict(row)
    item["results_shared"] = bool(item["results_shared"])
    item["required_skills"] = json.loads(item.pop("job_skills") or "[]")
    item["assessment_history"] = []
    if item["results_shared"]:
        attempts = allr("SELECT id,result,finished FROM attempts WHERE user_id=? AND status='done' ORDER BY finished DESC",
                        (item["candidate_id"],))
        for attempt in attempts:
            if not attempt["result"]:
                continue
            result = json.loads(attempt["result"])
            item["assessment_history"].append({
                "attempt_id": attempt["id"], "score": result.get("overall"),
                "scale": "0–100", "finished": attempt["finished"],
                "topics": result.get("topics", {}), "result_status": "prototype",
                "integrity_summary": "No recruiter-facing integrity evidence is available; human review status is not asserted."
            })
    notes = allr("SELECT id,body,author_id,created FROM application_notes WHERE application_id=? AND company_id=? ORDER BY created DESC",
                 (application_id, company["id"]))
    history = allr("SELECT from_stage,to_stage,reason,actor_id,created FROM application_history WHERE application_id=? AND company_id=? ORDER BY created DESC",
                   (application_id, company["id"]))
    item["notes"] = [dict(n) for n in notes] if company_role_for(user, company) in ("owner", "recruiter", "hiring_manager") else []
    item["history"] = [dict(h) for h in history]
    item["profile_visibility"] = "Candidate profile details are not exposed by this prototype unless a dedicated sharing flow is implemented."
    return item


@router.patch("/api/company/applications/{application_id}/stage")
def update_application_stage(application_id: str, body: ApplicationStageUpdate, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter", "hiring_manager")
    row = one("""SELECT a.status FROM applications a JOIN jobs j ON j.id=a.job_id
                 WHERE a.id=? AND j.company_id=?""", (application_id, company["id"]))
    if not row:
        raise HTTPException(404, "Application not found")
    current = row["status"]
    target = body.stage
    transitions = {
        "submitted": {"preliminary_assessment", "in_review", "withdrawn"},
        "applied": {"preliminary_assessment", "in_review", "withdrawn"},
        "preliminary_assessment": {"in_review", "phase_two_invited", "human_interview", "withdrawn"},
        "in_review": {"preliminary_assessment", "phase_two_invited", "human_interview", "withdrawn"},
        "phase_two_invited": {"human_interview", "further_review", "withdrawn"},
        "further_review": {"human_interview", "withdrawn"},
        "human_interview": {"offer", "rejected", "on_hold"},
        "on_hold": {"human_interview", "rejected"},
        "offer": {"hired", "offer_declined", "offer_withdrawn"},
        "offer_withdrawn": {"human_interview", "closed"},
        "hired": {"closed"}, "rejected": {"closed"},
        "withdrawn": {"closed"}
    }
    if target not in transitions.get(current, set()):
        raise HTTPException(409, "This pipeline transition is not allowed")
    if target in ("rejected", "hired", "offer_declined", "closed") and len(body.reason.strip()) < 3:
        raise HTTPException(422, "A structured human decision reason is required for this stage")
    now = time.time()
    run("UPDATE applications SET status=? WHERE id=?", (target, application_id))
    run("INSERT INTO application_history(id,application_id,company_id,actor_id,from_stage,to_stage,reason,created) VALUES (?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, application_id, company["id"], user["id"], current, target, body.reason.strip(), now))
    audit(user["id"], "application_stage_changed", application_id + ":" + current + "->" + target)
    return {"application_id": application_id, "from": current, "stage": target}


@router.post("/api/company/applications/{application_id}/notes")
def add_application_note(application_id: str, body: RecruiterNote, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter", "hiring_manager")
    if not one("""SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id
                  WHERE a.id=? AND j.company_id=?""", (application_id, company["id"])):
        raise HTTPException(404, "Application not found")
    nid = uuid.uuid4().hex
    run("INSERT INTO application_notes(id,application_id,company_id,author_id,body,created) VALUES (?,?,?,?,?,?)",
        (nid, application_id, company["id"], user["id"], body.body.strip(), time.time()))
    audit(user["id"], "recruiter_note_added", application_id)
    return {"id": nid}


@router.get("/api/company/team")
def company_team(user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner")
    rows = allr("SELECT id,email,company_role,status,created FROM company_members WHERE company_id=? ORDER BY created",
                (company["id"],))
    return {"owner": {"name": one("SELECT name FROM users WHERE id=?", (company["owner_user_id"],))["name"]},
            "role": company_role_for(user, company), "items": [dict(r) for r in rows]}


@router.post("/api/company/team/invitations")
def invite_company_member(body: TeamInviteInput, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner")
    roles = {"recruiter", "hiring_manager", "viewer"}
    email = body.email.strip().lower()
    if body.role not in roles:
        raise HTTPException(422, "Choose Recruiter, Hiring Manager or Viewer")
    existing = one("SELECT id FROM company_members WHERE company_id=? AND email=?", (company["id"], email))
    if existing:
        raise HTTPException(409, "This email already has a membership or invitation")
    token = secrets.token_urlsafe(24)
    mid = uuid.uuid4().hex
    run("INSERT INTO company_members(id,company_id,user_id,email,company_role,status,invite_token,created) VALUES (?,?,NULL,?,?, 'invited',?,?)",
        (mid, company["id"], email, body.role, token, time.time()))
    audit(user["id"], "company_member_invited", email + ":" + body.role)
    return {"id": mid, "status": "invited", "invite_token": token,
            "delivery": "manual_link", "message": "No email provider is configured; share this invitation token through your approved channel."}


@router.post("/api/company/team/accept/{token}")
def accept_company_invitation(token: str, user=Depends(current_user)):
    require_role(user, "company")
    row = one("SELECT * FROM company_members WHERE invite_token=? AND status='invited'", (token,))
    if not row or row["email"].lower() != user["email"].lower():
        raise HTTPException(404, "Invitation not found for this account")
    run("UPDATE company_members SET user_id=?,status='active',invite_token='' WHERE id=?", (user["id"], row["id"]))
    audit(user["id"], "company_invitation_accepted", row["company_id"])
    return {"status": "active", "company_id": row["company_id"], "role": row["company_role"]}


@router.patch("/api/company/team/{member_id}")
def update_company_member(member_id: str, body: TeamRoleUpdate, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner")
    row = one("SELECT id FROM company_members WHERE id=? AND company_id=?", (member_id, company["id"]))
    if not row:
        raise HTTPException(404, "Team member not found")
    if body.role not in ("recruiter", "hiring_manager", "viewer", "revoked"):
        raise HTTPException(422, "Unsupported team role")
    if body.role == "revoked":
        run("UPDATE company_members SET status='revoked' WHERE id=?", (member_id,))
    else:
        run("UPDATE company_members SET company_role=? WHERE id=?", (body.role, member_id))
    audit(user["id"], "company_member_permission_changed", member_id + ":" + body.role)
    return {"id": member_id, "role": body.role}


@router.get("/api/company/phase-two")
def list_phase_two(user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter", "hiring_manager", "viewer")
    rows = allr("""SELECT p.*,j.title AS job_title,a.status AS application_status,u.name AS candidate_name
                   FROM phase_two_invites p JOIN jobs j ON j.id=p.job_id
                   JOIN applications a ON a.id=p.application_id JOIN users u ON u.id=a.candidate_id
                   WHERE p.company_id=? ORDER BY p.created DESC""", (company["id"],))
    return {"items": [{**dict(r), "config": json.loads(r["config_json"])} for r in rows]}


@router.post("/api/company/applications/{application_id}/phase-two-invitations")
def create_phase_two_invitation(application_id: str, body: PhaseTwoInviteInput, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter")
    if not company["verified"]:
        raise HTTPException(403, "Company verification is required before inviting candidates to assessments")
    row = one("""SELECT a.id,a.job_id,a.status FROM applications a JOIN jobs j ON j.id=a.job_id
                 WHERE a.id=? AND j.company_id=?""", (application_id, company["id"]))
    if not row:
        raise HTTPException(404, "Application not found")
    if row["status"] not in ("submitted", "preliminary_assessment", "in_review", "phase_two_invited"):
        raise HTTPException(409, "This application is not eligible for a second phase")
    if body.integrity_mode not in ("light", "standard", "strict"):
        raise HTTPException(422, "Unsupported integrity mode")
    if sum(body.difficulty.values()) != 100 or any(v < 0 for v in body.difficulty.values()):
        raise HTTPException(422, "Difficulty distribution must total 100")
    if not body.syllabus:
        raise HTTPException(422, "Add at least one syllabus topic")
    now, iid = time.time(), uuid.uuid4().hex
    config = body.model_dump()
    run("""INSERT INTO phase_two_invites(id,company_id,job_id,application_id,title,requirements,config_json,version,status,deadline,created,updated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (iid, company["id"], row["job_id"], application_id, body.title.strip(), body.requirements.strip(),
         json.dumps(config), 1, "pending_platform_review", body.deadline, now, now))
    run("UPDATE applications SET status='phase_two_invited' WHERE id=?", (application_id,))
    run("INSERT INTO application_history(id,application_id,company_id,actor_id,from_stage,to_stage,reason,created) VALUES (?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, application_id, company["id"], user["id"], row["status"], "phase_two_invited",
         "Second-phase setup submitted for platform review", now))
    audit(user["id"], "phase_two_invitation_requested", iid)
    return {"id": iid, "status": "pending_platform_review",
            "message": "Configuration is saved. Candidate delivery remains pending platform approval and notification integration."}


@router.get("/api/company/jobs/{job_id}/revisions")
def company_job_revisions(job_id: str, user=Depends(current_user)):
    company = company_for_access(user)
    require_company_permission(user, company, "owner", "recruiter", "hiring_manager", "viewer")
    company_job(company["id"], job_id)
    rows = allr("SELECT revision,snapshot_json,actor_id,created FROM job_revisions WHERE job_id=? ORDER BY revision DESC",
                (job_id,))
    return {"items": [{**dict(r), "snapshot": json.loads(r["snapshot_json"])} for r in rows]}


# Platform review endpoints required to complete the company verification/job moderation loop.
class CompanyReviewDecision(BaseModel):
    decision: str
    reason: str = Field(min_length=3, max_length=1500)


@router.get("/api/admin/company-verifications")
def admin_company_verifications(user=Depends(current_user)):
    require_role(user, "admin")
    rows = allr("""SELECT v.company_id,v.legal_name,v.country,v.business_id,v.evidence_note,v.status,
                   v.decision_reason,v.submitted_at,c.name AS company_name,c.website
                   FROM company_verification v JOIN companies c ON c.id=v.company_id
                   ORDER BY v.submitted_at DESC""")
    return {"items": [dict(r) for r in rows]}


@router.post("/api/admin/companies/{company_id}/verification-decision")
def admin_company_verification_decision(company_id: str, body: CompanyReviewDecision, user=Depends(current_user)):
    admin_scope(user, "company_verification")
    if body.decision not in ("verified", "needs_information", "rejected"):
        raise HTTPException(422, "Decision must be verified, needs_information or rejected")
    row = one("SELECT company_id FROM company_verification WHERE company_id=?", (company_id,))
    if not row:
        raise HTTPException(404, "Verification request not found")
    now = time.time()
    run("""UPDATE company_verification SET status=?,decision_reason=?,reviewed_at=?,reviewed_by=?
           WHERE company_id=?""", (body.decision, body.reason.strip(), now, user["id"], company_id))
    run("UPDATE companies SET verified=? WHERE id=?", (1 if body.decision == "verified" else 0, company_id))
    audit(user["id"], "company_verification_decision", company_id + ":" + body.decision + ":" + body.reason.strip())
    return {"company_id": company_id, "status": body.decision}


@router.get("/api/admin/job-review-queue")
def admin_job_review_queue(user=Depends(current_user)):
    require_role(user, "admin")
    rows = allr("""SELECT j.id,j.title,j.description,j.location,j.employment_type,j.created,c.name AS company_name,c.id AS company_id
                   FROM jobs j JOIN companies c ON c.id=j.company_id
                   WHERE j.status='pending_review' ORDER BY j.created ASC""")
    return {"items": [dict(r) for r in rows]}


@router.post("/api/admin/jobs/{job_id}/moderation-decision")
def admin_job_moderation_decision(job_id: str, body: CompanyReviewDecision, user=Depends(current_user)):
    admin_scope(user, "job_moderation")
    if body.decision not in ("published", "needs_changes", "rejected", "paused"):
        raise HTTPException(422, "Unsupported moderation decision")
    row = one("SELECT j.id,j.company_id,c.verified FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?", (job_id,))
    if not row:
        raise HTTPException(404, "Job not found")
    if body.decision == "published" and not row["verified"]:
        raise HTTPException(409, "Company verification must be completed before a job can be published")
    run("UPDATE jobs SET status=? WHERE id=?", (body.decision, job_id))
    audit(user["id"], "job_moderation_decision", job_id + ":" + body.decision + ":" + body.reason.strip())
    return {"job_id": job_id, "status": body.decision}


# ---------------------------------------------------------------------------
# Admin console expansion — PRD v2.0
# The console exposes only data that exists in this R0/R1 implementation.
def ensure_admin_console_schema():
    for statement in [
        """CREATE TABLE IF NOT EXISTS admin_user_status (
            user_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active',
            reason TEXT NOT NULL DEFAULT '', changed_by INTEGER NOT NULL, changed_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS admin_attempt_reviews (
            attempt_id TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL,
            reviewer_id INTEGER NOT NULL, reviewed_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS admin_appeal_decisions (
            attempt_id TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL,
            reviewer_id INTEGER NOT NULL, decided_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS admin_staff_roles (
            user_id INTEGER PRIMARY KEY, admin_role TEXT NOT NULL DEFAULT 'super_admin',
            updated_by INTEGER NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS question_bank (
            id TEXT PRIMARY KEY, category_id TEXT NOT NULL, subcategory TEXT NOT NULL DEFAULT '',
            question_type TEXT NOT NULL, difficulty_level TEXT NOT NULL, question_text TEXT NOT NULL,
            options TEXT NOT NULL DEFAULT '[]', correct_answer TEXT NOT NULL DEFAULT '',
            explanation TEXT NOT NULL DEFAULT '', reference_answer TEXT NOT NULL DEFAULT '',
            grading_rubric TEXT NOT NULL DEFAULT '', marks INTEGER NOT NULL DEFAULT 1,
            time_allocation INTEGER NOT NULL DEFAULT 60, status TEXT NOT NULL DEFAULT 'draft',
            created_by TEXT NOT NULL, created_at REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1)"""
    ]:
        run(statement)
    # Existing internally provisioned admin accounts retain their current authority.
    for row in allr("SELECT user_id FROM user_roles WHERE role='admin'"):
        if not one("SELECT user_id FROM admin_staff_roles WHERE user_id=?", (row["user_id"],)):
            run("INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at) VALUES (?,?,?,?)",
                (row["user_id"], "super_admin", row["user_id"], time.time()))


ensure_admin_console_schema()


ADMIN_SCOPE_ROLES = {
    "overview": {"super_admin"}, "users_read": {"super_admin", "support_agent", "trust_safety_reviewer"},
    "users_write": {"super_admin", "trust_safety_reviewer"}, "company_verification": {"super_admin", "company_verification_admin"},
    "job_moderation": {"super_admin", "company_verification_admin"}, "assessment_review": {"super_admin", "trust_safety_reviewer"},
    "appeals": {"super_admin", "trust_safety_reviewer"}, "phase_two": {"super_admin", "question_content_admin", "company_verification_admin"},
    "questions": {"super_admin", "question_content_admin"}, "ai": {"super_admin", "compliance_officer"},
    "billing": {"super_admin", "finance_admin"}, "privacy": {"super_admin", "compliance_officer"},
    "quality": {"super_admin", "compliance_officer", "trust_safety_reviewer"}, "audit": {"super_admin", "compliance_officer"},
    "staff": {"super_admin"},
}
def admin_scope(user, scope="overview"):
    require_role(user, "admin")
    row = one("SELECT admin_role FROM admin_staff_roles WHERE user_id=?", (user["id"],))
    staff_role = row["admin_role"] if row else "super_admin"
    if staff_role not in ADMIN_SCOPE_ROLES.get(scope, {"super_admin"}):
        raise HTTPException(403, "Your admin staff role does not permit this action")
    return staff_role


def admin_only(user, scope="overview"):
    admin_scope(user, scope)
    return user


class AdminUserStatusInput(BaseModel):
    status: str
    reason: str = Field(min_length=3, max_length=1500)


class AdminReviewInput(BaseModel):
    decision: str
    reason: str = Field(min_length=3, max_length=1500)


@router.get("/api/admin/console/overview")
def admin_overview_v2(user=Depends(current_user)):
    admin_only(user, "overview")
    counts = {
        "users": one("SELECT COUNT(*) AS n FROM users")["n"],
        "candidates": one("SELECT COUNT(*) AS n FROM user_roles WHERE role='candidate'")["n"],
        "companies": one("SELECT COUNT(*) AS n FROM companies")["n"],
        "verified_companies": one("SELECT COUNT(*) AS n FROM companies WHERE verified=1")["n"],
        "pending_companies": one("SELECT COUNT(*) AS n FROM company_verification WHERE status='pending_review'")["n"],
        "jobs": one("SELECT COUNT(*) AS n FROM jobs")["n"],
        "pending_jobs": one("SELECT COUNT(*) AS n FROM jobs WHERE status='pending_review'")["n"],
        "published_jobs": one("SELECT COUNT(*) AS n FROM jobs WHERE status='published'")["n"],
        "applications": one("SELECT COUNT(*) AS n FROM applications")["n"],
        "attempts": one("SELECT COUNT(*) AS n FROM attempts")["n"],
        "completed_attempts": one("SELECT COUNT(*) AS n FROM attempts WHERE status='done'")["n"],
        "open_appeals": one("SELECT COUNT(*) AS n FROM attempts WHERE appeal IS NOT NULL AND appeal!=''")["n"],
        "assessment_requests": one("SELECT COUNT(*) AS n FROM phase_two_invites WHERE status='pending_platform_review'")["n"],
        "suspended_users": one("SELECT COUNT(*) AS n FROM admin_user_status WHERE status='suspended'")["n"],
    }
    return {"counts": counts, "demo_data": True,
            "note": "Counts may include explicitly labelled synthetic demo records. Billing and AI cost telemetry are not connected in this deployment."}


@router.get("/api/admin/console/users")
def admin_users_v2(user=Depends(current_user), q: str = "", role: str = "all", status: str = "all"):
    admin_only(user, "users_read")
    query = """SELECT u.id,u.name,u.email,COALESCE(r.role,'candidate') AS role,u.created,
               COALESCE(s.status,'active') AS account_status,COALESCE(s.reason,'') AS status_reason
               FROM users u LEFT JOIN user_roles r ON r.user_id=u.id
               LEFT JOIN admin_user_status s ON s.user_id=u.id WHERE 1=1"""
    args = []
    if q.strip():
        query += " AND (LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ?)"
        needle = "%" + q.strip().lower() + "%"
        args.extend([needle, needle])
    if role in ("candidate", "company", "admin"):
        query += " AND COALESCE(r.role,'candidate')=?"; args.append(role)
    if status in ("active", "suspended"):
        query += " AND COALESCE(s.status,'active')=?"; args.append(status)
    query += " ORDER BY u.created DESC LIMIT 500"
    return {"items": [dict(r) for r in allr(query, tuple(args))]}


@router.post("/api/admin/users/{user_id}/status")
def admin_set_user_status(user_id: int, body: AdminUserStatusInput, user=Depends(current_user)):
    admin_only(user, "users_write")
    if body.status not in ("active", "suspended"):
        raise HTTPException(422, "Status must be active or suspended")
    target = one("SELECT id FROM users WHERE id=?", (user_id,))
    if not target: raise HTTPException(404, "User not found")
    target_role = one("SELECT role FROM user_roles WHERE user_id=?", (user_id,))
    if user_id == user["id"] and body.status == "suspended":
        raise HTTPException(409, "You cannot suspend your own active admin session")
    if target_role and target_role["role"] == "admin" and body.status == "suspended":
        raise HTTPException(409, "Admin staff suspension requires the privileged staff workflow")
    run("""INSERT INTO admin_user_status(user_id,status,reason,changed_by,changed_at) VALUES (?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,
           changed_by=excluded.changed_by,changed_at=excluded.changed_at""",
        (user_id, body.status, body.reason.strip(), user["id"], time.time()))
    audit(user["id"], "admin_user_status_changed", str(user_id) + ":" + body.status + ":" + body.reason.strip())
    return {"user_id": user_id, "status": body.status}


@router.get("/api/admin/console/company-verifications")
def admin_company_verifications_v2(user=Depends(current_user)):
    admin_only(user, "company_verification")
    rows = allr("""SELECT v.company_id,v.legal_name,v.country,v.business_id,v.evidence_note,v.status,
                   v.decision_reason,v.submitted_at,v.reviewed_at,c.name AS company_name,c.website
                   FROM company_verification v JOIN companies c ON c.id=v.company_id
                   ORDER BY CASE WHEN v.status='pending_review' THEN 0 ELSE 1 END,v.submitted_at DESC""")
    return {"items": [dict(r) for r in rows]}


@router.get("/api/admin/console/job-review-queue")
def admin_job_review_queue_v2(user=Depends(current_user)):
    admin_only(user, "job_moderation")
    rows = allr("""SELECT j.id,j.title,j.description,j.location,j.employment_type,j.status,j.created,
                   c.name AS company_name,c.id AS company_id,c.verified
                   FROM jobs j JOIN companies c ON c.id=j.company_id
                   WHERE j.status IN ('pending_review','needs_changes','rejected')
                   ORDER BY CASE WHEN j.status='pending_review' THEN 0 ELSE 1 END,j.created ASC""")
    return {"items": [dict(r) for r in rows]}


@router.get("/api/admin/assessment-reviews")
def admin_assessment_reviews(user=Depends(current_user)):
    admin_only(user, "assessment_review")
    rows = allr("""SELECT a.id,a.user_id,a.kind,a.status,a.started,a.finished,a.result,a.appeal,
                   u.name AS candidate_name,u.email,
                   r.decision AS review_decision,r.reason AS review_reason,r.reviewed_at
                   FROM attempts a JOIN users u ON u.id=a.user_id
                   LEFT JOIN admin_attempt_reviews r ON r.attempt_id=a.id
                   WHERE a.status='done' OR (a.appeal IS NOT NULL AND a.appeal!='')
                   ORDER BY CASE WHEN a.appeal IS NOT NULL AND a.appeal!='' AND r.decision IS NULL THEN 0 ELSE 1 END,
                   COALESCE(a.finished,a.started) DESC LIMIT 300""")
    items=[]
    for row in rows:
        item=dict(row)
        try: item["result"]=json.loads(item["result"]) if item["result"] else None
        except Exception: item["result"]=None
        # The state is admin-only here. Preserve the original response for manual review;
        # legacy attempts created before response capture are explicitly marked unavailable.
        attempt_state = one("SELECT state FROM attempts WHERE id=?", (item["id"],))
        try:
            state = json.loads(attempt_state["state"]) if attempt_state and attempt_state["state"] else {}
        except Exception:
            state = {}
        evidence = []
        for index, saved in enumerate(state.get("answers", []), start=1):
            question = QBYID.get(saved.get("qid"), {})
            raw_answer = saved.get("answer")
            options = question.get("options") or []
            selected_option = None
            if saved.get("type") == "mcq" and raw_answer not in (None, ""):
                try:
                    selected_option = options[int(raw_answer)]
                except (ValueError, TypeError, IndexError):
                    selected_option = str(raw_answer)
            evidence.append({
                "number": index,
                "question_id": saved.get("qid"),
                "topic": saved.get("topic") or question.get("topic"),
                "type": saved.get("type") or question.get("type"),
                "difficulty": saved.get("difficulty") or question.get("difficulty"),
                "question": question.get("prompt") or "Question text unavailable",
                "options": options,
                "candidate_answer": raw_answer,
                "selected_option": selected_option,
                "answer_available": raw_answer is not None,
                "automated_correct": saved.get("correct"),
                "time_seconds": saved.get("seconds")
            })
        item["answer_evidence"] = evidence
        item["answer_evidence_complete"] = bool(evidence) and all(x["answer_available"] for x in evidence)
        item["evaluation_method"] = "Deterministic answer-key / SQL grader; no generative AI score was produced."
        items.append(item)
    return {"items":items}


@router.post("/api/admin/attempts/{attempt_id}/review")
def admin_review_attempt(attempt_id: str, body: AdminReviewInput, user=Depends(current_user)):
    admin_only(user, "assessment_review")
    if body.decision not in ("approved", "needs_more_info", "invalidated"):
        raise HTTPException(422, "Unsupported review decision")
    if not one("SELECT id FROM attempts WHERE id=?", (attempt_id,)):
        raise HTTPException(404, "Assessment attempt not found")
    run("""INSERT INTO admin_attempt_reviews(attempt_id,decision,reason,reviewer_id,reviewed_at)
           VALUES (?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET decision=excluded.decision,
           reason=excluded.reason,reviewer_id=excluded.reviewer_id,reviewed_at=excluded.reviewed_at""",
        (attempt_id, body.decision, body.reason.strip(), user["id"], time.time()))
    audit(user["id"], "assessment_result_reviewed", attempt_id + ":" + body.decision + ":" + body.reason.strip())
    return {"attempt_id": attempt_id, "decision": body.decision,
            "note": "The original answer record and deterministic score were not modified."}



@router.get("/api/admin/appeals")
def admin_appeals(user=Depends(current_user)):
    admin_only(user, "appeals")
    rows = allr("""SELECT a.id AS attempt_id,a.appeal,a.kind,a.finished,u.id AS user_id,u.name AS candidate_name,
                   u.email,d.decision,d.reason AS review_reason,d.decided_at,d.reviewer_id
                   FROM attempts a JOIN users u ON u.id=a.user_id
                   LEFT JOIN admin_appeal_decisions d ON d.attempt_id=a.id
                   WHERE a.appeal IS NOT NULL AND a.appeal!='' ORDER BY a.started DESC LIMIT 300""")
    return {"items":[dict(r) for r in rows]}


@router.post("/api/admin/appeals/{attempt_id}/decision")
def admin_appeal_decision(attempt_id: str, body: AdminReviewInput, user=Depends(current_user)):
    admin_only(user, "appeals")
    if body.decision not in ("upheld", "overturned", "needs_more_info"):
        raise HTTPException(422, "Unsupported appeal decision")
    row=one("SELECT id FROM attempts WHERE id=? AND appeal IS NOT NULL AND appeal!=''",(attempt_id,))
    if not row: raise HTTPException(404,"Appeal not found")
    prior=one("SELECT reviewer_id FROM admin_attempt_reviews WHERE attempt_id=?",(attempt_id,))
    if prior and prior["reviewer_id"]==user["id"]:
        raise HTTPException(409,"For separation of duties, an appeal should be reviewed by a different administrator.")
    run("""INSERT INTO admin_appeal_decisions(attempt_id,decision,reason,reviewer_id,decided_at)
           VALUES (?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET decision=excluded.decision,
           reason=excluded.reason,reviewer_id=excluded.reviewer_id,decided_at=excluded.decided_at""",
        (attempt_id,body.decision,body.reason.strip(),user["id"],time.time()))
    audit(user["id"],"candidate_appeal_decided",attempt_id+":"+body.decision+":"+body.reason.strip())
    return {"attempt_id":attempt_id,"decision":body.decision}


@router.get("/api/admin/phase-two-requests")
def admin_phase_two_requests(user=Depends(current_user)):
    admin_only(user, "phase_two")
    rows=allr("""SELECT p.id,p.title,p.requirements,p.config_json,p.status,p.deadline,p.created,
                 c.name AS company_name,j.title AS job_title,u.name AS candidate_name
                 FROM phase_two_invites p JOIN companies c ON c.id=p.company_id
                 JOIN jobs j ON j.id=p.job_id JOIN applications a ON a.id=p.application_id
                 JOIN users u ON u.id=a.candidate_id ORDER BY p.created DESC""")
    return {"items":[{**dict(r),"config":json.loads(r["config_json"])} for r in rows]}


@router.post("/api/admin/phase-two-requests/{request_id}/decision")
def admin_phase_two_decision(request_id: str, body: AdminReviewInput, user=Depends(current_user)):
    admin_only(user, "phase_two")
    if body.decision not in ("approved","needs_changes","rejected"):
        raise HTTPException(422,"Unsupported phase-two review decision")
    row=one("SELECT id FROM phase_two_invites WHERE id=?",(request_id,))
    if not row: raise HTTPException(404,"Assessment request not found")
    run("UPDATE phase_two_invites SET status=?,updated=? WHERE id=?",(body.decision,time.time(),request_id))
    audit(user["id"],"phase_two_request_reviewed",request_id+":"+body.decision+":"+body.reason.strip())
    return {"id":request_id,"status":body.decision}


@router.get("/api/admin/audit-logs")
def admin_audit_logs(user=Depends(current_user), limit: int = 100, action: str = ""):
    admin_only(user, "audit")
    limit=max(1,min(limit,300))
    if action.strip():
        rows=allr("""SELECT a.id,a.ts,a.actor,a.action,a.target,u.name AS actor_name,u.email AS actor_email
                     FROM audit a LEFT JOIN users u ON u.id=a.actor WHERE a.action LIKE ?
                     ORDER BY a.ts DESC LIMIT ?""",("%"+action.strip()+"%",limit))
    else:
        rows=allr("""SELECT a.id,a.ts,a.actor,a.action,a.target,u.name AS actor_name,u.email AS actor_email
                     FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.ts DESC LIMIT ?""",(limit,))
    return {"items":[dict(r) for r in rows]}



QUESTION_CATEGORIES = [
    {"id":"CAT-01","name":"SQL","icon":"▤","description":"Queries, joins, indexing and database operations","subcategories":["SQL Basics","Joins","Aggregation","Subqueries","Indexing","Transactions","Query Optimization"]},
    {"id":"CAT-02","name":"DSA","icon":"⌘","description":"Data structures, algorithms and complexity","subcategories":["Arrays & Strings","Linked Lists","Stacks & Queues","Trees","Graphs","Sorting & Searching","Dynamic Programming","Big-O Analysis"]},
    {"id":"CAT-03","name":"Coding","icon":"</>","description":"Programming exercises and implementation","subcategories":["Python","C++","Problem Solving","Debugging","Functions","OOP Coding"]},
    {"id":"CAT-04","name":"RAG & AI","icon":"✳","description":"LLMs, retrieval, embeddings and AI agents","subcategories":["LLM Fundamentals","RAG Pipelines","Embeddings","Vector Databases","Prompt Engineering","AI Agents","Evaluation"]},
    {"id":"CAT-05","name":"Java","icon":"☕","description":"Java language, JVM and ecosystem","subcategories":["Core Java","OOP","Collections","Exception Handling","Multithreading","JVM & Memory","Spring Basics"]},
    {"id":"CAT-06","name":"Scenario-Based","icon":"◇","description":"Practical workplace and technical situations","subcategories":["Troubleshooting","Decision Making","Production Issues","Team Collaboration","Case Study","Debugging Scenario"]},
    {"id":"CAT-07","name":"IQ & Aptitude","icon":"◎","description":"Logical, numerical and analytical reasoning","subcategories":["Logical Reasoning","Numerical Ability","Pattern Recognition","Verbal Reasoning","Data Interpretation"]},
    {"id":"CAT-08","name":"CS Fundamentals","icon":"▦","description":"Core computer science and engineering concepts","subcategories":["Operating Systems","Computer Networks","DBMS","OOP Concepts","Software Engineering","Computer Architecture","Compiler Basics"]},
    {"id":"CAT-09","name":"System Design","icon":"⌘","description":"Architecture, APIs, scalability and reliability","subcategories":["Architecture","API Design","Scalability","Caching","Messaging","Database Design","Security & Reliability"]}
]
QUESTION_CATEGORY_BY_ID = {x["id"]:x for x in QUESTION_CATEGORIES}
QUESTION_CATEGORY_BY_NAME = {x["name"].lower():x["id"] for x in QUESTION_CATEGORIES}

def _seed_question_bank():
    if one("SELECT id FROM question_bank LIMIT 1"):
        return
    from content import QUESTIONS as curated_questions
    legacy_map = {"sql":"CAT-01","python":"CAT-03","statistics":"CAT-08","visualization":"CAT-08","excel":"CAT-08","git":"CAT-08","communication":"CAT-06"}
    now = time.time()
    for q in curated_questions:
        options = q.get("options") or []
        answer = options[q["answer"]] if q.get("type") == "mcq" and isinstance(q.get("answer"), int) and options else q.get("ref", "")
        run("""INSERT INTO question_bank(id,category_id,subcategory,question_type,difficulty_level,question_text,
               options,correct_answer,explanation,reference_answer,grading_rubric,marks,time_allocation,status,
               created_by,created_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (q["id"],legacy_map.get(q.get("topic"),"CAT-08"),"Legacy curated", "MCQ" if q.get("type")=="mcq" else "Coding",
             {1:"Easy",2:"Medium",3:"Hard"}.get(q.get("difficulty"),"Medium"),q.get("prompt",""),
             json.dumps(options),str(answer or ""),q.get("explanation",""),q.get("ref",""),
             "Deterministic answer-key comparison for legacy curated content",1,
             {1:60,2:90,3:120}.get(q.get("difficulty"),90),"active","Curated question bank",now,1))

def _question_payload(raw, actor="Admin"):
    raw = dict(raw or {})
    category = str(raw.get("category_id") or raw.get("category") or "").strip()
    category_id = category.upper() if category.upper() in QUESTION_CATEGORY_BY_ID else QUESTION_CATEGORY_BY_NAME.get(category.lower())
    qtext = str(raw.get("question_text") or raw.get("question") or raw.get("prompt") or "").strip()
    qtype = str(raw.get("question_type") or raw.get("type") or ("MCQ" if raw.get("options") else "Short Answer")).strip()
    difficulty = str(raw.get("difficulty_level") or raw.get("difficulty") or "Medium").strip().title()
    if difficulty not in ("Easy","Medium","Hard"):
        difficulty = {"1":"Easy","2":"Medium","3":"Hard"}.get(difficulty,"")
    options = raw.get("options") or []
    if isinstance(options,str):
        try: options=json.loads(options)
        except Exception: options=[x.strip() for x in re.split(r"\s*[|;]\s*",options) if x.strip()]
    if not isinstance(options,list): options=[]
    options=[str(x).strip() for x in options if str(x).strip()]
    try: marks=int(raw.get("marks") or 1)
    except (TypeError,ValueError): marks=0
    try:
        if raw.get("time_allocation") not in (None,""):
            seconds=int(float(raw.get("time_allocation")))
        elif raw.get("time_allocation_seconds") not in (None,""):
            seconds=int(float(raw.get("time_allocation_seconds")))
        else:
            seconds=int(float(raw.get("time_allocation_minutes") or raw.get("time_minutes") or 1)*60)
    except (TypeError,ValueError): seconds=0
    status=str(raw.get("status") or "draft").lower()
    errors=[]
    if not category_id: errors.append("Choose a valid category.")
    if not qtext: errors.append("Question text is required.")
    if not qtype: errors.append("Question type is required.")
    if difficulty not in ("Easy","Medium","Hard"): errors.append("Difficulty must be Easy, Medium or Hard.")
    if marks<1 or marks>100: errors.append("Marks must be between 1 and 100.")
    if seconds<15 or seconds>7200: errors.append("Time allocation must be 15–7200 seconds.")
    if status not in ("draft","active","archived"): errors.append("Status must be Draft, Active or Archived.")
    if len(qtext)>10000: errors.append("Question text is too long.")
    if len(options)>10: errors.append("A question can have at most 10 options.")
    if qtype.lower() in ("mcq","multiple choice") and len(options)<2: errors.append("MCQ requires at least two options.")
    return {"id":str(raw.get("id") or uuid.uuid4().hex[:12]),"category_id":category_id or "",
        "subcategory":str(raw.get("subcategory") or "General").strip()[:100],"question_type":qtype[:40],
        "difficulty_level":difficulty,"question_text":qtext,"options":options,
        "correct_answer":str(raw.get("correct_answer") if raw.get("correct_answer") is not None else raw.get("answer") or "").strip(),
        "explanation":str(raw.get("explanation") or "").strip(),
        "reference_answer":str(raw.get("reference_answer") or raw.get("reference") or "").strip(),
        "grading_rubric":str(raw.get("grading_rubric") or raw.get("rubric") or "").strip(),
        "marks":marks,"time_allocation":seconds,"status":status,"created_by":actor,
        "created_at":time.time(),"version":int(raw.get("version") or 1),"errors":errors}

def _question_row(row):
    item=dict(row)
    try: item["options"]=json.loads(item.get("options") or "[]")
    except Exception: item["options"]=[]
    item["created_date"]=item.pop("created_at",None)
    return item

@router.get("/api/admin/questions")
def admin_questions(user=Depends(current_user)):
    admin_only(user, "questions")
    _seed_question_bank()
    items=[_question_row(r) for r in allr("SELECT * FROM question_bank ORDER BY created_at DESC")]
    counts={c["id"]:0 for c in QUESTION_CATEGORIES}
    for q in items: counts[q["category_id"]]=counts.get(q["category_id"],0)+1
    return {"items":items,"categories":[{**c,"count":counts.get(c["id"],0)} for c in QUESTION_CATEGORIES],
        "summary":{"total":len(items),"active":sum(q["status"]=="active" for q in items),
        "draft":sum(q["status"]=="draft" for q in items),"archived":sum(q["status"]=="archived" for q in items)},
        "governance":"Database-backed question bank with versioned edits, explicit status and audited imports."}

@router.post("/api/admin/questions")
def admin_create_question(body: dict, user=Depends(current_user)):
    admin_only(user,"questions")
    payload=_question_payload(body,user.get("name") or "Admin")
    if payload["errors"]: raise HTTPException(422,{"errors":payload["errors"]})
    duplicate=one("SELECT id FROM question_bank WHERE lower(question_text)=lower(?) AND category_id=?",(payload["question_text"],payload["category_id"]))
    if duplicate: raise HTTPException(409,"A question with the same text already exists in this category.")
    run("""INSERT INTO question_bank(id,category_id,subcategory,question_type,difficulty_level,question_text,
        options,correct_answer,explanation,reference_answer,grading_rubric,marks,time_allocation,status,
        created_by,created_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (payload["id"],payload["category_id"],payload["subcategory"],payload["question_type"],payload["difficulty_level"],
        payload["question_text"],json.dumps(payload["options"]),payload["correct_answer"],payload["explanation"],
        payload["reference_answer"],payload["grading_rubric"],payload["marks"],payload["time_allocation"],
        payload["status"],payload["created_by"],payload["created_at"],1))
    audit(user["id"],"question_created",payload["id"])
    return _question_row(one("SELECT * FROM question_bank WHERE id=?",(payload["id"],)))

@router.put("/api/admin/questions/{question_id}")
def admin_update_question(question_id: str, body: dict, user=Depends(current_user)):
    admin_only(user,"questions")
    current=one("SELECT * FROM question_bank WHERE id=?",(question_id,))
    if not current: raise HTTPException(404,"Question not found")
    merged=dict(current); merged.update(body); merged["id"]=question_id
    payload=_question_payload(merged,user.get("name") or "Admin")
    if payload["errors"]: raise HTTPException(422,{"errors":payload["errors"]})
    duplicate=one("SELECT id FROM question_bank WHERE lower(question_text)=lower(?) AND category_id=? AND id<>?",
        (payload["question_text"],payload["category_id"],question_id))
    if duplicate: raise HTTPException(409,"A question with the same text already exists in this category.")
    version=int(current["version"] or 1)+1
    run("""UPDATE question_bank SET category_id=?,subcategory=?,question_type=?,difficulty_level=?,question_text=?,
        options=?,correct_answer=?,explanation=?,reference_answer=?,grading_rubric=?,marks=?,time_allocation=?,
        status=?,version=? WHERE id=?""",
        (payload["category_id"],payload["subcategory"],payload["question_type"],payload["difficulty_level"],
        payload["question_text"],json.dumps(payload["options"]),payload["correct_answer"],payload["explanation"],
        payload["reference_answer"],payload["grading_rubric"],payload["marks"],payload["time_allocation"],
        payload["status"],version,question_id))
    audit(user["id"],"question_updated",question_id+":v"+str(version))
    return _question_row(one("SELECT * FROM question_bank WHERE id=?",(question_id,)))

@router.post("/api/admin/questions/import/preview")
async def admin_preview_question_import(file: UploadFile=File(...), category_id: str=Form(""), user=Depends(current_user)):
    admin_only(user,"questions")
    name=(file.filename or "").lower(); data=await file.read()
    if len(data)>5_000_000: raise HTTPException(413,"Upload must be smaller than 5 MB.")
    try:
        if name.endswith(".csv"):
            records=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        elif name.endswith(".json"):
            parsed=json.loads(data.decode("utf-8-sig"))
            records=parsed if isinstance(parsed,list) else parsed.get("questions",[])
        elif name.endswith(".pdf"):
            from pypdf import PdfReader
            extracted="\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
            matches=re.findall(r"(?:^|\n)\s*(?:Q(?:uestion)?\s*)?\d{1,4}[\).:\-]\s*(.+?)(?=(?:\n\s*(?:Q(?:uestion)?\s*)?\d{1,4}[\).:\-]\s*)|$)",extracted,re.S|re.I)
            records=[{"category_id":category_id,"subcategory":"PDF import","question_type":"Short Answer",
                "difficulty_level":"Medium","question_text":re.sub(r"\s+"," ",m).strip(),"marks":1,
                "time_allocation_minutes":2,"status":"draft"} for m in matches]
            if not records and extracted.strip():
                records=[{"category_id":category_id,"subcategory":"PDF import","question_type":"Short Answer",
                    "difficulty_level":"Medium","question_text":extracted.strip()[:10000],"marks":1,
                    "time_allocation_minutes":2,"status":"draft"}]
        else: raise HTTPException(415,"Upload a CSV, JSON or PDF file.")
    except HTTPException: raise
    except Exception as exc: raise HTTPException(422,"Could not read this file: "+str(exc)[:180])
    if not isinstance(records,list) or not records: raise HTTPException(422,"No question records were found in the uploaded file.")
    if len(records)>1000: raise HTTPException(413,"A single import can contain at most 1,000 questions.")
    preview=[]
    for raw in records:
        if not isinstance(raw,dict): raw={"question_text":str(raw),"category_id":category_id}
        if not raw.get("category_id") and category_id: raw["category_id"]=category_id
        item=_question_payload(raw,user.get("name") or "Admin")
        dup=one("SELECT id FROM question_bank WHERE lower(question_text)=lower(?) AND category_id=?",
            (item["question_text"],item["category_id"])) if item["question_text"] and item["category_id"] else None
        item["duplicate"]=bool(dup)
        if dup: item["errors"].append("Duplicate question already exists.")
        preview.append(item)
    valid=sum(not x["errors"] for x in preview)
    return {"filename":file.filename,"total":len(preview),"valid":valid,"invalid":len(preview)-valid,
        "items":preview,"pdf_note":"PDF extraction is text-based. Review and correct extracted questions before importing." if name.endswith(".pdf") else None}

class QuestionImportCommit(BaseModel):
    questions: List[dict] = Field(default_factory=list,max_length=1000)

@router.post("/api/admin/questions/import/commit")
def admin_commit_question_import(body: QuestionImportCommit,user=Depends(current_user)):
    admin_only(user,"questions")
    added=0; skipped=0; rejected=0
    for raw in body.questions:
        payload=_question_payload(raw,user.get("name") or "Admin")
        if payload["errors"]: rejected+=1; continue
        duplicate=one("SELECT id FROM question_bank WHERE lower(question_text)=lower(?) AND category_id=?",
            (payload["question_text"],payload["category_id"]))
        if duplicate: skipped+=1; continue
        run("""INSERT INTO question_bank(id,category_id,subcategory,question_type,difficulty_level,question_text,
            options,correct_answer,explanation,reference_answer,grading_rubric,marks,time_allocation,status,
            created_by,created_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload["id"],payload["category_id"],payload["subcategory"],payload["question_type"],payload["difficulty_level"],
            payload["question_text"],json.dumps(payload["options"]),payload["correct_answer"],payload["explanation"],
            payload["reference_answer"],payload["grading_rubric"],payload["marks"],payload["time_allocation"],
            payload["status"],payload["created_by"],payload["created_at"],1))
        added+=1
    audit(user["id"],"question_bulk_import",f"added={added};skipped={skipped};rejected={rejected}")
    return {"added":added,"skipped":skipped,"rejected":rejected}


@router.get("/api/admin/ai-operations")
def admin_ai_operations(user=Depends(current_user)):
    admin_only(user, "ai")
    import main as core
    return {"provider":"Anthropic-compatible configured provider","model":core.LLM_MODEL,
            "configured":bool(core.ANTHROPIC_API_KEY),"status":"configured" if core.ANTHROPIC_API_KEY else "fallback_mode",
            "fallback":"Rule-based CV extraction","metrics_available":False,
            "note":"Provider latency, token/cost telemetry, prompt evaluation history and a remote kill switch are not instrumented in this deployment."}


@router.get("/api/admin/billing")
def admin_billing(user=Depends(current_user)):
    admin_only(user, "billing")
    return {"enabled":False,"provider_configured":False,"plans":[],"invoices":[],"refunds":[],
            "ledger":[],"note":"Billing, payment webhooks, credit ledger, invoices and refund reconciliation are not implemented/configured in this deployment. No financial records are fabricated."}


@router.get("/api/admin/privacy")
def admin_privacy(user=Depends(current_user)):
    admin_only(user, "privacy")
    return {"consented_users":one("SELECT COUNT(DISTINCT user_id) AS n FROM consents")["n"],
            "consent_records":one("SELECT COUNT(*) AS n FROM consents")["n"],
            "export_capability":True,"self_service_deletion":True,
            "retention_jobs_configured":False,
            "note":"Consent history, candidate self-service export and deletion endpoints exist. Admin-managed requests, regional retention schedules and scheduled deletion workers are not configured."}


@router.get("/api/admin/quality")
def admin_quality(user=Depends(current_user)):
    admin_only(user, "quality")
    attempts=one("SELECT COUNT(*) AS n FROM attempts WHERE status='done'")["n"]
    appeals=one("SELECT COUNT(*) AS n FROM attempts WHERE appeal IS NOT NULL AND appeal!=''")["n"]
    reviewed=one("SELECT COUNT(*) AS n FROM admin_attempt_reviews")["n"]
    overturns=one("SELECT COUNT(*) AS n FROM admin_appeal_decisions WHERE decision='overturned'")["n"]
    return {"completed_attempts":attempts,"appeals":appeals,"reviewed_attempts":reviewed,
            "appeal_overturns":overturns,"appeal_overturn_rate":(round(overturns/appeals,4) if appeals else None),
            "demographic_analysis":"not_collected",
            "note":"No demographic or protected-group inference is performed. Metrics are descriptive and limited by the current prototype data."}

@router.get("/api/admin/console/staff")
def admin_console_staff(user=Depends(current_user)):
    admin_only(user, "staff")
    rows=allr("""SELECT u.id,u.name,u.email,s.admin_role,s.updated_at FROM user_roles r
                 JOIN users u ON u.id=r.user_id LEFT JOIN admin_staff_roles s ON s.user_id=u.id
                 WHERE r.role='admin' ORDER BY u.created""")
    return {"items":[{**dict(r),"admin_role":r["admin_role"] or "super_admin"} for r in rows]}

class AdminStaffRoleInput(BaseModel):
    admin_role: str

class AdminStaffCreateInput(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=14, max_length=128)
    admin_role: str = "support_agent"


@router.post("/api/admin/console/staff")
def admin_console_create_staff(body: AdminStaffCreateInput, user=Depends(current_user)):
    admin_only(user, "staff")
    email = body.email.strip().lower()
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "A valid admin name is required")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(422, "A valid email address is required")
    allowed = {"super_admin", "trust_safety_reviewer", "question_content_admin",
               "company_verification_admin", "support_agent", "finance_admin", "compliance_officer"}
    if body.admin_role not in allowed:
        raise HTTPException(422, "Unsupported admin staff role")
    if one("SELECT id FROM users WHERE email=?", (email,)):
        raise HTTPException(409, "An account with this email already exists")
    from main import hash_pw
    salt = secrets.token_hex(16)
    user_id = run("INSERT INTO users(email,name,salt,pw_hash,created) VALUES (?,?,?,?,?)",
                  (email, name, salt, hash_pw(body.password, salt), time.time()))
    run("INSERT INTO user_roles(user_id,role) VALUES (?, 'admin')", (user_id,))
    run("""INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at)
           VALUES (?,?,?,?)""", (user_id, body.admin_role, user["id"], time.time()))
    audit(user["id"], "admin_staff_created", str(user_id) + ":" + email + ":" + body.admin_role)
    return {"user_id": user_id, "name": name, "email": email, "admin_role": body.admin_role}


@router.put("/api/admin/console/staff/{user_id}")
def admin_console_set_staff_role(user_id: int, body: AdminStaffRoleInput, user=Depends(current_user)):
    admin_only(user, "staff")
    allowed={"super_admin","trust_safety_reviewer","question_content_admin","company_verification_admin","support_agent","finance_admin","compliance_officer"}
    if body.admin_role not in allowed: raise HTTPException(422,"Unsupported admin staff role")
    if user_id==user["id"] and body.admin_role!="super_admin": raise HTTPException(409,"You cannot remove your own Super Admin role")
    target=one("SELECT user_id FROM user_roles WHERE user_id=? AND role='admin'",(user_id,))
    if not target: raise HTTPException(404,"Provisioned admin staff account not found")
    run("""INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at) VALUES (?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET admin_role=excluded.admin_role,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
        (user_id,body.admin_role,user["id"],time.time()))
    audit(user["id"],"admin_staff_role_changed",str(user_id)+":"+body.admin_role)
    return {"user_id":user_id,"admin_role":body.admin_role}

@router.get("/api/admin/console/me")
def admin_console_me(user=Depends(current_user)):
    require_role(user, "admin")
    row=one("SELECT admin_role FROM admin_staff_roles WHERE user_id=?",(user["id"],))
    staff_role=row["admin_role"] if row else "super_admin"
    scopes=sorted([scope for scope,roles in ADMIN_SCOPE_ROLES.items() if staff_role in roles])
    return {"user_id":user["id"],"admin_role":staff_role,"scopes":scopes}
