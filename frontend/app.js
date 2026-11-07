// Envoy UI. Plain ES module: streams session events over SSE and renders the two desks, the corridor and the graphs.
const $ = (sel, el = document) => el.querySelector(sel);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
};
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
};
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

const STEPS = [["handshake", "Handshake"], ["discovery", "Discovery"], ["qa", "Questions"], ["proposal", "Proposal"], ["approval", "Approval"], ["booked", "Booked"]];
const ENDS = { declined: ["Declined", "end-neutral"], flagged: ["Blocked", "end-bad"], timed_out: ["Timed out", "end-neutral"], not_contacted: ["Not contacted", "end-neutral"], booked: ["Booked", "end-good"] };
const VERB = { introduce: "introduces", question: "asks", request_info: "asks", answer: "answers", propose: "proposes",
  counter_propose: "suggests another time", accept: "accepts", decline: "declines", end: "ends the session" };
const LEVEL = ["coordinate only", "propose with reasons", "make the case"];

const fmt = (iso) => {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};
const fmtTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const slotText = (s) => `${fmt(s.start)} to ${fmtTime(s.end)}`;
const firstName = (name) => (name || "").split(" ")[0];

const ui = { es: null, info: null, reached: new Set(), people: {}, scenarios: [], packs: {} };

/* ---------------- boot ---------------- */
async function boot() {
  const [health, scenarios, packs] = await Promise.all([api("/api/health"), api("/api/scenarios"), api("/api/packs")]);
  const mode = $("#mode");
  mode.textContent = health.mode === "live" ? "Live model" : "Offline demo";
  mode.classList.toggle("live", health.mode === "live");
  mode.title = health.mode === "live" ? `Claude writes and checks messages (${health.models.smart})` : "Runs without an API key: decisions in code, phrasing from templates";
  ui.scenarios = scenarios;
  packs.forEach((p) => (ui.packs[p.id] = p));
  const select = $("#scenario");
  for (const p of packs) {
    const group = h("optgroup", { label: p.title });
    scenarios.filter((s) => s.pack === p.id).forEach((s) => group.append(h("option", { value: s.id }, s.title)));
    if (group.children.length) select.append(group);
  }
  $("#run").addEventListener("click", start);
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  renderRoute(null);
  initGraphs();
}

function showView(view) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
  $("#view-negotiation").hidden = view !== "negotiation";
  $("#view-graphs").hidden = view !== "graphs";
  if (view === "graphs") loadPeople();
}

/* ---------------- session ---------------- */
async function start() {
  ui.es?.close();
  ui.reached = new Set();
  $("#approval").hidden = true;
  $("#letters").replaceChildren();
  for (const role of ["requester", "receiver"]) {
    const desk = $(`#desk-${role}`);
    desk.querySelector('[data-slot="log"]').replaceChildren();
    desk.querySelector('[data-slot="fit"]').replaceChildren(h("p", { class: "empty" }, role === "requester" ? "Checking public information..." : "Waiting for the first message."));
  }
  $('#desk-receiver [data-slot="spark"]').replaceChildren(h("p", { class: "empty" }, "Scored after each message."));
  const run = $("#run");
  run.disabled = true;
  try {
    const info = await post("/api/sessions", { scenario: $("#scenario").value });
    ui.info = info;
    setDesk("requester", info.requester);
    setDesk("receiver", info.receiver);
    const p = info.pack;
    $("#pack-line").textContent = `${p.title}. Persuasion level ${p.persuasion_level}: ${LEVEL[p.persuasion_level]}. Up to ${p.turn_limit} messages. Both people approve before anything is booked.`;
    renderRoute("handshake");
    ui.es = new EventSource(`/api/sessions/${info.id}/events`);
    ui.es.onmessage = (e) => handle(JSON.parse(e.data));
    ui.es.addEventListener("done", () => { ui.es.close(); run.disabled = false; });
    ui.es.onerror = () => { run.disabled = false; };
  } catch (err) {
    note(`Could not start: ${err.message}`);
    run.disabled = false;
  }
}

function setDesk(role, person) {
  const desk = $(`#desk-${role}`);
  desk.querySelector('[data-slot="name"]').textContent = person.name;
  desk.querySelector('[data-slot="headline"]').textContent = person.headline;
}

