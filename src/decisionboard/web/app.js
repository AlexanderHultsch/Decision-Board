/* Decision Board browser interface. Vanilla JS, no build step. Polls the
   session state once a second while the server is working. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const MEMBERS = {
    "Finance": { color: "var(--c-finance)", icon: '<path d="M12 3v18"/><path d="M16.5 7.5A3.5 3.5 0 0 0 13 5h-2.5a3 3 0 0 0 0 6h3a3 3 0 0 1 0 6H11a3.5 3.5 0 0 1-3.5-2.5"/>' },
    "HW Engineering": { color: "var(--c-hw)", icon: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4"/>' },
    "Mechanical Engineering": { color: "var(--c-mech)", icon: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>' },
    "Manufacturing": { color: "var(--c-manu)", icon: '<path d="M3 21V9l5 3V9l5 3V9l5 3v9H3z"/><path d="M17 12V4h3v8"/><path d="M7 17h2M11 17h2M15 17h2"/>' },
    "SW Engineering": { color: "var(--c-sw)", icon: '<path d="M8 7l-5 5 5 5"/><path d="M16 7l5 5-5 5"/><path d="M14 4l-4 16"/>' },
    "KPI Check": { color: "var(--c-kpi)", icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/><path d="M12 3v2M21 12h-2"/>' },
  };
  const SYNTHESIS_ICON = '<path d="M20 6 9 17l-5-5"/>';
  const SHORT = { "Finance": "Finance", "HW Engineering": "Hardware", "Mechanical Engineering": "Mechanical",
                  "Manufacturing": "Manufacturing", "SW Engineering": "Software", "KPI Check": "KPI" };

  let config = null;
  let session = null;
  let pollTimer = null;
  const openMembers = new Set();

  // -- helpers -----------------------------------------------------------

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function avatar(name, cls) {
    const m = MEMBERS[name];
    return `<span class="avatar ${cls || ""}" style="background:${m.color}" aria-hidden="true"><svg viewBox="0 0 24 24">${m.icon}</svg></span>`;
  }
  async function api(method, path, body) {
    const response = await fetch(path, {
      method, headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    if (!response.ok) throw new Error((data && data.error) || `${response.status} ${response.statusText}`);
    return data;
  }
  function show(name) {
    document.querySelectorAll(".screen").forEach((el) => { el.hidden = el.id !== `screen-${name}`; });
    window.scrollTo({ top: 0 });
  }
  function setError(id, message) {
    const el = $(id);
    el.textContent = message || "";
    el.hidden = !message;
  }
  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }
  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? "Good evening." : h < 12 ? "Good morning." : h < 18 ? "Good afternoon." : "Good evening.";
  }
  function lines(text) { return String(text || "").split("\n").map((s) => s.trim()).filter(Boolean); }

  // -- config / options --------------------------------------------------

  function renderKnowledgeChip() {
    const chip = $("knowledge-chip");
    const status = config.knowledge_status;
    chip.className = "chip";
    if (!status.configured) { chip.classList.add("none"); chip.textContent = "No knowledge source"; }
    else if (status.ok) { chip.classList.add("ok"); chip.textContent = `${status.notes} notes · ${config.vault_path}`; }
    else { chip.classList.add("bad"); chip.textContent = "Knowledge source not reachable"; }
    chip.title = status.error || config.vault_path || "Set a vault folder in Options";
    const hint = $("home-hint");
    if (!config.model) hint.innerHTML = '<span class="error">No model configured. Open Options.</span>';
    else if (!status.configured) hint.textContent = "No knowledge source set. The board answers from your question alone.";
    else if (!status.ok) hint.innerHTML = `<span class="error">${esc(status.error)}</span>`;
    else hint.textContent = `Reads up to ${config.token_budget} tokens of notes from your vault per call.`;
  }

  async function loadConfig() {
    config = await api("GET", "/api/config");
    applyTheme(config.theme);
    renderKnowledgeChip();
  }

  function openOptions() {
    $("opt-vault").value = config.vault_path || "";
    $("opt-budget").value = config.token_budget || 6000;
    $("opt-model").value = config.model || "";
    $("opt-occonfig").value = config.opencode_config || "";
    $("opt-limit").value = config.token_limit == null ? "" : config.token_limit;
    $("opt-auto").checked = !!config.auto_approve;
    $("opt-audit").value = config.audit_folder || "";
    $("opt-theme").value = config.theme || "system";
    $("opt-path").textContent = config.config_path ? `Saved to ${config.config_path}` : "";
    const s = config.knowledge_status;
    $("opt-vault-status").textContent = !s.configured ? "Not set." : s.ok ? `${s.notes} notes found.` : s.error;
    renderRolesStatus(config.roles_status);
    setError("options-error", "");
    $("options-dialog").hidden = false;
  }

  async function saveOptions(event) {
    event.preventDefault();
    try {
      config = await api("POST", "/api/config", {
        vault_path: $("opt-vault").value,
        token_budget: Number($("opt-budget").value) || 6000,
        model: $("opt-model").value,
        opencode_config: $("opt-occonfig").value,
        token_limit: $("opt-limit").value === "" ? null : Number($("opt-limit").value),
        auto_approve: $("opt-auto").checked,
        audit_folder: $("opt-audit").value,
        theme: $("opt-theme").value,
      });
      applyTheme(config.theme);
      renderKnowledgeChip();
      $("options-dialog").hidden = true;
    } catch (err) { setError("options-error", err.message); }
  }

  async function browse() {
    const btn = $("btn-browse");
    btn.disabled = true; btn.textContent = "Choose in the dialog…";
    try {
      const data = await api("POST", "/api/pick-folder", { initial: $("opt-vault").value });
      if (data.path) $("opt-vault").value = data.path;
      else if (data.path === null) $("opt-vault-status").textContent = "No folder chosen (or no folder dialog available on this machine - type the path instead).";
    } catch (err) { setError("options-error", err.message); }
    btn.disabled = false; btn.textContent = "Browse…";
  }

  function renderRolesStatus(r) {
    const el = $("opt-roles-status");
    if (!r) { el.textContent = ""; return; }
    if (r.error) { el.innerHTML = `<span class="error">${esc(r.error)}</span>`; return; }
    if (!r.expected_folder) { el.textContent = "Set the vault first; the Roles folder lives inside it."; return; }
    if (r.from_vault.length === 6) el.textContent = `All six profiles found in ${r.folder}.`;
    else if (r.from_vault.length === 0) el.textContent = `No profiles in ${r.expected_folder} - the built-in examples are used. Install them to edit in Obsidian.`;
    else el.textContent = `${r.from_vault.length} profile(s) in ${r.folder}; built-in examples for ${r.built_in.join(", ")}.`;
    $("btn-install-roles").hidden = r.from_vault.length === 6;
  }

  async function installRoles() {
    const btn = $("btn-install-roles");
    btn.disabled = true;
    try {
      const r = await api("POST", "/api/roles/install");
      renderRolesStatus(r);
      if (r.written && r.written.length) $("opt-roles-status").textContent += ` Written: ${r.written.length} file(s).`;
    } catch (err) { setError("options-error", err.message); }
    btn.disabled = false;
  }

  async function browseConfigFile() {
    const btn = $("btn-browse-occonfig");
    btn.disabled = true; btn.textContent = "Choose in the dialog…";
    try {
      const data = await api("POST", "/api/pick-file", { initial: $("opt-occonfig").value });
      if (data.path) $("opt-occonfig").value = data.path;
    } catch (err) { setError("options-error", err.message); }
    btn.disabled = false; btn.textContent = "Browse…";
  }

  // -- session flow -----------------------------------------------------

  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
  function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
      if (!session) return stopPolling();
      try { session = await api("GET", `/api/sessions/${session.id}`); render(); }
      catch (err) { stopPolling(); showError(err.message); }
    }, 1000);
  }
  function needsPolling(s) {
    return ["clarifying", "running", "synthesising", "proposing"].includes(s.phase) || s.busy;
  }

  async function ask(event) {
    event.preventDefault();
    const question = $("question").value.trim();
    if (!question) return;
    setError("home-error", "");
    try {
      session = await api("POST", "/api/sessions", { question });
      openMembers.clear();
      render();
    } catch (err) { setError("home-error", err.message); }
  }

  function render() {
    if (!session) return show("home");
    if (needsPolling(session)) { if (!pollTimer) startPolling(); } else stopPolling();
    switch (session.phase) {
      case "clarifying": return renderClarifying();
      case "questions": return renderQuestions();
      case "confirm": return renderConfirm();
      case "running":
      case "synthesising": return renderRunning();
      case "result": return renderResult();
      case "proposing": return show("proposing");
      case "proposal": return renderProposal();
      case "written":
      case "closed": return renderDone();
      case "error": return showError(session.error);
      default: return showError(`Unknown state: ${session.phase}`);
    }
  }

  function showError(message) {
    $("error-detail").textContent = message || "Unknown error.";
    show("error");
  }

  function renderClarifying() {
    const k = session.knowledge;
    $("clarifying-detail").textContent = k && k.vault_path
      ? `Selected ${k.selected} of ${k.total} notes from your vault (about ${k.tokens} tokens). Working out what is missing…`
      : "Working out what the board needs to know…";
    show("clarifying");
  }

  function renderQuestions() {
    if (!$("screen-questions").hidden) return;   // already drawn; keep the typed answers
    const form = $("questions-form");
    form.innerHTML = session.clarification.questions.map((q, i) => `
      <label><span class="q-text">${i + 1}. ${esc(q)}</span>
        <textarea rows="2" data-index="${i}" placeholder="Your answer, or leave blank">${esc(session.answers[i] || "")}</textarea></label>`).join("");
    show("questions");
    const first = form.querySelector("textarea");
    if (first) first.focus();
  }

  async function submitAnswers() {
    const answers = Array.from($("questions-form").querySelectorAll("textarea")).map((t) => t.value.trim());
    try { session = await api("POST", `/api/sessions/${session.id}/answers`, { answers }); render(); }
    catch (err) { showError(err.message); }
  }

  function renderConfirm() {
    if (!$("screen-confirm").hidden) return;
    const inp = session.inputs;
    $("in-topic").value = inp.topic || "";
    $("in-context").value = inp.context || "";
    $("in-options").value = (inp.options || []).join("\n");
    $("in-constraints").value = (inp.constraints || []).join("\n");
    const k = session.knowledge;
    const r = session.roles || { from_vault: [], built_in: [] };
    const rolesLine = r.from_vault.length === 6
      ? `All six role profiles come from your vault (${r.folder}).`
      : r.from_vault.length === 0
        ? "Role profiles: built-in examples for all six members - no Roles folder in your vault yet (Options → install)."
        : `Role profiles: ${r.from_vault.length} from your vault, built-in examples for ${r.built_in.join(", ")}.`;
    $("confirm-knowledge").textContent = (k && k.vault_path
      ? `${k.selected} note(s) from the vault are appended to the context for every member: ${k.notes.slice(0, 6).join(", ")}${k.notes.length > 6 ? ", …" : ""}. `
      : "No knowledge source configured. ") + rolesLine + " Seven model calls follow.";
    show("confirm");
  }

  async function runBoard() {
    const body = {
      topic: $("in-topic").value, context: $("in-context").value,
      options: lines($("in-options").value), constraints: lines($("in-constraints").value),
    };
    try { session = await api("POST", `/api/sessions/${session.id}/run`, body); render(); }
    catch (err) { showError(err.message); }
  }

  function renderRunning() {
    const grid = $("member-grid");
    grid.innerHTML = Object.entries(session.members).map(([name, state]) => `
      <div class="member-tile ${state}" style="color:${MEMBERS[name].color}">
        ${avatar(name)}
        <div><div style="color:var(--text);font-weight:600">${esc(SHORT[name])}</div><div class="muted small">${esc(name)}</div></div>
        <span class="state">${state === "pending" ? "waiting" : state === "running" ? "thinking…" : state === "done" ? "answered" : "failed"}</span>
      </div>`).join("");
    const synthesising = session.phase === "synthesising";
    $("running-title").textContent = synthesising ? "Consolidating the six assessments" : "The board is in session";
    $("running-detail").textContent = synthesising
      ? "The seventh call reads all six answers and writes the recommendation."
      : "Six members, each answering without seeing the others.";
    show("running");
  }

  function renderSynthesis(result) {
    const d = result.synthesis_data;
    if (!d) return `<p class="error">${esc(result.synthesis)}</p>`;
    const list = (items) => (items && items.length) ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "<span class='muted'>none stated</span>";
    return `
      <div class="rec">${esc(d.overall_recommendation)}</div>
      <dl>
        <dt>Decisive criterion</dt><dd>${esc(d.decisive_criterion)}</dd>
        <dt>Counter-arguments</dt><dd>${list(d.counter_arguments)}</dd>
        <dt>What would change it</dt><dd>${esc(d.what_would_change_it)}</dd>
      </dl>
      <div class="disagree"><dl><dt>Disagreements</dt><dd>${list(d.disagreements)}</dd></dl></div>`;
  }

  function renderResult() {
    const r = session.result;
    const alreadyShown = !$("screen-result").hidden;
    if (!alreadyShown) {
      $("result-topic").textContent = r.topic;
      $("synthesis-body").innerHTML = renderSynthesis(r);
      $("synthesis-card").querySelector(".avatar").innerHTML = `<svg viewBox="0 0 24 24">${SYNTHESIS_ICON}</svg>`;
      const answered = new Map(r.assessments.map((a) => [a.member, a]));
      const failed = new Map(r.failed_members.map((f) => [f.split(":")[0], f]));
      $("member-chips").innerHTML = Object.keys(MEMBERS).map((name) => `
        <button type="button" class="member-chip ${failed.has(name) ? "failed" : ""}" data-member="${esc(name)}" style="color:${MEMBERS[name].color}" ${failed.has(name) ? "disabled" : ""}>
          ${avatar(name)}<span style="color:var(--text)">${esc(SHORT[name])}</span></button>`).join("");
      $("member-chips").querySelectorAll(".member-chip").forEach((chip) => chip.addEventListener("click", () => {
        const name = chip.dataset.member;
        if (openMembers.has(name)) openMembers.delete(name); else openMembers.add(name);
        renderMemberCards(answered);
      }));
      renderMemberCards(answered);
      setError("failed-members", r.failed_members.length ? `Failed member(s): ${r.failed_members.join(" · ")}` : "");
    }
    $("turns").innerHTML = session.turns.map((t) => `
      <div class="turn ${t.pending ? "pending" : ""} ${t.error ? "error" : ""}">
        <div class="q">${esc(t.question)}</div>
        <div class="a">${t.pending ? "The board is thinking…" : esc(t.answer)}</div>
      </div>`).join("");
    $("btn-followup").disabled = session.busy;
    $("btn-close").disabled = session.busy;
    $("result-hint").textContent = `${session.llm_calls} model call(s) so far · each follow-up costs one`;
    setError("result-error", session.error || "");
    show("result");
  }

  function renderMemberCards(answered) {
    $("member-chips").querySelectorAll(".member-chip").forEach((chip) => chip.classList.toggle("active", openMembers.has(chip.dataset.member)));
    $("member-cards").innerHTML = Object.keys(MEMBERS).filter((n) => openMembers.has(n) && answered.has(n)).map((name) => {
      const a = answered.get(name);
      return `<div class="card member-card" style="border-left-color:${MEMBERS[name].color}">
        <div class="card-head">${avatar(name)}<div><div class="card-title">${esc(name)}</div></div></div>
        <dl><dt>View</dt><dd>${esc(a.view)}</dd><dt>Risks</dt><dd>${esc(a.risks)}</dd><dt>Recommendation</dt><dd>${esc(a.recommendation)}</dd></dl>
      </div>`;
    }).join("");
  }

  async function followUp(event) {
    event.preventDefault();
    const question = $("followup").value.trim();
    if (!question) return;
    try {
      session = await api("POST", `/api/sessions/${session.id}/follow-up`, { question });
      $("followup").value = "";
      render();
    } catch (err) { setError("result-error", err.message); }
  }

  async function closeTopic(remember) {
    setError("close-error", "");
    try {
      session = await api("POST", `/api/sessions/${session.id}/close`, { remember });
      $("close-dialog").hidden = true;
      render();
    } catch (err) { setError("close-error", err.message); }
  }

  function renderProposal() {
    if (!$("screen-proposal").hidden) return;
    const p = session.proposal;
    $("prop-vault").value = session.knowledge.vault_path || "";
    $("prop-path").value = p.path;
    $("prop-title").value = p.title;
    $("prop-tags").value = p.tags.join(", ");
    $("prop-body").value = p.body;
    $("prop-mode").textContent = p.mode === "append" ? "appends to existing note" : "new note";
    $("prop-preview").textContent = p.preview;
    setError("proposal-error", p.parse_error || "");
    show("proposal");
  }

  async function writeMemory() {
    const body = {
      path: $("prop-path").value, title: $("prop-title").value,
      tags: $("prop-tags").value.split(",").map((t) => t.trim()).filter(Boolean),
      body: $("prop-body").value,
    };
    try { session = await api("POST", `/api/sessions/${session.id}/memory`, body); render(); }
    catch (err) { setError("proposal-error", err.message); }
  }

  async function discardMemory() {
    try { session = await api("POST", `/api/sessions/${session.id}/discard-memory`); render(); }
    catch (err) { setError("proposal-error", err.message); }
  }

  function renderDone() {
    if (session.phase === "written") {
      $("done-title").textContent = "Written to your vault";
      $("done-detail").textContent = session.written_path;
    } else {
      $("done-title").textContent = "Topic closed";
      $("done-detail").textContent = `Nothing was written. ${session.llm_calls} model call(s) in total.`;
    }
    show("done");
  }

  function newTopic() {
    stopPolling();
    session = null;
    openMembers.clear();
    $("question").value = "";
    $("followup").value = "";
    show("home");
    $("question").focus();
  }

  // -- wiring -------------------------------------------------------------

  $("greeting").textContent = greeting();
  $("ask-form").addEventListener("submit", ask);
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask(e); });
  $("btn-answers").addEventListener("click", submitAnswers);
  $("btn-back-home").addEventListener("click", newTopic);
  $("btn-run").addEventListener("click", runBoard);
  $("btn-back-questions").addEventListener("click", () => { session.phase = "questions"; show("home"); renderQuestions(); });
  $("followup-form").addEventListener("submit", followUp);
  $("followup").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) followUp(e); });
  $("btn-close").addEventListener("click", () => { setError("close-error", ""); $("close-dialog").hidden = false; });
  $("btn-close-cancel").addEventListener("click", () => { $("close-dialog").hidden = true; });
  $("btn-close-no").addEventListener("click", () => closeTopic(false));
  $("btn-close-yes").addEventListener("click", () => closeTopic(true));
  $("btn-write").addEventListener("click", writeMemory);
  $("btn-discard").addEventListener("click", discardMemory);
  $("btn-new").addEventListener("click", newTopic);
  $("btn-error-home").addEventListener("click", newTopic);
  $("btn-error-options").addEventListener("click", openOptions);
  $("btn-options").addEventListener("click", openOptions);
  $("btn-options-cancel").addEventListener("click", () => { $("options-dialog").hidden = true; });
  $("options-form").addEventListener("submit", saveOptions);
  $("btn-browse").addEventListener("click", browse);
  $("btn-browse-occonfig").addEventListener("click", browseConfigFile);
  $("btn-install-roles").addEventListener("click", installRoles);
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true; }));

  loadConfig().then(() => show("home")).catch((err) => { $("home-hint").textContent = err.message; show("home"); });
})();
