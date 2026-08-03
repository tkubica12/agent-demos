const state = {
  runs: [],
  filter: "all",
  days: 30,
};

const el = (id) => document.getElementById(id);

function setStatus(message, kind) {
  const node = el("status");
  if (!message) {
    node.classList.add("hidden");
    return;
  }
  node.textContent = message;
  node.className = `status ${kind || ""}`.trim();
  node.classList.remove("hidden");
}

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString();
}

function latestVerdict(run) {
  const mine = (run.annotations || [])
    .slice()
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  return mine.length ? mine[0].label : null;
}

function matchesFilter(run) {
  const verdict = latestVerdict(run);
  if (state.filter === "all") return true;
  if (state.filter === "unrated") return verdict === null;
  if (state.filter === "bad") return verdict === "fail";
  if (state.filter === "good") return verdict === "pass";
  return true;
}

function renderExisting(run) {
  if (!run.annotations || !run.annotations.length) return "";
  const rows = run.annotations
    .map(
      (a) => `<div class="row">
        <span class="badge ${a.label === "pass" ? "pass" : "fail"}">${a.label === "pass" ? "Good answer" : "Needs work"}</span>
        <span>${escapeHtml(a.reviewer || a.source || "unknown")}</span>
        <span>${escapeHtml(formatTime(a.timestamp))}</span>
        <span>${escapeHtml(a.explanation || "")}</span>
      </div>`,
    )
    .join("");
  return `<div class="existing"><strong>Rating history</strong>${rows}</div>`;
}

function renderRuns() {
  const list = el("list");
  const visible = state.runs.filter(matchesFilter);
  const rated = state.runs.filter((r) => latestVerdict(r) !== null).length;
  const flagged = state.runs.filter((r) => latestVerdict(r) === "fail").length;
  el("counts").textContent =
    `${state.runs.length} answers · ${rated} rated · ${flagged} flagged`;

  if (!visible.length) {
    list.innerHTML = `<div class="empty">No answers match this filter.</div>`;
    return;
  }

  list.innerHTML = visible
    .map((run, index) => {
      const verdict = latestVerdict(run);
      const cls =
        verdict === "fail" ? "rated-bad" : verdict === "pass" ? "rated-good" : "";
      return `<article class="card ${cls}" data-index="${index}">
        <div class="meta">
          <span>${escapeHtml(formatTime(run.timestamp))}</span>
          <span>${escapeHtml(run.agentName || "agent")}${run.agentVersion ? ` v${escapeHtml(run.agentVersion)}` : ""}</span>
          ${run.model ? `<span>${escapeHtml(run.model)}</span>` : ""}
          <button class="link detail" data-trace="${escapeHtml(run.traceId)}">View full conversation</button>
        </div>
        <div class="qa">
          <div>
            <div class="label">Customer asked</div>
            <div class="text">${escapeHtml(run.question || "(no question captured)")}</div>
          </div>
          <div>
            <div class="label">Agent answered</div>
            <div class="text">${escapeHtml(run.answer || "(no answer captured)")}</div>
          </div>
        </div>
        <div class="actions">
          <button class="vote good ${verdict === "pass" ? "active" : ""}" data-vote="good">&#128077; Good answer</button>
          <button class="vote bad ${verdict === "fail" ? "active" : ""}" data-vote="bad">&#128078; Needs work</button>
          <textarea placeholder="Why? (optional, saved with your rating)"></textarea>
        </div>
        ${renderExisting(run)}
      </article>`;
    })
    .join("");

  list.querySelectorAll(".vote").forEach((button) => {
    button.addEventListener("click", async (event) => {
      const card = event.target.closest(".card");
      const run = visible[Number(card.dataset.index)];
      const explanation = card.querySelector("textarea").value;
      await submitAnnotation(run, event.target.dataset.vote === "good", explanation);
    });
  });

  list.querySelectorAll(".detail").forEach((button) => {
    button.addEventListener("click", (event) =>
      openDetail(event.target.dataset.trace),
    );
  });
}

async function submitAnnotation(run, passed, explanation) {
  setStatus("Saving your rating onto the trace…");
  try {
    const response = await fetch("/api/annotations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        traceId: run.traceId,
        spanId: run.spanId,
        passed,
        explanation,
        responseId: run.responseId,
        conversationId: run.conversationId,
        agentName: run.agentName,
        agentVersion: run.agentVersion,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Could not save the rating.");
    }
    run.annotations = [
      {
        label: passed ? "pass" : "fail",
        explanation,
        reviewer: payload.reviewer,
        source: "builder",
        timestamp: payload.timestamp || new Date().toISOString(),
      },
      ...(run.annotations || []),
    ];
    renderRuns();
    setStatus(
      "Saved onto the original trace. Application Insights takes about a minute " +
        "to index it, so Foundry and Refresh will show it shortly.",
      "ok",
    );
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function openDetail(traceId) {
  const drawer = el("drawer");
  const body = el("drawer-body");
  body.innerHTML = "<p>Loading…</p>";
  drawer.classList.remove("hidden");
  try {
    const response = await fetch(`/api/runs/${traceId}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not load the trace.");
    body.innerHTML = payload.steps
      .map((step) => {
        if (step.kind === "chat") {
          return `<div class="step">
            <h4>Answer · ${escapeHtml(formatTime(step.timestamp))}</h4>
            <pre>${escapeHtml(step.question || "")}</pre>
            <pre>${escapeHtml(step.answer || "")}</pre>
          </div>`;
        }
        return `<div class="step">
          <h4>Tool · ${escapeHtml(step.toolName || "")}</h4>
          <pre>${escapeHtml(step.toolArguments || "")}</pre>
          <pre>${escapeHtml((step.toolResult || "").slice(0, 4000))}</pre>
        </div>`;
      })
      .join("");
  } catch (error) {
    body.innerHTML = `<p class="status error">${escapeHtml(error.message)}</p>`;
  }
}

async function loadRuns() {
  setStatus("Loading agent answers…");
  try {
    const response = await fetch(`/api/runs?days=${state.days}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not load answers.");
    state.runs = payload.runs;
    setStatus("");
    renderRuns();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function init() {
  const me = await (await fetch("/api/me")).json();
  if (!me.signedIn) {
    el("signin").classList.remove("hidden");
    return;
  }
  el("account").innerHTML = `Signed in as <strong>${escapeHtml(me.name || me.username)}</strong>
    <br /><button class="secondary" id="logout">Sign out</button>`;
  el("logout").addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST" });
    location.reload();
  });
  el("workspace").classList.remove("hidden");

  el("days").addEventListener("change", (event) => {
    state.days = Number(event.target.value);
    loadRuns();
  });
  el("filter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    renderRuns();
  });
  el("refresh").addEventListener("click", loadRuns);
  el("export").addEventListener("click", () => {
    window.location.href = `/api/evaluation-set?days=${state.days}`;
  });
  el("drawer-close").addEventListener("click", () =>
    el("drawer").classList.add("hidden"),
  );

  await loadRuns();
}

init();
