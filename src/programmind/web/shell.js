/* Program Mind - the shell. Vanilla JS, no build step. Owns the top bar, the
   options, the statistics and proposal dialogs, the project picker, the
   routes and the start page; every agent registers itself with PM and owns
   its own screens (restructuring of 10 September 2026, spec section 11). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let config = null;

  const store = {
    get(key) { try { const v = localStorage.getItem(`programmind:${key}`); return v ? JSON.parse(v) : null; } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(`programmind:${key}`, JSON.stringify(value)); } catch (e) { /* no storage */ } },
    del(key) { try { localStorage.removeItem(`programmind:${key}`); } catch (e) { /* no storage */ } },
  };

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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

  function renderKnowledgeChip() {
    const chip = $("knowledge-chip");
    const status = config.knowledge_status;
    chip.className = "chip";
    if (!status.configured) { chip.classList.add("none"); chip.textContent = "No knowledge source"; }
    else if (status.ok) { chip.classList.add("ok"); chip.textContent = `${status.notes} notes`; }
    else { chip.classList.add("bad"); chip.textContent = "Knowledge source not reachable"; }
    // The path is a tooltip, not a label (9 September 2026): hover to see it.
    chip.title = status.error || (config.vault_path ? `Knowledge source: ${config.vault_path}` : "Set a vault folder in Options");
    const problem = !config.model ? '<span class="error">No model configured. Open Options.</span>'
      : !status.configured ? "No knowledge source set. The board answers from your question alone."
      : !status.ok ? `<span class="error">${esc(status.error)}</span>` : "";
    $("home-hint").innerHTML = problem || `${status.notes} notes in the vault.`;
    $("board-hint").innerHTML = problem || `Reads up to ${fmtNum(config.token_budget)} tokens of notes from your vault per member.`;
    $("ask-hint").innerHTML = problem || `Reads up to ${fmtNum(config.ask_budget)} tokens of notes from your vault per question, one call.`;
    $("site-address").innerHTML = `This site: <strong>http://${esc(config.site_name || "ai")}.localhost:${esc(location.port || "80")}/</strong> · also reachable at http://localhost:${esc(location.port || "80")}/`;
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

  function projectLabel() {
    return !chosenProjects || chosenProjects.length === 0 ? "All projects" : chosenProjects.length === 1 ? chosenProjects[0] : `${chosenProjects.length} projects`;
  }

  function renderProjectChips() {
    ["board-project", "ask-project", "thread-project"].forEach((id) => { if ($(id)) $(id).textContent = projectLabel(); });
  }

  function renderProjectPicker() {
    const names = (config.projects || []).slice();
    // Chosen once on the start page, remembered in the browser, carried into every use case (spec 9.5).
    if (chosenProjects === null) chosenProjects = Array.isArray(store.get("projects")) ? store.get("projects") : defaultProjects();
    renderProjectChips();
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
      store.set("projects", chosenProjects);
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

  function fmtNum(n) { return n == null ? "–" : Number(n).toLocaleString("en-GB"); }

  function fmtSec(s) { return s == null ? "–" : s < 60 ? `${Number(s).toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`; }

  function md(text) {
    const blocks = String(text || "").replace(/\r/g, "").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
    return blocks.map((b) => {
      const rows = b.split("\n").map((r) => r.trim()).filter(Boolean);
      if (rows.every((r) => /^[-*•]\s+/.test(r))) return `<ul>${rows.map((r) => `<li>${inline(r.replace(/^[-*•]\s+/, ""))}</li>`).join("")}</ul>`;
      if (rows.every((r) => /^\d+[.)]\s+/.test(r))) return `<ol>${rows.map((r) => `<li>${inline(r.replace(/^\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`;
      if (rows.length === 1 && /^#{1,6}\s+/.test(rows[0])) return `<p><strong>${inline(rows[0].replace(/^#{1,6}\s+/, ""))}</strong></p>`;
      return `<p>${rows.map(inline).join("<br>")}</p>`;
    }).join("");
  }

  function fmtDate(seconds) {
    try { return new Date(seconds * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }); } catch (e) { return ""; }
  }

  $("greeting").textContent = greeting();

  $("btn-stats").addEventListener("click", openStats);

  $("btn-stats-close").addEventListener("click", () => { $("stats-dialog").hidden = true; });

  $("btn-write").addEventListener("click", writeMemory);

  $("btn-discard").addEventListener("click", discardMemory);

  $("btn-brand").addEventListener("click", () => navigate("/"));

  document.querySelectorAll("a.tile, a.change-project, #link-threads, #btn-thread-new").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); navigate(a.getAttribute("href")); }));

  $("btn-projects").addEventListener("click", () => toggleProjectMenu());

  document.addEventListener("click", (e) => { if (!$("project-picker").contains(e.target)) toggleProjectMenu(false); });

  $("btn-projects-default").addEventListener("click", async () => {
    try { config = await api("POST", "/api/config", { project: (chosenProjects || []).join(", ") }); renderProjectPicker(); toggleProjectMenu(false); }
    catch (err) { setError("home-error", err.message); }
  });

  $("btn-error-options").addEventListener("click", openOptions);

  $("btn-options").addEventListener("click", openOptions);

  $("btn-options-cancel").addEventListener("click", () => { $("options-dialog").hidden = true; });

  $("options-form").addEventListener("submit", saveOptions);

  $("btn-browse").addEventListener("click", browse);

  $("btn-browse-occonfig").addEventListener("click", browseConfigFile);

  $("btn-install-roles").addEventListener("click", installRoles);

  $("btn-browse-roles").addEventListener("click", browseRoles);

  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true; }));

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

  // -- the shared proposal screen (the memory step of any agent) --------------

  let memoryHandlers = null;
  function showProposal(p, vaultPath, handlers) {
    if (!$("screen-proposal").hidden) return;
    memoryHandlers = handlers;
    $("prop-vault").value = vaultPath || "";
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
    try { await memoryHandlers.write(body); } catch (err) { setError("proposal-error", err.message); }
  }
  async function discardMemory() {
    try { await memoryHandlers.discard(); } catch (err) { setError("proposal-error", err.message); }
  }

  // -- statistics: tokens and time per step, for whichever agent is open ------

  function openStats() {
    const dialog = $("stats-dialog");
    const agent = agentFor(currentRoute);
    const source = agent && agent.stats ? agent.stats() : null;
    const stats = source && source.stats;
    if (!stats || !stats.calls.length) {
      $("stats-summary").textContent = source ? "No model call yet." : "Start a topic or a thread; the statistics fill in as the model works.";
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
      ${agent && agent.statsExtra ? agent.statsExtra() : ""}
      <p class="muted small">A retried empty run counts once, with the retry's tokens. Time you spent answering questions is not counted as model time.</p>`;
    dialog.hidden = false;
  }

  // -- routes (spec 9.5): one page, the path decides the screen; agents own theirs --

  const agents = [];
  let currentRoute = "start";
  function register(agent) { agents.push(agent); }
  function routeOf(pathname) {
    for (const a of agents) { const r = a.match(pathname); if (r) return r; }
    return "start";
  }
  function agentFor(route) { return agents.find((a) => a.match(location.pathname) === route && route !== "start") || null; }
  function setPath(path) {
    currentRoute = routeOf(path);
    if (location.pathname !== path) { try { history.pushState({ route: currentRoute }, "", path); } catch (e) { /* not available */ } }
  }
  function navigate(path) { setPath(path); renderRoute(); }
  function renderRoute() {
    currentRoute = routeOf(location.pathname);
    const agent = agentFor(currentRoute);
    agents.forEach((a) => { if (a !== agent && a.onLeave) a.onLeave(); });
    renderProjectChips();
    if (!agent) { show("start"); return; }
    if (agent.onEnter) agent.onEnter();
    agent.render(currentRoute);
  }
  window.addEventListener("popstate", () => {
    // The browser's own Back: between the use cases by path; inside an
    // agent, whatever the agent makes of it (the board steps back).
    if (routeOf(location.pathname) !== currentRoute) { renderRoute(); return; }
    const agent = agentFor(currentRoute);
    if (agent && agent.onPopState) agent.onPopState();
  });

  window.PM = {
    $, esc, api, store, show, setError, fmt, fmtNum, fmtSec, lines, md, fmtDate,
    register, navigate, setPath, showProposal, openOptions, renderProjectPicker,
    route: () => currentRoute,
    projects: () => (chosenProjects || []),
    get config() { return config; },
    set config(value) { config = value; },
  };

  // Boot once every agent script has registered (they load after this one).
  document.addEventListener("DOMContentLoaded", () => {
    loadConfig()
      .then(() => Promise.all(agents.map((a) => (a.boot ? a.boot() : null))))
      .then(() => renderRoute())
      .catch((err) => { $("home-hint").textContent = err.message; show("start"); });
  });
})();
