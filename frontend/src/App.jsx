import { useEffect, useState, useCallback } from "react";
import { api, getToken, setToken } from "./api.js";
import { LangCtx, useT } from "./i18n.js";

const SAMPLE_CV = `Sadia Rahman
House 14, Road 3, Mirpur, Dhaka
sadia.rahman@example.com | +880 1712-345678 | https://github.com/sadia-demo

Education: BSc in Computer Science, final year.
Projects: Sales dashboard in Power BI; customer analysis with pandas and MySQL joins; basic statistics for a class survey.
Skills: SQL, Python, Excel, Git. Presentation and report writing for class projects.
(Synthetic sample CV for demo purposes.)`;

/* ---------- small helpers ---------- */
const STEPS = ["profile", "gap", "assess", "learn", "retest", "passport"];
const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

function Err({ e }) {
  const t = useT();
  if (!e) return null;
  return <p className="err" role="alert">{t(e.code) !== e.code ? t(e.code) : e.message || t("error")}</p>;
}

function Bar({ score, threshold, unverified }) {
  return (
    <div className={"bar" + (unverified ? " bar-un" : "")} aria-label={unverified ? "unverified" : `${score}%`}>
      {!unverified && <span className="bar-fill" style={{ width: `${score}%` }} />}
      {threshold != null && <span className="bar-tick" style={{ left: `${threshold}%` }} title={`${threshold}%`} />}
    </div>
  );
}

function TrustChip({ trust }) {
  const t = useT();
  const cls = trust === "High" ? "chip-ok" : trust === "Medium" ? "chip-mid" : "chip-rev";
  return <span className={"chip " + cls}>{t("trusts")[trust]}</span>;
}

/* ---------- landing + auth ---------- */
function BridgeRail() {
  const t = useT();
  const xs = [80, 230, 380, 530, 680, 830];
  return (
    <svg className="rail" viewBox="0 0 910 150" role="img" aria-label="SkillBridge loop">
      <line x1="30" y1="86" x2="880" y2="86" className="rail-deck" />
      {xs.slice(0, -1).map((x, i) => (
        <path key={i} d={`M${x} 86 Q${(x + xs[i + 1]) / 2} 14 ${xs[i + 1]} 86`} className="rail-cable" />
      ))}
      {xs.map((x, i) => (
        <g key={i}>
          <circle cx={x} cy="86" r="9" className="rail-node" />
          <text x={x} y="116" textAnchor="middle" className="rail-label">{t("loop")[i]}</text>
          <text x={x} y="136" textAnchor="middle" className="rail-sub">{t("loopSub")[i]}</text>
        </g>
      ))}
    </svg>
  );
}

function Landing({ onAuthed }) {
  const t = useT();
  const [mode, setMode] = useState(location.hash === "#signin" ? "login" : "register");
  const [f, setF] = useState({ name: "", email: "", password: "", role: "candidate" });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const isRegister = mode === "register";
  useEffect(() => {
    const syncMode = () => { if (location.hash === "#signin") setMode("login"); if (location.hash === "#signup") setMode("register"); };
    window.addEventListener("hashchange", syncMode);
    return () => window.removeEventListener("hashchange", syncMode);
  }, []);
  const changeMode = (next) => { setErr(null); setMode(next); history.replaceState(null, "", next === "login" ? "#signin" : "#signup"); };
  const submit = async (e) => {
    e.preventDefault(); setErr(null); setBusy(true);
    try {
      const r = await api(isRegister ? "/auth/register" : "/auth/login", { method: "POST", body: isRegister ? f : { email: f.email, password: f.password } });
      setToken(r.token); onAuthed();
    } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <main className="sb-marketing">
      <section className="sb-hero" id="top">
        <div className="sb-hero-copy">
          <div className="sb-eyebrow"><span className="sb-eyebrow-dot"/> {isRegister ? "YOUR NEXT CHAPTER STARTS HERE" : "WELCOME BACK TO SKILLBRIDGE"}</div>
          <h1>{isRegister ? <>Build skills.<br/><em>Prove your potential.</em></> : <>Good to see<br/><em>you again.</em></>}</h1>
          <p className="sb-hero-lead">{isRegister ? "A clearer path from what you know to what you can do. Build your skill profile, take practical assessments and share verified evidence of your abilities." : "Sign in to continue your skill journey, review your progress and pick up right where you left off."}</p>
          <div className="sb-hero-points">
            <div><span className="sb-point-icon">01</span><span><b>Show what you can do</b><small>Turn skills into structured evidence.</small></span></div>
            <div><span className="sb-point-icon">02</span><span><b>Know what to improve</b><small>See where your strengths can grow.</small></span></div>
            <div><span className="sb-point-icon">03</span><span><b>Move forward with confidence</b><small>Share a skills passport when you're ready.</small></span></div>
          </div>
          <div className="sb-hero-foot"><span className="sb-avatar-stack"><i>C</i><i>R</i><i>S</i></span><span>One platform for candidates and hiring teams</span></div>
        </div>
        <div className="sb-auth-wrap" id="signup">
          <div className="sb-auth-glow"/>
          <div className="sb-auth-card">
            <div className="sb-auth-card-top"><span className="sb-auth-mark">S</span><span className="sb-auth-secure"><span/> Secure account access</span></div>
            <div className="sb-auth-heading">
              <div className="sb-kicker">{isRegister ? "GET STARTED — IT'S FREE TO JOIN" : "YOUR SKILLBRIDGE ACCOUNT"}</div>
              <h2>{isRegister ? "Create your account" : "Sign in to SkillBridge"}</h2>
              <p>{isRegister ? "A few details to get your journey moving." : "Enter your details to access your workspace."}</p>
            </div>
            <div className="sb-auth-tabs" role="tablist" aria-label="Account access">
              <button type="button" role="tab" aria-selected={isRegister} className={isRegister ? "active" : ""} onClick={() => changeMode("register")}>Create account</button>
              <button type="button" role="tab" aria-selected={!isRegister} className={!isRegister ? "active" : ""} onClick={() => changeMode("login")}>Sign in</button>
            </div>
            <form className="sb-auth-form" onSubmit={submit}>
              {isRegister && <>
                <label>Full name<input value={f.name} onChange={set("name")} required minLength={2} autoComplete="name" placeholder="e.g. Samira Ahmed"/></label>
                <div className="sb-field-label">I’m joining as</div>
                <div className="sb-role-choices">
                  <button type="button" className={f.role === "candidate" ? "selected" : ""} onClick={() => setF({...f,role:"candidate"})}><span className="sb-role-icon">↗</span><span><b>Candidate</b><small>Build my skill profile</small></span><i>{f.role === "candidate" ? "✓" : ""}</i></button>
                  <button type="button" className={f.role === "company" ? "selected" : ""} onClick={() => setF({...f,role:"company"})}><span className="sb-role-icon">⌂</span><span><b>Company</b><small>Hire with confidence</small></span><i>{f.role === "company" ? "✓" : ""}</i></button>
                </div>
              </>}
              <label>Email address<input type="email" value={f.email} onChange={set("email")} required autoComplete="email" placeholder="you@example.com"/></label>
              <label>Password<div className="sb-password-field"><input type={showPassword ? "text" : "password"} value={f.password} onChange={set("password")} required minLength={8} autoComplete={isRegister ? "new-password" : "current-password"} placeholder="At least 8 characters"/><button type="button" onClick={() => setShowPassword(v=>!v)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button></div></label>
              <Err e={err}/>
              <button className="sb-submit" disabled={busy}>{busy ? "Please wait…" : isRegister ? "Create my account" : "Sign in securely"}<span>→</span></button>
            </form>
            <div className="sb-auth-switch">{isRegister ? "Already have an account?" : "New to SkillBridge?"} <button type="button" onClick={() => changeMode(isRegister ? "login" : "register")}>{isRegister ? "Sign in" : "Create an account"}</button></div>
            <div className="sb-auth-legal">By continuing, you agree to use SkillBridge responsibly and provide accurate information.</div>
          </div>
          <div className="sb-auth-note"><span>✦</span> A more meaningful way to demonstrate what you know.</div>
        </div>
      </section>
      <section className="sb-proof-strip" aria-label="SkillBridge journey">
        <div className="sb-proof-title">A journey built around evidence</div>
        <div className="sb-journey"><span><i>01</i> Profile</span><b>—</b><span><i>02</i> Assess</span><b>—</b><span><i>03</i> Improve</span><b>—</b><span><i>04</i> Reassess</span><b>—</b><span><i>05</i> Share</span></div>
      </section>
      <section className="sb-value-section" id="how-it-works">
        <div className="sb-section-intro"><div className="sb-kicker">ONE BRIDGE. TWO SIDES.</div><h2>Make skills easier to see.<br/><em>And easier to trust.</em></h2><p>SkillBridge brings candidate development and evidence-led hiring into one connected experience.</p></div>
        <div className="sb-value-grid">
          <article className="sb-value-card"><div className="sb-value-number">01 / FOR CANDIDATES</div><div className="sb-value-icon">↗</div><h3>Turn your potential into proof.</h3><p>Build a structured profile, understand skill gaps, practise through assessments and share your skills passport with control.</p><button type="button" onClick={() => {setF(v=>({...v,role:"candidate"}));changeMode("register");document.getElementById("signup")?.scrollIntoView({behavior:"smooth"});}}>Start as a candidate <span>→</span></button></article>
          <article className="sb-value-card sb-value-card-dark"><div className="sb-value-number">02 / FOR COMPANIES</div><div className="sb-value-icon">⌂</div><h3>Hire with a clearer picture.</h3><p>Organise roles, review applicants, manage hiring stages and use structured assessment evidence to support decisions.</p><button type="button" onClick={() => {setF(v=>({...v,role:"company"}));changeMode("register");document.getElementById("signup")?.scrollIntoView({behavior:"smooth"});}}>Build your hiring workspace <span>→</span></button></article>
        </div>
      </section>
      <footer className="sb-footer"><div><span className="sb-footer-mark">S</span><b>SkillBridge</b><small>Skills. Evidence. Opportunity.</small></div><span>Connecting capability with opportunity.</span><a href="#top">Back to top ↑</a></footer>
    </main>
  );
}
/* ---------- consent ---------- */
function Consent({ lang, onDone }) {
  const t = useT();
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState(null);
  const go = async () => { try { await api("/consent", { method: "POST", body: { lang } }); onDone(); } catch (e) { setErr(e); } };
  return (
    <section className="card narrow">
      <h2>{t("consentTitle")}</h2>
      <ul className="consent">{t("consentItems").map((x, i) => <li key={i}>{x}</li>)}</ul>
      <label className="check"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> {t("consentCheck")}</label>
      <Err e={err} />
      <button className="btn" disabled={!ok} onClick={go}>{t("consentBtn")}</button>
    </section>
  );
}

/* ---------- profile: CV -> skills ---------- */
function Profile({ st, onDone }) {
  const t = useT();
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [res, setRes] = useState(null);
  const [sel, setSel] = useState([]);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const analyse = async () => {
    setErr(null); setBusy(true);
    try {
      const form = new FormData();
      form.append("text", text);
      if (file) form.append("file", file);
      const r = await api("/cv", { method: "POST", form });
      setRes(r); setSel(r.skills.map((s) => s.id));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const confirm = async () => {
    try { await api("/skills", { method: "PUT", body: { skills: sel } }); onDone(); } catch (e) { setErr(e); }
  };
  const toggle = (id) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);

  return (
    <section>
      <h2>{t("profileTitle")}</h2>
      {st.confirmed && !res && (
        <p className="note">{t("claimed")}: {st.confirmed.join(", ") || "-"}</p>
      )}
      <div className="card">
        <p>{t("cvHelp")}</p>
        <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={t("cvPaste")} />
        <div className="row">
          <input type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files[0] || null)} />
          <button type="button" className="btn ghost" onClick={() => { setText(SAMPLE_CV); setFile(null); }}>{t("sampleCv")}</button>
          <button className="btn" onClick={analyse} disabled={busy || (!text && !file)}>{t("analyse")}</button>
        </div>
        <Err e={err} />
      </div>

      {res && (
        <>
          <div className="card">
            <h3>{t("redacted")}</h3>
            <p className="chips">
              {Object.entries(res.redaction).map(([k, n]) => <span key={k} className="chip chip-mid">{n} {t("redactedLabels")[k]}</span>)}
            </p>
            <details><summary>{t("preview")}</summary><pre className="pre">{res.redacted_preview}</pre></details>
          </div>
          <div className="card">
            <h3>{t("confirmTitle")}</h3>
            <p className="note">{t("confirmHelp")} ({t("extractedBy")}: {res.source === "llm" ? t("llm") : t("rules")})</p>
            <ul className="skills">
              {res.taxonomy.map((s) => {
                const found = res.skills.find((x) => x.id === s.id);
                return (
                  <li key={s.id}>
                    <label className="check">
                      <input type="checkbox" checked={sel.includes(s.id)} onChange={() => toggle(s.id)} /> <strong>{s.name}</strong>
                      <span className="chip chip-mid">{t("claimed")}</span>
                    </label>
                    {found && <small className="evid">{t("evidence")}: “{found.evidence}”</small>}
                  </li>
                );
              })}
            </ul>
            <button className="btn" onClick={confirm}>{t("confirmBtn")}</button>
          </div>
        </>
      )}
    </section>
  );
}

/* ---------- gap ---------- */
function Gap({ onStart, refreshKey }) {
  const t = useT();
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { api("/job").then(setD).catch(setErr); }, [refreshKey]);
  if (err) return <Err e={err} />;
  if (!d) return <p>{t("loading")}</p>;
  return (
    <section>
      <h2>{t("gapTitle")}</h2>
      <div className="card">
        <div className="jobhead">
          <h3>{d.job.title}</h3>
          <span className="chip chip-mock">{t("jobMock")}</span>
        </div>
        <ul className="gaps">
          {d.gap.map((g) => (
            <li key={g.skill}>
              <div className="gaprow">
                <strong>{g.name}</strong>
                <span className="muted">{g.claimed ? t("claimedTag") : t("notClaimed")}</span>
                <span className={"status s-" + g.status}>{t("gs")[g.status]}{g.score != null ? ` · ${g.score}%` : ""}</span>
              </div>
              <Bar score={g.score || 0} threshold={g.threshold} unverified={g.status === "unverified"} />
              <small className="muted">{t("threshold")}: {g.threshold}%</small>
            </li>
          ))}
        </ul>
        <p className="note">{t("gapNote")}</p>
        <p className="muted">{t("preferred")}: {d.job.preferred.map((p) => `${p.name}${p.claimed ? " ✓" : ""}`).join(", ")}</p>
        {onStart && <button className="btn" onClick={onStart}>{t("startTest")}</button>}
      </div>
    </section>
  );
}

/* ---------- exam ---------- */
function ExamRun({ initial, onFinished }) {
  if (initial.graphrag) return <GraphRAGExamRun initial={initial} onFinished={onFinished} />;
  const t = useT();
  const [v, setV] = useState(initial);
  const [ans, setAns] = useState("");
  const [out, setOut] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(initial.remaining);
  const aid = v.attempt_id;

  useEffect(() => { const i = setInterval(() => setLeft((x) => Math.max(0, x - 1)), 1000); return () => clearInterval(i); }, []);
  useEffect(() => { setAns(""); setOut(null); }, [v.question?.id]);
  useEffect(() => {
    const onBlur = () => api(`/attempts/${aid}/event`, { method: "POST", body: { type: "blur" } }).catch(() => {});
    window.addEventListener("blur", onBlur);
    return () => window.removeEventListener("blur", onBlur);
  }, [aid]);

  const q = v.question;
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api(`/attempts/${aid}/answer`, { method: "POST", body: { question_id: q.id, answer: String(ans) } });
      setV(r); setLeft(r.remaining);
      if (r.status === "done") onFinished(aid);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const runSql = async () => { try { setOut(await api(`/attempts/${aid}/sql-run`, { method: "POST", body: { sql: ans } })); } catch (e) { setErr(e); } };
  const onPaste = () => api(`/attempts/${aid}/event`, { method: "POST", body: { type: "paste" } }).catch(() => {});

  if (!q) return <p>{t("loading")}</p>;
  return (
    <section className="card exam">
      <div className="examhead">
        <span>{t("q")} {q.index + 1} {t("of")} {q.total}</span>
        <progress value={q.index} max={q.total} />
        <span className={"timer" + (left < 120 ? " low" : "")}>{t("timeLeft")}: {fmt(left)}</span>
      </div>
      <h3 className="qtext">{q.prompt}</h3>
      {q.type === "mcq" ? (
        <fieldset className="opts">
          {q.options.map((o, i) => (
            <label key={i} className={"opt" + (String(ans) === String(i) ? " on" : "")}>
              <input type="radio" name="opt" checked={String(ans) === String(i)} onChange={() => setAns(String(i))} /> {o}
            </label>
          ))}
        </fieldset>
      ) : (
        <>
          <details open><summary>{t("schema")}</summary><pre className="pre">{q.schema}</pre></details>
          <p className="note">{t("sqlHelp")}</p>
          <textarea className="code" rows={6} spellCheck={false} value={ans} onChange={(e) => setAns(e.target.value)} onPaste={onPaste} placeholder="Write your answer..." />
          <p className="muted">{t("pasteNote")}</p>
          {String(q.type).toLowerCase().includes("sql") && <button className="btn ghost" onClick={runSql} disabled={!ans.trim()}>{t("run")}</button>}
          {out && <pre className="pre">{JSON.stringify(out, null, 2)}</pre>}
        </>
      )}
      <Err e={err} />
      <button className="btn" onClick={submit} disabled={busy || String(ans).trim() === ""}>{busy ? t("saving") : t("submit")}</button>
    </section>
  );
}

