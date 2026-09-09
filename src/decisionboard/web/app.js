/* Decision Board browser interface. Vanilla JS, no build step. Polls the
   session state once a second while the server is working. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const ICONS = {
    dollar: '<path d="M12 3v18"/><path d="M16.5 7.5A3.5 3.5 0 0 0 13 5h-2.5a3 3 0 0 0 0 6h3a3 3 0 0 1 0 6H11a3.5 3.5 0 0 1-3.5-2.5"/>',
    chip: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    factory: '<path d="M3 21V9l5 3V9l5 3V9l5 3v9H3z"/><path d="M17 12V4h3v8"/><path d="M7 17h2M11 17h2M15 17h2"/>',
    code: '<path d="M8 7l-5 5 5 5"/><path d="M16 7l5 5-5 5"/><path d="M14 4l-4 16"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/><path d="M12 3v2M21 12h-2"/>',
    scale: '<path d="M12 3v18M5 21h14"/><path d="M3 7h18"/><path d="M6 7l-3 7a3 3 0 0 0 6 0L6 7zM18 7l-3 7a3 3 0 0 0 6 0l-3-7z"/>',
    people: '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M15 20a4.5 4.5 0 0 1 7 -3"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
    truck: '<rect x="2" y="7" width="12" height="9" rx="1"/><path d="M14 10h4l3 3v3h-7z"/><circle cx="6" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
    flask: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3"/><path d="M7 15h10"/>',
    chart: '<path d="M3 21h18"/><path d="M6 17v-5M11 17V7M16 17v-8M21 17V4"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',
    person: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  };
  const SYNTHESIS_ICON = '<path d="M20 6 9 17l-5-5"/>';
  let memberMeta = {};   // name -> {title, short, icon, color, perspective}, from the session

  function meta(name) {
    return memberMeta[name] || { title: name, short: name.slice(0, 14), icon: "person", color: "#6b7280", perspective: "" };
  }
  function setMemberMeta(list) {
    memberMeta = {};
    (list || []).forEach((m) => { memberMeta[m.name] = m; });
  }

  let config = null;
  let session = null;
  let pollTimer = null;
  const openMembers = new Set();

  // What the browser keeps between reloads (9 September 2026: nothing typed
  // is lost to an error or a refresh): the current session's id and a draft
  // of everything typed, per session. localStorage may be unavailable; every
  // access is guarded and the page works without it.
  const store = {
    get(key) { try { const v = localStorage.getItem(`decision-board:${key}`); return v ? JSON.parse(v) : null; } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(`decision-board:${key}`, JSON.stringify(value)); } catch (e) { /* no storage */ } },
    del(key) { try { localStorage.removeItem(`decision-board:${key}`); } catch (e) { /* no storage */ } },
  };
  function draft() { return (session && store.get(`draft:${session.id}`)) || {}; }
  function saveDraft(patch) { if (session) store.set(`draft:${session.id}`, Object.assign(draft(), patch)); }
  function forgetSession() { if (session) store.del(`draft:${session.id}`); store.del("session"); }

  // -- helpers -----------------------------------------------------------

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function avatar(name, cls) {
    const m = meta(name);
    const icon = Object.prototype.hasOwnProperty.call(ICONS, m.icon) ? ICONS[m.icon] : ICONS.person;
    return `<span class="avatar ${cls || ""}" style="background:${esc(m.color)}" aria-hidden="true"><svg viewBox="0 0 24 24">${icon}</svg></span>`;
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
    // Scroll to the top only when the screen actually changes: a poll that
    // redraws the same screen must not pull the page back up (9 September 2026).
    const target = $(`screen-${name}`);
    const changed = !target || target.hidden;
    document.querySelectorAll(".screen").forEach((el) => { el.hidden = el.id !== `screen-${name}`; });
    if (changed) window.scrollTo({ top: 0 });
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
  // Text that the model wrote as bullets ("- " lines) becomes a list; anything
  // else is shown as it is. Arrays are lists too.
  function fmt(value) {
    if (Array.isArray(value)) return value.length ? `<ul class="bullets">${value.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "<span class='muted'>none stated</span>";
    const text = String(value == null ? "" : value).trim();
    if (!text) return "<span class='muted'>none stated</span>";
    const rows = text.split("\n").map((r) => r.trim()).filter(Boolean);
    if (rows.length && rows.every((r) => /^[-*•]\s+/.test(r))) {
      return `<ul class="bullets">${rows.map((r) => `<li>${esc(r.replace(/^[-*•]\s+/, ""))}</li>`).join("")}</ul>`;
    }
    return esc(text);
  }
  // A pick-list of members: a labelled checkbox per member, all ticked at first.
  function renderPicks(container, names, ticked) {
    container.innerHTML = names.map((name) => `
      <label class="${ticked.has(name) ? "" : "off"}" style="color:${esc(meta(name).color)}">
        <input type="checkbox" value="${esc(name)}" ${ticked.has(name) ? "checked" : ""}>
        ${avatar(name)}<span style="color:var(--text)">${esc(name)}</span></label>`).join("");
    container.querySelectorAll("input").forEach((box) => box.addEventListener("change", () => {
      box.closest("label").classList.toggle("off", !box.checked);
    }));
  }
  // Where a member's material came from: verified facts from the knowledge
  // net (with the note), flagged citations, and the member's own judgement.
  function sourcesRows(a) {
    const facts = a.sources || [];
    const net = facts.length
      ? `<ul class="bullets sources">${facts.map((s) => `<li class="${s.verified ? "ok" : "bad"}"><span class="mark">${s.verified ? "✓" : "!"}</span> ${esc(s.fact)} <span class="note">${esc(s.source || "no note named")}${s.verified ? "" : ` · ${esc(s.note)}`}</span></li>`).join("")}</ul>`
      : "<span class='muted'>nothing taken from the knowledge net</span>";
    const own = a.judgement ? fmt(a.judgement) : "<span class='muted'>none listed</span>";
    const flags = (a.flags || []).length ? `<dt class="bad">Check</dt><dd class="flag">${a.flags.map(esc).join("; ")}</dd>` : "";
    return `<dt>From the knowledge net</dt><dd>${net}</dd><dt>Own judgement</dt><dd>${own}</dd>${flags}`;
  }
  function modeOf(id) { return $(id).value || "individual"; }
  function setMode(id, value) { $(id).value = value; if ($(id).value !== value) $(id).value = "individual"; }
  // One member's answer as rows: the full assessment, or the reasons why the
  // topic does not touch it. Used on the member cards and inside follow-ups.
  function memberBody(a) {
    if (a.applies === false) {
      return `<p class="na-note">This member says the topic does not touch its responsibilities. Its reasons:</p><dl><dt>Why not</dt><dd>${fmt(a.view)}</dd></dl>`;
    }
    return `<dl><dt>View</dt><dd>${fmt(a.view)}</dd>${a.impact ? `<dt>Impact on my area</dt><dd>${fmt(a.impact)}</dd>` : ""}<dt>Risks</dt><dd>${fmt(a.risks)}</dd><dt>Recommendation</dt><dd>${fmt(a.recommendation)}</dd>${sourcesRows(a)}</dl>`;
  }
  function picked(container) {
    return Array.from(container.querySelectorAll("input:checked")).map((box) => box.value);
  }

  // -- config / options --------------------------------------------------

  function renderKnowledgeChip() {
    const chip = $("knowledge-chip");
    const status = config.knowledge_status;
    chip.className = "chip";
    if (!status.configured) { chip.classList.add("none"); chip.textContent = "No knowledge source"; }
    else if (status.ok) { chip.classList.add("ok"); chip.textContent = `${status.notes} notes`; }
    else { chip.classList.add("bad"); chip.textContent = "Knowledge source not reachable"; }
    // The path is a tooltip, not a label (9 September 2026): hover to see it.
    chip.title = status.error || (config.vault_path ? `Knowledge source: ${config.vault_path}` : "Set a vault folder in Options");
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
    renderProjectPicker();
  }

  // The project picker on the home page: a dropdown with checkboxes, like a
  // spreadsheet filter. The default comes from the configuration; the
  // choice travels with the question.
  let chosenProjects = null;   // null until the config is known
  function defaultProjects() {
    return String(config.project || "").split(/[,;]/).map((s) => s.trim()).filter(Boolean);
  }
  function renderProjectPicker() {
    const names = (config.projects || []).slice();
    if (chosenProjects === null) chosenProjects = defaultProjects();
    chosenProjects.forEach((p) => { if (!names.some((n) => n.toLowerCase() === p.toLowerCase())) names.push(p); });
    const all = chosenProjects.length === 0;
    $("projects-list").innerHTML = `<label class="all"><input type="checkbox" value="" ${all ? "checked" : ""}> All projects</label>` +
      names.map((n) => `<label><input type="checkbox" value="${esc(n)}" ${chosenProjects.some((p) => p.toLowerCase() === n.toLowerCase()) ? "checked" : ""}> ${esc(n)}</label>`).join("");
    $("projects-label").textContent = all ? "All projects" : chosenProjects.length === 1 ? chosenProjects[0] : `${chosenProjects.length} projects`;
    $("projects-list").querySelectorAll("input").forEach((box) => box.addEventListener("change", () => {
      if (box.value === "") chosenProjects = [];
      else {
        chosenProjects = Array.from($("projects-list").querySelectorAll("input")).filter((b) => b.value && b.checked).map((b) => b.value);
      }
      renderProjectPicker();
    }));
    $("btn-projects").disabled = names.length === 0;
    if (names.length === 0) $("projects-label").textContent = "No project pages in the vault";
  }
  function toggleProjectMenu(open) {
    const menu = $("projects-menu");
    menu.hidden = open === undefined ? !menu.hidden : !open;
    $("btn-projects").setAttribute("aria-expanded", String(!menu.hidden));
  }

  function openOptions() {
    $("opt-vault").value = config.vault_path || "";
    $("opt-roles").value = config.roles_folder || "";
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
        roles_folder: $("opt-roles").value,
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

  // One folder or file dialog on the server, its result written into an input.
  async function browseInto(buttonId, inputId, endpoint, extra) {
    const btn = $(buttonId);
    btn.disabled = true; btn.textContent = "Choose in the dialog…";
    try {
      const data = await api("POST", endpoint, Object.assign({ initial: $(inputId).value }, extra || {}));
      if (data.path) $(inputId).value = data.path;
    } catch (err) { setError("options-error", err.message); }
    btn.disabled = false; btn.textContent = "Browse…";
  }
  const browse = () => browseInto("btn-browse", "opt-vault", "/api/pick-folder", { title: "Choose the knowledge source (Obsidian vault)" });
  const browseRoles = () => browseInto("btn-browse-roles", "opt-roles", "/api/pick-folder", { title: "Choose the roles folder" });
  const browseConfigFile = () => browseInto("btn-browse-occonfig", "opt-occonfig", "/api/pick-file");

  function renderRolesStatus(r) {
    const el = $("opt-roles-status");
    if (!r) { el.textContent = ""; return; }
    if (r.error) {
      el.innerHTML = `<span class="error">${esc(r.error)}</span>`;
      $("btn-install-roles").hidden = false;
      return;
    }
    let text = `Board of ${r.count}: ${r.members.join(", ")} - from ${r.folder}.`;
    if (r.skipped && r.skipped.length) text += ` Not on the board: ${r.skipped.map((x) => `${x.member} (${x.reason})`).join("; ")}.`;
    const conduct = r.conduct || [];
    text += conduct.length ? ` Common note(s): ${conduct.join(", ")}.` : " No common note (kind: conduct) in the folder.";
    el.textContent = text;
    $("btn-install-roles").hidden = conduct.length > 0;
  }

  async function installRoles() {
    const btn = $("btn-install-roles");
    btn.disabled = true;
    try {
      // Save the folder typed above first, so the examples land where the user said.
      if ($("opt-roles").value !== (config.roles_folder || "") || $("opt-vault").value !== (config.vault_path || "")) {
        config = await api("POST", "/api/config", { roles_folder: $("opt-roles").value, vault_path: $("opt-vault").value });
        renderProjectPicker();
      }
      const r = await api("POST", "/api/roles/install");
      config.roles_status = r;
      renderRolesStatus(r);
      if (r.written) $("opt-roles-status").textContent += ` Written ${r.written.length} file(s) to ${r.target}.`;
    } catch (err) { setError("options-error", err.message); }
    btn.disabled = false;
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
      session = await api("POST", "/api/sessions", { question, projects: chosenProjects || [] });
      store.del("question");
      openMembers.clear();
      $("screen-result").dataset.phase = "";
      $("turns").dataset.key = "";
      render();
    } catch (err) { setError("home-error", err.message); }
  }

  function renderNav() {
    const nav = (session && session.nav) || { back: false, forward: false };
    const onHome = !$("screen-home").hidden;
    $("btn-nav-back").disabled = !session || onHome || !nav.back && !["questions"].includes(session.phase);
    $("btn-nav-forward").disabled = !session || (onHome ? ["closed", "written"].includes(session.phase) : !nav.forward);
  }

  let lastPhase = null;
  function pushHistory() {
    const phase = session ? `${session.id}:${session.phase}` : "home";
    if (phase === lastPhase) return;
    lastPhase = phase;
    try { history.pushState({ phase }, ""); } catch (e) { /* not available */ }
  }

  function render() {
    if (!session) { show("home"); renderNav(); return; }
    store.set("session", session.id);
    if (session.member_meta && session.member_meta.length) setMemberMeta(session.member_meta);
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
  // Every render also refreshes the arrows and the browser history.
  const _render = render;
  render = function () { _render(); renderNav(); pushHistory(); };

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
    const done = session.rounds || [];
    const round = done.length + 1;
    $("questions-intro").textContent = round === 1
      ? "A few things would change the recommendation. Answer what you can; leave the rest blank."
      : `Round ${round}: your answers raised a few more points. Answer what you can; leave the rest blank.`;
    $("rounds-done").innerHTML = done.map((r, n) => `
      <details><summary>Round ${n + 1}: ${r.questions.length} question(s) answered</summary>
        <dl>${r.questions.map((q, i) => `<dt>${esc(q)}</dt><dd>${esc(r.answers[i] || "(not answered)")}</dd>`).join("")}</dl>
      </details>`).join("");
    const form = $("questions-form");
    const kept = draft().answers || {};
    const key = session.clarification.questions.join("|");
    form.innerHTML = session.clarification.questions.map((q, i) => `
      <label><span class="q-text">${i + 1}. ${esc(q)}</span>
        <textarea rows="2" data-index="${i}" placeholder="Your answer, or leave blank">${esc(session.answers[i] || (kept.key === key ? kept.values[i] : "") || "")}</textarea></label>`).join("");
    form.oninput = () => saveDraft({ answers: { key, values: Array.from(form.querySelectorAll("textarea")).map((t) => t.value) } });
    const last = round >= (session.max_rounds || 5);
    $("btn-answers").textContent = last ? "Continue to the board" : "Continue";
    $("btn-answers-final").hidden = last;
    $("questions-hint").textContent = last
      ? `This is the last round (${session.max_rounds}). The board is asked next.`
      : "Continue: the clarifier checks whether anything is still missing and asks again if so, or hands over to the board. Ask the board now: skip further questions.";
    show("questions");
    const first = form.querySelector("textarea");
    if (first) first.focus();
  }

  async function submitAnswers(final) {
    const answers = Array.from($("questions-form").querySelectorAll("textarea")).map((t) => t.value.trim());
    try { session = await api("POST", `/api/sessions/${session.id}/answers`, { answers, final: !!final }); render(); }
    catch (err) { showError(err.message); }
  }

  function renderConfirm() {
    if (!$("screen-confirm").hidden) return;
    const inp = session.inputs;
    const kept = draft().confirm;
    $("in-topic").value = (kept && kept.topic) || inp.topic || "";
    $("in-context").value = kept ? kept.context : (inp.context || "");
    ["in-topic", "in-context", "in-options", "in-constraints"].forEach((id) => { $(id).addEventListener("input", requestEstimate); });
    $("in-options").value = kept ? kept.options : (inp.options || []).join("\n");
    $("in-constraints").value = kept ? kept.constraints : (inp.constraints || []).join("\n");
    $("confirm-form").oninput = saveConfirmDraft;
    const k = session.knowledge;
    const r = session.roles || { members: [], count: 0, source: "", folder: null };
    let rolesLine = `Board of ${r.count}: ${r.members.join(", ")} (profiles from ${r.folder}).`;
    if (r.skipped && r.skipped.length) rolesLine += ` Not on the board: ${r.skipped.map((x) => `${x.member}, ${x.reason}`).join("; ")}.`;
    const kpi = r.kpi_members || [];
    rolesLine += kpi.length ? ` KPI notes from the vault attached for: ${kpi.join(", ")}.` : " No KPI notes (kind: kpi) in the vault yet.";
    const names = r.members || [];
    const ticked = new Set(kept && kept.members ? kept.members : (session.selected_members && session.selected_members.length ? session.selected_members : names));
    renderPicks($("confirm-members"), names, ticked);
    $("confirm-members").querySelectorAll("input").forEach((box) => box.addEventListener("change", saveConfirmDraft));
    setMode("run-mode", (kept && kept.mode) || session.mode || "individual");
    $("budget").value = (kept && kept.budget != null) ? kept.budget : (session.budget != null ? session.budget : (config.token_budget || 6000));
    picks = { extra: (kept && kept.extra) || session.extra || [], exclude: (kept && kept.exclude) || session.exclude || [] };
    outlineCache = null;
    $("budget").disabled = !(k && k.vault_path);
    const countLine = () => {
      $("budget-value").textContent = `${fmtNum(Number($("budget").value))} tokens`;
      $("confirm-knowledge").textContent = (k && k.vault_path ? "" : "No knowledge source configured. ") + rolesLine;
      requestEstimate();
    };
    $("confirm-members").querySelectorAll("input").forEach((box) => box.addEventListener("change", countLine));
    $("run-mode").onchange = () => { countLine(); saveConfirmDraft(); };
    $("budget").oninput = () => { countLine(); saveConfirmDraft(); };
    countLine();
    show("confirm");
  }

  function saveConfirmDraft() {
    saveDraft({ confirm: {
      topic: $("in-topic").value, context: $("in-context").value, options: $("in-options").value,
      constraints: $("in-constraints").value, members: picked($("confirm-members")), mode: modeOf("run-mode"),
      budget: Number($("budget").value), extra: picks.extra, exclude: picks.exclude,
    } });
  }

  // The live estimate: exact calls, "about" tokens, from the server's own
  // prompt builders. Debounced, and a stale answer never overwrites a newer one.
  let estimateTimer = null;
  let estimateSeq = 0;
  let picks = { extra: [], exclude: [] };     // manual picks for the open topic: section ids
  let outlineCache = null;
  function renderOutline() {
    const filter = ($("outline-filter").value || "").toLowerCase();
    const rows = (outlineCache || []).map((note) => {
      const secs = note.sections.filter((s) => !filter || note.path.toLowerCase().includes(filter) || (s.heading || "").toLowerCase().includes(filter));
      if (!secs.length) return "";
      return `<div class="note"><div class="note-title">${esc(note.path)}</div>${secs.map((s) => `
        <label><input type="checkbox" data-id="${esc(s.id)}" ${picks.extra.includes(s.id) ? "checked" : ""}> ${esc(s.heading || "(whole note)")}<span class="tok">${fmtNum(s.tokens)}</span></label>`).join("")}</div>`;
    }).join("");
    $("outline").innerHTML = rows || "<span class='muted small'>Nothing matches.</span>";
    $("outline").querySelectorAll("input").forEach((box) => box.addEventListener("change", () => {
      const id = box.dataset.id;
      picks.extra = picks.extra.filter((x) => x !== id);
      picks.exclude = picks.exclude.filter((x) => x !== id);
      if (box.checked) picks.extra.push(id);
      saveConfirmDraft(); requestEstimate();
    }));
  }
  function renderMemberSections(e) {
    $("estimate-notes").innerHTML = `<dl>${Object.entries(e.sections || {}).map(([m, secs]) => `<dt>${esc(m)}</dt><dd>${secs.length ? `<ul class="sec-list">${secs.map((s) => `
      <li><input type="checkbox" data-id="${esc(s.id)}" ${picks.exclude.includes(s.id) ? "" : "checked"} title="Untick to leave this out for every member"> ${esc(s.path)}${s.heading ? ` · ${esc(s.heading)}` : ""}${s.forced ? ' <span class="forced">your pick</span>' : ""}<span class="tok">${fmtNum(s.tokens)}</span></li>`).join("")}</ul>` : "<span class='muted'>nothing from the vault</span>"}</dd>`).join("")}</dl>`;
    $("estimate-notes").querySelectorAll("input").forEach((box) => box.addEventListener("change", () => {
      const id = box.dataset.id;
      picks.exclude = picks.exclude.filter((x) => x !== id);
      if (!box.checked) { picks.exclude.push(id); picks.extra = picks.extra.filter((x) => x !== id); }
      saveConfirmDraft(); requestEstimate();
    }));
  }
  function requestEstimate() {
    if (estimateTimer) clearTimeout(estimateTimer);
    estimateTimer = setTimeout(async () => {
      if (!session || $("screen-confirm").hidden) return;
      const seq = ++estimateSeq;
      const members = picked($("confirm-members"));
      if (!members.length) { $("estimate").textContent = "Tick at least one member."; return; }
      try {
        const e = await api("POST", `/api/sessions/${session.id}/estimate`, {
          members, mode: modeOf("run-mode"), budget: Number($("budget").value),
          topic: $("in-topic").value, context: $("in-context").value,
          options: lines($("in-options").value), constraints: lines($("in-constraints").value),
          extra: picks.extra, exclude: picks.exclude, outline: !outlineCache,
        });
        if (seq !== estimateSeq) return;
        const learned = e.overhead_learned_from ? `overhead of ${fmtNum(e.overhead_per_call)} per call learned from ${e.overhead_learned_from} real call(s) of this topic` : `overhead of ${fmtNum(e.overhead_per_call)} per call assumed until the first real call`;
        const forced = e.forced_tokens ? ` · <strong>${fmtNum(e.forced_tokens)} tokens</strong> from your picks on top of the slider` : "";
        $("estimate").innerHTML = `<strong>${e.calls} model call(s)</strong>, about <strong>${fmtNum(e.tokens_in)} tokens in</strong>${forced} · ${esc(learned)} · tokens are an estimate, calls are exact`;
        renderMemberSections(e);
        if (e.outline) { outlineCache = e.outline; renderOutline(); }
      } catch (err) {
        if (seq === estimateSeq) $("estimate").textContent = `No estimate: ${err.message}`;
      }
    }, 250);
  }

  async function goBack() {
    if (!session) return newTopic();
    if (!$("screen-home").hidden) return;                 // nothing behind the home screen
    try {
      session = await api("POST", `/api/sessions/${session.id}/back`);
      render();
    } catch (err) {
      // Nothing to return to on the server: the home screen with the question kept,
      // the topic still reachable with Forward.
      $("question").value = session.question;
      show("home"); renderNav(); pushHistory();
    }
  }

  async function goForward() {
    if (!session) return;
    if (!$("screen-home").hidden) { render(); return; }    // back into the open topic
    try { session = await api("POST", `/api/sessions/${session.id}/forward`); render(); }
    catch (err) { setError("home-error", err.message); }
  }

  async function runBoard() {
    const members = picked($("confirm-members"));
    if (!members.length) { showError("Tick at least one member to ask."); return; }
    const body = {
      topic: $("in-topic").value, context: $("in-context").value,
      options: lines($("in-options").value), constraints: lines($("in-constraints").value),
      members, mode: modeOf("run-mode"), budget: Number($("budget").value),
      extra: picks.extra, exclude: picks.exclude,
    };
    try { session = await api("POST", `/api/sessions/${session.id}/run`, body); render(); }
    catch (err) { showError(err.message); }
  }

  // One screen for "in session", "consolidating" and the result: the member
  // tiles stay in place and become clickable once their answer is in; the
  // "Board direction" box below turns into the recommendation.
  function memberStateLabel(name, state, answered, failed) {
    if (failed.has(name) || state === "failed") return "failed";
    const a = answered.get(name);
    if (a) {
      if (a.applies === false) return "not affected";
      return openMembers.has(name) ? "hide answer" : "read answer";
    }
    return state === "pending" ? "waiting" : state === "running" ? "thinking…" : state === "done" ? "answered" : "failed";
  }

  // A tile is clickable as soon as that member's answer is in (9 September
  // 2026: read the early answers while the others still think).
  function renderTiles(answered, failed) {
    $("member-grid").innerHTML = Object.entries(session.members).map(([name, state]) => {
      const a = answered.get(name);
      const clickable = !!a && !failed.has(name);
      const classes = ["member-tile", state,
        clickable ? "clickable" : "",
        openMembers.has(name) ? "active" : "",
        a && a.applies === false ? "na" : ""].filter(Boolean).join(" ");
      return `<button type="button" class="${classes}" data-member="${esc(name)}" style="color:${esc(meta(name).color)}" ${clickable ? "" : "disabled"}>
        ${avatar(name)}<span class="name">${esc(name)}</span><span class="state">${esc(memberStateLabel(name, state, answered, failed))}</span></button>`;
    }).join("");
    $("member-grid").querySelectorAll("button.clickable").forEach((tile) => tile.addEventListener("click", () => {
      const name = tile.dataset.member;
      if (openMembers.has(name)) openMembers.delete(name); else openMembers.add(name);
      renderTiles(answered, failed);
      renderMemberCards(answered);
    }));
  }

  function renderRunning() {
    $("screen-result").dataset.phase = session.phase;
    const synthesising = session.phase === "synthesising";
    const n = Object.keys(session.members).length;
    const count = Object.values(session.members).filter((s) => s === "done").length;
    $("result-topic").textContent = (session.inputs && session.inputs.topic) || session.question || "";
    const early = new Map(Object.entries(session.partial || {}));
    const combined = session.mode === "combined";
    $("board-state").textContent = combined
      ? `The board is in session, combined: one call writes the entries of ${n} member(s) and the direction together.`
      : synthesising
        ? "Every member has answered. One more call reads every answer and writes the board direction."
        : `The board is in session: ${n} member(s), each answering without seeing the others.${early.size ? " Answers already in can be read now." : ""}`;
    const failedNow = new Map(Object.entries(session.members).filter(([, s]) => s === "failed").map(([m]) => [m, m]));
    const key = `${session.phase}:${Object.values(session.members).join(",")}:${early.size}:${Array.from(openMembers).join(",")}`;
    if ($("member-grid").dataset.key !== key) {     // redraw only on change: keeps the open cards steady
      $("member-grid").dataset.key = key;
      renderTiles(early, failedNow);
      renderMemberCards(early);
    }
    setError("failed-members", "");
    const card = $("synthesis-card");
    card.className = `card synthesis direction-tile ${synthesising ? "running" : "pending"}`;
    card.querySelector(".avatar").innerHTML = `<svg viewBox="0 0 24 24">${SYNTHESIS_ICON}</svg>`;
    $("direction-state").textContent = combined
      ? "Thinking: one call writes every member's entry and the direction…"
      : synthesising
        ? "Thinking: reading all answers, weighing the disagreements, writing the recommendation…"
        : `Waits for every member to answer (${count} of ${n} so far)`;
    $("direction-spinner").hidden = !(synthesising || combined);
    if (combined) card.className = "card synthesis direction-tile running";
    $("synthesis-body").innerHTML = "";
    $("ask-back").hidden = true;
    show("result");
  }

  function renderSources(result) {
    const src = result.sources || {};
    const d = result.synthesis_data || {};
    const net = (src.network || []).length
      ? `<ul class="bullets">${src.network.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`
      : "<span class='muted'>no note was cited by any member</span>";
    const rests = (d.rests_on_judgement || []).length
      ? fmt(d.rests_on_judgement)
      : `<span class='muted'>${src.judgement_count ? `${src.judgement_count} statement(s) of the members' own judgement, none named as decisive` : "nothing"}</span>`;
    const bad = (src.unverified || []).length
      ? `<dt class="bad">Citations that failed the check</dt><dd><ul class="bullets sources">${src.unverified.map((u) => `<li class="bad"><span class="mark">!</span> ${esc(u)}</li>`).join("")}</ul></dd>`
      : "";
    return `<div class="sources-block"><dl>
      <dt>From the knowledge net</dt><dd>${net}</dd>
      <dt>Rests on judgement</dt><dd>${rests}</dd>
      ${bad}
    </dl></div>`;
  }

  function renderSynthesis(result) {
    const d = result.synthesis_data;
    if (!d) return `<p class="error">${esc(result.synthesis)}</p>${renderSources(result)}`;
    const list = (items) => (items && items.length) ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "<span class='muted'>none stated</span>";
    const notAffected = d.not_affected && d.not_affected.length ? `<dt>Not affected</dt><dd>${list(d.not_affected)}</dd>` : "";
    return `
      <div class="rec">${esc(d.overall_recommendation)}</div>
      <dl>
        <dt>Decisive criterion</dt><dd>${fmt(d.decisive_criterion)}</dd>
        <dt>Counter-arguments</dt><dd>${list(d.counter_arguments)}</dd>
        <dt>What would change it</dt><dd>${fmt(d.what_would_change_it)}</dd>
        ${notAffected}
      </dl>
      <div class="disagree"><dl><dt>Disagreements</dt><dd>${list(d.disagreements)}</dd></dl></div>
      ${renderSources(result)}`;
  }

  function renderTurn(t) {
    const who = t.members && t.members.length ? `<div class="members">Asked again${t.mode === "combined" ? " (combined, one call)" : ""}: ${t.members.map(esc).join(", ")}</div>` : "";
    if (t.pending) return `<div class="turn pending"><div class="q">${esc(t.question)}</div>${who}<div class="a">The board is thinking…</div></div>`;
    if (t.error) return `<div class="turn error"><div class="q">${esc(t.question)}</div>${who}<div class="a">${esc(t.answer)}</div></div>`;
    const d = t.data;
    let body;
    if (d) {
      body = `<div class="a">${fmt(d.answer)}</div><dl>
        <dt>Reasons</dt><dd>${fmt(d.reasons)}</dd>
        <dt>Recommendation now</dt><dd>${esc(d.recommendation_now || "")}</dd>
        <dt>Disagreements</dt><dd>${fmt(d.disagreements)}</dd></dl>`;
    } else {
      body = `<div class="a">${fmt(t.answer)}</div>`;
    }
    const answers = (t.assessments || []).map((a) => `
      <div class="turn-member" style="border-left-color:${esc(meta(a.member).color)}">
        <div class="who">${esc(a.member)}${a.applies === false ? ' <span class="na-note">· not affected</span>' : ""}</div>
        ${memberBody(a)}
      </div>`).join("");
    const failed = (t.failed_members || []).length ? `<p class="error small">Failed: ${t.failed_members.map(esc).join(" · ")}</p>` : "";
    const details = answers ? `<details class="turn-members"><summary class="muted small">What each member said</summary>${answers}</details>` : "";
    return `<div class="turn"><div class="q">${esc(t.question)}</div>${who}${body}${details}${failed}</div>`;
  }

  function renderResult() {
    const r = session.result;
    const answered = new Map(r.assessments.map((a) => [a.member, a]));
    const failed = new Map(r.failed_members.map((f) => [f.split(":")[0], f]));
    const alreadyShown = !$("screen-result").hidden && $("screen-result").dataset.phase === "result";
    if (!alreadyShown) {
      $("screen-result").dataset.phase = "result";
      $("member-grid").dataset.key = "";
      $("result-topic").textContent = r.topic;
      $("board-state").textContent = `${r.assessments.length} member(s) answered. Click a member to read its answer.`;
      renderTiles(answered, failed);
      renderMemberCards(answered);
      setError("failed-members", r.failed_members.length ? `Failed member(s): ${r.failed_members.join(" · ")}` : "");
      const card = $("synthesis-card");
      card.className = "card synthesis direction-tile done";
      card.querySelector(".avatar").innerHTML = `<svg viewBox="0 0 24 24">${SYNTHESIS_ICON}</svg>`;
      $("direction-state").textContent = r.mode === "combined"
        ? `Combined answer: ${r.assessments.length} member entries and the direction from one call`
        : `Synthesis of ${r.assessments.length} independent assessment(s)`;
      $("board-state").innerHTML = `${r.assessments.length} member(s) answered. Click a member to read its answer.` +
        (r.mode === "combined" ? ` <span class="mode-mark" title="One call wrote every entry; the entries can lean towards each other.">combined</span>` : "");
      $("direction-spinner").hidden = true;
      $("synthesis-body").innerHTML = renderSynthesis(r);
      const board = (session.roles && session.roles.members && session.roles.members.length) ? session.roles.members : Object.keys(session.members);
      renderPicks($("followup-members"), board.filter((n) => !failed.has(n)), new Set());
      $("followup-members").querySelectorAll("label").forEach((label) => {
        const name = label.querySelector("input").value;
        if (!(name in session.members)) label.insertAdjacentHTML("beforeend", ' <span class="muted small">not asked yet</span>');
      });
      $("ask-back").hidden = false;
    }
    const last = session.turns[session.turns.length - 1];
    const turnsKey = `${session.turns.length}:${last ? `${last.pending}:${(last.answer || "").length}:${(last.assessments || []).length}` : ""}`;
    if ($("turns").dataset.key !== turnsKey) {     // redraw only on change: keeps <details> open while polling
      $("turns").innerHTML = session.turns.map(renderTurn).join("");
      $("turns").dataset.key = turnsKey;
    }
    $("btn-followup").disabled = session.busy;
    $("btn-close").disabled = session.busy;
    const again = picked($("followup-members")).length;
    $("followup-mode-row").hidden = again === 0;
    const combinedFollow = modeOf("followup-mode") === "combined";
    $("result-hint").textContent = `${session.llm_calls} model call(s) so far · this follow-up costs ${again ? (combinedFollow ? "one (combined)" : `${again + 1} (${again} member(s) asked again, plus one)`) : "one"}`;
    setError("result-error", session.error || "");
    show("result");
  }

  function renderMemberCards(answered) {
    $("member-cards").innerHTML = Object.keys(session.members).filter((n) => openMembers.has(n) && answered.has(n)).map((name) => {
      const a = answered.get(name);
      return `<div class="card member-card" style="border-left-color:${esc(meta(name).color)}">
        <div class="card-head">${avatar(name)}<div><div class="card-title">${esc(name)}</div><div class="muted small">${esc(meta(name).title)}${meta(name).level ? ` · level ${esc(meta(name).level)}` : ""}${(meta(name).roles || []).length > 1 ? ` · ${esc(meta(name).roles.length)} roles` : ""}</div></div></div>
        ${memberBody(a)}
      </div>`;
    }).join("");
  }

  async function followUp(event) {
    event.preventDefault();
    const question = $("followup").value.trim();
    if (!question) return;
    const members = picked($("followup-members"));
    try {
      session = await api("POST", `/api/sessions/${session.id}/follow-up`, { question, members, mode: modeOf("followup-mode") });
      $("followup").value = "";
      saveDraft({ followup: "" });
      $("followup-members").querySelectorAll("input").forEach((box) => { box.checked = false; box.closest("label").classList.add("off"); });
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

  // -- statistics: tokens and time per step (9 September 2026) ----------------

  function fmtNum(n) { return n == null ? "–" : Number(n).toLocaleString("en-GB"); }
  function fmtSec(s) { return s == null ? "–" : s < 60 ? `${Number(s).toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`; }

  function wallTimes(marks) {
    // Wall time per phase, from the phase marks: each mark lasts until the next.
    const rows = [];
    const names = { running: "board members", synthesising: "consolidation", "follow-up": "follow-up", proposing: "memory proposal" };
    let followUps = 0;
    for (let i = 0; i < marks.length - 1; i++) {
      const name = marks[i].phase;
      if (!names[name]) continue;   // the rest is time waiting for Alex, not for the model
      const seconds = marks[i + 1].at - marks[i].at;
      if (seconds < 0.05) continue;  // the combined mode has no separate consolidation
      rows.push({ phase: name === "follow-up" ? `follow-up ${++followUps}` : names[name], seconds });
    }
    // The clarifier's own time: from the start (or an answer) to the questions.
    const clar = [];
    for (let i = 0; i < marks.length - 1; i++) {
      if (["started", "questions"].includes(marks[i].phase) && ["questions", "confirm"].includes(marks[i + 1].phase) && marks[i + 1].at - marks[i].at < 3600) {
        clar.push(marks[i + 1].at - marks[i].at);
      }
    }
    return { rows, clarifier: clar.reduce((a, b) => a + b, 0) };
  }

  function openStats() {
    const dialog = $("stats-dialog");
    const stats = session && session.stats;
    if (!stats || !stats.calls.length) {
      $("stats-summary").textContent = session ? "No model call yet for this topic." : "Start a topic; the statistics fill in as the board works.";
      $("stats-body").innerHTML = "";
      dialog.hidden = false;
      return;
    }
    const calls = stats.calls;
    const sum = (key) => calls.reduce((a, c) => a + (c[key] || 0), 0);
    const groups = new Map();
    calls.forEach((c) => {
      const key = c.step;
      const g = groups.get(key) || { step: key, calls: 0, input: 0, output: 0, seconds: 0, members: [] };
      g.calls += 1; g.input += c.input_tokens || 0; g.output += c.output_tokens || 0; g.seconds += c.seconds || 0;
      if (c.member) g.members.push(c);
      groups.set(key, g);
    });
    const wall = wallTimes(stats.marks || []);
    const last = stats.marks && stats.marks.length ? stats.marks[stats.marks.length - 1].at : Date.now() / 1000;
    const total = last - stats.started;
    $("stats-summary").textContent = `${calls.length} model call(s) · ${fmtNum(sum("input_tokens"))} tokens in · ${fmtNum(sum("output_tokens"))} tokens out · ${fmtSec(sum("seconds"))} of model time · ${fmtSec(total)} from the question to now`;
    const rows = [];
    groups.forEach((g) => {
      rows.push(`<tr><td>${esc(g.step)}</td><td class="num">${g.calls}</td><td class="num">${fmtNum(g.input)}</td><td class="num">${fmtNum(g.output)}</td><td class="num">${fmtSec(g.seconds)}</td></tr>`);
      g.members.forEach((c) => {
        rows.push(`<tr class="group"><td>&nbsp;&nbsp;${esc(c.member)}${c.error ? ` <span class="err">failed: ${esc(c.error)}</span>` : ""}</td><td class="num">1</td><td class="num">${fmtNum(c.input_tokens)}</td><td class="num">${fmtNum(c.output_tokens)}</td><td class="num">${fmtSec(c.seconds)}</td></tr>`);
      });
    });
    rows.push(`<tr class="total"><td>Total</td><td class="num">${calls.length}</td><td class="num">${fmtNum(sum("input_tokens"))}</td><td class="num">${fmtNum(sum("output_tokens"))}</td><td class="num">${fmtSec(sum("seconds"))}</td></tr>`);
    const wallRows = wall.rows.map((r) => `<tr><td>${esc(r.phase)}</td><td class="num">${fmtSec(r.seconds)}</td></tr>`).join("");
    $("stats-body").innerHTML = `
      <table><thead><tr><th>Step</th><th class="num">Calls</th><th class="num">Tokens in</th><th class="num">Tokens out</th><th class="num">Model time</th></tr></thead><tbody>${rows.join("")}</tbody></table>
      <p class="eyebrow">Wall time <span class="muted small">members run in parallel, so a step is shorter than its calls added up</span></p>
      <table><thead><tr><th>Phase</th><th class="num">Duration</th></tr></thead><tbody>
        <tr><td>clarifier (all rounds)</td><td class="num">${fmtSec(wall.clarifier)}</td></tr>${wallRows}
        <tr class="total"><td>From the question to now</td><td class="num">${fmtSec(total)}</td></tr></tbody></table>
      <p class="muted small">A retried empty run counts once, with the retry's tokens. Time you spent answering questions is not counted as model time.</p>`;
    dialog.hidden = false;
  }

  function renderDone() {
    forgetSession();
    if (session.phase === "written") {
      $("done-title").textContent = "Written to your vault";
      $("done-detail").textContent = session.written_path;
    } else {
      $("done-title").textContent = "Topic closed";
      $("done-detail").textContent = `Nothing was written. ${session.llm_calls} model call(s) in total.`;
    }
    show("done");
  }

  function newTopic(keepQuestion) {
    stopPolling();
    const question = session ? session.question : (store.get("question") || "");
    if (session && (["clarifying", "running", "synthesising", "proposing"].includes(session.phase) || session.busy)) {
      api("POST", `/api/sessions/${session.id}/abandon`).catch(() => {});   // stop the running calls
    }
    forgetSession();
    session = null;
    openMembers.clear();
    $("question").value = keepQuestion ? question : "";
    store.set("question", $("question").value);
    $("followup").value = "";
    show("home");
    $("question").focus();
  }

  async function resumeSession() {
    // A reload or a closed tab must not lose the topic: the server still
    // holds the session, the browser remembers which one.
    const id = store.get("session");
    if (!id) return false;
    try {
      const data = await api("GET", `/api/sessions/${id}`);
      if (["closed", "written"].includes(data.phase)) { forgetSession(); return false; }
      session = data;
      render();
      if (session.phase === "result") $("followup").value = draft().followup || "";
      return true;
    } catch (e) {
      store.del("session");
      return false;
    }
  }

  // -- wiring -------------------------------------------------------------

  $("greeting").textContent = greeting();
  $("ask-form").addEventListener("submit", ask);
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask(e); });
  $("btn-answers").addEventListener("click", () => submitAnswers(false));
  $("btn-answers-final").addEventListener("click", () => submitAnswers(true));
  $("followup-members").addEventListener("change", () => { if (session && session.phase === "result") renderResult(); });
  $("followup-mode").addEventListener("change", () => { if (session && session.phase === "result") renderResult(); });
  $("btn-mode-info").addEventListener("click", () => { $("mode-info").hidden = !$("mode-info").hidden; });
  $("btn-followup-mode-info").addEventListener("click", () => { $("mode-info").hidden = false; $("mode-info").scrollIntoView({ block: "center" }); });
  $("btn-stats").addEventListener("click", openStats);
  $("btn-stats-close").addEventListener("click", () => { $("stats-dialog").hidden = true; });
  $("btn-back-home").addEventListener("click", () => newTopic(false));
  $("btn-run").addEventListener("click", runBoard);
  $("btn-back-questions").addEventListener("click", goBack);
  $("followup-form").addEventListener("submit", followUp);
  $("followup").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) followUp(e); });
  $("btn-close").addEventListener("click", () => { setError("close-error", ""); $("close-dialog").hidden = false; });
  $("btn-close-cancel").addEventListener("click", () => { $("close-dialog").hidden = true; });
  $("btn-close-no").addEventListener("click", () => closeTopic(false));
  $("btn-close-yes").addEventListener("click", () => closeTopic(true));
  $("btn-write").addEventListener("click", writeMemory);
  $("btn-discard").addEventListener("click", discardMemory);
  $("btn-new").addEventListener("click", () => newTopic(false));
  $("btn-error-home").addEventListener("click", () => newTopic(true));
  $("btn-brand").addEventListener("click", () => newTopic(false));
  $("btn-projects").addEventListener("click", () => toggleProjectMenu());
  document.addEventListener("click", (e) => { if (!$("project-picker").contains(e.target)) toggleProjectMenu(false); });
  $("btn-projects-default").addEventListener("click", async () => {
    try { config = await api("POST", "/api/config", { project: (chosenProjects || []).join(", ") }); renderProjectPicker(); toggleProjectMenu(false); }
    catch (err) { setError("home-error", err.message); }
  });
  $("outline-filter").addEventListener("input", renderOutline);
  $("btn-nav-back").addEventListener("click", goBack);
  $("btn-nav-forward").addEventListener("click", goForward);
  window.addEventListener("popstate", () => {
    // The browser's own Back: one step back in the topic, not out of the page.
    if (session && $("screen-home").hidden) goBack(); else if (session) goForward();
  });
  $("btn-error-back").addEventListener("click", goBack);
  $("question").addEventListener("input", () => store.set("question", $("question").value));
  $("followup").addEventListener("input", () => saveDraft({ followup: $("followup").value }));
  $("btn-error-options").addEventListener("click", openOptions);
  $("btn-options").addEventListener("click", openOptions);
  $("btn-options-cancel").addEventListener("click", () => { $("options-dialog").hidden = true; });
  $("options-form").addEventListener("submit", saveOptions);
  $("btn-browse").addEventListener("click", browse);
  $("btn-browse-occonfig").addEventListener("click", browseConfigFile);
  $("btn-install-roles").addEventListener("click", installRoles);
  $("btn-browse-roles").addEventListener("click", browseRoles);
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true; }));

  $("question").value = store.get("question") || "";
  loadConfig()
    .then(() => resumeSession())
    .then((resumed) => { if (!resumed) show("home"); })
    .catch((err) => { $("home-hint").textContent = err.message; show("home"); });
})();