const nameOf = (role) => (role === "requester" ? ui.info.requester.name : ui.info.receiver.name);

function handle(ev) {
  switch (ev.type) {
    case "handshake": return note(ev.text);
    case "thinking": return log(ev.role, ev.step, ev.detail);
    case "fit": return renderFit(ev.role, ev.label, ev.fit);
    case "message": return letter(ev.message, ev.flags);
    case "checks": return stampChecks(ev);
    case "score": return renderSpark(ev.history);
    case "state": return onState(ev);
    case "approval": return showApproval(ev);
    case "approval_vote": return onVote(ev);
    case "booked": return onBooked(ev);
    case "reflection": return log("requester", "Memory", `Saved for next time: ${ev.text}`);
    case "error": return note(ev.text);
  }
}

function log(role, step, detail) {
  const list = $(`#desk-${role} [data-slot="log"]`);
  list.prepend(h("li", {}, h("b", {}, step), detail));
}

function note(text) {
  $("#letters").append(h("p", { class: "note" }, text));
}

/* route */
function renderRoute(state) {
  if (state) ui.reached.add(state);
  const route = $("#route");
  route.replaceChildren();
  const end = ENDS[state] && state !== "booked" ? state : null;
  const nowIdx = STEPS.findIndex(([id]) => id === state);
  STEPS.forEach(([id, label], i) => {
    if (end && !ui.reached.has(id)) return;
    let cls = "";
    if (state === "booked" && id === "booked") cls = "end-good";
    else if (id === state) cls = "now";
    else if (ui.reached.has(id)) cls = "done";
    else if (nowIdx >= 0 && i < nowIdx) cls = "skipped";
    route.append(h("li", { class: cls }, h("span", { class: "stop" }, h("span", { class: "pip" }), label)));
  });
  if (end) {
    const [label, cls] = ENDS[end];
    route.append(h("li", { class: cls }, h("span", { class: "stop" }, h("span", { class: "pip" }), label)));
  }
}

function onState(ev) {
  renderRoute(ev.state);
  if (ev.state === "flagged") log("receiver", "Broker", "The broker blocked the message before it reached this agent. Its human was told.");
  document.body.classList.toggle("awaiting-approval", ev.state === "approval");
  if (ENDS[ev.state] && ev.state !== "booked") {
    const tone = ev.state === "flagged" ? "bad" : "";
    const title = {
      declined: "No meeting this time.",
      flagged: "The broker stopped this session.",
      timed_out: "The agents ran out of turns.",
      not_contacted: "The agent decided not to reach out.",
    }[ev.state];
    $("#letters").append(h("div", { class: `outcome ${tone}` }, h("p", {}, title), h("p", {}, ev.reason || "")));
    $("#approval").hidden = true;
  }
}

/* letters */
function letter(msg, flags) {
  const role = msg.sender.role;
  const evidence = Object.fromEntries((msg.evidence || []).map((e) => [e.id, e]));
  const blocked = flags && flags.length;
  const el = h("article", { class: `letter from-${role} arrive${blocked ? " blocked" : ""}`, "data-turn": msg.turn },
    h("header", {},
      h("span", { class: "who" }, `${firstName(msg.sender.acting_for)}'s agent`),
      h("span", { class: "act" }, VERB[msg.act] || msg.act),
      h("span", { class: "turn" }, `Message ${msg.turn}`)),
    h("p", { class: "body" }, msg.text),
    msg.claims?.length ? h("ul", { class: "claims", "aria-label": "Claims" },
      msg.claims.map((c) => {
        const ev = c.evidence && evidence[c.evidence];
        return h("li", { class: `claim${ev ? " verified" : ""}`, title: ev ? `Evidence: ${ev.summary} (${ev.ref})` : "No evidence attached" }, c.statement);
      })) : null,
    msg.proposal ? renderProposal(msg.proposal) : null);
  $("#letters").append(el);
  const stamps = h("div", { class: "stamps", "data-turn": msg.turn });
  if (blocked) flags.forEach((f) => stamps.append(h("span", { class: "stamp bad" }, `Blocked: ${f}`)));
  else stamps.append(h("span", { class: "stamp" }, "Firewall clear"));
  $("#letters").append(stamps);
  el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
}

