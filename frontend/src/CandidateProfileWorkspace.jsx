import { useEffect, useState } from "react";
import { api } from "./api.js";

const EMPTY_PROFILE = {
  name: "",
  email: "",
  headline: "",
  phone: "",
  location: "",
  summary: "",
  linkedin: "",
  github: "",
  portfolio: "",
  education: [],
  experience: [],
  projects: [],
  skills: [],
  certifications: [],
  achievements: [],
  languages: [],
  references: [],
  volunteer: [],
  preferences: { job_types: [], work_mode: "", locations: [] },
};

const SECTIONS = [
  ["personal", "Personal Details"],
  ["snapshot", "Career Snapshot"],
  ["experience", "Work Experience"],
  ["education", "Education"],
  ["skills", "Key Skills"],
  ["preferences", "Availability & Job Preferences"],
  ["certifications", "Training & Certification"],
  ["projects", "Projects"],
  ["languages", "Language Skills"],
  ["references", "References"],
  ["club", "Club & Volunteer Experience"],
  ["links", "Portfolio & Social Links"],
];

const DEFAULTS = {
  education: { institution: "", degree: "", field: "", location: "", start_year: "", end_year: "", grade: "", details: "" },
  experience: { company: "", title: "", location: "", employment_type: "", start_date: "", end_date: "", current: false, duration_months: 0, description: "", achievements: [], technologies: [] },
  projects: { name: "", url: "" },
  skills: { name: "", category: "Other", status: "claimed" },
  certifications: { name: "", issuer: "", issue_date: "", expiry_date: "", credential_id: "", credential_url: "" },
  achievements: { title: "", description: "", date: "" },
  languages: { name: "", proficiency: "" },
  references: { name: "", relationship: "", email: "", phone: "" },
  volunteer: { organization: "", role: "", start_date: "", end_date: "", description: "" },
};

const normalizeUrl = (value) => {
  const url = String(value || "").trim();
  if (!url) return "";
  return /^https?:\/\//i.test(url) ? url.replace(/^http:/i, "https:") : `https://${url.replace(/^www\./i, "")}`;
};

function Field({ label, value, onChange, flagged, type = "text", multiline = false, ...props }) {
  const Control = multiline ? "textarea" : "input";
  return (
    <label>
      {label}
      {flagged && <small className="profile-check-this">Check this</small>}
      <Control
        type={multiline ? undefined : type}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
        {...props}
      />
    </label>
  );
}

