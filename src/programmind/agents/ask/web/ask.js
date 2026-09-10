/* Program Mind - the Ask the vault agent (spec section 10). Loaded after
   shell.js; registers itself with the shell and owns the thread screens. */
(function () {
  "use strict";
  const PM = window.PM;
  const { $, esc, api, store, show, setError, fmtNum, md, fmtDate } = PM;

  let thread = null;                 // the open thread's snapshot

  let threadPollTimer = null;

  let askPicks = { extra: [], exclude: [] };

  let askOutlineCache = null;

  let askEstimateTimer = null;

  let askEstimateSeq = 0;

  function stopThreadPolling() { if (threadPollTimer) { clearInterval(threadPollTimer); threadPollTimer = null; } }

  function startThreadPolling() {
    stopThreadPolling();
    threadPollTimer = setInterval(async () => {
      if (!thread || PM.route() !== "thread") return stopThreadPolling();
      try { thread = await api("GET", `/api/ask/${thread.id}`); renderThread(); }
      catch (err) { stopThreadPolling(); setError("thread-error", err.message); }
    }, 1000);
  }
  // A small Markdown: paragraphs, bullet and numbered lists, bold, code. Escaped first.

  function obsidianLink(path) {
    const file = path.replace(/\.md$/i, "");
    return `obsidian://open?vault=${encodeURIComponent(PM.config.vault_name || "")}&file=${encodeURIComponent(file)}`;
  }

  async function loadThreads() {
    const box = $("thread-list");
    try {
      const data = await api("GET", "/api/ask");
      // Only open threads (spec 11.1, decision 10): a closed one moves to
      // the archive by itself and stays readable there.
      const rows = (data.threads || []).filter((r) => r.status !== "closed");
      box.innerHTML = rows.length ? rows.map((r) => `
        <div class="thread-row ask-row" data-id="${esc(r.id)}" title="Open this thread">
          <button type="button" class="thread-open">${esc(r.title)}</button>
          <span class="thread-meta">${r.questions} question(s) · ${esc(fmtDate(r.updated))}${r.projects && r.projects.length ? ` · ${esc(r.projects.join(", "))}` : ""}</span>
          <button type="button" class="ghost small-btn thread-delete" title="Delete this thread">Delete</button>
        </div>`).join("") : "<p class='muted small'>No open thread. Ask the first question above; closed threads are in the archive.</p>";
      PM.openOnRowClick(box, (row) => PM.navigate(`/ask/${row.dataset.id}`));
      box.querySelectorAll(".thread-delete").forEach((b) => b.addEventListener("click", async () => {
        const id = b.closest(".thread-row").dataset.id;
        if (!window.confirm("Delete this thread? Its questions and answers are removed; a note written to the vault stays.")) return;
        try { await api("DELETE", `/api/ask/${id}`); loadThreads(); } catch (err) { setError("ask-error", err.message); }
      }));
    } catch (err) { setError("ask-error", err.message); }
  }

  async function newThread(event) {
    event.preventDefault();
    const question = $("ask-question").value.trim();
    if (!question) return;
    setError("ask-error", "");
    $("btn-ask-new").disabled = true;
    try {
      const created = await api("POST", "/api/ask", { projects: PM.projects(), budget: PM.config.ask_budget });
      thread = await api("POST", `/api/ask/${created.id}/question`, { question });
      store.del("ask-question");
      $("ask-question").value = "";
      PM.setPath(`/ask/${thread.id}`);
      askPicks = { extra: [], exclude: [] }; askOutlineCache = null;
      renderThread();
    } catch (err) { setError("ask-error", err.message); }
    $("btn-ask-new").disabled = false;
  }

  async function openThread(id) {
    try {
      thread = await api("GET", `/api/ask/${id}`);
      askPicks = { extra: thread.extra || [], exclude: thread.exclude || [] }; askOutlineCache = null;
      $("thread-question").value = store.get(`thread-draft:${id}`) || "";
      $("ask-budget").value = thread.budget != null ? thread.budget : PM.config.ask_budget;
      renderThread();
    } catch (err) { setError("ask-error", err.message); PM.navigate("/ask"); }
  }

  function renderTurnCard(t) {
    const sources = (t.sources || []).length
      ? `<ul>${t.sources.map((s) => `<li><a href="${obsidianLink(s.path)}" title="Open in Obsidian">${esc(s.path)}</a>${s.heading ? ` · ${esc(s.heading)}` : ""}${s.brief ? ' <span class="brief-tag" title="Only its one-line summary was sent">summary only</span>' : ""}${s.why ? ` <span class="why">${esc(s.why)}</span>` : ""}</li>`).join("")}</ul>`
      : "<span class='muted'>no page named</span>";
    const dropped = t.dropped ? `<p class="muted small">${t.dropped} source(s) the model named were not among the pages sent and were dropped.</p>` : "";
    const gaps = (t.gaps || []).length ? `<dt>Not in the vault</dt><dd class="gaps"><ul>${t.gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul></dd>` : "";
    const hint = t.decision_question ? `<p class="hint-board muted">This reads like a decision. <a href="/board" class="to-board">Put it to the Board</a> for an assessment by every swim lane.</p>` : "";
    const parse = t.parse_error ? `<p class="error small">${esc(t.parse_error)}; the text is shown as it came.</p>` : "";
    const fresh = (t.new_pages || []).length
      ? `<p class="fresh-pages small">Read from the vault for this question: ${t.new_pages.map(esc).join(" · ")}</p>` : "";
    return `<div class="turn answer"><div class="q">${esc(t.question)}</div><div class="a">${md(t.answer)}</div>${parse}
      <dl class="sources"><dt>Sources</dt><dd>${sources}</dd>${gaps}</dl>${fresh}${dropped}${hint}</div>`;
  }

  function renderThread() {
    if (!thread) return;
    const closed = thread.status === "closed";
    if (thread.phase === "proposing") { show("proposing"); startThreadPolling(); return; }
    if (thread.phase === "proposal" && thread.proposal) { stopThreadPolling(); PM.showProposal(thread.proposal, PM.config.vault_path || "", memoryHandlers); return; }
    $("thread-title").textContent = thread.title || "New thread";
    const calls = (thread.stats && thread.stats.calls || []).length;
    $("thread-state").textContent = `${(thread.turns || []).length} question(s) · ${calls} model call(s)` + (closed ? " · closed" : " · open until you close it");
    const key = `${thread.id}:${(thread.turns || []).length}:${thread.busy}:${thread.status}:${thread.phase}`;
    if ($("thread-turns").dataset.key !== key) {
      $("thread-turns").dataset.key = key;
      $("thread-turns").innerHTML = (thread.turns || []).map(renderTurnCard).join("");
      $("thread-turns").querySelectorAll("a.to-board").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); PM.navigate("/board"); }));
    }
    $("thread-pending").hidden = !thread.busy;
    if (thread.busy) $("thread-pending-text").textContent = `Reading the vault and answering: ${thread.pending_question || ""}`;
    $("thread-form").hidden = closed || thread.busy;
    $("thread-closed").hidden = !closed || thread.busy;
    if (closed) {
      $("thread-closed-text").textContent = thread.written_path
        ? `This thread is closed. A note was written to your vault: ${thread.written_path}`
        : "This thread is closed. It stays here to read; it takes no further questions.";
    }
    setError("thread-error", thread.error || "");
    show("thread");
    if (thread.busy) startThreadPolling(); else { stopThreadPolling(); if (!closed) requestAskEstimate(); }
  }

  async function askInThread(event) {
    event.preventDefault();
    const question = $("thread-question").value.trim();
    if (!question || !thread) return;
    setError("thread-error", "");
    try {
      thread = await api("POST", `/api/ask/${thread.id}/question`, { question, budget: Number($("ask-budget").value), extra: askPicks.extra, exclude: askPicks.exclude });
      $("thread-question").value = "";
      store.del(`thread-draft:${thread.id}`);
      renderThread();
    } catch (err) { setError("thread-error", err.message); }
  }

  async function stopThread() {
    if (!thread) return;
    try { thread = await api("POST", `/api/ask/${thread.id}/stop`); renderThread(); } catch (err) { setError("thread-error", err.message); }
  }

  async function closeThread(remember) {
    if (!thread) return;
    try {
      thread = await api("POST", `/api/ask/${thread.id}/close`, { remember });
      $("thread-close-dialog").hidden = true;
      renderThread();
    } catch (err) { setError("thread-close-error", err.message); }
  }

  function renderAskSections(e) {
    const row = (s) => `
      <li><input type="checkbox" data-id="${esc(s.id)}" ${askPicks.exclude.includes(s.id) ? "" : "checked"} title="Untick to leave this out"> ${esc(s.path)}${s.heading ? ` · ${esc(s.heading)}` : ""}${s.core ? ' <span class="core-tag" title="The shared core">core</span>' : ""}${s.forced ? ' <span class="forced">your pick</span>' : ""}<span class="tok">${fmtNum(s.tokens)}</span></li>`;
    const briefRow = (b) => `
      <li><input type="checkbox" data-id="${esc(b.path)}" ${askPicks.exclude.includes(b.path) ? "" : "checked"} title="Untick to leave this page out"> ${esc(b.path)} <span class="brief-tag" title="One line, not the page">summary</span>${b.summary ? `<span class="reason">${esc(b.summary)}</span>` : ""}</li>`;
    const full = (e.sections || []).length ? `<p class="tier">In full <span class="muted small">${fmtNum(e.knowledge_tokens)} tokens · KPI notes ${fmtNum(e.kpi_tokens)} tokens</span></p><ul class="sec-list">${e.sections.map(row).join("")}</ul>` : "<span class='muted'>nothing from the vault</span>";
    const brief = (e.briefs || []).length ? `<p class="tier">As one line each</p><ul class="sec-list">${e.briefs.map(briefRow).join("")}</ul>` : "";
    $("ask-estimate-notes").innerHTML = full + brief;
    $("ask-estimate-notes").querySelectorAll("input").forEach((box) => box.addEventListener("change", () => {
      const id = box.dataset.id;
      askPicks.exclude = askPicks.exclude.filter((x) => x !== id);
      if (!box.checked) { askPicks.exclude.push(id); askPicks.extra = askPicks.extra.filter((x) => x !== id); }
      requestAskEstimate();
    }));
  }

  function renderAskOutline() {
    const filter = ($("ask-outline-filter").value || "").toLowerCase();
    const rows = (askOutlineCache || []).map((note) => {
      const secs = note.sections.filter((s) => !filter || note.path.toLowerCase().includes(filter) || (s.heading || "").toLowerCase().includes(filter));
      if (!secs.length) return "";
      return `<div class="note"><div class="note-title">${esc(note.path)}</div>${secs.map((s) => `
        <label><input type="checkbox" data-id="${esc(s.id)}" ${askPicks.extra.includes(s.id) ? "checked" : ""}> ${esc(s.heading || "(whole note)")}<span class="tok">${fmtNum(s.tokens)}</span></label>`).join("")}</div>`;
    }).join("");
    $("ask-outline").innerHTML = rows || "<span class='muted small'>Nothing matches.</span>";
    $("ask-outline").querySelectorAll("input").forEach((box) => box.addEventListener("change", () => {
      const id = box.dataset.id;
      askPicks.extra = askPicks.extra.filter((x) => x !== id);
      askPicks.exclude = askPicks.exclude.filter((x) => x !== id);
      if (box.checked) askPicks.extra.push(id);
      requestAskEstimate();
    }));
  }

  function requestAskEstimate() {
    if (askEstimateTimer) clearTimeout(askEstimateTimer);
    askEstimateTimer = setTimeout(async () => {
      if (!thread || PM.route() !== "thread" || $("thread-form").hidden) return;
      const seq = ++askEstimateSeq;
      $("ask-budget-value").textContent = `${fmtNum(Number($("ask-budget").value))} tokens`;
      try {
        const e = await api("POST", `/api/ask/${thread.id}/estimate`, {
          question: $("thread-question").value, budget: Number($("ask-budget").value),
          extra: askPicks.extra, exclude: askPicks.exclude, outline: !askOutlineCache,
        });
        if (seq !== askEstimateSeq) return;
        const learned = e.overhead_learned_from ? `overhead of ${fmtNum(e.overhead_per_call)} learned from ${e.overhead_learned_from} real call(s) of this thread` : `overhead of ${fmtNum(e.overhead_per_call)} assumed until the first real call`;
        const forced = e.forced_tokens ? ` · <strong>${fmtNum(e.forced_tokens)} tokens</strong> from your picks on top of the slider` : "";
        $("ask-estimate").innerHTML = `<strong>1 model call</strong>, about <strong>${fmtNum(e.tokens_in)} tokens in</strong>${forced} · ${esc(learned)} · tokens are an estimate, the call count is exact`;
        renderAskSections(e);
        if (e.outline) { askOutlineCache = e.outline; renderAskOutline(); }
      } catch (err) {
        if (seq === askEstimateSeq) $("ask-estimate").textContent = `No estimate: ${err.message}`;
      }
    }, 250);
  }


  $("ask-new-form").addEventListener("submit", newThread);

  $("ask-question").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) newThread(e); });

  $("ask-question").addEventListener("input", () => store.set("ask-question", $("ask-question").value));

  $("thread-form").addEventListener("submit", askInThread);

  $("thread-question").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) askInThread(e); });

  $("thread-question").addEventListener("input", () => { if (thread) store.set(`thread-draft:${thread.id}`, $("thread-question").value); requestAskEstimate(); });

  $("ask-budget").addEventListener("input", requestAskEstimate);

  $("btn-ask-budget-info").addEventListener("click", () => { $("ask-budget-info").hidden = !$("ask-budget-info").hidden; });

  $("ask-outline-filter").addEventListener("input", renderAskOutline);

  $("btn-thread-stop").addEventListener("click", stopThread);

  $("btn-thread-close").addEventListener("click", () => { setError("thread-close-error", ""); $("thread-close-dialog").hidden = false; });

  $("btn-thread-close-cancel").addEventListener("click", () => { $("thread-close-dialog").hidden = true; });

  $("btn-thread-close-no").addEventListener("click", () => closeThread(false));

  $("btn-thread-close-yes").addEventListener("click", () => closeThread(true));

  $("ask-question").value = store.get("ask-question") || "";

  const memoryHandlers = {
    write: async (body) => { thread = await api("POST", `/api/ask/${thread.id}/memory`, body); renderThread(); },
    discard: async () => { thread = await api("POST", `/api/ask/${thread.id}/discard-memory`); renderThread(); },
  };

  PM.register({
    id: "ask",
    match: (pathname) => (pathname === "/ask" ? "ask" : pathname.startsWith("/ask/") ? "thread" : null),
    render: (route) => { if (route === "ask") { show("ask"); loadThreads(); } else openThread(location.pathname.split("/")[2]); },
    onLeave: stopThreadPolling,
    boot: () => { $("ask-question").value = store.get("ask-question") || ""; },
    stats: () => thread,
  });
})();