function renderProposal(p) {
  const f = p.fields || {};
  const rows = [];
  if (f.slot) rows.push(h("div", {}, h("strong", {}, "When: "), slotText(f.slot)));
  else if (f.windows?.length) rows.push(h("div", {}, h("strong", {}, "Free: "), f.windows.map(slotText).join("; ")));
  if (f.duration_min) rows.push(h("div", {}, h("strong", {}, "Length: "), `${f.duration_min} minutes`));
  if (f.activities?.length) rows.push(h("div", {}, h("strong", {}, "Ideas: "), f.activities.join(", ")));
  if (f.place) rows.push(h("div", {}, h("strong", {}, "Where: "), f.place));
  if (f.format) rows.push(h("div", {}, h("strong", {}, "Format: "), f.format));
  return h("div", { class: "proposal" }, rows);
}

function stampChecks(ev) {
  const stamps = $(`.stamps[data-turn="${ev.turn}"]`);
  if (!stamps) return;
  if (ev.total) stamps.append(h("span", { class: `stamp${ev.verified === ev.total ? " good" : ""}` }, `${ev.verified} of ${ev.total} claims with evidence`));
  (ev.problems || []).forEach((p) => stamps.append(h("span", { class: "stamp bad" }, p)));
}

/* desks */
function renderFit(role, label, fit) {
  const desk = $(`#desk-${role}`);
  desk.querySelector('[data-slot="fit-label"]').textContent = label;
  const icon = { match: "\u2713", miss: "\u2715", unknown: "?" };
  desk.querySelector('[data-slot="fit"]').replaceChildren(...[
    h("div", { class: "fit-score" }, h("strong", {}, `${Math.round(fit.score * 100)}%`),
      h("span", {}, `fit on the ${Math.round(fit.coverage * 100)}% of criteria known`)),
    h("ul", { class: "fit-rows" }, fit.rows.map((r) =>
      h("li", { class: r.status }, h("span", { class: "ico", "aria-label": r.status }, icon[r.status]),
        h("span", {}, r.label, h("small", {}, r.detail))))),
    fit.veto ? h("p", { class: "veto" }, fit.veto) : null].filter(Boolean));
}