function CandidateProfileWorkspace({ onSaved, draftMode = false }) {
  const [profile, setProfile] = useState(EMPTY_PROFILE);
  const [method, setMethod] = useState("");
  const [review, setReview] = useState({ low_confidence_fields: [], missing_fields: [] });
  const [completeness, setCompleteness] = useState({ total: 0, sections: {} });
  const [editing, setEditing] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const result = await api(draftMode ? "/candidate/profile/draft" : "/candidate/profile");
        if (!active) return;
        const data = result.profile || result.draft || {};
        setProfile({
          ...EMPTY_PROFILE,
          ...data,
          projects: (data.projects || []).map(({ name = "", url = "", project_url = "" }) => ({
            name,
            url: url || project_url,
          })),
          skills: (data.skills || []).map((skill) => ({ ...skill, status: "claimed" })),
          preferences: { ...EMPTY_PROFILE.preferences, ...(data.preferences || {}) },
        });
        setMethod(result.method || "");
        setReview(result.extraction || { low_confidence_fields: [], missing_fields: [] });
        setCompleteness(result.completeness || { total: 0, sections: {} });
      } catch (loadError) {
        setError(loadError.message || "Could not load profile");
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    return () => { active = false; };
  }, [draftMode]);

  const update = (key, value) => setProfile((old) => ({ ...old, [key]: value }));
  const updateItem = (key, index, field, value) => setProfile((old) => ({
    ...old,
    [key]: old[key].map((item, current) => current === index ? { ...item, [field]: value } : item),
  }));
  const addItem = (key) => update(key, [...(profile[key] || []), { ...DEFAULTS[key] }]);
  const removeItem = (key, index) => update(key, profile[key].filter((_, current) => current !== index));

  const save = async (confirm = false) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (draftMode) {
        const saved = await api("/candidate/profile/draft", { method: "PUT", body: profile });
        if (saved.completeness) setCompleteness(saved.completeness);
        if (saved.extraction) setReview(saved.extraction);
        if (confirm) {
          await api("/candidate/profile/confirm", { method: "POST", body: {} });
          setMessage("Profile saved.");
          await onSaved?.();
        } else {
          setMessage("Draft saved.");
        }
      } else {
        const saved = await api("/candidate/profile", { method: "PUT", body: profile });
        if (saved.completeness) setCompleteness(saved.completeness);
        setMessage("Profile saved.");
        await onSaved?.();
      }
    } catch (saveError) {
      setError(saveError.message || "Could not save profile");
    } finally {
      setBusy(false);
    }
  };

  const flagged = (section, field = "") => {
    const targets = {
      personal: ["name", "email", "phone", "location"],
      snapshot: ["headline", "summary"],
      experience: ["experience"],
      education: ["education"],
      skills: ["skills"],
      preferences: ["preferences"],
      certifications: ["certifications"],
      projects: ["projects"],
      languages: ["languages"],
      references: ["references"],
      club: ["achievements", "volunteer"],
      links: ["linkedin", "github", "portfolio"],
    }[section] || [];
    const path = field || targets[0];
    return review.low_confidence_fields.some((entry) =>
      entry === path || entry.startsWith(`${path}[`) || entry.startsWith(`${path}.`) ||
      (!field && targets.some((target) => entry.startsWith(`${target}[`) || entry.startsWith(`${target}.`))),
    );
  };

  const field = (label, value, updateValue, section, key, props = {}) => (
    <Field
      key={key || label}
      label={label}
      value={value}
      onChange={updateValue}
      flagged={flagged(section, key)}
      {...props}
    />
  );

  const collectionEditor = (key, section, labels, fields) => (
    <div className="profile-list-editor">
      {(profile[key] || []).map((item, index) => (
        <div className="repeat-card" key={`${key}-${index}`}>
          <div className="repeat-top">
            <strong>{labels} {index + 1}</strong>
            <button type="button" className="text-danger" onClick={() => removeItem(key, index)}>Remove</button>
          </div>
          <div className="form-grid">
            {fields.map(([name, label, type]) => field(
              label,
              item[name],
              (value) => updateItem(key, index, name, value),
              section,
              `${key}[${index}].${name}`,
              { type: type || "text" },
            ))}
          </div>
          {key === "experience" && (
            <>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={!!item.current}
                  onChange={(event) => updateItem(key, index, "current", event.target.checked)}
                />
                Current role
              </label>
              {field("Description", item.description, (value) => updateItem(key, index, "description", value), section, `${key}[${index}].description`, { multiline: true, rows: 3 })}
            </>
          )}
          {key === "achievements" && field("Description", item.description, (value) => updateItem(key, index, "description", value), section, `${key}[${index}].description`, { multiline: true, rows: 2 })}
          {key === "volunteer" && field("Description", item.description, (value) => updateItem(key, index, "description", value), section, `${key}[${index}].description`, { multiline: true, rows: 2 })}
        </div>
      ))}
      <button type="button" className="btn ghost sm" onClick={() => addItem(key)}>+ Add {labels.toLowerCase()}</button>
      {profile[key]?.length === 0 && <p className="empty-inline">Add {labels.toLowerCase()} when you are ready.</p>}
    </div>
  );

  const content = {
    personal: (
      <div className="form-grid">
        {field("Profile name", profile.name, (value) => update("name", value), "personal", "name", { maxLength: 120 })}
        {field("CV email", profile.email, (value) => update("email", value), "personal", "email", { type: "email" })}
        {field("Phone", profile.phone, (value) => update("phone", value), "personal", "phone")}
        {field("Location", profile.location, (value) => update("location", value), "personal", "location")}
      </div>
    ),
    snapshot: (
      <>
        {field("Professional headline", profile.headline, (value) => update("headline", value), "snapshot", "headline", { maxLength: 180 })}
        {field("Summary", profile.summary, (value) => update("summary", value), "snapshot", "summary", { multiline: true, rows: 4, maxLength: 3000 })}
      </>
    ),
    experience: collectionEditor("experience", "experience", "Experience", [
      ["title", "Role / title"], ["company", "Company"], ["location", "Location"],
      ["employment_type", "Employment type"], ["start_date", "Start date"], ["end_date", "End date"],
    ]),
    education: collectionEditor("education", "education", "Education", [
      ["institution", "Institution"], ["degree", "Degree"], ["field", "Field of study"],
      ["start_year", "Start year"], ["end_year", "End year"], ["grade", "Grade"],
    ]),
    skills: (
      <div className="profile-list-editor">
        {(profile.skills || []).map((item, index) => (
          <div className="repeat-card form-grid" key={`skill-${index}`}>
            {field("Skill", item.name, (value) => updateItem("skills", index, "name", value), "skills", `skills[${index}].name`)}
            <label>
              Category
              <select value={item.category || "Other"} onChange={(event) => updateItem("skills", index, "category", event.target.value)}>
                {["Language", "Frontend", "Backend", "Database", "Tool", "Other"].map((category) => <option key={category}>{category}</option>)}
              </select>
            </label>
            <button type="button" className="text-danger" onClick={() => removeItem("skills", index)}>Remove</button>
          </div>
        ))}
        <button type="button" className="btn ghost sm" onClick={() => addItem("skills")}>+ Add skill</button>
        {profile.skills.length === 0 && <p className="empty-inline">Add skills you can discuss from your own experience.</p>}
      </div>
    ),
    preferences: (
      <>
        <label>
          Preferred work mode
          <select value={profile.preferences.work_mode || ""} onChange={(event) => update("preferences", { ...profile.preferences, work_mode: event.target.value })}>
            {["", "Any", "Remote", "Hybrid", "On-site"].map((mode) => <option key={mode} value={mode}>{mode || "Not specified"}</option>)}
          </select>
        </label>
        <div className="preference-options">
          {["Internship", "Full-time", "Part-time", "Contract", "Temporary"].map((type) => (
            <label className="check" key={type}>
              <input
                type="checkbox"
                checked={profile.preferences.job_types.includes(type)}
                onChange={(event) => update("preferences", {
                  ...profile.preferences,
                  job_types: event.target.checked
                    ? [...profile.preferences.job_types, type]
                    : profile.preferences.job_types.filter((item) => item !== type),
                })}
              />
              {type}
            </label>
          ))}
        </div>
        {field("Preferred locations, comma-separated", profile.preferences.locations.join(", "), (value) => update("preferences", {
          ...profile.preferences,
          locations: value.split(",").map((item) => item.trim()).filter(Boolean),
        }), "preferences", "preferences.locations")}
      </>
    ),
    certifications: collectionEditor("certifications", "certifications", "Certification", [
      ["name", "Certification"], ["issuer", "Issuing organization"],
      ["issue_date", "Issue date"], ["expiry_date", "Expiry date"],
      ["credential_id", "Credential ID"], ["credential_url", "Credential URL"],
    ]),
    projects: collectionEditor("projects", "projects", "Project", [["name", "Project title"], ["url", "Project link", "url"]]),
    languages: collectionEditor("languages", "languages", "Language", [["name", "Language"], ["proficiency", "Proficiency"]]),
    references: collectionEditor("references", "references", "Reference", [
      ["name", "Name"], ["relationship", "Relationship"], ["email", "Email", "email"], ["phone", "Phone"],
    ]),
    club: (
      <>
        <h3>Clubs, awards & achievements</h3>
        {collectionEditor("achievements", "club", "Achievement", [["title", "Title"], ["date", "Date"]])}
        <h3>Volunteer experience</h3>
        {collectionEditor("volunteer", "club", "Volunteer role", [
          ["organization", "Organization"], ["role", "Role"], ["start_date", "Start date"], ["end_date", "End date"],
        ])}
      </>
    ),
    links: (
      <div className="form-grid">
        {field("LinkedIn URL", profile.linkedin, (value) => update("linkedin", value), "links", "linkedin", { placeholder: "https://linkedin.com/in/..." })}
        {field("GitHub URL", profile.github, (value) => update("github", value), "links", "github", { placeholder: "https://github.com/..." })}
        {field("Portfolio URL", profile.portfolio, (value) => update("portfolio", value), "links", "portfolio", { placeholder: "https://..." })}
      </div>
    ),
  };

  const summary = (key) => {
    if (key === "personal") return profile.name || profile.email || "Add your contact details";
    if (key === "snapshot") return profile.headline || profile.summary || "Add a headline and short summary";
    if (key === "experience") return `${profile.experience.length} role${profile.experience.length === 1 ? "" : "s"}`;
    if (key === "education") return `${profile.education.length} education entr${profile.education.length === 1 ? "y" : "ies"}`;
    if (key === "skills") return `${profile.skills.length} claimed skill${profile.skills.length === 1 ? "" : "s"}`;
    if (key === "preferences") return profile.preferences.work_mode || "Add availability and preferences";
    if (key === "certifications") return `${profile.certifications.length} certification${profile.certifications.length === 1 ? "" : "s"}`;
    if (key === "projects") return `${profile.projects.length} project${profile.projects.length === 1 ? "" : "s"}`;
    if (key === "languages") return `${profile.languages.length} language${profile.languages.length === 1 ? "" : "s"}`;
    if (key === "references") return profile.references.length ? `${profile.references.length} reference${profile.references.length === 1 ? "" : "s"}` : "Add references if you want to share them";
    if (key === "club") return profile.achievements.length + profile.volunteer.length
      ? `${profile.achievements.length + profile.volunteer.length} club, volunteer or achievement entr${profile.achievements.length + profile.volunteer.length === 1 ? "y" : "ies"}`
      : "Add a club, volunteer role or achievement if you have one";
    return [profile.linkedin, profile.github, profile.portfolio].filter(Boolean).length
      ? "Social and portfolio links added"
      : "Add portfolio or social links";
  };

  const preview = (key) => {
    if (key === "personal") return <p>{[profile.name, profile.email, profile.phone, profile.location].filter(Boolean).join(" · ") || summary(key)}</p>;
    if (key === "snapshot") return <p>{profile.summary || summary(key)}</p>;
    if (key === "experience") return profile.experience.length
      ? profile.experience.map((item, index) => <p key={`experience-preview-${index}`}><strong>{item.title}</strong>{item.company && ` · ${item.company}`}{item.start_date && ` · ${item.start_date}–${item.current ? "Present" : item.end_date || ""}`}</p>)
      : <p>{summary(key)}</p>;
    if (key === "education") return profile.education.length
      ? profile.education.map((item, index) => <p key={`education-preview-${index}`}><strong>{item.degree || "Education"}</strong>{item.institution && ` · ${item.institution}`}{item.end_year && ` · ${item.end_year}`}</p>)
      : <p>{summary(key)}</p>;
    if (key === "skills") {
      const groups = profile.skills.reduce((all, item) => {
        const category = item.category || "Other";
        all[category] = [...(all[category] || []), item.name];
        return all;
      }, {});
      return Object.keys(groups).length
        ? Object.entries(groups).map(([category, names]) => <p key={`skill-group-${category}`}><strong>{category}</strong> · {names.join(", ")}</p>)
        : <p>{summary(key)}</p>;
    }
    if (key === "preferences") return <p>{[
      profile.preferences.work_mode,
      ...profile.preferences.job_types,
      ...profile.preferences.locations,
    ].filter(Boolean).join(" · ") || summary(key)}</p>;
    if (key === "certifications") return profile.certifications.length
      ? profile.certifications.map((item, index) => <p key={`cert-preview-${index}`}><strong>{item.name}</strong>{item.issuer && ` · ${item.issuer}`}</p>)
      : <p>{summary(key)}</p>;
    if (key === "projects") return profile.projects.length
      ? profile.projects.map((item, index) => <p className="profile-project-preview" key={`project-preview-${index}`}>
        <strong>{item.name || "Untitled project"}</strong>
        {item.url && <> · <a href={normalizeUrl(item.url)} target="_blank" rel="noreferrer">Project link</a></>}
      </p>)
      : <p>{summary(key)}</p>;
    if (key === "languages") return profile.languages.length
      ? <p>{profile.languages.map((item) => [item.name, item.proficiency].filter(Boolean).join(" — ")).join(" · ")}</p>
      : <p>{summary(key)}</p>;
    if (key === "references") return profile.references.length
      ? <p>{profile.references.map((item) => [item.name, item.relationship].filter(Boolean).join(" — ")).join(" · ")}</p>
      : <p>{summary(key)}</p>;
    if (key === "club") {
      const entries = [
        ...profile.achievements.map((item) => item.title),
        ...profile.volunteer.map((item) => [item.role, item.organization].filter(Boolean).join(" — ")),
      ].filter(Boolean);
      return entries.length ? <p>{entries.join(" · ")}</p> : <p>{summary(key)}</p>;
    }
    const links = [
      ["LinkedIn", profile.linkedin],
      ["GitHub", profile.github],
      ["Portfolio", profile.portfolio],
    ].filter(([, url]) => url);
    return links.length
      ? <p>{links.map(([label, url]) => <a className="profile-social-link" key={label} href={normalizeUrl(url)} target="_blank" rel="noreferrer">{label}</a>)}</p>
      : <p>{summary(key)}</p>;
  };

  if (loading) return <div className="portal-panel">Loading profile…</div>;

  return (
    <div className="profile-layout">
      <aside className="profile-side-card">
        <div className="avatar-circle">{(profile.name || "C").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase()}</div>
        <h2>{profile.name || "Candidate profile"}</h2>
        <p className="muted">{profile.headline || "Add a professional headline"}</p>
        <div className="completion-row"><span>Profile completeness</span><strong>{completeness.total}%</strong></div>
        <div className="completion-track"><span style={{ width: `${completeness.total}%` }} /></div>
        <div className="profile-mini-stat"><strong>{profile.skills.length}</strong><span>Skills</span></div>
        <div className="profile-mini-stat"><strong>{profile.experience.length}</strong><span>Experience</span></div>
        <div className="profile-mini-stat"><strong>{profile.projects.length}</strong><span>Projects</span></div>
        <p className="muted">Skills are self-reported until verified.</p>
      </aside>
      <form className="profile-editor" onSubmit={(event) => event.preventDefault()}>
        {error && <p className="err" role="alert">{error}</p>}
        {message && <p className="success-note" role="status">{message}</p>}
        {draftMode && method.startsWith("cv") && (
          <p className="cv-profile-banner">
            Filled from your CV. Skills are self-reported until you verify them. Check the highlighted items, then save.
          </p>
        )}
        {SECTIONS.map(([key, title]) => {
          const sectionMissing = review.missing_fields.includes(title);
          const isFlagged = sectionMissing || flagged(key);
          const pct = completeness.sections[title] ?? 0;
          return (
            <section className="portal-panel profile-section-card" key={key}>
              <div className="profile-section-heading">
                <div>
                  <h2>{title} <span className="profile-section-pct">{pct}%</span></h2>
                  <div className="profile-section-preview">{preview(key)}</div>
                  {isFlagged && <small className="profile-check-this">Check this</small>}
                </div>
                <button type="button" className="btn ghost sm" onClick={() => setEditing(editing === key ? "" : key)}>
                  {editing === key ? "Done" : "Edit"}
                </button>
              </div>
              {editing === key && <div className="profile-section-fields">{content[key]}</div>}
            </section>
          );
        })}
        <div className="profile-savebar">
          <span className="muted">{draftMode ? "Your profile stays private until you save it." : "Save when you are done editing."}</span>
          {draftMode && (
            <button type="button" className="btn ghost" disabled={busy} onClick={() => save(false)}>
              {busy ? "Saving…" : "Save draft"}
            </button>
          )}
          <button type="button" className="btn" disabled={busy} onClick={() => save(draftMode)}>
            {busy ? "Saving…" : "Save profile"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default CandidateProfileWorkspace;