function GraphRAGExamRun({ initial, onFinished }) {
  const [v, setV] = useState(initial);
  const [ans, setAns] = useState(initial.page?.answers?.[initial.question?.id] || "");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(initial.remaining);
  const [sqlOut, setSqlOut] = useState(null);
  const aid = v.attempt_id;
  const q = v.question;
  const page = v.page || {questions:[], page:0, page_count:0, answers:{}};

  useEffect(() => { const i = setInterval(() => setLeft((x) => Math.max(0, x - 1)), 1000); return () => clearInterval(i); }, []);
  useEffect(() => { setAns(page.answers?.[q?.id] || ""); setSqlOut(null); }, [q?.id, v.question_index]);
  useEffect(() => {
    const onBlur = () => api(`/attempts/${aid}/event`, {method:"POST",body:{type:"blur"}}).catch(()=>{});
    window.addEventListener("blur", onBlur);
    return () => window.removeEventListener("blur", onBlur);
  }, [aid]);

  const saveAnswer = async () => {
    if (!q || !String(ans).trim()) return v;
    const next = await api(`/attempts/${aid}/answer`, {method:"POST",body:{question_id:q.id,answer:String(ans)}});
    setV(next);
    return next;
  };
  const goTo = async (index) => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await saveAnswer();
      const next = await api(`/attempts/${aid}/navigate`, {method:"POST",body:{index}});
      setV(next); setAns(next.page?.answers?.[next.question?.id] || "");
    } catch(e) { setErr(e); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setErr(null);
    try { await saveAnswer(); } catch(e) { setErr(e); } finally { setBusy(false); }
  };
  const runSql = async () => {
    try { setSqlOut(await api(`/attempts/${aid}/sql-run`,{method:"POST",body:{sql:ans}})); } catch(e) { setErr(e); }
  };
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      await saveAnswer();
      const r = await api(`/attempts/${aid}/submit`,{method:"POST"});
      if (r.status === "done") onFinished(aid); else setV(r);
    } catch(e) { setErr(e); } finally { setBusy(false); }
  };
  if (!q) return <p>Loading assessment…</p>;
  const isChoice = ["mcq","multiple choice"].includes(String(q.question_type).toLowerCase());
  const answered = page.answers?.[q.id] != null && String(page.answers[q.id]).trim() !== "";
  const previousIndex = Math.max(0, page.page * 5 - 1);
  const nextIndex = Math.min(v.total - 1, (page.page + 1) * 5);
  return (
    <section className="card exam gr-exam">
      <div className="examhead">
        <span>Question {v.answered} of {v.total} answered</span>
        <progress value={v.answered} max={v.total} />
        <span className={"timer" + (left < 120 ? " low" : "")}>Time left: {fmt(left)}</span>
      </div>
      <div className="gr-exam-meta">
        <span>{v.total} questions · {Math.ceil(v.duration_seconds / 60)} minutes allocated</span>
        <span>Fundamental + CV skills</span>
      </div>
      <div className="gr-question-table">
        <table>
          <thead><tr><th>#</th><th>Question / Topic</th><th>Level</th><th>Type</th><th>Status</th></tr></thead>
          <tbody>{page.questions.map((item) => (
            <tr key={item.id} className={item.id === q.id ? "selected" : ""} onClick={() => goTo(item.question_number - 1)}>
              <td>{item.question_number}</td>
              <td><strong>{item.assessment_topic}</strong><small>{item.question_text}</small></td>
              <td>{item.difficulty_level}</td><td>{item.question_type}</td>
              <td>{page.answers?.[item.id] ? "Answered" : "Pending"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="gr-question-current">
        <div className="gr-question-label">
          <span>{q.assessment_section === "cv" ? "CV-BASED" : "FUNDAMENTAL"} · {q.assessment_topic}</span>
          <span>{q.difficulty_level} · {q.question_type}</span>
        </div>
        <h3 className="qtext">{q.prompt}</h3>
        {isChoice ? (
          <fieldset className="opts">
            {q.options.map((option, index) => (
              <label key={index} className={"opt" + (String(ans) === String(option) ? " on" : "")}>
                <input type="radio" name={"gr-option-"+q.id} checked={String(ans) === String(option)} onChange={() => setAns(String(option))} /> {option}
              </label>
            ))}
          </fieldset>
        ) : (
          <>
            {String(q.question_type).toLowerCase().includes("sql")&&<details open><summary>SQL schema</summary><pre className="pre">{q.schema||"Schema supplied by the question author."}</pre></details>}
            <textarea className="code" rows={7} spellCheck={false} value={ans} onChange={(e)=>setAns(e.target.value)}
              onPaste={()=>api(`/attempts/${aid}/event`,{method:"POST",body:{type:"paste"}}).catch(()=>{})}
              placeholder={String(q.question_type).toLowerCase().includes("sql") ? "SELECT ..." : "Write your explanation, scenario response or code here…"} />
            {String(q.question_type).toLowerCase().includes("sql") && <button className="btn ghost" onClick={runSql} disabled={!ans.trim()}>Run SQL</button>}
            {sqlOut && <pre className="pre">{JSON.stringify(sqlOut,null,2)}</pre>}
          </>
        )}
      </div>
      <div className="gr-exam-actions">
        <button className="btn ghost" onClick={()=>goTo(previousIndex)} disabled={page.page===0 || busy}>Previous 5</button>
        <span>Page {page.page+1} of {page.page_count}</span>
        <button className="btn ghost" onClick={()=>goTo(nextIndex)} disabled={!page.has_next || busy}>Next 5</button>
        <button className="btn ghost" onClick={save} disabled={busy || !String(ans).trim()}>Save answer</button>
        <button className="btn" onClick={submit} disabled={busy || v.answered + (answered ? 0 : (String(ans).trim() ? 1 : 0)) < v.total}>
          {busy ? "Saving…" : "Submit assessment"}
        </button>
      </div>
      <p className="muted">Answers are saved to your attempt. Correct answers and explanations remain hidden until submission. Coding, SQL, scenario and explanation responses may require human review.</p>
      <Err e={err} />
    </section>
  );
}

function Result({ aid, next, nextLabel }) {
  const t = useT();
  const [r, setR] = useState(null);
  const [reviewPage,setReviewPage]=useState(0);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { api(`/attempts/${aid}/result`).then(setR).catch(setErr); }, [aid]);
  if (err) return <Err e={err} />;
  if (!r) return <p>{t("loading")}</p>;
  const sendAppeal = async () => { try { await api(`/attempts/${aid}/appeal`, { method: "POST", body: { note } }); setSent(true); } catch (e) { setErr(e); } };
  return (
    <section>
      <h2>{t("resultTitle")}</h2>
      <div className="card result">
        <div className="bignum"><span>{r.overall==null?"Pending":r.overall}</span><small>{r.overall==null?"review":"%"}</small></div>
        <dl className="facts">
          <div><dt>{t("level")}</dt><dd>{t("levels")[r.level]}</dd></div>
          <div><dt>{t("trust")}</dt><dd><TrustChip trust={r.trust} /></dd></div>
          <div><dt>{t("date")}</dt><dd>{r.date}</dd></div>
        </dl>
      </div>
      <div className="card">
        <h3>{t("topics")}</h3>
        <ul className="gaps">
          {Object.entries(r.topics).map(([k, v]) => (
            <li key={k}>
              <div className="gaprow"><strong>{k}</strong><span className="muted">{v.score==null?"Pending review":t("levels")[v.level]}</span><span>{v.score==null?"—":v.score+"%"}</span></div>
              <Bar score={v.score??0} />
            </li>
          ))}
        </ul>
        <p className="muted">{t("percentile")}</p>
        {r.manual_review_required&&<p className="note">Some coding, SQL, scenario or explanation responses are awaiting human review. The displayed score covers automatically gradable questions only.</p>}
      </div>
      {r.question_results?.length>0&&<div className="card gr-review">
        <h3>Question and answer review</h3>
        <p className="muted">Correct answers are revealed only after submission. Five questions are shown per page.</p>
        <div className="gr-question-table"><table><thead><tr><th>#</th><th>Question</th><th>Your answer</th><th>Expected answer / review</th><th>Status</th></tr></thead><tbody>
          {r.question_results.slice(reviewPage*5,reviewPage*5+5).map((item,index)=><tr key={item.question_id}><td>{reviewPage*5+index+1}</td><td>{item.question}</td><td>{item.answer||"No answer"}</td><td>{item.correct_answer||"Pending human review"}</td><td>{item.review_status}</td></tr>)}
        </tbody></table></div>
        <div className="gr-exam-actions"><button className="btn ghost" onClick={()=>setReviewPage(x=>Math.max(0,x-1))} disabled={reviewPage===0}>Previous 5</button><span>Page {reviewPage+1} of {Math.ceil(r.question_results.length/5)}</span><button className="btn ghost" onClick={()=>setReviewPage(x=>Math.min(Math.ceil(r.question_results.length/5)-1,x+1))} disabled={(reviewPage+1)*5>=r.question_results.length}>Next 5</button></div>
      </div>}
      <div className="card quiet">
        <p>{t("trustNote")}</p>
        {r.trust_reasons.length > 0 && <ul>{r.trust_reasons.map((x) => <li key={x.key}>{x.n} × {t("reasonLabels")[x.key]}</li>)}</ul>}
        <h3>{t("appealTitle")}</h3>
        {r.appeal || sent ? <p className="note">{t("appealSent")}</p> : (
          <div className="row">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("appealPh")} />
            <button className="btn ghost" disabled={note.trim().length < 3} onClick={sendAppeal}>{t("appealBtn")}</button>
          </div>
        )}
      </div>
      {next && <button className="btn" onClick={next}>{nextLabel}</button>}
    </section>
  );
}

function Uplift() {
  const t = useT();
  const [u, setU] = useState(null);
  useEffect(() => { api("/uplift").then(setU).catch(() => {}); }, []);
  if (!u || !u.available) return null;
  return (
    <div className="card">
      <h3>{t("upliftTitle")}</h3>
      <p className="bignum small"><span>{u.overall.delta >= 0 ? "+" : ""}{u.overall.delta}</span><small> pts ({u.overall.pre}% → {u.overall.post}%)</small></p>
      <ul className="gaps">
        {Object.entries(u.topics).map(([k, v]) => (
          <li key={k}>
            <div className="gaprow"><strong>{k}</strong><span>{t("pre")} {v.pre}% → {t("post")} {v.post}%</span><span>{v.delta >= 0 ? "+" : ""}{v.delta}</span></div>
            <Bar score={v.post} threshold={v.pre} />
          </li>
        ))}
      </ul>
      <p className="muted">{t("upliftNote")}</p>
    </div>
  );
}

function Exam({ kind, st, refresh, afterResult, afterLabel }) {
  const t = useT();
  const [active, setActive] = useState(null);
  const [err, setErr] = useState(null);
  const done = st.attempts.filter((a) => a.status === "done");
  const mine = kind === "pre" ? done[0] : done.length > 1 ? done[done.length - 1] : null;
  const act = st.attempts.find((a) => a.status === "active");
  const start = useCallback(async () => {
    setErr(null);
    try {
      const blueprint = await api("/assessment/blueprint", {method:"POST"});
      if (blueprint.status !== "ready") {
        const missing = (blueprint.shortages || []).slice(0, 5).map((x) => `${x.topic} · ${x.difficulty}: ${x.available_selected}/${x.required}`).join("; ");
        throw new Error("The approved question bank is not ready yet. " + (missing || "Add and approve the required questions in Admin → Question Bank."));
      }
      const v = await api("/attempts", {method:"POST",body:{blueprint_id:blueprint.blueprint_id}});
      if (v.status === "active") setActive(v); else refresh();
    } catch (e) { setErr(e); }
  }, [refresh]);
  const finished = async () => { setActive(null); await refresh(); };

  if (active) return <ExamRun initial={active} onFinished={finished} />;
  if (act && (act.kind === kind)) return <section className="card narrow"><button className="btn" onClick={start}>{t("resume")}</button><Err e={err} /></section>;
  if (mine) return (
    <>
      <Result aid={mine.id} next={afterResult} nextLabel={afterLabel} />
      {kind === "post" && <Uplift />}
    </>
  );
  if (kind === "post" && done.length === 0) return <p className="note">{t("needPre")}</p>;
  return (
    <section className="card narrow">
      <h2>{kind === "pre" ? t("startTest") : t("steps").retest}</h2>
      <p>{kind === "pre" ? t("testIntro") : t("retestIntro")}</p>
      <p className="muted">This assessment uses 25 fundamental questions plus five questions for every CV skill you confirm. Analyse your CV and confirm the detected technologies first. The question bank must also contain enough approved questions for every topic, difficulty and question format.</p>
      <Err e={err} />
      <button className="btn" onClick={start}>{t("startTest")}</button>
    </section>
  );
}

/* ---------- learning path ---------- */
function Learn({ onNext }) {
  const [data,setData]=useState(null),[error,setError]=useState(""),[busy,setBusy]=useState("");
  const load=async()=>{try{setData(await api("/learning/dashboard"));setError("");}catch(e){setError(e.message||"Learning data could not be loaded.");}};
  useEffect(()=>{load();},[]);
  const update=async(resource,status)=>{setBusy(resource.id);try{await api("/learning/resources/"+resource.id+"/progress",{method:"PATCH",body:{status}});await load();}catch(e){setError(e.message||"Progress could not be saved.");}finally{setBusy("");}};
  const feedback=async(resource,helpful)=>{try{await api("/learning/resources/"+resource.id+"/feedback",{method:"POST",body:{helpful,comment:""}});setError("");}catch(e){setError(e.message||"Feedback could not be saved.");}};
  if(!data)return <div className="learn-loading">{error||"Building your verified learning plan…"}</div>;
  const topics=[...new Set(data.resources.map(r=>r.topic))],retake=data.retake||{};
  const fmt=v=>v?new Date(v*1000).toLocaleDateString():"—";
  return <section className="learn-workspace">
    <div className="learn-hero"><div><span className="learn-kicker">YOUR PERSONAL DEVELOPMENT PLAN</span><h2>Turn skill gaps into progress.</h2><p>Curated resources are reviewed by SkillBridge before they appear here. Track what you study and measure progress with a separate assessment attempt.</p></div><div className="learn-hero-mark">↗</div></div>
    {error&&<div className="learn-alert" role="alert">{error}</div>}
    <div className="learn-stats"><article><small>Topics to strengthen</small><strong>{data.gaps.length}</strong><span>Based on your latest result</span></article><article><small>Resources completed</small><strong>{data.progress.completed}</strong><span>{data.progress.started} in progress</span></article><article><small>Retake credits</small><strong>{retake.balance}</strong><span>{retake.credit_cost} required per retake</span></article><article><small>Attempts in 90 days</small><strong>{retake.attempts_used_90d}/{retake.max_attempts_90d}</strong><span>Policy-controlled limit</span></article></div>
    <div className="learn-section-head"><div><span className="learn-kicker">CURATED FOR YOUR GAPS</span><h3>Recommended learning</h3><p>Only verified links are shown. No suitable resource is invented when the catalogue has no match.</p></div><span className="learn-verified">✓ Verified catalogue</span></div>
    {!topics.length?<div className="learn-empty"><b>{data.based_on?"Your current skill gaps are covered.":"Start with a skill assessment."}</b><p>{data.gaps.length?"There are skill gaps, but no curator-verified resources are currently available for them. Check back after the catalogue is reviewed.":data.based_on?"Your latest result did not identify any priority gaps.":"Complete your first assessment to receive recommendations matched to your topic scores."}</p></div>:topics.map(topic=><section className="learn-topic" key={topic}><header><div><span>SKILL GAP</span><h4>{topic.replaceAll("_"," ")}</h4></div><small>{data.resources.filter(r=>r.topic===topic).length} verified resources</small></header><div className="learn-resource-grid">{data.resources.filter(r=>r.topic===topic).map(r=><article className="learn-resource" key={r.id}><div className="learn-resource-top"><span className="learn-resource-icon">↗</span><span className="learn-resource-level">Level {r.level}</span></div><h5>{r.title}</h5><p>{r.provider} · {r.language.toUpperCase()}</p><div className="learn-resource-meta"><span>◷ {r.estimated_minutes} min</span><span>✓ Verified</span></div><div className="learn-resource-actions"><button disabled={busy===r.id||r.progress_status==="completed"} onClick={()=>update(r,"started")}>{r.progress_status==="completed"?"Completed":r.progress_status==="started"?"In progress":"Mark started"}</button><button disabled={busy===r.id||r.progress_status==="completed"} onClick={()=>update(r,"completed")}>Mark complete</button><a href={r.url} target="_blank" rel="noreferrer noopener" onClick={()=>{if(r.progress_status!=="completed")update(r,"started");}}>Open resource ↗</a></div><div className="learn-resource-feedback"><span>Was this useful?</span><button onClick={()=>feedback(r,true)} aria-label="Helpful">👍</button><button onClick={()=>feedback(r,false)} aria-label="Not helpful">👎</button></div><small className="learn-licence">{r.licence||"Licence details not supplied"}</small></article>)}</div></section>)}
    <div className="learn-bottom-grid"><section className="learn-panel"><span className="learn-kicker">REASSESSMENT</span><h3>Ready to measure your progress?</h3><p>Retakes are separate official attempts. Your previous scores remain in your history and are never overwritten.</p><div className="learn-retake-line"><span>Available credits</span><b>{retake.balance}</b></div><div className="learn-retake-line"><span>Credits per attempt</span><b>{retake.credit_cost}</b></div><div className="learn-retake-line"><span>Cooldown</span><b>{retake.cooldown_hours} hours</b></div>{retake.cooldown_until&&<small>Next eligible date: {fmt(retake.cooldown_until)}</small>}<button className="btn" disabled={!retake.eligible&&!retake.active_attempt} onClick={onNext}>{retake.active_attempt?"Resume active assessment":retake.eligible?"Start official retake":"Retake not available yet"}</button>{!retake.eligible&&<small className="learn-reason">{retake.active_attempt?"An assessment is already in progress.":retake.balance<retake.credit_cost?"You need more retake credits.":retake.attempts_used_90d>=retake.max_attempts_90d?"You have reached the 90-day attempt limit.":retake.has_result?"The retake cooldown is still active.":"Complete your first assessment to unlock retakes."}</small>}</section>
    <section className="learn-panel"><span className="learn-kicker">YOUR LEARNING ACTIVITY</span><h3>Progress overview</h3><div className="learn-progress-track"><span style={{width:(data.progress.started+data.progress.completed?Math.round(data.progress.completed/(data.progress.started+data.progress.completed)*100):0)+"%"}}/></div><p>{data.progress.completed} completed · {data.progress.started} started</p><p className="learn-note">Recommendations are based on your latest saved assessment. Learning activity does not directly change your assessment score.</p></section></div>
    <section className="learn-panel learn-history"><div className="learn-section-head"><div><span className="learn-kicker">IMMUTABLE RECORD</span><h3>Assessment attempt history</h3><p>Every official attempt is recorded separately, including consumed credits.</p></div><span className="learn-count">{data.attempt_history.length} attempts</span></div>{data.attempt_history.length?<div className="learn-history-table"><table><thead><tr><th>Attempt</th><th>Type</th><th>Started</th><th>Result</th><th>Credits used</th><th>Status</th></tr></thead><tbody>{data.attempt_history.map((a,i)=><tr key={a.id}><td>#{data.attempt_history.length-i}<small>{a.id.slice(0,8)}</small></td><td>{a.kind==="pre"?"Baseline":"Retake"}</td><td>{fmt(a.started)}</td><td>{a.score==null?"—":a.score+"%"}</td><td>{a.credit_consumed}</td><td><span>{a.status}</span></td></tr>)}</tbody></table></div>:<p className="learn-note">No assessment attempts have been recorded yet.</p>}</section>
  </section>;
}

/* ---------- passport ---------- */
function Passport({ st, refresh }) {
  const t = useT();
  const [res, setRes] = useState(null);
  const [sel, setSel] = useState([]);
  const [name, setName] = useState(false);
  const [err, setErr] = useState(null);
  const [copied, setCopied] = useState(null);
  const done = st.attempts.filter((a) => a.status === "done");
  const latest = done[done.length - 1];
  useEffect(() => {
    if (latest) api(`/attempts/${latest.id}/result`).then((r) => { setRes(r); setSel(Object.keys(r.topics)); }).catch(setErr);
  }, [latest?.id]);
  const create = async () => { try { await api("/passport", { method: "POST", body: { topics: sel, show_name: name } }); refresh(); } catch (e) { setErr(e); } };
  const revoke = async (id) => { await api(`/passport/${id}`, { method: "DELETE" }); refresh(); };
  const link = (id) => `${location.origin}/#/p/${id}`;
  const copy = async (id) => { try { await navigator.clipboard.writeText(link(id)); setCopied(id); } catch { /* ignore */ } };
  if (!latest) return <p className="note">{t("needVerified")}</p>;
  return (
    <section>
      <h2>{t("passportTitle")}</h2>
      <div className="card">
        <p>{t("passportHelp")}</p>
        {res && <ul className="skills">{Object.entries(res.topics).map(([k, v]) => (
          <li key={k}><label className="check"><input type="checkbox" checked={sel.includes(k)} onChange={() => setSel(sel.includes(k) ? sel.filter((x) => x !== k) : [...sel, k])} /> <strong>{k}</strong> · {v.score}% · {t("levels")[v.level]}</label></li>
        ))}</ul>}
        <label className="check"><input type="checkbox" checked={name} onChange={(e) => setName(e.target.checked)} /> {t("showName")}</label>
        <Err e={err} />
        <button className="btn" onClick={create} disabled={!sel.length}>{t("createPass")}</button>
      </div>
      {st.passports.length > 0 && (
        <div className="card">
          <h3>{t("yourPass")}</h3>
          <ul className="plist">
            {st.passports.map((p) => (
              <li key={p.id}>
                <code>{link(p.id)}</code>
                {p.revoked ? <span className="chip chip-rev">{t("revoked")}</span> : (
                  <span className="row">
                    <a className="btn ghost sm" href={`#/p/${p.id}`} target="_blank" rel="noreferrer">{t("open")}</a>
                    <button className="btn ghost sm" onClick={() => copy(p.id)}>{copied === p.id ? t("copied") : t("copy")}</button>
                    <button className="btn ghost sm" onClick={() => revoke(p.id)}>{t("revoke")}</button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <DataRights />
    </section>
  );
}

function DataRights() {
  const t = useT();
  const exp = async () => {
    const d = await api("/me/export");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }));
    a.download = "skillbridge-my-data.json"; a.click();
  };
  const del = async () => { if (confirm(t("deleteConfirm"))) { await api("/me", { method: "DELETE" }); setToken(null); location.hash = ""; location.reload(); } };
  return (
    <div className="card quiet">
      <h3>{t("dataRights")}</h3>
      <div className="row"><button className="btn ghost" onClick={exp}>{t("exportData")}</button><button className="btn ghost" onClick={del}>{t("deleteData")}</button></div>
    </div>
  );
}

function PublicPassport({ id }) {
  const t = useT();
  const [d, setD] = useState(null);
  const [nf, setNf] = useState(false);
  useEffect(() => { api(`/public/passport/${id}`).then(setD).catch(() => setNf(true)); }, [id]);
  if (nf) return <main className="wrap"><p className="note">{t("passNotFound")}</p></main>;
  if (!d) return <main className="wrap"><p>{t("loading")}</p></main>;
  if (d.revoked) return <main className="wrap"><p className="note">{t("passRevoked")}</p></main>;
  const p = d.passport;
  return (
    <main className="wrap">
      <article className="card passport">
        <header>
          <h1>{t("passportTitle")}</h1>
          <span className={"chip " + (d.signature_valid ? "chip-ok" : "chip-rev")}>{d.signature_valid ? t("sigOk") : t("sigBad")}</span>
        </header>
        {d.expired && <p className="err">{t("passExpired")}</p>}
        <dl className="facts">
          <div><dt>{t("holder")}</dt><dd>{p.holder}</dd></div>
          <div><dt>{t("issued")}</dt><dd>{p.issued}</dd></div>
          <div><dt>{t("expires")}</dt><dd>{p.expires}</dd></div>
        </dl>
        <h3>{t("verified")}</h3>
        <ul className="gaps">
          {p.skills.map((s) => (
            <li key={s.skill}>
              <div className="gaprow"><strong>{s.name}</strong><span className="muted">{t("levels")[s.level]} · {s.date}</span><span>{s.score}% <TrustChip trust={s.trust} /></span></div>
              <Bar score={s.score} />
            </li>
          ))}
        </ul>
        {p.uplift && <p className="note">{t("upliftTitle")}: {p.uplift.pre}% → {p.uplift.post}% ({p.uplift.delta >= 0 ? "+" : ""}{p.uplift.delta})</p>}
        <small className="muted">{p.issuer}</small>
        <p><a href="#/" className="link">{t("back")}</a></p>
      </article>
    </main>
  );
}


const emptyCandidateProfile = {headline:"",phone:"",location:"",summary:"",linkedin:"",github:"",portfolio:"",education:[],experience:[],projects:[],preferences:{job_types:["Internship","Full-time"],work_mode:"Any",locations:[]}};
function CandidateProfileEditor({onSaved}) {
 const [p,setP]=useState(emptyCandidateProfile),[account,setAccount]=useState({}),[cv,setCv]=useState(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[message,setMessage]=useState(""),[error,setError]=useState("");
 const load=async()=>{try{const r=await api("/candidate/profile");setAccount(r.user||{});setP({...emptyCandidateProfile,...r.profile,preferences:{...emptyCandidateProfile.preferences,...(r.profile?.preferences||{})}});setCv(r.cv);}catch(e){setError(e.message||"Could not load profile");}finally{setLoading(false);}};
 useEffect(()=>{load();},[]);
 const set=(k,v)=>setP(old=>({...old,[k]:v}));
 const item=(key,i,k,v)=>setP(old=>({...old,[key]:old[key].map((x,n)=>n===i?({...x,[k]:v}):x)}));
 const add=(key,value)=>set(key,[...p[key],value]);
 const remove=(key,i)=>set(key,p[key].filter((_,n)=>n!==i));
 const save=async e=>{e.preventDefault();setBusy(true);setMessage("");setError("");try{await api("/candidate/profile",{method:"PUT",body:p});setMessage("Profile saved successfully.");onSaved?.();}catch(e){setError(e.message||"Could not save profile");}finally{setBusy(false);}};
 if(loading)return <div className="portal-panel">Loading profile…</div>;
 const completed=[p.headline,p.location,p.summary,cv?.uploaded,p.education.length,p.experience.length,p.projects.length].filter(Boolean).length;
 const progress=Math.round(completed/7*100);
 return <div className="profile-layout"><aside className="profile-side-card"><div className="avatar-circle">{(account.name||"C").split(" ").map(x=>x[0]).join("").slice(0,2).toUpperCase()}</div><h2>{account.name}</h2><p className="muted">{p.headline||"Add a professional headline"}</p><p className="muted">{account.email}</p><div className="completion-row"><span>Profile completeness</span><strong>{progress}%</strong></div><div className="completion-track"><span style={{width:progress+"%"}}/></div><div className="profile-mini-stat"><strong>{cv?.skills?.length||0}</strong><span>Confirmed skills</span></div><div className="profile-mini-stat"><strong>{p.experience.length}</strong><span>Experience entries</span></div><div className="profile-mini-stat"><strong>{p.projects.length}</strong><span>Projects</span></div><p className="muted">Only share assessment results with employers when you choose to.</p></aside>
 <form className="profile-editor" onSubmit={save}>{message&&<p className="success-note" role="status">{message}</p>}{error&&<p className="err" role="alert">{error}</p>}
 <section className="portal-panel"><div className="panel-heading"><div><span className="eyebrow">YOUR PROFILE</span><h2>Professional overview</h2><p>Give employers a clear picture of your strengths and goals.</p></div><span className="status-pill">{progress>=70?"Good progress":"In progress"}</span></div>
 <div className="form-grid"><label>Full name<input disabled value={account.name||""}/></label><label>Professional headline<input value={p.headline} onChange={e=>set("headline",e.target.value)} maxLength={180} placeholder="Junior Data Analyst | SQL & Python"/></label><label>Email<input disabled value={account.email||""}/></label><label>Phone<input value={p.phone} onChange={e=>set("phone",e.target.value)} placeholder="+880…"/></label><label>Location<input value={p.location} onChange={e=>set("location",e.target.value)} placeholder="Dhaka, Bangladesh"/></label></div><label>Professional summary<textarea rows={4} value={p.summary} onChange={e=>set("summary",e.target.value)} maxLength={3000} placeholder="Your interests, strengths, experience and the problems you enjoy solving."/></label><div className="form-grid"><label>LinkedIn URL<input value={p.linkedin} onChange={e=>set("linkedin",e.target.value)} placeholder="https://linkedin.com/in/…"/></label><label>GitHub URL<input value={p.github} onChange={e=>set("github",e.target.value)} placeholder="https://github.com/…"/></label><label>Portfolio URL<input value={p.portfolio} onChange={e=>set("portfolio",e.target.value)} placeholder="https://…"/></label></div></section>
 <section className="portal-panel"><div className="panel-heading"><div><span className="eyebrow">BACKGROUND</span><h2>Education</h2></div><button type="button" className="btn ghost sm" onClick={()=>add("education",{institution:"",degree:"",field:"",start_year:"",end_year:"",details:""})}>+ Add education</button></div>{p.education.map((x,i)=><div className="repeat-card" key={i}><div className="repeat-top"><strong>Education {i+1}</strong><button type="button" className="text-danger" onClick={()=>remove("education",i)}>Remove</button></div><div className="form-grid"><label>Institution<input required value={x.institution} onChange={e=>item("education",i,"institution",e.target.value)}/></label><label>Degree<input value={x.degree} onChange={e=>item("education",i,"degree",e.target.value)}/></label><label>Field of study<input value={x.field} onChange={e=>item("education",i,"field",e.target.value)}/></label><label>Start year<input value={x.start_year} onChange={e=>item("education",i,"start_year",e.target.value)}/></label><label>End year / expected<input value={x.end_year} onChange={e=>item("education",i,"end_year",e.target.value)}/></label></div><label>Details<textarea rows={2} value={x.details} onChange={e=>item("education",i,"details",e.target.value)}/></label></div>)}{!p.education.length&&<p className="empty-inline">Add your university, school or relevant training.</p>}</section>
 <section className="portal-panel"><div className="panel-heading"><div><span className="eyebrow">CAREER</span><h2>Experience</h2></div><button type="button" className="btn ghost sm" onClick={()=>add("experience",{company:"",title:"",employment_type:"",start_date:"",end_date:"",current:false,description:""})}>+ Add experience</button></div>{p.experience.map((x,i)=><div className="repeat-card" key={i}><div className="repeat-top"><strong>Experience {i+1}</strong><button type="button" className="text-danger" onClick={()=>remove("experience",i)}>Remove</button></div><div className="form-grid"><label>Company<input required value={x.company} onChange={e=>item("experience",i,"company",e.target.value)}/></label><label>Role / title<input required value={x.title} onChange={e=>item("experience",i,"title",e.target.value)}/></label><label>Employment type<input value={x.employment_type} onChange={e=>item("experience",i,"employment_type",e.target.value)}/></label><label>Start date<input value={x.start_date} onChange={e=>item("experience",i,"start_date",e.target.value)}/></label><label>End date<input disabled={x.current} value={x.end_date} onChange={e=>item("experience",i,"end_date",e.target.value)}/></label><label className="check"><input type="checkbox" checked={!!x.current} onChange={e=>item("experience",i,"current",e.target.checked)}/> Current role</label></div><label>Description<textarea rows={3} value={x.description} onChange={e=>item("experience",i,"description",e.target.value)}/></label></div>)}{!p.experience.length&&<p className="empty-inline">Internships, volunteering and practical work all count.</p>}</section>
 <section className="portal-panel"><div className="panel-heading"><div><span className="eyebrow">PORTFOLIO</span><h2>Projects</h2></div><button type="button" className="btn ghost sm" onClick={()=>add("projects",{name:"",description:"",technologies:[],url:""})}>+ Add project</button></div>{p.projects.map((x,i)=><div className="repeat-card" key={i}><div className="repeat-top"><strong>Project {i+1}</strong><button type="button" className="text-danger" onClick={()=>remove("projects",i)}>Remove</button></div><div className="form-grid"><label>Project name<input required value={x.name} onChange={e=>item("projects",i,"name",e.target.value)}/></label><label>Repository / demo URL<input value={x.url} onChange={e=>item("projects",i,"url",e.target.value)}/></label></div><label>Description<textarea rows={3} value={x.description} onChange={e=>item("projects",i,"description",e.target.value)}/></label><label>Technologies, comma-separated<input value={(x.technologies||[]).join(", ")} onChange={e=>item("projects",i,"technologies",e.target.value.split(",").map(y=>y.trim()).filter(Boolean))}/></label></div>)}{!p.projects.length&&<p className="empty-inline">Add projects to demonstrate what you can build.</p>}</section>
 <section className="portal-panel"><div className="panel-heading"><div><span className="eyebrow">JOB SEARCH</span><h2>Preferences</h2><p>Used to organize your search, not to guarantee a match.</p></div></div><label>Preferred work mode<select value={p.preferences.work_mode||"Any"} onChange={e=>set("preferences",{...p.preferences,work_mode:e.target.value})}><option>Any</option><option>Remote</option><option>Hybrid</option><option>On-site</option></select></label><strong>Employment type</strong><div className="preference-options">{["Internship","Full-time","Part-time","Contract","Temporary"].map(x=><label className="check" key={x}><input type="checkbox" checked={(p.preferences.job_types||[]).includes(x)} onChange={e=>set("preferences",{...p.preferences,job_types:e.target.checked?[...(p.preferences.job_types||[]),x]:(p.preferences.job_types||[]).filter(y=>y!==x)})}/>{x}</label>)}</div><label>Preferred locations, comma-separated<input value={(p.preferences.locations||[]).join(", ")} onChange={e=>set("preferences",{...p.preferences,locations:e.target.value.split(",").map(x=>x.trim()).filter(Boolean)})} placeholder="Dhaka, Remote"/></label></section>
 <div className="profile-savebar"><span className="muted">Remember to save your changes.</span><button className="btn" disabled={busy}>{busy?"Saving…":"Save profile"}</button></div></form></div>;
}
function CandidateJobs({onApply}) {
 const [jobs,setJobs]=useState([]),[apps,setApps]=useState([]),[search,setSearch]=useState(""),[kind,setKind]=useState("All"),[error,setError]=useState(""),[notice,setNotice]=useState(""),[loading,setLoading]=useState(true);
 const load=async()=>{try{const [j,a]=await Promise.all([api("/jobs"),api("/applications/mine")]);setJobs(j.items||[]);setApps(a.items||[]);}catch(e){setError(e.message||"Could not load jobs");}finally{setLoading(false);}};
 useEffect(()=>{load();},[]);
 const apply=async id=>{try{const r=await api("/jobs/"+id+"/apply",{method:"POST"});setNotice(r.status==="already_applied"?"You have already applied.":"Application submitted.");await load();}catch(e){setError(e.message||"Could not apply");}};
 const applied=new Set(apps.map(a=>a.job_id));const filtered=jobs.filter(j=>(kind==="All"||j.employment_type===kind)&&[j.title,j.description,j.company_name,...(j.skills||[])].join(" ").toLowerCase().includes(search.toLowerCase()));
 if(loading)return <div className="portal-panel">Loading jobs…</div>;
 return <section><div className="job-filters"><input aria-label="Search jobs" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search title, company or skill…"/><select value={kind} onChange={e=>setKind(e.target.value)}><option>All</option>{["Internship","Full-time","Part-time","Contract","Temporary"].map(x=><option key={x}>{x}</option>)}</select></div>{notice&&<p className="success-note" role="status">{notice}</p>}{error&&<p className="err" role="alert">{error}</p>}<div className="job-grid">{filtered.map(j=><article className="job-card" key={j.id}><div className="job-card-top"><span className="job-icon">↗</span>{j.is_mock&&<span className="chip chip-mock">MOCK DATA</span>}</div><h3>{j.title}</h3><p className="job-company">{j.company_name}</p><p>{j.description}</p><div className="job-meta"><span>{j.location}</span><span>{j.employment_type}</span></div><div className="job-skill-tags">{(j.skills||[]).map(s=><span key={s}>{s}</span>)}</div><button className={"btn "+(applied.has(j.id)?"ghost":"")} disabled={applied.has(j.id)} onClick={()=>apply(j.id)}>{applied.has(j.id)?"Application sent":"Apply now"}</button></article>)}</div>{!filtered.length&&<div className="empty-state"><h3>No matching jobs</h3><p>Try another search term or filter.</p></div>}</section>;
}
function CandidateApplications() {
 const [items,setItems]=useState([]),[loading,setLoading]=useState(true),[error,setError]=useState("");
 const load=async()=>{try{const r=await api("/applications/mine");setItems(r.items||[]);}catch(e){setError(e.message||"Could not load applications");}finally{setLoading(false);}};
 useEffect(()=>{load();},[]);
 const share=async(id,value)=>{try{await api("/applications/"+id+"/share-results",{method:"PUT",body:{share:value}});await load();}catch(e){setError(e.message||"Could not update sharing");}};
 if(loading)return <div className="portal-panel">Loading applications…</div>;
 return <section className="portal-panel"><div className="section-toolbar"><div><span className="eyebrow">YOUR JOB SEARCH</span><h2>My applications</h2><p>Track submitted applications and control result sharing.</p></div><span className="count-badge">{items.length} total</span></div>{error&&<p className="err">{error}</p>}{!items.length&&<div className="empty-state"><h3>No applications yet</h3><p>Browse jobs and apply to a role that matches your interests.</p></div>}{items.map(a=><article className="application-row" key={a.application_id}><div className="application-logo">SB</div><div className="application-main"><h3>{a.title}</h3><p>{a.company_name} · {a.location}</p><span className="status-pill">{a.status}</span></div><div className="application-consent"><label className="check"><input type="checkbox" checked={!!a.results_shared} onChange={e=>share(a.application_id,e.target.checked)}/> Share assessment results with this employer</label><small className="muted">You can change this choice at any time.</small></div></article>)}</section>;
}
function CandidatePortal({st,refresh,tick,setTick,onLogout}) {
 const [section,setSection]=useState("overview");const [journeyView,setJourneyView]=useState(st.confirmed?"gap":"profile");const t=useT();const done=(st?.attempts||[]).filter(a=>a.status==="done");const latest=done[done.length-1];
 const nav=[{id:"overview",label:"Overview",icon:"⌂",group:"WORKSPACE"},{id:"profile",label:"My profile",icon:"◉",group:"WORKSPACE"},{id:"jobs",label:"Find jobs",icon:"↗",group:"WORKSPACE"},{id:"applications",label:"Applications",icon:"▤",group:"WORKSPACE"},{id:"journey",label:"Skill journey",icon:"⌁",group:"GROWTH"},{id:"learning",label:"Learning & retakes",icon:"↗",group:"GROWTH"},{id:"passport",label:"Skill Passport",icon:"◇",group:"GROWTH"},{id:"privacy",label:"Data & privacy",icon:"▣",group:"ACCOUNT"}];
 const go=id=>{setSection(id);window.scrollTo({top:0,behavior:"smooth"});};
 return <div className="candidate-shell"><aside className="candidate-sidebar"><div className="sidebar-brand"><span className="brand-mark">S</span><span>SkillBridge<small>CAREER WORKSPACE</small></span></div><div className="sidebar-profile"><div className="avatar-small">{(st.user?.name||"C").split(" ").map(x=>x[0]).join("").slice(0,2).toUpperCase()}</div><div><strong>{st.user?.name}</strong><small>Candidate account</small></div></div>{["WORKSPACE","GROWTH","ACCOUNT"].map(group=><div className="sidebar-group" key={group}><p>{group}</p>{nav.filter(n=>n.group===group).map(n=><button key={n.id} className={section===n.id?"active":""} onClick={()=>go(n.id)}><span>{n.icon}</span>{n.label}</button>)}</div>)}<div className="sidebar-bottom"><div className="sidebar-tip"><strong>Keep growing</strong><p>Verify skills, learn from gaps and build proof of progress.</p><button onClick={()=>go("journey")}>Continue learning →</button></div><p className="sidebar-version">SkillBridge · Candidate</p></div></aside>
 <main className="candidate-main"><header className="candidate-topbar"><div className="mobile-brand"><span className="brand-mark">S</span><strong>SkillBridge</strong></div><div className="breadcrumb">Candidate workspace <span>/</span> {nav.find(n=>n.id===section)?.label}</div><div className="topbar-actions"><div className="language-switch"><select aria-label="Language" value={localStorage.getItem("sb_lang")||"en"} onChange={e=>{localStorage.setItem("sb_lang",e.target.value);location.reload();}}><option value="en">English</option><option value="bn">বাংলা</option></select></div><div className="topbar-avatar">{(st.user?.name||"C")[0]?.toUpperCase()}</div><button className="candidate-logout" onClick={onLogout}>Sign out</button></div></header><div className="candidate-page">
 {section==="overview"&&<><section className="welcome-banner"><div><span className="eyebrow light">YOUR CAREER, YOUR PROGRESS</span><h1>Welcome back, {(st.user?.name||"Candidate").split(" ")[0]}.</h1><p>Build your profile, verify your skills and discover opportunities that fit your goals.</p><div className="welcome-actions"><button className="btn btn-light" onClick={()=>go("profile")}>Complete my profile →</button><button className="welcome-link" onClick={()=>go("journey")}>Continue skill journey ↗</button></div></div><div className="welcome-art" aria-hidden="true"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-center">SB</div><span className="art-node node-one">✦</span><span className="art-node node-two">✓</span><span className="art-node node-three">↗</span></div></section><div className="dashboard-section-heading"><div><span className="eyebrow">YOUR WORKSPACE</span><h2>Your career at a glance</h2></div></div><div className="metric-grid"><button className="metric-card" onClick={()=>go("profile")}><span className="metric-icon">◉</span><span className="metric-label">Profile & CV</span><strong>{st.has_cv?"CV added":"Build profile"}</strong><small>{st.confirmed?.length||0} confirmed skills</small></button><button className="metric-card" onClick={()=>go("journey")}><span className="metric-icon">✓</span><span className="metric-label">Assessments</span><strong>{done.length} completed</strong><small>{latest?"Latest score: "+(latest.overall??"—")+"%":"Verify your skills"}</small></button><button className="metric-card" onClick={()=>go("applications")}><span className="metric-icon">▤</span><span className="metric-label">Applications</span><strong>Track progress</strong><small>Manage result sharing</small></button><button className="metric-card" onClick={()=>go("passport")}><span className="metric-icon">◇</span><span className="metric-label">Skill Passport</span><strong>{st.passports?.filter(x=>!x.revoked).length||0} active</strong><small>Share verified evidence</small></button></div><div className="dashboard-lower"><section className="portal-panel"><span className="eyebrow">RECOMMENDED NEXT STEP</span><h2>{!st.confirmed?"Build your candidate profile":done.length===0?"Verify your skills":"Keep growing your skills"}</h2><p>{!st.confirmed?"Add your background and review the skills extracted from your CV.":done.length===0?"Take an assessment to understand your strengths and learning gaps.":"Use your learning path to practice and measure your progress."}</p><button className="btn" onClick={()=>go(!st.confirmed?"profile":"journey")}>{!st.confirmed?"Open profile":"Open skill journey"} →</button></section><section className="portal-panel quick-links"><h2>Quick links</h2>{[{id:"profile",label:"Edit professional profile"},{id:"jobs",label:"Browse opportunities"},{id:"applications",label:"Manage result sharing"},{id:"privacy",label:"Export or delete data"}].map(x=><button key={x.id} onClick={()=>go(x.id)}>{x.label}<b>→</b></button>)}</section></div></>}
 {section==="profile"&&<><div className="page-title"><div><span className="eyebrow">CANDIDATE DETAILS</span><h1>My profile</h1><p>Manage your professional story, education, experience, projects and job preferences.</p></div></div><CandidateProfileEditor onSaved={refresh}/><div className="page-title cv-title"><div><span className="eyebrow">CV & SKILLS</span><h2>Resume and skills</h2><p>Upload a CV, review extracted skills and confirm what represents you.</p></div></div><Profile st={st} onDone={async()=>{await refresh();go("journey");}}/></>}
 {section==="jobs"&&<><div className="page-title"><div><span className="eyebrow">OPPORTUNITIES</span><h1>Find jobs</h1><p>Search the current marketplace and apply to a suitable role.</p></div></div><CandidateJobs/></>}
 {section==="applications"&&<><div className="page-title"><div><span className="eyebrow">YOUR JOB SEARCH</span><h1>Applications</h1><p>Track submitted applications and manage assessment result sharing.</p></div></div><CandidateApplications/></>}
 {section==="journey"&&<><div className="page-title"><div><span className="eyebrow">SKILL DEVELOPMENT</span><h1>Skill journey</h1><p>Move from claimed skills to verified evidence, learning and re-assessment.</p></div></div><nav className="steps">{STEPS.map((s,i)=><button key={s} className={(journeyView===s?"on ":"")+(done.length===0&&["learn","retest","passport"].includes(s)?"dim":"")} onClick={()=>setJourneyView(s)}><span className="n">{i+1}</span>{t("steps")[s]}</button>)}</nav><div className="portal-panel journey-content"><ProfileJourneyRouter st={st} refresh={refresh} tick={tick} setTick={setTick} done={done} v={journeyView} setV={setJourneyView}/></div></>}
 {section==="learning"&&<><div className="page-title"><div><span className="eyebrow">LEARN · TRACK · REASSESS</span><h1>Learning & retakes</h1><p>Verified resources, visible progress and transparent attempt history.</p></div></div><div className="portal-panel journey-content"><Learn onNext={()=>{setJourneyView("retest");go("journey");}}/></div></>}
 {section==="passport"&&<><div className="page-title"><div><span className="eyebrow">VERIFIABLE PROOF</span><h1>Skill Passport</h1><p>Choose verified topics to share and revoke public links whenever you need.</p></div></div><Passport st={st} refresh={refresh}/></>}
 {section==="privacy"&&<><div className="page-title"><div><span className="eyebrow">YOUR DATA</span><h1>Data & privacy</h1><p>Export a copy of your data or request account deletion.</p></div></div><DataRights/><section className="portal-panel"><h2>Privacy basics</h2><ul className="privacy-list"><li>Review CV-extracted skills before confirming them.</li><li>Assessment integrity signals are review signals, not automatic proof of misconduct.</li><li>Sharing results with an employer is optional and revocable.</li></ul></section></>}
 </div><footer className="candidate-footer"><span>SkillBridge Candidate Workspace</span><span>Build skills. Show evidence. Keep growing.</span></footer></main></div>;
}
function ProfileJourneyRouter({st,refresh,tick,setTick,done,v,setV}) {
 return <>{v==="profile"&&<Profile st={st} onDone={async()=>{await refresh();setV("gap");}}/>}{v==="gap"&&<Gap refreshKey={tick} onStart={()=>setV("assess")}/ >}{v==="assess"&&<Exam kind="pre" st={st} refresh={refresh} afterResult={()=>setV("learn")} afterLabel="Learning path"/>}{v==="learn"&&<Learn onNext={()=>setV("retest")}/ >}{v==="retest"&&<Exam kind="post" st={st} refresh={refresh} afterResult={()=>setV("passport")} afterLabel="Skill Passport"/>}{v==="passport"&&<Passport st={st} refresh={refresh}/>}</>;
}

/* ---------- platform administration ---------- */
function AdminConsole({ onLogout }) {
  const [page,setPage]=useState("overview");
  const [adminRole,setAdminRole]=useState("super_admin");
  const [data,setData]=useState({});
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [toast,setToast]=useState("");
  const [search,setSearch]=useState("");
  const [filter,setFilter]=useState("all");
  const [action,setAction]=useState(null);
  const [reason,setReason]=useState("");
  const [saving,setSaving]=useState(false);
  const [adminDraft,setAdminDraft]=useState({name:"",email:"",password:"",admin_role:"support_agent"});
  const [creatingAdmin,setCreatingAdmin]=useState(false);
  const [selectedReport,setSelectedReport]=useState(null);
  const [questionCategory,setQuestionCategory]=useState("all"),[questionQuery,setQuestionQuery]=useState(""),[questionStatus,setQuestionStatus]=useState("all");
  const [questionModal,setQuestionModal]=useState(false),[questionEditing,setQuestionEditing]=useState(null),[questionSaving,setQuestionSaving]=useState(false);
  const [questionDraft,setQuestionDraft]=useState({category_id:"CAT-01",subcategory:"",question_type:"MCQ",difficulty_level:"Medium",question_text:"",options:"",correct_answer:"",explanation:"",reference_answer:"",grading_rubric:"",marks:2,time_minutes:2,status:"draft"});
  const [importFile,setImportFile]=useState(null),[importCategory,setImportCategory]=useState("CAT-01"),[importPreview,setImportPreview]=useState(null),[importBusy,setImportBusy]=useState(false);
  const nav=[
    ["overview","Overview","⌂"],["users","Users & accounts","♙"],["staff","Admin staff & roles","♜"],["companies","Company verification","▣"],
    ["jobs","Job moderation","▤"],["assessments","Assessment reviews","◎"],["appeals","Candidate appeals","⚑"],
    ["questions","Question governance","◇"],["learning","Learning & retakes","↗"],["ai","AI operations","✳"],["billing","Billing","৳"],
    ["privacy","Privacy & compliance","◈"],["quality","Fairness & quality","▥"],["audit","Audit trail","≋"]
  ];
  const load=async()=>{
    setBusy(true);setError("");
    try { const me=await api("/admin/console/me"); setAdminRole(me.admin_role||"super_admin"); } catch(x) { setError(x.message||"Admin identity unavailable"); setBusy(false); return; }
    const endpoints={
      overview:"/admin/console/overview",users:"/admin/console/users",staff:"/admin/console/staff",companies:"/admin/console/company-verifications",
      jobs:"/admin/console/job-review-queue",assessments:"/admin/assessment-reviews",appeals:"/admin/appeals",
      questions:"/admin/questions",graphrag:"/admin/assessment/graphrag/status",learning:"/admin/learning",ai:"/admin/ai-operations",billing:"/admin/billing",privacy:"/admin/privacy",
      quality:"/admin/quality",audit:"/admin/audit-logs",phaseTwo:"/admin/phase-two-requests"
    };
    const keys=Object.keys(endpoints);
    const results=await Promise.allSettled(keys.map(k=>api(endpoints[k])));
    const next={};
    results.forEach((r,i)=>{if(r.status==="fulfilled")next[keys[i]]=r.value;else next[keys[i]]={error:r.reason?.message||"Unavailable"};});
    setData(next);
    const failures=results.filter(r=>r.status==="rejected");
    if(failures.length===keys.length)setError("Admin services could not be reached. Check your session and backend connection.");
    setBusy(false);
  };
  useEffect(()=>{load();},[]);
  const flash=(s)=>{setToast(s);setError("");};
  const createAdmin=async(e)=>{
    e.preventDefault();
    setCreatingAdmin(true);setError("");
    try{
      await api("/admin/console/staff",{method:"POST",body:adminDraft});
      setAdminDraft({name:"",email:"",password:"",admin_role:"support_agent"});
      await load();
      flash("Admin account created. Share the credentials securely with the new admin.");
    }catch(x){setError(x.message||"Could not create admin account.");}
    finally{setCreatingAdmin(false);}
  };
  const openAction=(kind,id,decision)=>{setAction({kind,id,decision});setReason("");};
  const submitAction=async(e)=>{
    e.preventDefault();if(!action||reason.trim().length<3)return;setSaving(true);
    try{
      const body={reason:reason.trim()};
      if(action.kind==="user") {body.status=action.decision;await api("/admin/users/"+action.id+"/status",{method:"POST",body});}
      if(action.kind==="company") {body.decision=action.decision;await api("/admin/companies/"+action.id+"/verification-decision",{method:"POST",body});}
      if(action.kind==="job") {body.decision=action.decision;await api("/admin/jobs/"+action.id+"/moderation-decision",{method:"POST",body});}
      if(action.kind==="assessment") {body.decision=action.decision;await api("/admin/attempts/"+action.id+"/review",{method:"POST",body});}
      if(action.kind==="appeal") {body.decision=action.decision;await api("/admin/appeals/"+action.id+"/decision",{method:"POST",body});}
      if(action.kind==="phase") {body.decision=action.decision;await api("/admin/phase-two-requests/"+action.id+"/decision",{method:"POST",body});}
      if(action.kind==="learning-resource") {body.status=action.decision;await api("/admin/learning/resources/"+action.id,{method:"PATCH",body});}
      setAction(null);flash("Decision saved and recorded in the audit trail.");await load();
    }catch(x){setError(x.message||"Action failed.");}finally{setSaving(false);}
  };
  const openQuestionEditor=(question=null)=>{
    setQuestionEditing(question);
    setQuestionDraft(question?{...question,options:(question.options||[]).join("\n"),time_minutes:Math.round((question.time_allocation||60)/60)}:
      {category_id:questionCategory==="all"?"CAT-01":questionCategory,subcategory:"",question_type:"MCQ",difficulty_level:"Medium",question_text:"",options:"",correct_answer:"",explanation:"",reference_answer:"",grading_rubric:"",marks:2,time_minutes:2,status:"draft"});
    setQuestionModal(true);
  };
  const saveQuestion=async(e)=>{
    e.preventDefault();setQuestionSaving(true);setError("");
    const payload={...questionDraft,options:questionDraft.options.split("\n").map(x=>x.trim()).filter(Boolean),marks:Number(questionDraft.marks),time_allocation:Number(questionDraft.time_minutes)*60};delete payload.time_minutes;
    try{if(questionEditing)await api("/admin/questions/"+questionEditing.id,{method:"PUT",body:payload});else await api("/admin/questions",{method:"POST",body:payload});
      setQuestionModal(false);flash(questionEditing?"Question updated successfully.":"Question added to the bank.");await load();
    }catch(x){setError(x.message||"Could not save question.");}finally{setQuestionSaving(false);}
  };
  const previewQuestionImport=async(e)=>{
    e.preventDefault();if(!importFile)return;setImportBusy(true);setError("");setImportPreview(null);
    try{const form=new FormData();form.append("file",importFile);form.append("category_id",importCategory);setImportPreview(await api("/admin/questions/import/preview",{method:"POST",form}));}
    catch(x){setError(x.message||"Could not preview this file.");}finally{setImportBusy(false);}
  };
  const commitQuestionImport=async()=>{
    if(!importPreview)return;setImportBusy(true);setError("");
    try{const valid=importPreview.items.filter(x=>!x.errors).map(({errors,duplicate,...q})=>q);
      const result=await api("/admin/questions/import/commit",{method:"POST",body:{questions:valid}});
      setImportPreview(null);setImportFile(null);flash("Import complete · "+result.added+" added · "+result.skipped+" duplicates skipped · "+result.rejected+" rejected.");await load();
    }catch(x){setError(x.message||"Import could not be completed.");}finally{setImportBusy(false);}
  };
  const printAssessmentReport=(report)=>{
    const esc=(v)=>String(v??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const evidence=report.answer_evidence||[];
    const questions=evidence.map(q=>`<article class="question"><h3>Question ${esc(q.number)} · ${esc(q.topic)} · ${esc(q.type)}</h3><p class="prompt">${esc(q.question)}</p>${q.options?.length?`<ol>${q.options.map(o=>`<li>${esc(o)}</li>`).join("")}</ol>`:""}<div class="answer"><b>Candidate response</b><pre>${esc(q.selected_option??q.candidate_answer??"Not captured in this attempt")}</pre></div><div class="meta">Automated result: ${q.automated_correct===true?"Correct":q.automated_correct===false?"Incorrect":"Not available"} · Difficulty: ${esc(q.difficulty)} · Response time: ${esc(q.time_seconds)} sec</div></article>`).join("");
    const html=`<!doctype html><html><head><meta charset="utf-8"><title>Assessment evaluation report</title><style>body{font:14px/1.55 Arial,sans-serif;color:#18232b;margin:34px}h1{font-size:26px;margin:0}h2{margin-top:28px} .sub{color:#61717b}.summary{display:flex;gap:16px;margin:22px 0}.metric{padding:14px;border:1px solid #dce4e8;border-radius:10px;min-width:120px}.metric b{display:block;font-size:24px}.question{border:1px solid #dce4e8;border-radius:10px;padding:16px;margin:14px 0;break-inside:avoid}.prompt{font-weight:600}.answer{background:#f4f7f8;padding:12px;border-radius:8px}.answer pre{white-space:pre-wrap;font:13px/1.5 monospace;margin:8px 0}.meta{font-size:12px;color:#61717b;margin-top:10px}.notice{background:#fff8e8;border-left:4px solid #d59a35;padding:12px;margin:18px 0} @media print{body{margin:15mm}.question{break-inside:avoid}}</style></head><body><h1>SkillBridge · Assessment evaluation report</h1><p class="sub">Candidate: ${esc(report.candidate_name)} · ${esc(report.email)}<br>Attempt: ${esc(report.id)} · Type: ${esc(report.kind)} · Completed: ${esc(stamp(report.finished))}</p><div class="summary"><div class="metric"><b>${esc(report.result?.overall??"—")}/100</b>Overall score</div><div class="metric"><b>${esc(report.result?.level??"—")}</b>Estimated level</div><div class="metric"><b>${esc(report.result?.trust??"—")}</b>Integrity signal</div></div><div class="notice"><b>Evaluation method:</b> ${esc(report.evaluation_method||"Automated grading details unavailable.")} The score is a support signal, not a final hiring decision. Manual review is required.</div><h2>Topic breakdown</h2><ul>${Object.entries(report.result?.topics||{}).map(([k,v])=>`<li>${esc(k)}: ${esc(v.score)}% · ${esc(v.level)} · ${esc(v.n)} questions</li>`).join("")}</ul><h2>Question-by-question evidence</h2>${questions||"<p>No question-level evidence is available for this attempt.</p>"}<p class="sub">Generated by SkillBridge admin review. Save or print this page as PDF using your browser's print dialog.</p><script>window.onload=()=>setTimeout(()=>window.print(),300)</script></body></html>`;
    const win=window.open("","_blank");
    if(!win){setError("Allow pop-ups to generate the printable PDF report.");return;}
    win.document.open();win.document.write(html);win.document.close();
  };
  const counts=data.overview?.counts||{};
  const rows=(key)=>data[key]?.items||[];
  const filtered=(items,fields)=>items.filter(x=>fields.some(f=>String(x[f]??"").toLowerCase().includes(search.toLowerCase())));
  const stamp=(v)=>v?new Date(Number(v)*1000).toLocaleString():"—";
  const pill=(v)=><span className={"ad-pill ad-"+String(v||"unknown").toLowerCase().replace(/[^a-z0-9]+/g,"-")}>{String(v||"unknown").replaceAll("_"," ")}</span>;
  const metric=(label,value,sub,icon)=> <article className="ad-metric"><span>{icon}</span><div><small>{label}</small><strong>{value??"—"}</strong><em>{sub}</em></div></article>;
  const table=(heads,body,empty)=> <div className="ad-table-wrap"><table className="ad-table"><thead><tr>{heads.map(h=><th>{h}</th>)}</tr></thead><tbody>{body}</tbody></table>{!body.length&&<div className="ad-empty">{empty||"No records to display."}</div>}</div>;
  const section=(k)=>setPage(k);
  const scopePages={super_admin:["overview","users","staff","companies","jobs","assessments","appeals","questions","learning","ai","billing","privacy","quality","audit"],trust_safety_reviewer:["users","assessments","appeals","quality"],question_content_admin:["questions","learning","assessments"],company_verification_admin:["companies","jobs","assessments"],support_agent:["users"],finance_admin:["billing","learning"],compliance_officer:["ai","privacy","quality","audit"]};
  const visibleNav=nav.filter(([id])=>(scopePages[adminRole]||[]).includes(id));
  useEffect(()=>{if(visibleNav.length&&!visibleNav.some(x=>x[0]===page))setPage(visibleNav[0][0]);},[adminRole]);
  const current=nav.find(x=>x[0]===page);
  return <div className="ad-app">
    <aside className="ad-sidebar">
      <div className="ad-brand"><span className="ad-brand-mark">S</span><span>SkillBridge<small>PLATFORM CONTROL</small></span></div>
      <div className="ad-admin-label"><span className="ad-online-dot"/> ADMIN CONSOLE</div>
      <nav className="ad-nav">{visibleNav.map(([id,label,icon])=><button key={id} className={page===id?"active":""} onClick={()=>{setPage(id);setSearch("");}}><span>{icon}</span>{label}{id==="companies"&&counts.pending_companies>0?<b>{counts.pending_companies}</b>:id==="jobs"&&counts.pending_jobs>0?<b>{counts.pending_jobs}</b>:id==="appeals"&&counts.open_appeals>0?<b>{counts.open_appeals}</b>:null}</button>)}</nav>
      <div className="ad-sidebar-bottom"><div className="ad-security-card"><span>◈</span><strong>Privileged workspace</strong><p>Admin actions are permission-checked and recorded.</p></div><div className="ad-admin-profile"><div>SA</div><span><b>{adminRole.replaceAll("_"," ")}</b><small>Internal staff access</small></span><i>●</i></div><button className="ad-sidebar-signout" type="button" onClick={onLogout}>↪ Sign out of admin</button></div>
    </aside>
    <main className="ad-main">
      <header className="ad-topbar"><div><span>Platform</span><b>/</b>{current?.[1]||"Overview"}</div><div className="ad-top-actions"><span className="ad-secure-label"><i/> Secure admin session</span><button onClick={load} disabled={busy} title="Refresh data">↻</button><span className="ad-top-avatar">SA</span><button className="ad-signout" onClick={onLogout} type="button">↪ Sign out</button></div></header>
      <div className="ad-content">
        {toast&&<div className="ad-toast" role="status">✓ {toast}<button onClick={()=>setToast("")}>×</button></div>}
        {error&&<div className="ad-error" role="alert">{error}<button onClick={()=>setError("")}>×</button></div>}
        <div className="ad-page-heading"><div><div className="ad-eyebrow">SKILLBRIDGE GLOBAL · PLATFORM OPERATIONS</div><h1>{page==="overview"?"Platform overview":current?.[1]}</h1><p>{page==="overview"?"A clear view of platform activity, trust queues and operational health.":({users:"Manage account access with reason-coded status changes.",companies:"Verify organisations before they can publish jobs or access applicants.",jobs:"Review reported and pending job posts with documented decisions.",assessments:"Review completed assessment evidence without altering original answers or scores.",appeals:"Handle candidate appeals with an auditable, reasoned outcome.",questions:"Inspect the curated assessment bank and its current governance limitations.",ai:"Monitor configured AI provider status and fallback behaviour.",billing:"Review billing module availability and integration readiness.",learning:"Verify learning resources, monitor candidate progress and govern retake credits.",privacy:"Review consent coverage and the current data-rights capabilities.",quality:"Monitor review outcomes and quality signals without protected-trait inference.",audit:"Search the immutable-style application audit event history."})[page]}</p></div><button className="ad-refresh" onClick={load} disabled={busy}>{busy?"Refreshing…":"↻ Refresh data"}</button></div>
        {page==="overview"&&<section>
          <div className="ad-metrics">{metric("Total accounts",counts.users,"Registered users","♙")}{metric("Candidates",counts.candidates,"Individual accounts","◎")}{metric("Companies",counts.companies,counts.verified_companies+" verified","▣")}{metric("Published jobs",counts.published_jobs,counts.pending_jobs+" awaiting review","▤")}{metric("Applications",counts.applications,"Submitted applications","↗")}{metric("Completed assessments",counts.completed_attempts,counts.open_appeals+" open appeals","✓")}</div>
          <div className="ad-overview-grid">
            <div className="ad-panel"><div className="ad-panel-head"><div><h2>Needs attention</h2><p>Queues that may require an administrator</p></div><span className="ad-live-tag">LIVE DATA</span></div>{[["Company verification",counts.pending_companies,"companies","companies"],["Job moderation",counts.pending_jobs,"posts","jobs"],["Assessment appeals",counts.open_appeals,"appeals","appeals"],["Phase-two requests",rows("phaseTwo").filter(x=>x.status==="pending_platform_review").length,"requests","assessments"]].map(([label,n,unit,target])=><button className="ad-queue-row" onClick={()=>section(target)}><span className="ad-queue-icon">◇</span><span><b>{label}</b><small>{n||0} {unit} pending review</small></span><strong>{n||0}</strong><i>→</i></button>)}</div>
            <div className="ad-panel"><div className="ad-panel-head"><div><h2>Platform readiness</h2><p>Connected service status</p></div></div><div className="ad-health-row"><span><i className="ad-health-green"/> API / database</span><b>Connected</b></div><div className="ad-health-row"><span><i className={data.ai?.configured?"ad-health-green":"ad-health-amber"}/> AI provider</span><b>{data.ai?.status==="configured"?"Configured":"Fallback mode"}</b></div><div className="ad-health-row"><span><i className="ad-health-amber"/> Billing</span><b>Not enabled</b></div><div className="ad-health-row"><span><i className="ad-health-amber"/> Retention workers</span><b>Not configured</b></div><p className="ad-footnote">Health indicators reflect available application configuration, not external uptime guarantees.</p></div>
          </div>
          <div className="ad-panel ad-lower-note"><strong>Platform principles</strong><div><span>✓ Human-led hiring decisions</span><span>✓ Deterministic assessment scoring</span><span>✓ Candidate-controlled sharing</span><span>✓ Auditable admin actions</span></div></div>
        </section>}
        {page==="users"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Accounts directory</h2><p>Search users and manage account availability.</p></div><span className="ad-count">{rows("users").length} records</span></div><div className="ad-toolbar"><input placeholder="Search name or email…" value={search} onChange={e=>setSearch(e.target.value)}/><select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All roles</option><option value="candidate">Candidates</option><option value="company">Company users</option><option value="admin">Admins</option></select></div>{table(["USER","ROLE","CREATED","STATUS","ACTIONS"],filtered(rows("users").filter(x=>filter==="all"||x.role===filter),["name","email"]).map(u=><tr><td><b>{u.name}</b><small>{u.email}</small><small className="ad-id">ID {u.id}</small></td><td>{pill(u.role)}</td><td>{stamp(u.created)}</td><td>{pill(u.account_status||"active")}</td><td>{u.role!=="admin"&&adminRole!=="support_agent"&&<button className="ad-action-btn" onClick={()=>openAction("user",u.id,(u.account_status||"active")==="active"?"suspended":"active")}>{(u.account_status||"active")==="active"?"Suspend":"Reactivate"}</button>}</td></tr>),"No accounts match your search.")}</section>}

        {page==="staff"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Privileged staff access</h2><p>Admin accounts are provisioned internally. Assign each staff account only the scope required for their duties.</p></div><span className="ad-count">{rows("staff").length} staff accounts</span></div><div className="ad-info-banner">Create internal admin accounts here. Public signup cannot grant admin access. Choose the least-privilege role needed for each person.</div><form onSubmit={createAdmin} style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:12,alignItems:"end",padding:"16px 0"}}><label>Full name<input className="ad-role-select" required minLength={2} maxLength={80} value={adminDraft.name} onChange={e=>setAdminDraft({...adminDraft,name:e.target.value})} placeholder="Admin name"/></label><label>Admin email<input className="ad-role-select" type="email" required value={adminDraft.email} onChange={e=>setAdminDraft({...adminDraft,email:e.target.value})} placeholder="admin@example.com"/></label><label>Password<input className="ad-role-select" type="password" required minLength={14} maxLength={128} autoComplete="new-password" value={adminDraft.password} onChange={e=>setAdminDraft({...adminDraft,password:e.target.value})} placeholder="At least 14 characters"/></label><label>Permission role<select className="ad-role-select" value={adminDraft.admin_role} onChange={e=>setAdminDraft({...adminDraft,admin_role:e.target.value})}><option value="support_agent">Support Agent</option><option value="trust_safety_reviewer">Trust & Safety Reviewer</option><option value="question_content_admin">Question / Content Admin</option><option value="company_verification_admin">Company Verification Admin</option><option value="finance_admin">Finance Admin</option><option value="compliance_officer">Compliance Officer</option><option value="super_admin">Super Admin</option></select></label><button className="ad-mini-primary" disabled={creatingAdmin||adminDraft.password.length<14}>{creatingAdmin?"Creating…":"＋ Add Admin"}</button></form><div className="ad-info-banner">Passwords are stored as secure hashes. Do not reuse or share passwords in public channels; provide each admin their credentials privately.</div>{table(["STAFF MEMBER","ADMIN ROLE","UPDATED","ACCESS SCOPE"],rows("staff").map(s=><tr><td><b>{s.name}</b><small>{s.email}</small><small>ID {s.id}</small></td><td><select className="ad-role-select" value={s.admin_role} onChange={async e=>{try{await api("/admin/console/staff/"+s.id,{method:"PUT",body:{admin_role:e.target.value}});flash("Admin role updated.");await load();}catch(x){setError(x.message);}}}><option value="super_admin">Super Admin</option><option value="trust_safety_reviewer">Trust & Safety Reviewer</option><option value="question_content_admin">Question / Content Admin</option><option value="company_verification_admin">Company Verification Admin</option><option value="support_agent">Support Agent</option><option value="finance_admin">Finance Admin</option><option value="compliance_officer">Compliance Officer</option></select></td><td>{stamp(s.updated_at)}</td><td><small>{s.admin_role.replaceAll("_"," ")}</small></td></tr>),"No internally provisioned admin staff accounts found.")}</section>}
        {page==="companies"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Organisation verification queue</h2><p>Review legal identity details and evidence notes.</p></div><span className="ad-count">{rows("companies").length} requests</span></div><div className="ad-toolbar"><input placeholder="Search company or legal name…" value={search} onChange={e=>setSearch(e.target.value)}/><select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All statuses</option><option value="pending_review">Pending</option><option value="verified">Verified</option><option value="needs_information">Needs information</option><option value="rejected">Rejected</option></select></div>{table(["ORGANISATION","LEGAL DETAILS","SUBMITTED","STATUS","DECISION"],filtered(rows("companies").filter(x=>filter==="all"||x.status===filter),["company_name","legal_name","country"]).map(x=><tr><td><b>{x.company_name}</b><small>{x.website||"Website not provided"}</small></td><td><b>{x.legal_name}</b><small>{x.country} · {x.business_id||"No registration ID"}</small><small>{x.evidence_note}</small></td><td>{stamp(x.submitted_at)}</td><td>{pill(x.status)}</td><td><div className="ad-row-actions">{x.status==="pending_review"&&<><button className="ad-mini-primary" onClick={()=>openAction("company",x.company_id,"verified")}>Verify</button><button onClick={()=>openAction("company",x.company_id,"needs_information")}>Request info</button><button className="ad-danger-link" onClick={()=>openAction("company",x.company_id,"rejected")}>Reject</button></>}</div></td></tr>),"No verification requests found.")}</section>}
        {page==="jobs"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Job moderation</h2><p>Approve compliant listings or return them with a clear reason.</p></div><span className="ad-count">{rows("jobs").length} records</span></div><div className="ad-toolbar"><input placeholder="Search job title or company…" value={search} onChange={e=>setSearch(e.target.value)}/></div>{table(["JOB / COMPANY","LOCATION","SUBMITTED","STATUS","MODERATION"],filtered(rows("jobs"),["title","company_name","description"]).map(x=><tr><td><b>{x.title}</b><small>{x.company_name}</small><small>{x.description?.slice(0,130)}{x.description?.length>130?"…":""}</small></td><td>{x.location}<small>{x.employment_type}</small></td><td>{stamp(x.created)}</td><td>{pill(x.status)}</td><td><div className="ad-row-actions">{x.status==="pending_review"&&<><button className="ad-mini-primary" disabled={!x.verified} onClick={()=>openAction("job",x.id,"published")}>Approve</button><button onClick={()=>openAction("job",x.id,"needs_changes")}>Request changes</button><button className="ad-danger-link" onClick={()=>openAction("job",x.id,"rejected")}>Reject</button></>}</div></td></tr>),"No jobs are awaiting moderation.")}</section>}
        {page==="assessments"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Assessment review</h2><p>Review the automated score alongside every question and the candidate's submitted response before recording a human decision.</p></div><span className="ad-count">{rows("assessments").length} attempts</span></div><div className="ad-info-banner">The current assessment engine uses deterministic answer-key / SQL grading. This report shows that automated evaluation and source evidence; it does not claim an LLM-generated judgement. Older attempts may not contain saved raw answers.</div><div className="ad-toolbar"><input placeholder="Search candidate or email…" value={search} onChange={e=>setSearch(e.target.value)}/></div>{table(["CANDIDATE","ASSESSMENT","SCORE","REVIEW STATUS","REPORT / ACTION"],filtered(rows("assessments"),["candidate_name","email","kind"]).map(x=><tr><td><b>{x.candidate_name}</b><small>{x.email}</small></td><td>{x.kind}<small>{stamp(x.finished)}</small></td><td><strong>{x.result?.overall??"—"}<small>/ 100</small></strong></td><td>{pill(x.review_decision||"pending_review")}</td><td><div className="ad-row-actions"><button onClick={()=>setSelectedReport(x)}>View report</button><button className="ad-mini-primary" onClick={()=>openAction("assessment",x.id,"approved")}>Confirm valid</button><button onClick={()=>openAction("assessment",x.id,"needs_more_info")}>More info</button><button className="ad-danger-link" onClick={()=>openAction("assessment",x.id,"invalidated")}>Invalid</button></div></td></tr>),"No completed assessments in the review list.")}<div className="ad-panel-head ad-subhead"><div><h2>Company assessment requests</h2><p>Company-authored phase-two requests require platform approval.</p></div></div>{table(["REQUEST","COMPANY / CANDIDATE","STATUS","REVIEW"],rows("phaseTwo").map(x=><tr><td><b>{x.title}</b><small>{x.requirements}</small></td><td>{x.company_name}<small>{x.candidate_name} · {x.job_title}</small></td><td>{pill(x.status)}</td><td>{x.status==="pending_platform_review"&&<div className="ad-row-actions"><button className="ad-mini-primary" onClick={()=>openAction("phase",x.id,"approved")}>Approve</button><button onClick={()=>openAction("phase",x.id,"needs_changes")}>Request changes</button><button className="ad-danger-link" onClick={()=>openAction("phase",x.id,"rejected")}>Reject</button></div>}</td></tr>),"No company assessment requests.")}</section>}
        {page==="appeals"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Candidate appeals</h2><p>Appeals should be reviewed independently where staffing permits.</p></div><span className="ad-count">{rows("appeals").length} appeals</span></div>{table(["CANDIDATE","ATTEMPT","APPEAL NOTE","STATUS","OUTCOME"],rows("appeals").map(x=><tr><td><b>{x.candidate_name}</b><small>{x.email}</small></td><td><code>{x.attempt_id}</code><small>{stamp(x.finished)}</small></td><td>{x.appeal}</td><td>{pill(x.decision||"open")}</td><td>{!x.decision&&<div className="ad-row-actions"><button className="ad-mini-primary" onClick={()=>openAction("appeal",x.attempt_id,"upheld")}>Uphold</button><button onClick={()=>openAction("appeal",x.attempt_id,"overturned")}>Overturn</button><button onClick={()=>openAction("appeal",x.attempt_id,"needs_more_info")}>More info</button></div>}</td></tr>),"No candidate appeals have been submitted.")}</section>}
        {page==="questions"&&(()=>{
          const qdata=data.questions||{},cats=qdata.categories||[],allQuestions=qdata.items||[];
          const visibleQuestions=allQuestions.filter(q=>(questionCategory==="all"||q.category_id===questionCategory)&&(questionStatus==="all"||q.status===questionStatus)&&[q.question_text,q.subcategory,q.question_type,q.id].some(v=>String(v||"").toLowerCase().includes(questionQuery.toLowerCase())));
          const activeCategory=cats.find(c=>c.id===questionCategory);
          return <section className="ad-question-workspace">
            <div className="ad-question-hero"><div><div className="ad-eyebrow">ASSESSMENT CONTENT · GOVERNANCE</div><h2>Build a stronger question bank.</h2><p>Organise, author and import assessment questions across nine skill domains. Every change is versioned and audited.</p><div className="ad-question-hero-tags"><span>✓ Database-backed</span><span>◈ Versioned edits</span><span>↗ Bulk import</span></div></div><div className="ad-question-hero-icon">Q<span>+</span></div></div>
            <div className="ad-graphrag-panel">
              <div><span className="ad-eyebrow">GRAPH RETRIEVAL · ASSESSMENT READINESS</span><h3>CV + Fundamental GraphRAG</h3><p>{data.graphrag?.active_question_count??"—"} active questions · Fundamental {data.graphrag?.fundamental_selected_count??0}/25 · Meilisearch {data.graphrag?.meilisearch_configured?"configured":"not connected"} · Neo4j {data.graphrag?.neo4j_configured?"configured":"not connected"}</p><small>Fundamental readiness: {data.graphrag?.fundamental_readiness||"loading"}. Required: 25 fundamental questions plus 5 approved questions for every confirmed CV skill. Use category + subcategory, difficulty and format carefully.</small>{data.graphrag?.fundamental_shortages?.length>0&&<small>Missing: {data.graphrag.fundamental_shortages.slice(0,4).map(x=>x.format||x.topic+" "+x.difficulty).join(" · ")}</small>}</div>
              <button className="ad-q-add-btn" onClick={async()=>{try{const result=await api("/admin/assessment/graphrag/reindex",{method:"POST"});flash("Graph index updated · "+result.active_questions+" approved questions.");await load();}catch(x){setError(x.message||"Graph index update failed.");}}}>Rebuild search + graph index</button>
            </div>
            <div className="ad-question-stats"><article><span className="ad-qstat-icon">▤</span><div><small>Total questions</small><strong>{qdata.summary?.total??allQuestions.length}</strong><em>Across all categories</em></div></article><article><span className="ad-qstat-icon ad-qstat-green">✓</span><div><small>Active</small><strong>{qdata.summary?.active??0}</strong><em>Ready for use</em></div></article><article><span className="ad-qstat-icon ad-qstat-amber">◷</span><div><small>Draft</small><strong>{qdata.summary?.draft??0}</strong><em>Awaiting approval</em></div></article><article><span className="ad-qstat-icon ad-qstat-grey">▣</span><div><small>Archived</small><strong>{qdata.summary?.archived??0}</strong><em>Retained for history</em></div></article></div>
            <div className="ad-question-section-head"><div><h3>Explore by category</h3><p>Select a domain to view its question inventory.</p></div><button className="ad-q-add-btn" onClick={()=>openQuestionEditor()}>＋ Create question</button></div>
            <div className="ad-category-grid">{cats.map((c,i)=><button key={c.id} className={"ad-category-card "+(questionCategory===c.id?"selected":"")} onClick={()=>setQuestionCategory(questionCategory===c.id?"all":c.id)}><span className={"ad-category-icon ad-cat-"+(i+1)}>{c.icon}</span><span className="ad-category-copy"><b>{c.name}</b><small>{c.description}</small></span><strong>{c.count||0}<small>questions</small></strong></button>)}</div>
            <div className="ad-question-panel"><div className="ad-question-section-head"><div><h3>{activeCategory?activeCategory.name+" questions":"All questions"}</h3><p>{activeCategory?activeCategory.description:"Browse the complete question bank and manage its lifecycle."}</p></div><span className="ad-count">{visibleQuestions.length} shown</span></div>
              <div className="ad-question-toolbar"><label className="ad-q-search">⌕<input value={questionQuery} onChange={e=>setQuestionQuery(e.target.value)} placeholder="Search question, subcategory or ID…"/></label><select value={questionStatus} onChange={e=>setQuestionStatus(e.target.value)}><option value="all">All statuses</option><option value="active">Active</option><option value="draft">Draft</option><option value="archived">Archived</option></select><button className="ad-q-clear" onClick={()=>{setQuestionCategory("all");setQuestionStatus("all");setQuestionQuery("");}}>Reset filters</button></div>
              <div className="ad-question-list">{visibleQuestions.map(q=><article className="ad-question-row" key={q.id}><div className="ad-q-type-icon">{q.question_type==="MCQ"?"☷":q.question_type.toLowerCase().includes("coding")?"</>":"✎"}</div><div className="ad-question-main"><div className="ad-question-meta"><span>{cats.find(c=>c.id===q.category_id)?.name||q.category_id}</span><i>·</i><span>{q.subcategory}</span><i>·</i><span>{q.question_type}</span></div><h4>{q.question_text}</h4><div className="ad-question-tags"><span className={"ad-q-difficulty ad-q-"+q.difficulty_level.toLowerCase()}>{q.difficulty_level}</span><span>{q.marks} marks</span><span>{Math.round(q.time_allocation/60)} min</span><span>v{q.version}</span></div></div><div className="ad-question-actions">{pill(q.status)}<button onClick={()=>openQuestionEditor(q)}>Edit ↗</button></div></article>)}{!visibleQuestions.length&&<div className="ad-q-empty"><span>⌕</span><b>No questions found</b><p>Try another category or adjust your search filters.</p><button onClick={()=>openQuestionEditor()}>Create the first question</button></div>}</div>
            </div>
            <div className="ad-question-import"><div><span className="ad-q-upload-icon">⇧</span><div><h3>Import questions in bulk</h3><p>Upload CSV, JSON or text-based PDF. Preview and validate records before saving them to the database.</p><small>Maximum 5 MB · Up to 1,000 questions per import</small></div></div>
              <form onSubmit={previewQuestionImport}><label className="ad-q-file"><input type="file" accept=".csv,.json,.pdf,application/pdf,text/csv,application/json" onChange={e=>{setImportFile(e.target.files?.[0]||null);setImportPreview(null);}}/><span>＋ Choose file</span><b>{importFile?.name||"No file selected"}</b></label><select value={importCategory} onChange={e=>setImportCategory(e.target.value)}><option value="">Choose default category (if file has no category)</option>{cats.map(c=><option value={c.id}>{c.name}</option>)}</select><button className="ad-q-add-btn" disabled={!importFile||importBusy}>{importBusy?"Preparing preview…":"Preview import →"}</button></form>
              {importPreview&&<div className="ad-import-preview"><div className="ad-import-preview-head"><div><b>Import preview</b><small>{importPreview.filename} · Review before committing</small></div><button onClick={()=>setImportPreview(null)}>×</button></div><div className="ad-import-counts"><span><b>{importPreview.total}</b>Total</span><span><b>{importPreview.valid}</b>Valid</span><span><b>{importPreview.invalid}</b>Invalid / duplicate</span></div>{importPreview.pdf_note&&<p className="ad-q-pdf-note">{importPreview.pdf_note}</p>}<div className="ad-import-items">{importPreview.items.slice(0,8).map((q,i)=><div><b>{i+1}. {q.question_text.slice(0,150)||"Empty question"}</b><small>{q.category_id} · {q.difficulty_level} · {q.question_type} · {q.time_allocation}s</small>{q.errors.length>0&&<em>{q.errors.join(" ")}</em>}</div>)}</div>{importPreview.items.length>8&&<small>Showing first 8 of {importPreview.items.length} records.</small>}<button className="ad-q-add-btn" disabled={!importPreview.valid||importBusy} onClick={commitQuestionImport}>{importBusy?"Importing…":"Import "+importPreview.valid+" valid questions"}</button></div>}
            </div>
          </section>;
        })()}
        {page==="ai"&&<section className="ad-two-col"><div className="ad-panel"><div className="ad-panel-head"><div><h2>AI provider</h2><p>Current extraction provider configuration</p></div>{pill(data.ai?.status||"unknown")}</div><div className="ad-detail-list"><div><span>Provider</span><b>{data.ai?.provider||"—"}</b></div><div><span>Model</span><b>{data.ai?.model||"—"}</b></div><div><span>API configured</span><b>{data.ai?.configured?"Yes":"No"}</b></div><div><span>Fallback</span><b>{data.ai?.fallback||"—"}</b></div><div><span>Usage telemetry</span><b>{data.ai?.metrics_available?"Available":"Not instrumented"}</b></div></div></div><div className="ad-panel"><h2>Operations notes</h2><p>{data.ai?.note||"AI operations telemetry is not connected."}</p><div className="ad-info-banner">No AI-generated score or hiring decision is authorised. AI output must be validated and grounded in source data.</div></div></section>}
        {page==="billing"&&<section className="ad-billing">
          <div className="ad-billing-hero"><div><span className="ad-eyebrow">FINANCE OPERATIONS</span><h2>Billing & payments</h2><p>Manage plans, review payment records, refunds and retake credits.</p></div><div className="ad-billing-provider"><span className="ad-provider-mark">৳</span><div><b>{data.billing?.provider||"SSLCommerz"}</b><small>{data.billing?.live_enabled?"Live mode":data.billing?.provider_configured?"Credentials detected · live disabled":"Gateway not connected"}</small></div><span className={data.billing?.live_enabled?"ad-status-live":"ad-status-pending"}>{data.billing?.live_enabled?"LIVE":"NOT LIVE"}</span></div></div>
          <div className="ad-billing-notice"><b>Checkout is safely disabled.</b><span>{data.billing?.note||"Merchant approval, credentials and verified callbacks are required before accepting payments."}</span></div>
          <div className="ad-billing-metrics">
            <article><small>Confirmed paid invoices</small><strong>{data.billing?.summary?.paid_invoices??0}</strong><span>Verified records only</span></article>
            <article><small>Gross confirmed payments</small><strong>৳{((data.billing?.summary?.gross_paid_minor||0)/100).toLocaleString("en-BD",{minimumFractionDigits:2,maximumFractionDigits:2})}</strong><span>BDT minor units converted</span></article>
            <article><small>Awaiting payment</small><strong>{data.billing?.summary?.pending_invoices??0}</strong><span>Not counted as revenue</span></article>
            <article><small>Refund requests</small><strong>{data.billing?.summary?.refund_requests??0}</strong><span>Awaiting Finance review</span></article>
          </div>
          <div className="ad-panel">
            <div className="ad-panel-head"><div><h3>Pricing plans</h3><p>New plans are saved inactive. Configure final pricing before launch.</p></div><span className="ad-count">{data.billing?.plans?.length||0} plans</span></div>
            <div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Plan</th><th>Price</th><th>Billing cycle</th><th>Retake credits</th><th>Status</th></tr></thead><tbody>{(data.billing?.plans||[]).map(p=><tr key={p.id}><td><b>{p.name}</b><small>{p.description||"—"}</small></td><td>৳{(p.amount_minor/100).toLocaleString("en-BD",{minimumFractionDigits:2})}</td><td>{p.interval}</td><td>{p.retake_credits}</td><td>{pill(p.active?"active":"inactive")}</td></tr>)}</tbody></table></div>
            {!(data.billing?.plans||[]).length&&<div className="ad-billing-empty">No pricing plans have been configured yet. Final prices remain intentionally unset.</div>}
          </div>
          <div className="ad-two-col ad-billing-lists">
            <div className="ad-panel"><div className="ad-panel-head"><div><h3>Recent invoices</h3><p>Only verified successful payments count as paid.</p></div><span className="ad-count">{data.billing?.invoices?.length||0}</span></div>{(data.billing?.invoices||[]).length?<div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Invoice</th><th>Customer</th><th>Amount</th><th>Status</th></tr></thead><tbody>{data.billing.invoices.slice(0,8).map(x=><tr key={x.id}><td>{x.id.slice(0,10)}</td><td>{x.customer_email||"—"}</td><td>৳{(x.amount_minor/100).toFixed(2)}</td><td>{pill(x.status)}</td></tr>)}</tbody></table></div>:<div className="ad-billing-empty">No invoices recorded.</div>}</div>
            <div className="ad-panel"><div className="ad-panel-head"><div><h3>Refund queue</h3><p>Approval is not a completed refund.</p></div><span className="ad-count">{data.billing?.refunds?.length||0}</span></div>{(data.billing?.refunds||[]).length?<div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Request</th><th>Customer</th><th>Amount</th><th>Status</th></tr></thead><tbody>{data.billing.refunds.slice(0,8).map(x=><tr key={x.id}><td>{x.id.slice(0,10)}</td><td>{x.customer_email||"—"}</td><td>৳{(x.amount_minor/100).toFixed(2)}</td><td>{pill(x.status)}</td></tr>)}</tbody></table></div>:<div className="ad-billing-empty">No refund requests.</div>}</div>
          </div>
          <div className="ad-panel"><div className="ad-panel-head"><div><h3>Retake credit ledger</h3><p>Append-only entries; balance changes are traceable to an actor and reason.</p></div><span className="ad-count">{data.billing?.ledger?.length||0} entries</span></div>{(data.billing?.ledger||[]).length?<div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Customer</th><th>Change</th><th>Entry</th><th>Reason</th><th>Date</th></tr></thead><tbody>{data.billing.ledger.slice(0,12).map(x=><tr key={x.id}><td>{x.customer_email||x.user_id}</td><td>{x.delta>0?"+":""}{x.delta}</td><td>{x.entry_type}</td><td>{x.reason}</td><td>{new Date(x.created*1000).toLocaleDateString()}</td></tr>)}</tbody></table></div>:<div className="ad-billing-empty">No credit adjustments recorded.</div>}</div>
        </section>}
        {page==="learning"&&<section className="ad-learning">
          <div className="ad-learning-hero"><div><span className="ad-eyebrow">LEARNING QUALITY & RETAKE GOVERNANCE</span><h2>Build a learning system candidates can trust.</h2><p>Verify every resource, understand real learning progress and keep retake entitlements transparent and auditable.</p></div><div className="ad-learning-mark">↗</div></div>
          <div className="ad-billing-metrics"><article><small>Catalogue resources</small><strong>{data.learning?.summary?.resources??0}</strong><span>All catalogue statuses</span></article><article><small>Verified resources</small><strong>{data.learning?.summary?.verified??0}</strong><span>Visible to candidates</span></article><article><small>Pending review</small><strong>{data.learning?.summary?.pending??0}</strong><span>Awaiting curator</span></article><article><small>Retakes consumed</small><strong>{data.learning?.summary?.retakes_consumed??0}</strong><span>Ledger-confirmed attempts</span></article></div>
          <div className="ad-panel"><div className="ad-panel-head"><div><h3>Resource governance</h3><p>New resources remain hidden from candidates until a human verifies them.</p></div><span className="ad-count">{data.learning?.resources?.length||0} resources</span></div>
            {(adminRole==="super_admin"||adminRole==="question_content_admin")&&<form className="ad-learning-form" onSubmit={async e=>{e.preventDefault();try{const f=e.currentTarget;await api("/admin/learning/resources",{method:"POST",body:{topic:f.topic.value,title:f.title.value,provider:f.provider.value,url:f.url.value,language:f.language.value,level:Number(f.level.value),licence:f.licence.value,estimated_minutes:Number(f.minutes.value)}});f.reset();flash("Resource added to the pending review queue.");await load();}catch(x){setError(x.message||"Could not add resource.");}}}>
              <label>Topic<input name="topic" required placeholder="e.g. python"/></label><label>Resource title<input name="title" required placeholder="Official Python tutorial"/></label><label>Provider<input name="provider" required placeholder="Python Software Foundation"/></label><label>HTTPS URL<input name="url" type="url" required placeholder="https://…"/></label><label>Language<input name="language" defaultValue="en" required/></label><label>Level<select name="level"><option value="1">Beginner</option><option value="2">Intermediate</option><option value="3">Advanced</option><option value="4">Expert</option><option value="5">Specialist</option></select></label><label>Licence / usage<input name="licence" placeholder="Licence or usage note"/></label><label>Estimated minutes<input name="minutes" type="number" min="1" defaultValue="30"/></label><button className="ad-q-add-btn" type="submit">+ Add for review</button>
            </form>}
            <div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Resource</th><th>Topic</th><th>Provider / language</th><th>Status</th><th>Last checked</th><th>Review action</th></tr></thead><tbody>{(data.learning?.resources||[]).map(r=><tr key={r.id}><td><b>{r.title}</b><small><a href={r.url} target="_blank" rel="noreferrer">{r.url}</a></small></td><td>{r.topic}</td><td>{r.provider}<small>{r.language}</small></td><td>{pill(r.verification_status)}</td><td>{stamp(r.last_checked_at)}</td><td>{(adminRole==="super_admin"||adminRole==="question_content_admin")&&<div className="ad-learning-actions">{r.verification_status!=="verified"&&<button className="ad-action-btn" onClick={()=>openAction("learning-resource",r.id,"verified")}>Verify</button>}{r.verification_status!=="quarantined"&&<button className="ad-action-btn" onClick={()=>openAction("learning-resource",r.id,"quarantined")}>Quarantine</button>}{r.verification_status!=="retired"&&<button className="ad-action-btn" onClick={()=>openAction("learning-resource",r.id,"retired")}>Retire</button>}</div>}</td></tr>)}</tbody></table>{!(data.learning?.resources||[]).length&&<div className="ad-billing-empty">No resources in the catalogue.</div>}</div>
          </div>
          <div className="ad-panel"><div className="ad-panel-head"><div><h3>Retake policy</h3><p>Configurable limits are applied by the backend when an attempt is created.</p></div></div>{(adminRole==="super_admin"||adminRole==="finance_admin")&&<form className="ad-learning-policy" onSubmit={async e=>{e.preventDefault();try{await api("/admin/learning/policy",{method:"PUT",body:{cooldown_hours:Number(e.currentTarget.cooldown.value),max_attempts_90d:Number(e.currentTarget.cap.value),credits_per_attempt:Number(e.currentTarget.cost.value)}});flash("Retake policy updated.");await load();}catch(x){setError(x.message||"Could not update policy.");}}}><label>Cooldown hours<input name="cooldown" type="number" min="0" max="2160" defaultValue={data.learning?.policy?.cooldown_hours??24}/></label><label>Maximum attempts / 90 days<input name="cap" type="number" min="1" max="20" defaultValue={data.learning?.policy?.max_attempts_90d??3}/></label><label>Credits per retake<input name="cost" type="number" min="1" max="20" defaultValue={data.learning?.policy?.credits_per_attempt??1}/></label><button className="ad-q-add-btn">Save policy</button></form>}</div>
          <div className="ad-two-col"><div className="ad-panel"><div className="ad-panel-head"><div><h3>Candidate learning progress</h3><p>Progress events from the verified catalogue.</p></div></div><div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Candidate</th><th>Resource</th><th>Topic</th><th>Status</th><th>Updated</th></tr></thead><tbody>{(data.learning?.progress||[]).map((p,i)=><tr key={i}><td>{p.candidate_name}<small>{p.candidate_email}</small></td><td>{p.resource_title}</td><td>{p.topic}</td><td>{pill(p.status)}</td><td>{stamp(p.updated_at)}</td></tr>)}</tbody></table>{!(data.learning?.progress||[]).length&&<div className="ad-billing-empty">No learning progress has been recorded.</div>}</div></div>
          <div className="ad-panel"><div className="ad-panel-head"><div><h3>Credit consumption & adjustments</h3><p>Grant credits only with a clear operational reason. All entries remain traceable.</p></div></div>{(adminRole==="super_admin"||adminRole==="finance_admin")&&<form className="ad-learning-credit-form" onSubmit={async e=>{e.preventDefault();try{const f=e.currentTarget;await api("/admin/learning/credits",{method:"POST",body:{user_id:Number(f.user_id.value),delta:Number(f.delta.value),reason:f.reason.value}});f.reset();flash("Retake credits granted and logged.");await load();}catch(x){setError(x.message||"Could not grant credits.");}}}><label>Candidate user ID<input name="user_id" type="number" min="1" required/></label><label>Credits to grant<input name="delta" type="number" min="1" max="100" defaultValue="1" required/></label><label>Reason<input name="reason" minLength="8" required placeholder="e.g. verified platform interruption"/></label><button className="ad-q-add-btn">Grant credits</button></form>}{(adminRole==="super_admin"||adminRole==="finance_admin")&&<div className="ad-table-wrap"><table className="ad-table"><thead><tr><th>Candidate</th><th>Change</th><th>Entry</th><th>Reason</th><th>Date</th></tr></thead><tbody>{(data.learning?.ledger||[]).map(l=><tr key={l.id}><td>{l.candidate_name||l.candidate_email||l.user_id}<small>{l.candidate_email}</small></td><td>{l.delta>0?"+":""}{l.delta}</td><td>{l.entry_type}</td><td>{l.reason}</td><td>{stamp(l.created)}</td></tr>)}</tbody></table>{!(data.learning?.ledger||[]).length&&<div className="ad-billing-empty">No retake ledger entries.</div>}</div>}</div></div>
        </section>}
        {page==="privacy"&&<section className="ad-two-col"><div className="ad-panel"><h2>Privacy & data rights</h2><p>Available candidate rights capabilities and consent coverage.</p><div className="ad-metrics ad-metrics-compact">{metric("Consented users",data.privacy?.consented_users,"Distinct accounts","✓")}{metric("Consent records",data.privacy?.consent_records,"Versioned records","◈")}</div><div className="ad-health-row"><span>Candidate data export</span>{pill(data.privacy?.export_capability?"available":"unavailable")}</div><div className="ad-health-row"><span>Self-service deletion</span>{pill(data.privacy?.self_service_deletion?"available":"unavailable")}</div><div className="ad-health-row"><span>Scheduled retention jobs</span>{pill(data.privacy?.retention_jobs_configured?"configured":"not_configured")}</div></div><div className="ad-panel"><h2>Implementation notes</h2><p>{data.privacy?.note||"Privacy controls not available."}</p><div className="ad-info-banner">Admin access to sensitive candidate evidence must remain purpose-limited, reason-coded and audited. Raw media access is not provided by this console.</div></div></section>}
        {page==="quality"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Fairness & quality signals</h2><p>Descriptive operational metrics; not candidate ranking or protected-trait profiling.</p></div></div><div className="ad-metrics">{metric("Completed attempts",data.quality?.completed_attempts,"Scored attempts","◎")}{metric("Appeals",data.quality?.appeals,"Candidate-raised","⚑")}{metric("Reviewed attempts",data.quality?.reviewed_attempts,"Human review records","✓")}{metric("Appeal overturns",data.quality?.appeal_overturns,"Outcomes","↺")}</div><div className="ad-health-row"><span>Appeal overturn rate</span><b>{data.quality?.appeal_overturn_rate==null?"Not enough data":(data.quality.appeal_overturn_rate*100).toFixed(1)+"%"}</b></div><div className="ad-info-banner">{data.quality?.note||"Quality metrics are limited by available data."} Protected demographic data is not collected for this analysis.</div></section>}
        {page==="audit"&&<section className="ad-panel"><div className="ad-panel-head"><div><h2>Audit event history</h2><p>Recent account, moderation and review events.</p></div><span className="ad-count">{rows("audit").length} events</span></div><div className="ad-toolbar"><input placeholder="Filter by action…" value={search} onChange={e=>setSearch(e.target.value)}/></div>{table(["TIME","ACTOR","ACTION","TARGET"],filtered(rows("audit"),["action","target","actor_name","actor_email"]).map(x=><tr><td>{stamp(x.ts)}</td><td><b>{x.actor_name||"System"}</b><small>{x.actor_email||("ID "+x.actor)}</small></td><td><code>{x.action}</code></td><td className="ad-audit-target">{x.target}</td></tr>),"No audit events found.")}</section>}
        {busy&&<div className="ad-loading">Syncing platform data…</div>}
        <footer className="ad-footer"><span>SkillBridge Global · Admin Console</span><span>Least privilege · Human review · Evidence-led operations</span></footer>
      </div>
    </main>
    {questionModal&&<div className="ad-modal-backdrop" onClick={()=>!questionSaving&&setQuestionModal(false)}><form className="ad-question-modal" onSubmit={saveQuestion} onClick={e=>e.stopPropagation()}><header><div><div className="ad-eyebrow">QUESTION AUTHORING</div><h2>{questionEditing?"Edit question":"Create a question"}</h2><p>Define the question, answer key and fair assessment timing.</p></div><button type="button" onClick={()=>setQuestionModal(false)}>×</button></header><div className="ad-q-form-grid">
      <label>Category<select required value={questionDraft.category_id} onChange={e=>setQuestionDraft(v=>({...v,category_id:e.target.value}))}>{(data.questions?.categories||[]).map(c=><option value={c.id}>{c.name}</option>)}</select></label><label>Subcategory<input required value={questionDraft.subcategory} onChange={e=>setQuestionDraft(v=>({...v,subcategory:e.target.value}))} placeholder="e.g. Joins"/></label>
      <label>Question type<select value={questionDraft.question_type} onChange={e=>setQuestionDraft(v=>({...v,question_type:e.target.value}))}>{["MCQ","Coding","SQL","Explanation","Short Answer","Scenario-Based","System Design","True/False"].map(x=><option>{x}</option>)}</select></label><label>Difficulty<select value={questionDraft.difficulty_level} onChange={e=>setQuestionDraft(v=>({...v,difficulty_level:e.target.value}))}>{["Easy","Medium","Hard"].map(x=><option>{x}</option>)}</select></label>
      <label className="ad-q-full">Question text<textarea required rows="4" value={questionDraft.question_text} onChange={e=>setQuestionDraft(v=>({...v,question_text:e.target.value}))} placeholder="Write a clear, unambiguous question…"/></label>
      {questionDraft.question_type==="MCQ"&&<label className="ad-q-full">Options <small>One option per line</small><textarea rows="4" value={questionDraft.options} onChange={e=>setQuestionDraft(v=>({...v,options:e.target.value}))} placeholder={"Option A\nOption B\nOption C\nOption D"}/></label>}
      <label>Correct answer<input value={questionDraft.correct_answer} onChange={e=>setQuestionDraft(v=>({...v,correct_answer:e.target.value}))} placeholder="Exact answer or option"/></label><label>Marks<input type="number" min="1" max="100" value={questionDraft.marks} onChange={e=>setQuestionDraft(v=>({...v,marks:e.target.value}))}/></label><label>Time allocation (minutes)<input type="number" min="0.25" max="120" step="0.25" value={questionDraft.time_minutes} onChange={e=>setQuestionDraft(v=>({...v,time_minutes:e.target.value}))}/></label><label>Status<select value={questionDraft.status} onChange={e=>setQuestionDraft(v=>({...v,status:e.target.value}))}><option value="draft">Draft</option><option value="active">Active</option><option value="archived">Archived</option></select></label>
      <label className="ad-q-full">Explanation<textarea rows="2" value={questionDraft.explanation} onChange={e=>setQuestionDraft(v=>({...v,explanation:e.target.value}))}/></label><label className="ad-q-full">Reference answer<textarea rows="2" value={questionDraft.reference_answer} onChange={e=>setQuestionDraft(v=>({...v,reference_answer:e.target.value}))} placeholder={questionDraft.question_type==="SQL"?"Canonical SQL query used for automatic grading against hidden datasets.":"Expected answer, reference implementation or guidance for human review."}/></label><label className="ad-q-full">Grading rubric<textarea rows="2" value={questionDraft.grading_rubric} onChange={e=>setQuestionDraft(v=>({...v,grading_rubric:e.target.value}))}/></label>
    </div><div className="ad-q-system-meta"><span>Created by: {questionEditing?.created_by||"Current admin"}</span><span>Version: {questionEditing?"v"+questionEditing.version+" → v"+(Number(questionEditing.version)+1):"v1 on save"}</span><span>Created date: {questionEditing?stamp(questionEditing.created_date):"Generated on save"}</span></div><footer><button type="button" onClick={()=>setQuestionModal(false)} disabled={questionSaving}>Cancel</button><button className="ad-q-add-btn" disabled={questionSaving}>{questionSaving?"Saving…":questionEditing?"Save new version":"Create question"}</button></footer></form></div>}
    {selectedReport&&<div className="ad-modal-backdrop" onClick={()=>setSelectedReport(null)}><section className="ad-report-modal" onClick={e=>e.stopPropagation()}><header><div><div className="ad-eyebrow">CANDIDATE EVIDENCE · MANUAL REVIEW</div><h2>Assessment evaluation report</h2><p>{selectedReport.candidate_name} · {selectedReport.email} · {selectedReport.kind} assessment</p></div><button type="button" onClick={()=>setSelectedReport(null)}>×</button></header><div className="ad-report-summary"><div><small>Overall score</small><strong>{selectedReport.result?.overall??"—"}<small>/100</small></strong></div><div><small>Level</small><strong>{selectedReport.result?.level??"—"}</strong></div><div><small>Integrity signal</small><strong>{selectedReport.result?.trust??"—"}</strong></div></div><div className="ad-info-banner">{selectedReport.evaluation_method||"Automated evaluation method unavailable."} This is a deterministic grading summary, not a generative AI judgement. Use your own judgement for the final manual review.</div>{!selectedReport.answer_evidence_complete&&<div className="ad-warning-banner">Some or all candidate responses were not captured for this older attempt. The original question list may be visible, but missing answers cannot be reconstructed.</div>}<h3>Topic performance</h3><div className="ad-report-topics">{Object.entries(selectedReport.result?.topics||{}).map(([topic,v])=><div><b>{topic}</b><span>{v.score}% · {v.level} · {v.n} questions</span></div>)}</div><h3>Questions and candidate answers ({selectedReport.answer_evidence?.length||0})</h3><div className="ad-report-questions">{(selectedReport.answer_evidence||[]).map(q=><article><div className="ad-report-qmeta">Q{q.number} · {q.topic} · {q.type} · Difficulty {q.difficulty}</div><h4>{q.question}</h4>{q.options?.length>0&&<ol>{q.options.map(o=><li>{o}</li>)}</ol>}<div className="ad-report-answer"><b>Candidate answer</b><pre>{q.selected_option??q.candidate_answer??"Not captured in this attempt"}</pre></div>{q.expected_answer&&<div className="ad-report-answer"><b>Expected / reference answer</b><pre>{q.expected_answer}</pre></div>}{q.explanation&&<p className="ad-report-qmeta">Explanation: {q.explanation}</p>}{q.grading_rubric&&<p className="ad-report-qmeta">Rubric: {q.grading_rubric}</p>}<div className="ad-report-qmeta">Evaluation: {q.review_status|| (q.automated_correct===true?"Correct":q.automated_correct===false?"Incorrect":"Unavailable")} · Response time: {q.time_seconds??"—"} sec</div></article>)}</div><footer><span>Use browser print dialog and choose “Save as PDF” for a downloadable report.</span><button className="ad-mini-primary" onClick={()=>printAssessmentReport(selectedReport)}>Print / Save PDF</button><button onClick={()=>setSelectedReport(null)}>Close</button></footer></section></div>}
    {action&&<div className="ad-modal-backdrop" onClick={()=>!saving&&setAction(null)}><form className="ad-modal" onSubmit={submitAction} onClick={e=>e.stopPropagation()}><button type="button" className="ad-modal-close" onClick={()=>setAction(null)}>×</button><div className="ad-eyebrow">REASON-CODED ADMIN ACTION</div><h2>Confirm {action.decision.replaceAll("_"," ")}</h2><p>This decision will be recorded with your administrator identity and timestamp. Add a clear reason for the audit trail.</p><label>Decision reason<textarea required minLength={action.kind==="learning-resource"?8:3} rows="4" value={reason} onChange={e=>setReason(e.target.value)} placeholder="Explain the evidence reviewed and why this action is appropriate…"/></label><div className="ad-modal-actions"><button type="button" onClick={()=>setAction(null)} disabled={saving}>Cancel</button><button className="ad-modal-confirm" disabled={saving||reason.trim().length<(action.kind==="learning-resource"?8:3)}>{saving?"Saving…":"Confirm decision"}</button></div></form></div>}
  </div>;
}

function HiringWorkspace({ role, onLogout }) {
  const [page, setPage] = useState("overview");
  const [workspace, setWorkspace] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [applications, setApplications] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [team, setTeam] = useState([]);
  const [phaseTwo, setPhaseTwo] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [companyName, setCompanyName] = useState("");
  const [website, setWebsite] = useState("");
  const [verification, setVerification] = useState({legal_name:"",country:"Bangladesh",business_id:"",evidence_note:""});
  const [jobForm, setJobForm] = useState({title:"",description:"",location:"Dhaka, Bangladesh",employment_type:"Full-time",work_mode:"Hybrid",experience_level:"Mid-level",salary_min:"",salary_max:"",salary_currency:"BDT",deadline:"",required_skills:"",preferred_skills:"",eligibility:"",hard_requirements:""});
  const [jobEditId, setJobEditId] = useState(null);
  const [search, setSearch] = useState("");
  const [filterJob, setFilterJob] = useState("all");
  const [note, setNote] = useState("");
  const [stageReason, setStageReason] = useState("");
  const [teamForm, setTeamForm] = useState({email:"",role:"recruiter"});
  const [inviteToken, setInviteToken] = useState("");
  const [phaseForm, setPhaseForm] = useState({title:"Role-specific practical assessment",requirements:"Assess practical skills relevant to this role.",syllabus:"",question_types:["scenario","short_answer"],duration_minutes:60,deadline:"",attempt_count:1,integrity_mode:"light",accommodations:"",credit_cost:0,allowed_resources:"",rubric:"",difficulty_easy:30,difficulty_medium:50,difficulty_hard:20});
  const [selectedApp, setSelectedApp] = useState("");
  const [verificationForm, setVerificationForm] = useState(false);
  const isOwner = workspace?.company?.role === "owner";
  const canManageJobs = workspace?.permissions?.manage_jobs;
  const canManagePipeline = workspace?.permissions?.manage_pipeline;
  const canManageTeam = workspace?.permissions?.manage_team;
  const money = (n) => n == null || n === "" ? "Not disclosed" : new Intl.NumberFormat("en-BD",{style:"currency",currency:"BDT",maximumFractionDigits:0}).format(Number(n));
  const date = (v) => v ? new Date(Number(v)*1000).toLocaleDateString("en-GB",{day:"numeric",month:"short",year:"numeric"}) : "—";
  const initials = (s="") => s.split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0]).join("").toUpperCase() || "SB";
  const flash = (message) => { setToast(message); setError(""); };
  const fail = (e) => { setError(e.message || "Something went wrong"); setToast(""); };
  const load = async () => {
    setBusy(true); setError("");
    try {
      const w = await api("/company/workspace");
      setWorkspace(w); setCompanyName(w.company.name); setWebsite(w.company.website || "");
      const j = await api("/company/jobs");
      setJobs(j.items || []);
      if (w.company.verified && w.permissions.manage_pipeline) {
        const a = await api("/company/applications"); setApplications(a.items || []);
      } else { setApplications([]); }
      if (w.permissions.view_reports) {
        const x = await api("/company/analytics"); setAnalytics(x);
      }
      if (w.permissions.manage_team) {
        const t = await api("/company/team"); setTeam(t.items || []);
      }
      if (w.permissions.view_reports) {
        const p = await api("/company/phase-two"); setPhaseTwo(p.items || []);
      }
    } catch (e) { fail(e); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, [role]);
  const openApplication = async (id) => {
    try { const d = await api("/company/applications/"+id); setSelected(id); setDetail(d); }
    catch(e) { fail(e); }
  };
  const saveCompany = async (e) => {
    e.preventDefault();
    try { await api("/company/profile",{method:"POST",body:{name:companyName,website}}); flash("Company profile saved. Next, submit verification."); await load(); }
    catch(e) { fail(e); }
  };
  const submitVerification = async (e) => {
    e.preventDefault();
    try { const r=await api("/company/verification",{method:"POST",body:verification}); flash(r.message); setVerificationForm(false); await load(); }
    catch(e) { fail(e); }
  };
  const setJob = (key,value) => setJobForm(v=>({...v,[key]:value}));
  const editJob = (j) => {
    const d=j.details||{};
    setJobForm({title:j.title,description:j.description,location:j.location,employment_type:j.employment_type,work_mode:d.work_mode||"Remote",experience_level:d.experience_level||"Any",salary_min:d.salary_min??"",salary_max:d.salary_max??"",salary_currency:d.salary_currency||"BDT",deadline:d.deadline||"",required_skills:(d.required_skills||j.skills||[]).join(", "),preferred_skills:(d.preferred_skills||[]).join(", "),eligibility:d.eligibility||"",hard_requirements:(d.hard_requirements||[]).join("\n")});
    setJobEditId(j.id); setPage("jobs");
  };
  const submitJob = async (e) => {
    e.preventDefault();
    const payload={...jobForm,salary_min:jobForm.salary_min===""?null:Number(jobForm.salary_min),salary_max:jobForm.salary_max===""?null:Number(jobForm.salary_max),required_skills:jobForm.required_skills.split(",").map(x=>x.trim()).filter(Boolean),preferred_skills:jobForm.preferred_skills.split(",").map(x=>x.trim()).filter(Boolean),hard_requirements:jobForm.hard_requirements.split("\n").map(x=>x.trim()).filter(Boolean)};
    try {
      if(jobEditId) await api("/company/jobs/"+jobEditId,{method:"PATCH",body:payload});
      else await api("/company/jobs",{method:"POST",body:payload});
      setJobEditId(null); setJobForm({title:"",description:"",location:"Dhaka, Bangladesh",employment_type:"Full-time",work_mode:"Hybrid",experience_level:"Mid-level",salary_min:"",salary_max:"",salary_currency:"BDT",deadline:"",required_skills:"",preferred_skills:"",eligibility:"",hard_requirements:""});
      flash(jobEditId?"Job changes saved.":"Job draft created."); await load();
    } catch(e) { fail(e); }
  };
  const publishJob = async (id) => { try { const r=await api("/company/jobs/"+id+"/publish",{method:"POST"}); flash(r.status==="published"?"Job published.":"Job submitted for platform review."); await load(); } catch(e){fail(e);} };
  const jobStatus = async (id,status) => { try { await api("/company/jobs/"+id+"/status",{method:"POST",body:{status,reason:"Company requested status change"}}); flash("Job status updated."); await load(); } catch(e){fail(e);} };
  const moveStage = async (id,stage) => {
    try { await api("/company/applications/"+id+"/stage",{method:"PATCH",body:{stage,reason:stageReason}}); setStageReason(""); await openApplication(id); flash("Candidate pipeline updated."); await load(); }
    catch(e){fail(e);}
  };
  const addNote = async (e) => { e.preventDefault(); try { await api("/company/applications/"+selected+"/notes",{method:"POST",body:{body:note}}); setNote(""); await openApplication(selected); flash("Private recruiter note added."); } catch(e){fail(e);} };
  const inviteMember = async (e) => { e.preventDefault(); try { const r=await api("/company/team/invitations",{method:"POST",body:teamForm}); setInviteToken(r.invite_token); flash("Invitation created. Share the token securely with the invited user."); const t=await api("/company/team");setTeam(t.items||[]); } catch(e){fail(e);} };
  const acceptInvite = async (e) => { e.preventDefault(); try { await api("/company/team/accept/"+inviteToken,{method:"POST"}); setInviteToken(""); flash("Organisation invitation accepted. Refreshing workspace."); await load(); } catch(e){fail(e);} };
  const createPhase = async (e) => { e.preventDefault(); if(!selectedApp){fail(new Error("Choose an applicant first."));return;} try { const topics=phaseForm.syllabus.split("\n").map(x=>x.trim()).filter(Boolean); const rubricLines=phaseForm.rubric.split("\n").map(x=>x.trim()).filter(Boolean); const difficulty={easy:Number(phaseForm.difficulty_easy),medium:Number(phaseForm.difficulty_medium),hard:Number(phaseForm.difficulty_hard)}; if(topics.length===0||rubricLines.length===0||phaseForm.question_types.length===0||Object.values(difficulty).reduce((x,y)=>x+y,0)!==100){fail(new Error("Add syllabus and rubric criteria; difficulty percentages must total 100."));return;} const payload={...phaseForm,syllabus:topics,rubric:rubricLines.map((criterion,index)=>({criterion,weight:100/rubricLines.length})),allowed_resources:phaseForm.allowed_resources.split("\n").map(x=>x.trim()).filter(Boolean),difficulty}; await api("/company/applications/"+selectedApp+"/phase-two-invitations",{method:"POST",body:payload}); flash("Second-phase assessment configuration submitted for platform review. Candidate delivery is not active yet."); await load(); setPage("assessments"); } catch(e){fail(e);} };
  const nav=[["overview","Overview","⌂"],["jobs","Jobs","▤"],...(canManagePipeline?[["applicants","Applicants","♙"]]:[]),["assessments","Assessments","◎"],["analytics","Analytics","▥"],...(canManageTeam?[["team","Team","♧"]]:[]),["settings","Company settings","⚙"]];
  const stages=[["submitted","Applied"],["preliminary_assessment","Preliminary"],["in_review","In review"],["phase_two_invited","Phase two"],["further_review","Further review"],["human_interview","Interview"],["on_hold","On hold"],["offer","Offer"],["offer_declined","Offer declined"],["offer_withdrawn","Offer withdrawn"],["hired","Hired"],["rejected","Rejected"],["withdrawn","Withdrawn"],["closed","Closed"]];
  const stageTransitions={submitted:["preliminary_assessment","in_review","withdrawn"],preliminary_assessment:["in_review","phase_two_invited","human_interview","withdrawn"],in_review:["preliminary_assessment","phase_two_invited","human_interview","withdrawn"],phase_two_invited:["human_interview","further_review","withdrawn"],further_review:["human_interview","withdrawn"],human_interview:["offer","rejected","on_hold"],on_hold:["human_interview","rejected"],offer:["hired","offer_declined","offer_withdrawn"],offer_withdrawn:["human_interview","closed"],hired:["closed"],rejected:["closed"],withdrawn:["closed"]};
  const filteredApps=applications.filter(a=>(filterJob==="all"||a.job_id===filterJob)&&((a.candidate_name||"").toLowerCase().includes(search.toLowerCase())||(a.job_title||"").toLowerCase().includes(search.toLowerCase())));
  const metric=(label,value,sub,icon)=> <div className="cr-metric"><span className="cr-metric-icon">{icon}</span><div><small>{label}</small><strong>{value}</strong><span>{sub}</span></div></div>;
  if(role==="admin") return <AdminConsole onLogout={onLogout}/>;
  if(!workspace && error.toLowerCase().includes("organisation")) return <main className="cr-onboard"><div className="cr-onboard-brand">skillbridge<span>.</span></div><section className="cr-onboard-card"><div className="cr-kicker">COMPANY WORKSPACE · SETUP</div><h1>Build your hiring workspace.</h1><p>Create your organisation profile to start drafting roles and inviting your team. Public jobs and real applicant access require company verification.</p><form onSubmit={saveCompany}><label>Company / organisation name<input value={companyName} onChange={e=>setCompanyName(e.target.value)} required minLength={2} placeholder="e.g. Northstar Technologies"/></label><label>Company website<input value={website} onChange={e=>setWebsite(e.target.value)} placeholder="https://company.com"/></label><button className="cr-primary" disabled={busy}>Create company workspace <span>→</span></button></form><div className="cr-onboard-divider"><span>OR</span></div><form onSubmit={async e=>{e.preventDefault();try{await api("/company/team/accept/"+inviteToken,{method:"POST"});setInviteToken("");flash("Invitation accepted.");await load();}catch(e){fail(e);}}}><label>Have a team invitation? Enter its token<input value={inviteToken} onChange={e=>setInviteToken(e.target.value)} required placeholder="Paste invitation token"/></label><button className="cr-outline-btn">Accept team invitation</button></form>{error&&<p className="cr-error">{error}</p>}</section></main>;
  return (
    <div className="cr-app">
      <aside className="cr-sidebar">
        <div className="cr-brand"><span className="cr-brand-mark">s</span><span>skillbridge<span className="cr-brand-dot">.</span><small>HIRING WORKSPACE</small></span></div>
        <div className="cr-org"><div className="cr-org-avatar">{initials(workspace?.company?.name)}</div><div><strong>{workspace?.company?.name||"Your organisation"}</strong><small>{workspace?.company?.role==="owner"?"Organisation owner":workspace?.company?.role?.replace("_"," ")}</small></div><span className="cr-chevron">⌄</span></div>
        <div className="cr-nav-label">WORKSPACE</div>
        <nav className="cr-nav">{nav.map(([id,label,icon])=><button key={id} className={page===id?"active":""} onClick={()=>{setPage(id);setSelected(null);setDetail(null);}}><span>{icon}</span>{label}{id==="applicants"&&applications.length>0?<b>{applications.length}</b>:null}</button>)}</nav>
        <div className="cr-sidebar-bottom"><div className="cr-help-card"><span>✦</span><strong>Hiring with evidence</strong><p>Review verified skills, not opaque AI scores.</p></div><div className="cr-user"><div className="cr-user-avatar">{initials(workspace?.company?.name)}</div><div><strong>{workspace?.company?.role||"Team member"}</strong><small>Company account</small></div><button title="Refresh workspace" onClick={load}>↻</button></div></div>
      </aside>
      <main className="cr-main">
        <header className="cr-topbar"><div className="cr-breadcrumb">Workspace <span>/</span> {nav.find(x=>x[0]===page)?.[1]||"Overview"}</div><div className="cr-top-actions"><span className="cr-secure"><i/> Private company workspace</span><button className="cr-icon-btn" onClick={load} title="Refresh" aria-label="Refresh workspace">↻</button><div className="cr-top-avatar">{initials(workspace?.company?.name)}</div><button type="button" className="cr-logout-btn" onClick={onLogout}><span aria-hidden="true">↪</span> Log out</button></div></header>
        <div className="cr-content">
          {toast&&<div className="cr-toast" role="status"><span>✓</span>{toast}<button onClick={()=>setToast("")}>×</button></div>}
          {error&&!(!workspace&&error.toLowerCase().includes("organisation"))&&<div className="cr-error-banner" role="alert">{error}<button onClick={()=>setError("")}>×</button></div>}
          {workspace?.verification?.status!=="verified"&&<div className="cr-verification-banner"><div><strong>Complete company verification</strong><span>{workspace?.verification?.status==="pending_review"?"Your organisation is awaiting review.":"Verify your organisation to publish jobs and access applicant workflows."}</span></div><button onClick={()=>{setPage("settings");setVerificationForm(true);}}>Review status →</button></div>}
          {page==="overview"&&<section>
            <div className="cr-page-heading"><div><div className="cr-kicker">YOUR HIRING OVERVIEW</div><h1>Good to see you back.</h1><p>Here’s what’s happening across your hiring workspace.</p></div><button className="cr-primary" onClick={()=>{setJobEditId(null);setPage("jobs");}}>＋ Create a job</button></div>
            <div className="cr-metrics">{metric("Open roles",analytics?.totals?.published_jobs??jobs.filter(j=>j.status==="published").length,"Published opportunities","▤")}{metric("Total applicants",analytics?.totals?.applicants??applications.length,"Across all roles","♙")}{metric("In review",applications.filter(a=>["in_review","preliminary_assessment"].includes(a.status)).length,"Needs recruiter attention","◷")}{metric("Shared assessments",analytics?.totals?.shared_results??applications.filter(a=>a.results_shared).length,"Candidate-consented evidence","✓")}</div>
            <div className="cr-overview-grid"><div className="cr-panel"><div className="cr-panel-head"><div><h2>Recent applicants</h2><p>Latest applications across your roles</p></div><button className="cr-text-btn" onClick={()=>setPage("applicants")}>View all →</button></div>{applications.slice(0,5).length?applications.slice(0,5).map(a=><button className="cr-applicant-row" key={a.application_id} onClick={()=>openApplication(a.application_id)}><div className="cr-person-avatar">{initials(a.candidate_name)}</div><div className="cr-person-copy"><strong>{a.candidate_name}</strong><span>{a.job_title}</span></div><span className={"cr-stage cr-stage-"+a.status}>{a.status.replaceAll("_"," ")}</span><span className="cr-row-arrow">→</span></button>):<div className="cr-empty"><span>♙</span><strong>No applicants yet</strong><p>Published roles will appear here as candidates apply.</p></div>}</div>
              <div className="cr-panel"><div className="cr-panel-head"><div><h2>Hiring activity</h2><p>Application stages</p></div></div>{stages.slice(0,7).map(([key,label])=><div className="cr-stage-line" key={key}><span>{label}</span><strong>{applications.filter(a=>a.status===key).length}</strong><div><i style={{width:(applications.length?Math.max(2,applications.filter(a=>a.status===key).length/applications.length*100):0)+"%"}}/></div></div>)}<p className="cr-footnote">Pipeline counts reflect applications in this organisation only.</p></div></div>
            <div className="cr-panel cr-jobs-panel"><div className="cr-panel-head"><div><h2>Your jobs</h2><p>Manage drafts, published roles and review status</p></div><button className="cr-text-btn" onClick={()=>setPage("jobs")}>Manage jobs →</button></div><div className="cr-table-wrap"><table className="cr-table"><thead><tr><th>ROLE</th><th>STATUS</th><th>APPLICANTS</th><th>POSTED</th><th></th></tr></thead><tbody>{jobs.slice(0,5).map(j=><tr key={j.id}><td><strong>{j.title}</strong><small>{j.location} · {j.employment_type}</small></td><td><span className={"cr-status cr-status-"+j.status}>{j.status.replaceAll("_"," ")}</span></td><td>{applications.filter(a=>a.job_id===j.id).length}</td><td>{date(j.created)}</td><td><button className="cr-text-btn" onClick={()=>editJob(j)}>Edit</button></td></tr>)}</tbody></table>{!jobs.length&&<div className="cr-empty"><strong>Your first role starts here</strong><p>Create a structured job description to begin attracting applicants.</p></div>}</div></div>
          </section>}
          {page==="jobs"&&<section><div className="cr-page-heading"><div><div className="cr-kicker">JOB MANAGEMENT</div><h1>{jobEditId?"Edit job":"Your jobs"}</h1><p>Create, review and manage every opportunity from one place.</p></div>{!jobEditId&&canManageJobs&&<button className="cr-primary" onClick={()=>setJobEditId("new")}>＋ New job</button>}</div>
            {jobEditId!==null&&<form className="cr-panel cr-form-panel" onSubmit={submitJob}><div className="cr-panel-head"><div><h2>{jobEditId==="new"?"Create a job":"Edit job details"}</h2><p>Complete the required details before submitting for publication.</p></div><button type="button" className="cr-text-btn" onClick={()=>setJobEditId(null)}>Cancel</button></div><div className="cr-form-grid"><label className="cr-span-2">Job title<input required minLength={3} value={jobForm.title} onChange={e=>setJob("title",e.target.value)} placeholder="e.g. Senior Product Designer"/></label><label>Location<input value={jobForm.location} onChange={e=>setJob("location",e.target.value)}/></label><label>Employment type<select value={jobForm.employment_type} onChange={e=>setJob("employment_type",e.target.value)}>{["Full-time","Part-time","Contract","Internship","Temporary"].map(x=><option>{x}</option>)}</select></label><label>Work arrangement<select value={jobForm.work_mode} onChange={e=>setJob("work_mode",e.target.value)}>{["Remote","Hybrid","On-site"].map(x=><option>{x}</option>)}</select></label><label>Experience level<select value={jobForm.experience_level} onChange={e=>setJob("experience_level",e.target.value)}>{["Any","Entry-level","Junior","Mid-level","Senior","Lead"].map(x=><option>{x}</option>)}</select></label><label>Minimum salary<input type="number" min="0" value={jobForm.salary_min} onChange={e=>setJob("salary_min",e.target.value)} placeholder="Optional"/></label><label>Maximum salary<input type="number" min="0" value={jobForm.salary_max} onChange={e=>setJob("salary_max",e.target.value)} placeholder="Optional"/></label><label>Salary currency<select value={jobForm.salary_currency} onChange={e=>setJob("salary_currency",e.target.value)}><option>BDT</option><option>USD</option><option>INR</option></select></label><label>Application deadline<input type="date" value={jobForm.deadline} onChange={e=>setJob("deadline",e.target.value)}/></label><label className="cr-span-2">Job description<textarea required minLength={20} rows="6" value={jobForm.description} onChange={e=>setJob("description",e.target.value)} placeholder="Describe the role, responsibilities and what success looks like..."/></label><label className="cr-span-2">Required skills <small>Comma-separated</small><input value={jobForm.required_skills} onChange={e=>setJob("required_skills",e.target.value)} placeholder="React, TypeScript, UX research"/></label><label className="cr-span-2">Preferred skills <small>Comma-separated</small><input value={jobForm.preferred_skills} onChange={e=>setJob("preferred_skills",e.target.value)} placeholder="Figma, accessibility, design systems"/></label><label className="cr-span-2">Eligibility criteria<textarea rows="2" value={jobForm.eligibility} onChange={e=>setJob("eligibility",e.target.value)}/></label><label className="cr-span-2">Hard requirements <small>One per line</small><textarea rows="3" value={jobForm.hard_requirements} onChange={e=>setJob("hard_requirements",e.target.value)}/></label></div><div className="cr-form-footer"><span>Jobs are saved as drafts. Verified organisations can submit them for review.</span><button className="cr-primary" disabled={busy}>Save job draft</button></div></form>}
            <div className="cr-job-list">{jobs.map(j=><article className="cr-job-card" key={j.id}><div className="cr-job-icon">▤</div><div className="cr-job-main"><div className="cr-job-title"><h3>{j.title}</h3><span className={"cr-status cr-status-"+j.status}>{j.status.replaceAll("_"," ")}</span></div><p>{j.location} · {j.employment_type} · {applications.filter(a=>a.job_id===j.id).length} applicants</p><div className="cr-tags">{(j.skills||[]).slice(0,5).map(s=><span>{s}</span>)}</div><small>Created {date(j.created)} · Revision {j.revision||1}</small></div><div className="cr-job-actions">{canManageJobs&&["draft","needs_changes","paused"].includes(j.status)&&<button className="cr-outline-btn" onClick={()=>editJob(j)}>Edit</button>}{j.status==="draft"||j.status==="needs_changes"||j.status==="paused"?<button className="cr-primary cr-small" onClick={()=>publishJob(j.id)} disabled={!workspace?.company?.verified}>Submit for review</button>:null}{j.status==="published"?<button className="cr-outline-btn" onClick={()=>jobStatus(j.id,"paused")}>Pause</button>:null}{j.status==="paused"?<button className="cr-outline-btn" onClick={()=>jobStatus(j.id,"closed")}>Close</button>:null}<button className="cr-text-btn" onClick={async()=>{try{const r=await api("/company/jobs/"+j.id+"/revisions");flash(r.items.length+" saved revisions");}catch(e){fail(e);}}}>History</button></div></article>)}{!jobs.length&&<div className="cr-empty"><strong>No jobs created yet</strong><p>Start with one clear, structured job description.</p></div>}</div>
          </section>}
          {page==="applicants"&&<section><div className="cr-page-heading"><div><div className="cr-kicker">TALENT PIPELINE</div><h1>Applicants</h1><p>Review applications with candidate-consented evidence and human-led decisions.</p></div></div><div className="cr-toolbar"><div className="cr-search">⌕<input placeholder="Search candidates or roles..." value={search} onChange={e=>setSearch(e.target.value)}/></div><select value={filterJob} onChange={e=>setFilterJob(e.target.value)}><option value="all">All jobs</option>{jobs.map(j=><option value={j.id}>{j.title}</option>)}</select><span>{filteredApps.length} applicants</span></div><div className="cr-pipeline">{stages.slice(0,6).map(([key,label])=><div className="cr-pipeline-col" key={key}><div className="cr-pipeline-head"><strong>{label}</strong><span>{filteredApps.filter(a=>a.status===key).length}</span></div>{filteredApps.filter(a=>a.status===key).map(a=><button className="cr-candidate-card" key={a.application_id} onClick={()=>openApplication(a.application_id)}><div className="cr-person-avatar">{initials(a.candidate_name)}</div><strong>{a.candidate_name}</strong><small>{a.job_title}</small><span className="cr-candidate-meta">{a.results_shared?"✓ Assessment shared":"Assessment not shared"}</span></button>)}{!filteredApps.some(a=>a.status===key)&&<div className="cr-pipeline-empty">No candidates</div>}</div>)}</div><div className="cr-panel cr-applicant-table"><div className="cr-panel-head"><div><h2>All applications</h2><p>Detailed list across your organisation</p></div></div><div className="cr-table-wrap"><table className="cr-table"><thead><tr><th>CANDIDATE</th><th>ROLE</th><th>STAGE</th><th>ASSESSMENT</th><th>APPLIED</th><th></th></tr></thead><tbody>{filteredApps.map(a=><tr key={a.application_id}><td><strong>{a.candidate_name}</strong></td><td>{a.job_title}</td><td><span className={"cr-stage cr-stage-"+a.status}>{a.status.replaceAll("_"," ")}</span></td><td>{a.results_shared?"Shared by candidate":"Not shared"}</td><td>{date(a.created)}</td><td><button className="cr-text-btn" onClick={()=>openApplication(a.application_id)}>Review →</button></td></tr>)}</tbody></table></div></div></section>}
          {page==="assessments"&&<section><div className="cr-page-heading"><div><div className="cr-kicker">ROLE-SPECIFIC EVALUATION</div><h1>Second-phase assessments</h1><p>Configure job-specific tasks with a clear syllabus, rubric and candidate notice.</p></div></div><div className="cr-info-banner"><strong>Human approval is required.</strong> New company-authored assessment content is saved as a draft request for platform review. It is not sent to candidates until approved and a notification flow is available.</div>{canManageJobs&&<div className="cr-panel cr-form-panel"><div className="cr-panel-head"><div><h2>Configure an assessment</h2><p>Choose an applicant, define the scope and set fair, job-relevant criteria.</p></div></div><form onSubmit={createPhase}><div className="cr-form-grid"><label className="cr-span-2">Applicant<select required value={selectedApp} onChange={e=>setSelectedApp(e.target.value)}><option value="">Select an applicant</option>{applications.filter(a=>["submitted","preliminary_assessment","in_review","phase_two_invited"].includes(a.status)).map(a=><option value={a.application_id}>{a.candidate_name} · {a.job_title}</option>)}</select></label><label className="cr-span-2">Assessment title<input required value={phaseForm.title} onChange={e=>setPhaseForm(v=>({...v,title:e.target.value}))}/></label><label className="cr-span-2">Requirements<textarea rows="3" value={phaseForm.requirements} onChange={e=>setPhaseForm(v=>({...v,requirements:e.target.value}))}/></label><label className="cr-span-2">Syllabus / topics <small>One topic per line</small><textarea rows="4" required value={phaseForm.syllabus} onChange={e=>setPhaseForm(v=>({...v,syllabus:e.target.value}))} placeholder={"SQL joins and aggregation\nData interpretation\nProblem solving"}/></label><label className="cr-span-2">Question types <small>Select all that apply</small><div className="cr-check-grid">{["mcq","sql","coding","scenario","short_answer"].map(q=><label className="cr-check-option"><input type="checkbox" checked={phaseForm.question_types.includes(q)} onChange={e=>setPhaseForm(v=>({...v,question_types:e.target.checked?[...v.question_types,q]:v.question_types.filter(x=>x!==q)}))}/>{q.replaceAll("_"," ")}</label>)}</div></label><label>Easy (%)<input type="number" min="0" max="100" value={phaseForm.difficulty_easy} onChange={e=>setPhaseForm(v=>({...v,difficulty_easy:Number(e.target.value)}))}/></label><label>Medium (%)<input type="number" min="0" max="100" value={phaseForm.difficulty_medium} onChange={e=>setPhaseForm(v=>({...v,difficulty_medium:Number(e.target.value)}))}/></label><label>Hard (%)<input type="number" min="0" max="100" value={phaseForm.difficulty_hard} onChange={e=>setPhaseForm(v=>({...v,difficulty_hard:Number(e.target.value)}))}/></label><label>Duration (minutes)<input type="number" min="10" max="360" value={phaseForm.duration_minutes} onChange={e=>setPhaseForm(v=>({...v,duration_minutes:Number(e.target.value)}))}/></label><label>Deadline<input type="date" value={phaseForm.deadline} onChange={e=>setPhaseForm(v=>({...v,deadline:e.target.value}))}/></label><label>Allowed attempts<input type="number" min="1" max="3" value={phaseForm.attempt_count} onChange={e=>setPhaseForm(v=>({...v,attempt_count:Number(e.target.value)}))}/></label><label>Integrity mode<select value={phaseForm.integrity_mode} onChange={e=>setPhaseForm(v=>({...v,integrity_mode:e.target.value}))}><option value="light">Light · default</option><option value="standard">Standard · consent required</option><option value="strict">Strict · legal approval required</option></select></label><label>Credit cost<input type="number" min="0" value={phaseForm.credit_cost} onChange={e=>setPhaseForm(v=>({...v,credit_cost:Number(e.target.value)}))}/></label><label className="cr-span-2">Scoring rubric <small>One job-related criterion per line</small><textarea rows="3" required value={phaseForm.rubric} onChange={e=>setPhaseForm(v=>({...v,rubric:e.target.value}))} placeholder={"Correctness and reasoning\nProblem decomposition\nCommunication of trade-offs"}/></label><label className="cr-span-2">Allowed resources <small>One allowed resource per line</small><textarea rows="2" value={phaseForm.allowed_resources} onChange={e=>setPhaseForm(v=>({...v,allowed_resources:e.target.value}))}/></label><label className="cr-span-2">Accommodations / alternate route<textarea rows="2" value={phaseForm.accommodations} onChange={e=>setPhaseForm(v=>({...v,accommodations:e.target.value}))}/></label></div><div className="cr-form-footer"><span>Unreviewed AI-generated questions cannot be used in a live assessment.</span><button className="cr-primary">Submit for platform review</button></div></form></div>}<div className="cr-panel"><div className="cr-panel-head"><div><h2>Assessment requests</h2><p>Saved configurations and their review status</p></div></div>{phaseTwo.length?phaseTwo.map(p=><div className="cr-assessment-row"><div><strong>{p.title}</strong><span>{p.candidate_name} · {p.job_title}</span></div><span className="cr-status cr-status-pending_review">{p.status.replaceAll("_"," ")}</span></div>):<div className="cr-empty"><strong>No assessment requests yet</strong><p>Configured requests will appear here.</p></div>}</div></section>}
          {page==="analytics"&&<section><div className="cr-page-heading"><div><div className="cr-kicker">HIRING PERFORMANCE</div><h1>Analytics</h1><p>Understand pipeline volume and role performance. Counts are descriptive, not candidate quality rankings.</p></div></div><div className="cr-metrics">{metric("Total roles",analytics?.totals?.jobs??0,"All job records","▤")}{metric("Published roles",analytics?.totals?.published_jobs??0,"Live opportunities","↗")}{metric("Applications",analytics?.totals?.applicants??0,"Received applications","♙")}{metric("Shared results",analytics?.totals?.shared_results??0,"Candidate opted-in","✓")}</div><div className="cr-overview-grid"><div className="cr-panel"><div className="cr-panel-head"><div><h2>Pipeline distribution</h2><p>Current application counts by stage</p></div></div>{stages.map(([key,label])=><div className="cr-stage-line" key={key}><span>{label}</span><strong>{applications.filter(a=>a.status===key).length}</strong><div><i style={{width:(applications.length?Math.max(2,applications.filter(a=>a.status===key).length/applications.length*100):0)+"%"}}/></div></div>)}</div><div className="cr-panel"><div className="cr-panel-head"><div><h2>Role performance</h2><p>Application volume by job</p></div></div>{(analytics?.jobs||[]).map(j=><div className="cr-performance-row"><div><strong>{j.title}</strong><span>{j.status}</span></div><b>{j.applicants}</b></div>)}</div></div></section>}
          {page==="team"&&<section><div className="cr-page-heading"><div><div className="cr-kicker">ORGANISATION ACCESS</div><h1>Team members</h1><p>Invite colleagues with least-privilege roles.</p></div></div><div className="cr-info-banner">Owner manages organisation settings and billing. Recruiters manage jobs and applicants. Hiring Managers provide structured review. Viewers have read-only reporting access.</div>{canManageTeam&&<form className="cr-panel cr-team-invite" onSubmit={inviteMember}><div><h2>Invite a teammate</h2><p>Invitation token is generated for secure manual delivery.</p></div><label>Email address<input type="email" required value={teamForm.email} onChange={e=>setTeamForm(v=>({...v,email:e.target.value}))}/></label><label>Role<select value={teamForm.role} onChange={e=>setTeamForm(v=>({...v,role:e.target.value}))}><option value="recruiter">Recruiter</option><option value="hiring_manager">Hiring Manager</option><option value="viewer">Viewer / Analyst</option></select></label><button className="cr-primary">Create invitation</button>{inviteToken&&<div className="cr-invite-token"><strong>Invitation token</strong><code>{inviteToken}</code><button type="button" className="cr-outline-btn" onClick={()=>navigator.clipboard?.writeText(inviteToken)}>Copy token</button><p className="cr-footnote">The invited teammate can enter this token on the company setup screen after signing in.</p></div>}</form>}<div className="cr-panel"><div className="cr-panel-head"><div><h2>Workspace access</h2><p>Current invitations and active memberships</p></div></div>{team.map(m=><div className="cr-team-row"><div className="cr-person-avatar">{initials(m.email)}</div><div><strong>{m.email}</strong><span>{m.status}</span></div><span className="cr-role-pill">{m.company_role.replace("_"," ")}</span>{canManageTeam&&m.status!=="revoked"&&<select value={m.company_role} onChange={e=>api("/company/team/"+m.id,{method:"PATCH",body:{role:e.target.value==="revoked"?"revoked":e.target.value}}).then(load).catch(fail)}><option value="recruiter">Recruiter</option><option value="hiring_manager">Hiring Manager</option><option value="viewer">Viewer</option><option value="revoked">Revoke</option></select>}</div>)}{!team.length&&<div className="cr-empty">No additional members yet.</div>}</div></section>}
          {page==="settings"&&<section><div className="cr-page-heading"><div><div className="cr-kicker">ORGANISATION PROFILE</div><h1>Company settings</h1><p>Manage your organisation identity and verification details.</p></div></div><div className="cr-settings-grid"><div className="cr-panel"><h2>Company profile</h2>{isOwner?<form onSubmit={saveCompany}><label>Organisation name<input required value={companyName} onChange={e=>setCompanyName(e.target.value)}/></label><label>Website<input value={website} onChange={e=>setWebsite(e.target.value)}/></label><button className="cr-primary">Save profile</button></form>:<p>Only the organisation owner can edit company identity details.</p>}</div><div className="cr-panel"><h2>Verification status</h2><span className={"cr-status cr-status-"+(workspace?.verification?.status||"not_submitted")}>{(workspace?.verification?.status||"not submitted").replaceAll("_"," ")}</span><p>{workspace?.verification?.decision_reason||"Company verification is required before public job publishing and applicant access."}</p>{isOwner&&<button className="cr-outline-btn" onClick={()=>setVerificationForm(!verificationForm)}>{verificationForm?"Cancel":"Submit / update verification"}</button>}</div></div>{verificationForm&&isOwner&&<form className="cr-panel cr-form-panel" onSubmit={submitVerification}><div className="cr-panel-head"><div><h2>Organisation verification</h2><p>Provide legal details and a reference to supporting evidence. Do not upload sensitive identity documents through this prototype.</p></div></div><div className="cr-form-grid"><label>Legal organisation name<input required value={verification.legal_name} onChange={e=>setVerification(v=>({...v,legal_name:e.target.value}))}/></label><label>Country<input required value={verification.country} onChange={e=>setVerification(v=>({...v,country:e.target.value}))}/></label><label>Business registration ID<input value={verification.business_id} onChange={e=>setVerification(v=>({...v,business_id:e.target.value}))}/></label><label className="cr-span-2">Evidence reference / notes<textarea required minLength="10" value={verification.evidence_note} onChange={e=>setVerification(v=>({...v,evidence_note:e.target.value}))}/></label></div><button className="cr-primary">Submit for verification review</button></form>}</section>}
          {busy&&<div className="cr-loading">Refreshing workspace…</div>}
        </div>
        <footer className="cr-footer"><span>SkillBridge Global · Company workspace</span><span>Evidence-led hiring · Human decisions · Privacy by design</span></footer>
      </main>
      {detail&&<div className="cr-drawer-backdrop" onClick={()=>{setDetail(null);setSelected(null);}}><aside className="cr-drawer" onClick={e=>e.stopPropagation()}><div className="cr-drawer-head"><div><div className="cr-kicker">APPLICATION REPORT</div><h2>{detail.candidate_name}</h2><p>{detail.job_title}</p></div><button onClick={()=>{setDetail(null);setSelected(null);}}>×</button></div><div className="cr-drawer-body"><div className="cr-report-note"><strong>Evidence visibility</strong><p>Candidate assessment data is shown only when the candidate has opted in for this application. Profile and CV fields are not exposed by this prototype.</p></div><div className="cr-report-section"><h3>Application status</h3><span className={"cr-stage cr-stage-"+detail.status}>{detail.status.replaceAll("_"," ")}</span><p>Applied {date(detail.created)}</p></div><div className="cr-report-section"><h3>Required job skills</h3><div className="cr-tags">{(detail.required_skills||[]).map(s=><span>{s}</span>)}</div><p className="cr-footnote">Candidate-level verified / claimed / missing evidence is not yet available in the API.</p></div><div className="cr-report-section"><h3>Shared assessment evidence</h3>{detail.results_shared?detail.assessment_history?.map(r=><div className="cr-score-card"><strong>{r.score??"—"}<small>/100</small></strong><div><b>{r.result_status}</b><span>{r.finished?date(r.finished):"Date unavailable"} · {r.scale}</span><small>Integrity: {r.integrity_summary}</small></div></div>):<p className="cr-muted-box">Candidate has not shared assessment results for this application.</p>}</div>{canManagePipeline&&<div className="cr-report-section"><h3>Move through pipeline</h3><label>New stage<select id="cr-stage-select" defaultValue=""><option value="" disabled>Select a stage</option>{(stageTransitions[detail.status]||[]).map(s=><option value={s}>{s.replaceAll("_"," ")}</option>)}</select></label><label>Decision / transition reason<input value={stageReason} onChange={e=>setStageReason(e.target.value)} placeholder="Required for final decisions"/></label><button className="cr-primary" onClick={()=>{const s=document.getElementById("cr-stage-select")?.value;if(s)moveStage(selected,s);}}>Update application stage</button><button className="cr-outline-btn" onClick={()=>{setSelectedApp(selected);setPage("assessments");setDetail(null);}}>Configure phase-two assessment</button></div>} {canManagePipeline&&<div className="cr-report-section"><h3>Private recruiter notes</h3>{detail.notes?.map(n=><div className="cr-note-item"><p>{n.body}</p><small>{date(n.created)}</small></div>)}<form onSubmit={addNote}><textarea rows="3" required value={note} onChange={e=>setNote(e.target.value)} placeholder="Add a structured, job-related note for your team..."/><button className="cr-outline-btn">Add note</button></form></div>}<div className="cr-report-section"><h3>Decision history</h3>{detail.history?.map(h=><div className="cr-history-item"><strong>{h.from_stage.replaceAll("_"," ")} → {h.to_stage.replaceAll("_"," ")}</strong><span>{h.reason}</span><small>{date(h.created)}</small></div>)}{!detail.history?.length&&<p className="cr-footnote">No stage changes recorded yet.</p>}</div></div></aside></div>}
    </div>
  );
}


/* ---------- shell ---------- */
function Shell({ lang, setLang }) {
  const t = useT();
  const [authed, setAuthed] = useState(!!getToken());
  const [role, setRole] = useState(null);
  const [st, setSt] = useState(null);
  const [view, setView] = useState(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const identity = await api("/enterprise/me");
      setRole(identity.role || "candidate");
      const s = await api("/state"); setSt(s); setTick((x) => x + 1); return s;
    } catch (e) { if (e.status === 401) { setToken(null); setAuthed(false); setRole(null); } }
  }, []);
  useEffect(() => { if (authed) refresh().then((s) => {
    if (!s) return;
    const done = s.attempts.filter((a) => a.status === "done");
    const active = s.attempts.find((a) => a.status === "active");
    setView(active ? (active.kind === "post" ? "retest" : "assess") : !s.confirmed ? "profile" : done.length === 0 ? "gap" : done.length === 1 ? "learn" : "passport");
  }); }, [authed, refresh]);

  const logout = () => { setToken(null); setAuthed(false); setRole(null); setSt(null); setView(null); };
  const Head = (
    <header className="top">
      <a className="brand sb-top-brand" href="#top"><span className="sb-brand-mark">S</span><span>SkillBridge<small>SKILLS · EVIDENCE · OPPORTUNITY</small></span></a>
      {!authed && <nav className="sb-top-nav"><a href="#how-it-works">How it works</a><a href="#signup">For candidates & companies</a></nav>}
      <span className="grow" />
      <div className="seg" role="group" aria-label="Language">
        <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>EN</button>
        <button className={lang === "bn" ? "on" : ""} onClick={() => setLang("bn")}>বাংলা</button>
      </div>
      {authed ? <button className="link" onClick={logout}>{t("logout")}</button> : <a className="sb-top-signin" href="#signin">Sign in <span>→</span></a>}
    </header>
  );

  let body;
  if (!authed) body = <Landing onAuthed={() => setAuthed(true)} />;
  else if (!st || !view) body = <main className="wrap"><p>{t("loading")}</p></main>;
  else if (!st.consented) body = <main className="wrap"><Consent lang={lang} onDone={refresh} /></main>;
  else if (role && role !== "candidate") body = <HiringWorkspace role={role} onLogout={logout} />;
  else body = <CandidatePortal st={st} refresh={refresh} tick={tick} setTick={setTick} onLogout={logout} />;
  const modernCandidateShell = authed && role === "candidate" && !!st?.consented;
  return (
    <>
      {!modernCandidateShell && Head}
      
      {body}
    </>
  );
}

export default function App() {
  const [lang, setLang] = useState(localStorage.getItem("sb_lang") || "en");
  const [hash, setHash] = useState(location.hash);
  useEffect(() => { localStorage.setItem("sb_lang", lang); document.documentElement.lang = lang; }, [lang]);
  useEffect(() => { const h = () => setHash(location.hash); window.addEventListener("hashchange", h); return () => window.removeEventListener("hashchange", h); }, []);
  const m = hash.match(/^#\/p\/([\w-]+)/);
  return (
    <LangCtx.Provider value={lang}>
      {m ? <PublicPassport id={m[1]} /> : <Shell lang={lang} setLang={setLang} />}
    </LangCtx.Provider>
  );
}

function PubBanner() { const t = useT(); return <>{t("mockBanner")}</>; }