function renderSpark(history) {
  const W = 260, H = 72, pad = 18;
  const x = (i) => pad + (history.length === 1 ? (W - 2 * pad) / 2 : (i * (W - 2 * pad)) / (history.length - 1));
  const y = (v) => H - 10 - ((v - 1) / 4) * (H - 24);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Interest by turn: ${history.join(", ")} out of 5`);
  const add = (tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text) e.textContent = text; svg.append(e); return e; };
  [1, 5].forEach((v) => { add("line", { class: "axis", x1: pad, x2: W - pad, y1: y(v), y2: y(v) }); add("text", { x: 0, y: y(v) + 4 }, String(v)); });
  if (history.length > 1) add("polyline", { class: "line", points: history.map((v, i) => `${x(i)},${y(v)}`).join(" ") });
  history.forEach((v, i) => add("circle", { class: "pt", cx: x(i), cy: y(v), r: 4 }));
  const last = history.at(-1);
  const lx = x(history.length - 1);
  add("text", { class: "last", x: lx > W - 40 ? lx - 10 : lx + 8, y: Math.max(y(last) - 8, 12), "text-anchor": lx > W - 40 ? "end" : "start" }, last.toFixed(1));
  const box = $('#desk-receiver [data-slot="spark"]');
  box.classList.add("spark");
  box.replaceChildren(svg);
}

/* approval */
function showApproval(ev) {
  const bar = $("#approval");
  bar.hidden = false;
  const slot = ev.proposal.fields.slot;
  $("#approval-title").textContent = slot ? `The agents agreed on ${slotText(slot)}.` : "The agents agreed.";
  $("#approval-sub").textContent = "Nothing is booked until both people approve.";
  const card = (role, summary) => {
    const name = nameOf(role);
    return h("div", { class: `approver ${role}`, "data-role": role },
      h("p", { class: "name" }, name), h("p", {}, summary),
      h("div", { class: "actions" },
        h("button", { class: "yes", onclick: () => vote(role, true) }, `Approve for ${firstName(name)}`),
        h("button", { onclick: () => vote(role, false) }, `Decline for ${firstName(name)}`)));
  };
  $("#approvers").replaceChildren(card("requester", ev.summary_for_requester), card("receiver", ev.summary_for_receiver));
}

async function vote(role, approve) {
  const actions = $(`.approver[data-role="${role}"] .actions`);
  actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try { await post(`/api/sessions/${ui.info.id}/approve`, { role, approve }); }
  catch (err) { actions.querySelectorAll("button").forEach((b) => (b.disabled = false)); note(err.message); }
}

function onVote(ev) {
  const actions = $(`.approver[data-role="${ev.role}"] .actions`);
  if (actions) actions.replaceChildren(h("span", { class: "voted" }, ev.approve ? `${firstName(ev.who)} approved` : `${firstName(ev.who)} declined`));
}

function onBooked(ev) {
  $("#approval").hidden = true;
  document.body.classList.remove("awaiting-approval");
  $("#letters").append(h("div", { class: "outcome good" },
    h("p", {}, ev.slot ? `Booked for ${slotText(ev.slot)}.` : "Booked."),
    h("p", {}, "Both people approved. ", h("a", { href: ev.ics, download: "" }, "Download the calendar invite"))));
}

/* ---------------- knowledge graphs ---------------- */
let graphState = { person: null, selected: null };

function initGraphs() {
  $("#ingest-interview").addEventListener("click", async () => {
    const text = $("#interview").value.trim();
    if (!graphState.person) return status("Pick a person first.");
    if (!text) return status("Paste some of what the person said first.");
    status("Extracting facts...");
    try {
      const r = await post(`/api/people/${graphState.person}/ingest/interview`, { transcript: text });
      status(r.added.length ? `Added ${r.added.length} facts: ${r.added.map((a) => a.label).join(", ")}.` : "No facts found. Try sentences like \"I work at...\" or \"I love...\".");
      $("#interview").value = "";
      openGraph(graphState.person);
    } catch (err) { status(`Could not extract facts: ${err.message}`); }
  });
  $("#ingest-github").addEventListener("click", async () => {
    const username = $("#gh").value.trim();
    if (!graphState.person) return status("Pick a person first.");
    if (!username) return status("Enter a GitHub username first.");
    status("Importing repos...");
    try {
      const r = await post(`/api/people/${graphState.person}/ingest/github`, { username });
      status(`Added ${r.added.length} repos.`);
      openGraph(graphState.person);
    } catch (err) { status(`${err.message}. Set GITHUB_TOKEN in .env if you hit rate limits.`); }
  });
}
const status = (t) => ($("#ingest-status").textContent = t);

async function loadPeople() {
  const people = await api("/api/people");
  const list = $("#people-list");
  list.replaceChildren(...people.map((p) =>
    h("li", {}, h("button", { "data-id": p.id, onclick: () => openGraph(p.id) }, p.name, h("small", {}, `${p.headline} (${p.nodes} facts)`)))));
  if (!graphState.person && people.length) openGraph(people[0].id);
}

async function openGraph(id) {
  graphState.person = id;
  document.querySelectorAll("#people-list button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
  const g = await api(`/api/people/${id}/graph`);
  $("#graph-name").textContent = g.name;
  $("#graph-headline").textContent = g.headline;
  drawGraph(g);
}

function drawGraph(g) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = $("#graph");
  svg.replaceChildren();
  const W = 900, H = 640, cx = W / 2, cy = H / 2;
  const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
  const pos = {};
  pos[g.id] = { x: cx, y: cy, a: 0 };
  const facts = g.nodes.filter((n) => n.id !== g.id && n.type !== "Evidence");
  const order = ["Work", "Goal", "Traction", "Achievement", "Priority", "Limit", "Interest", "Availability", "Mandate", "Contact", "Preference", "Style"];
  facts.sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type));
  facts.forEach((n, i) => {
    const a = -Math.PI / 2 + (i / Math.max(facts.length, 1)) * Math.PI * 2;
    pos[n.id] = { x: cx + Math.cos(a) * 200, y: cy + Math.sin(a) * 175, a };
  });
  const evidence = g.nodes.filter((n) => n.type === "Evidence");
  const used = {};
  evidence.forEach((n, i) => {
    const src = g.edges.find((e) => e.type === "SUPPORTED_BY" && e.target === n.id && pos[e.source]);
    let a = src ? pos[src.source].a : -Math.PI / 2 + (i / evidence.length) * Math.PI * 2;
    const k = src ? (used[src.source] = (used[src.source] || 0) + 1) - 1 : 0;
    a += k * 0.12;
    pos[n.id] = { x: cx + Math.cos(a) * 270, y: cy + Math.sin(a) * 250, a };
  });
  const add = (parent, tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; parent.append(e); return e; };
  const edges = add(svg, "g", {});
  g.edges.forEach((e) => {
    const a = pos[e.source], b = pos[e.target];
    if (a && b) add(edges, "line", { class: `edge${e.type === "SUPPORTED_BY" ? " evidence" : ""}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y });
  });
  g.nodes.forEach((n) => {
    const p = pos[n.id];
    if (!p) return;
    const center = n.id === g.id;
    const grp = add(svg, "g", { class: "node", tabindex: 0, role: "button", "aria-label": `${n.label}, ${n.type}, ${n.tier}` });
    add(grp, "circle", { class: `tier-${n.tier}`, cx: p.x, cy: p.y, r: center ? 26 : n.type === "Evidence" ? 7 : 11 });
    const right = Math.cos(p.a) >= -0.01;
    const tx = center ? p.x : p.x + (right ? 16 : -16);
    const anchor = center ? "middle" : right ? "start" : "end";
    const max = n.type === "Evidence" ? 22 : 30;
    const label = n.label.length > max ? `${n.label.slice(0, max - 1)}\u2026` : n.label;
    add(grp, "text", { x: tx, y: center ? p.y + 46 : p.y + 1, "text-anchor": anchor, "font-weight": center ? 600 : 400 }, center ? n.label : label);
    if (!center) add(grp, "text", { class: "type", x: tx, y: p.y + 15, "text-anchor": anchor }, n.type);
    const select = () => { svg.querySelectorAll(".node").forEach((x) => x.classList.remove("selected")); grp.classList.add("selected"); showNode(n, g, byId); };
    grp.addEventListener("click", select);
    grp.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } });
  });
}

const TIER_TEXT = { public: "Public", fit: "Shared when there is fit", approval: "Needs their approval", private: "Private" };

function showNode(n, g, byId) {
  const facts = n.attrs?.facts || {};
  const supports = g.edges.filter((e) => e.type === "SUPPORTED_BY" && e.source === n.id).map((e) => byId[e.target]).filter(Boolean);
  const rows = [["Type", n.type], ["Tier", TIER_TEXT[n.tier] || n.tier], ["Source", n.source]];
  Object.entries(facts).forEach(([k, v]) => rows.push([k.replace(/_/g, " "), Array.isArray(v) ? v.join(", ") : String(v)]));
  ["start", "end", "one_liner", "role"].forEach((k) => n.attrs?.[k] && rows.push([k.replace("_", " "), k === "start" || k === "end" ? fmt(n.attrs[k]) : n.attrs[k]]));
  if (n.attrs?.prefs) Object.entries(n.attrs.prefs).forEach(([k, v]) => rows.push([k.replace(/_/g, " "), Array.isArray(v) ? v.join(", ") : String(v)]));
  const ref = n.attrs?.ref;
  $("#node-detail").replaceChildren(h("div", { class: "detail" },
    h("p", { class: "label" }, n.label),
    h("dl", {}, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]),
      ref ? [h("dt", {}, "Link"), h("dd", {}, /^https?:/.test(ref) ? h("a", { href: ref, target: "_blank", rel: "noopener" }, ref) : ref)] : [],
      supports.length ? [h("dt", {}, "Evidence"), h("dd", {}, supports.map((s) => s.label).join("; "))] : [])));
}

boot().catch((err) => note(`Could not load: ${err.message}`));
