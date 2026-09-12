/* OpenMetric dashboard. No build step, no dependencies, works offline.
 *
 * Structure: state (URL-backed) -> fetch -> render. Three views share one filter row.
 * Series colours are assigned in a fixed, validated order and follow the entity,
 * never its rank - a filter that changes the series count must not repaint survivors. */

"use strict";

/* ------------------------------------------------------------------ utils */

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};
const SVG_NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, text) => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
};

const usd = (n) => {
  if (!n) return "$0";
  if (n < 0.01) return "$" + n.toFixed(5);
  if (n < 1000) return "$" + n.toFixed(2);
  return "$" + Math.round(n).toLocaleString();
};
const num = (n) => Math.round(n || 0).toLocaleString();
const compact = (n) => {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
};
const fmtMetric = (metric, v) =>
  metric === "cost_usd" ? usd(v) : metric === "avg_latency_ms" ? num(v) + "ms" : compact(v);
const fmtWhen = (iso) => {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return d.toLocaleDateString();
};
const DASH = "–";

let toastTimer;
function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    toast("Select and copy manually");
  }
}

/* ------------------------------------------------------------------ state */

const FILTER_KEYS = ["days", "project", "use_case", "provider", "model", "credential"];
const VIEW_CONTROLS = ["ts-dimension", "ts-bucket", "ts-metric", "group-dimension", "group-order", "matrix-rows", "matrix-cols", "matrix-metric"];
const state = {
  view: "overview",
  filters: { days: "30" },
  gateway: { host: "127.0.0.1", port: 8099 },
  setup: null,
  firstRunPoll: null,
};

function readUrl() {
  const hash = location.hash.replace(/^#/, "");
  const [view, query] = hash.split("?");
  state.view = ["overview", "explore", "setup"].includes(view) ? view : "overview";
  const params = new URLSearchParams(query || "");
  state.filters = { days: "30" };
  for (const key of FILTER_KEYS) if (params.get(key)) state.filters[key] = params.get(key);
  for (const id of VIEW_CONTROLS) if (params.get(id) && $(id)) $(id).value = params.get(id);
}

function defaultOf(select) {
  const preset = select.querySelector("option[selected]");
  return preset ? preset.value : select.options[0].value;
}

function writeUrl() {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    if (!state.filters[key]) continue;
    if (key === "days" && state.filters[key] === "30") continue;
    params.set(key, state.filters[key]);
  }
  for (const id of VIEW_CONTROLS) {
    const node = $(id);
    if (node && node.value !== defaultOf(node)) params.set(id, node.value);
  }
  const query = params.toString();
  history.replaceState(null, "", "#" + state.view + (query ? "?" + query : ""));
}

function setFilter(key, value) {
  if (value) state.filters[key] = value;
  else delete state.filters[key];
  syncFilterControls();
  refresh();
}

function syncFilterControls() {
  $("f-days").value = state.filters.days || "30";
  for (const [key, id] of [["project", "f-project"], ["use_case", "f-use-case"], ["provider", "f-provider"]]) {
    const select = $(id);
    const value = state.filters[key] || "";
    if (value && ![...select.options].some((o) => o.value === value)) select.append(el("option", { value }, value));
    select.value = value;
  }
  // Chips: every active filter, removable. Model and key filters only live here.
  const labels = { project: "project", use_case: "use case", provider: "provider", model: "model", credential: "key" };
  $("chips").replaceChildren(
    ...FILTER_KEYS.filter((k) => k !== "days" && state.filters[k]).map((k) =>
      el("span", { class: "chip" },
        el("span", { class: "dim" }, labels[k] + ":"),
        state.filters[k],
        el("button", { title: "Remove filter", onclick: () => setFilter(k, null) }, "×")))
  );
}

/* -------------------------------------------------------------------- api */

let adminToken = "";
try { adminToken = sessionStorage.getItem("openmetric_admin_token") || ""; } catch { /* storage blocked */ }

function params(extra = {}) {
  const p = new URLSearchParams();
  for (const key of FILTER_KEYS) if (state.filters[key]) p.set(key, state.filters[key]);
  for (const [k, v] of Object.entries(extra)) if (v != null && v !== "") p.set(k, v);
  return p;
}

async function request(url, init = {}) {
  const headers = { ...(init.headers || {}) };
  if (adminToken) headers["X-OpenMetric-Admin-Token"] = adminToken;
  if (init.body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    const token = prompt("This gateway has an admin token set. Enter it to continue:");
    if (token) {
      adminToken = token;
      try { sessionStorage.setItem("openmetric_admin_token", token); } catch { /* storage blocked */ }
      return request(url, init);
    }
    throw new Error("Admin token required");
  }
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${url} -> ${response.status}`);
  return body;
}

/* Filtered read: carries the active filter set. */
const api = (path, extra = {}) => request(path + "?" + params(extra));
/* Unfiltered read: management endpoints and explicit parameter sets. */
const apiRaw = (path, extra = {}) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(extra)) if (v != null && v !== "") p.set(k, v);
  const query = p.toString();
  return request(path + (query ? "?" + query : ""));
};
const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body) });
const del = (path) => request(path, { method: "DELETE" });

/* ------------------------------------------------------------ colours */

const SLOTS = 8;
const colourMap = new Map();
try {
  for (const [k, v] of JSON.parse(sessionStorage.getItem("openmetric_colours") || "[]")) colourMap.set(k, v);
} catch { /* storage blocked */ }

/* Stable slot per (dimension, label): first seen, first free slot; never re-assigned. */
function colourFor(dimension, label) {
  const key = dimension + " " + label;
  if (!colourMap.has(key)) {
    const used = new Set([...colourMap.keys()].filter((k) => k.startsWith(dimension + " ")).map((k) => colourMap.get(k)));
    let slot = 1;
    while (used.has(slot) && slot <= SLOTS) slot++;
    colourMap.set(key, slot > SLOTS ? ((colourMap.size % SLOTS) + 1) : slot);
    try { sessionStorage.setItem("openmetric_colours", JSON.stringify([...colourMap.entries()])); } catch { /* storage blocked */ }
  }
  return `var(--s${colourMap.get(key)})`;
}

/* ------------------------------------------------------------- KPIs */

function deltaBadge(pct, upIsGood) {
  if (pct == null) return el("span", { class: "delta flat" }, "new");
  if (Math.abs(pct) < 1) return el("span", { class: "delta flat" }, "±0%");
  const up = pct > 0;
  const tone = upIsGood == null ? "flat" : `${up ? "up" : "down"}-${up === upIsGood ? "good" : "bad"}`;
  return el("span", { class: `delta ${tone}`, title: "vs the previous period" },
    (up ? "▲ " : "▼ ") + Math.abs(pct).toFixed(0) + "%");
}

function renderKpis(s, spots) {
  const d = s.delta_pct || {};
  const days = s.window?.days || 30;
  const tiles = [
    { label: "Spend", value: usd(s.cost_usd), foot: [`${usd(s.cost_usd / days)}/day`, deltaBadge(d.cost_usd, false)] },
    { label: "Requests", value: num(s.requests), foot: [`${compact(s.total_tokens)} tokens`, deltaBadge(d.requests, null)] },
    { label: "Error rate", value: (s.error_rate * 100).toFixed(1) + "%", foot: [`${num(s.errors)} failed`, deltaBadge(d.error_rate, false)] },
    { label: "Latency p95", value: num(s.p95_latency_ms) + "ms", foot: [`${num(s.avg_latency_ms)}ms avg`, deltaBadge(d.p95_latency_ms, false)] },
    { label: "Unpriced calls", value: num(s.unpriced_requests), foot: [s.unpriced_requests ? "counted as $0" : "all priced"] },
    { label: "Blindspots", value: String((spots?.findings || []).length), foot: [spots?.counts?.high ? `${spots.counts.high} need attention` : "nothing urgent"] },
  ];
  $("kpis").replaceChildren(...tiles.map((t) =>
    el("div", { class: "kpi" },
      el("div", { class: "label" }, t.label),
      el("div", { class: "value" }, t.value),
      el("div", { class: "foot" }, ...t.foot))));
}

/* ------------------------------------------------------------ chart */

let chartTableVisible = false;

function renderChart(data, metric) {
  const wrap = $("chart");
  const dimension = data.dimension || "all";
  const series = (data.series || []).filter((s) => s.points.length);
  if (!series.length) {
    wrap.replaceChildren(el("div", { class: "empty" }, "No calls in this window."));
    $("chart-legend").replaceChildren();
    $("chart-table").replaceChildren();
    return;
  }

  const buckets = [...new Set(series.flatMap((s) => s.points.map((p) => p.bucket)))].sort();
  const rows = series.map((s) => {
    const map = new Map(s.points.map((p) => [p.bucket, p[metric] || 0]));
    return { label: s.label, colour: colourFor(dimension, s.label), values: buckets.map((b) => map.get(b) || 0) };
  });
  const totals = buckets.map((_, i) => rows.reduce((sum, s) => sum + s.values[i], 0));
  const peak = Math.max(...totals, 1e-9);

  const W = 1000, H = 270, padL = 56, padR = 12, padT = 14, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const slot = plotW / buckets.length;
  const barW = Math.max(Math.min(slot * 0.66, 24), 2);
  const GAP = 2;

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${metric} over time by ${dimension}` });

  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i) / 4;
    root.append(svg("line", { class: "gridline", x1: padL, x2: W - padR, y1: y, y2: y }));
    root.append(svg("text", { x: padL - 8, y: y + 3, "text-anchor": "end" }, fmtMetric(metric, peak * (1 - i / 4))));
  }

  const columns = [];
  buckets.forEach((bucket, i) => {
    const x = padL + slot * i + (slot - barW) / 2;
    let top = padT + plotH;
    const segments = rows.filter((s) => s.values[i] > 0).length;
    let drawn = 0;
    rows.forEach((s) => {
      const value = s.values[i];
      if (!value) return;
      drawn++;
      const height = (value / peak) * plotH;
      const isTop = drawn === segments;
      // 2px surface gap between stacked segments; 4px rounded data-end on the top one.
      const h = Math.max(height - (isTop ? 0 : GAP), 0.5);
      const y = top - height;
      if (isTop && h > 4) {
        const r = 4;
        const d = `M${x},${y + h} V${y + r} a${r},${r} 0 0 1 ${r},-${r} H${x + barW - r} a${r},${r} 0 0 1 ${r},${r} V${y + h} Z`;
        root.append(svg("path", { d, fill: s.colour }));
      } else {
        root.append(svg("rect", { x, y, width: barW, height: h, fill: s.colour }));
      }
      top -= height;
    });
    const step = Math.ceil(buckets.length / 12);
    if (i % step === 0) root.append(svg("text", { x: padL + slot * i + slot / 2, y: H - 10, "text-anchor": "middle" }, bucket.slice(5)));
    columns.push({ x: padL + slot * i, bucket, i });
  });

  // Hit targets are whole columns, not painted pixels. One tooltip lists every series.
  const tip = el("div", { class: "tooltip" });
  tip.hidden = true;
  columns.forEach(({ x, bucket, i }) => {
    const hit = svg("rect", { class: "hit", x, y: padT, width: slot, height: plotH, tabindex: "0" });
    const show = () => {
      const present = rows.filter((s) => s.values[i] > 0).sort((a, b) => b.values[i] - a.values[i]);
      tip.replaceChildren(
        el("div", { class: "when" }, bucket),
        ...present.map((s) => el("div", { class: "row" },
          el("i", { style: `background:${s.colour}` }),
          el("span", { class: "n" }, s.label),
          el("span", { class: "v" }, fmtMetric(metric, s.values[i])))),
        present.length > 1 && el("div", { class: "total" }, el("span", {}, "total"), el("span", {}, fmtMetric(metric, totals[i]))));
      tip.hidden = false;
      const frac = (x + slot / 2) / W;
      tip.style.left = frac > 0.6 ? "auto" : `calc(${frac * 100}% + 12px)`;
      tip.style.right = frac > 0.6 ? `calc(${(1 - frac) * 100}% + 12px)` : "auto";
      tip.style.top = "8px";
      hit.classList.add("active");
    };
    const hide = () => { tip.hidden = true; hit.classList.remove("active"); };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("blur", hide);
    root.append(hit);
  });

  wrap.replaceChildren(root, tip);
  $("chart-legend").replaceChildren(...rows.map((s) => el("span", {}, el("i", { style: `background:${s.colour}` }), s.label)));

  // Table twin: every value reachable without hovering.
  $("chart-table").replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Period"), ...rows.map((s) => el("th", { class: "num" }, s.label)), el("th", { class: "num" }, "Total"))),
    el("tbody", {}, ...buckets.map((b, i) => el("tr", {},
      el("td", {}, b),
      ...rows.map((s) => el("td", { class: "num" }, s.values[i] ? fmtMetric(metric, s.values[i]) : DASH)),
      el("td", { class: "num" }, fmtMetric(metric, totals[i])))))));
  $("chart-table").hidden = !chartTableVisible;
}

/* -------------------------------------------------------- breakdown */

const FILTERABLE = new Set(["project", "use_case", "provider", "model", "credential"]);
const isPlaceholder = (label) => /^\((untagged|unknown|none)\)$/.test(label || "");

function renderGroup(rows, dimension) {
  const table = $("group-table");
  if (!rows.length) { table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No data.")))); return; }
  const peak = Math.max(...rows.map((r) => r.cost_usd), 1e-9);
  const clickable = FILTERABLE.has(dimension);
  table.replaceChildren(
    el("thead", {}, el("tr", {},
      el("th", {}, dimension.replace("_", " ")),
      el("th", { class: "num" }, "Cost"), el("th", { class: "num" }, "Calls"), el("th", { class: "num" }, "Tokens"),
      el("th", { class: "num" }, "Err"), el("th", { class: "num" }, "Avg ms"))),
    el("tbody", {}, ...rows.map((row) => {
      const can = clickable && !isPlaceholder(row.label);
      return el("tr", {
        class: can ? "clickable" : null,
        title: can ? `Filter to ${row.label}` : null,
        onclick: can ? () => setFilter(dimension, row.label) : null,
      },
        el("td", { class: "bar-cell" },
          el("span", { class: "bar", style: `width:${(row.cost_usd / peak) * 100}%` }),
          el("span", { class: "text" }, el("i", { class: "swatch", style: `background:${colourFor(dimension, row.label)}` }), row.label)),
        el("td", { class: "num" }, usd(row.cost_usd)),
        el("td", { class: "num" }, num(row.requests)),
        el("td", { class: "num" }, compact(row.total_tokens)),
        el("td", { class: "num" }, row.errors ? String(row.errors) : DASH),
        el("td", { class: "num" }, num(row.avg_latency_ms)));
    })));
}

function renderMatrix(data) {
  const table = $("matrix-table");
  if (!data.rows.length) { table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No data.")))); return; }
  const peak = Math.max(...data.cells.flat(), 1e-9);
  const canRow = FILTERABLE.has(data.rows_by), canCol = FILTERABLE.has(data.cols_by);
  const corner = `${data.rows_by} \\ ${data.cols_by}`.replace(/_/g, " ");
  table.replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, corner), ...data.cols.map((c) => el("th", { class: "num" }, c)), el("th", { class: "num" }, "Total"))),
    el("tbody", {}, ...data.rows.map((rowName, r) => el("tr", {},
      el("td", {}, rowName),
      ...data.cells[r].map((value, c) => el("td", {
        class: "cell",
        style: `background:rgba(var(--heat),${(value / peak) * 0.45})`,
        title: value ? `${rowName} × ${data.cols[c]}: ${fmtMetric(data.metric, value)}. Click to filter.` : null,
        onclick: value ? () => {
          if (canRow && !isPlaceholder(rowName)) state.filters[data.rows_by] = rowName;
          if (canCol && !isPlaceholder(data.cols[c])) state.filters[data.cols_by] = data.cols[c];
          syncFilterControls(); refresh();
        } : null,
      }, value ? fmtMetric(data.metric, value) : DASH)),
      el("td", { class: "num" }, fmtMetric(data.metric, data.row_totals[r]))))),
    el("tfoot", {}, el("tr", {}, el("th", {}, "Total"), ...data.col_totals.map((t) => el("th", { class: "num" }, fmtMetric(data.metric, t))), el("th", { class: "num" }, fmtMetric(data.metric, data.grand_total)))));
}

/* ------------------------------------------------------- blindspots */

function renderBlindspots(data) {
  const findings = data.findings || [], counts = data.counts || {};
  $("blindspot-count").replaceChildren(findings.length
    ? el("span", { class: `pill ${counts.high ? "high" : counts.warn ? "warn" : "info"}` }, `${counts.high || 0} high · ${counts.warn || 0} warn · ${counts.info || 0} info`)
    : el("span", { class: "pill ok" }, "all clear"));
  if (!findings.length) { $("blindspots").replaceChildren(el("div", { class: "empty" }, "Everything is tagged and priced. Nothing hiding.")); return; }
  $("blindspots").replaceChildren(...findings.map((f) => {
    const action = el("div", { class: "action" }, "Fix: ");
    // Split on backticks so command snippets render as code without innerHTML.
    f.action.split("`").forEach((part, i) => action.append(i % 2 ? el("code", {}, part) : document.createTextNode(part)));
    const evidence = f.evidence && Object.keys(f.evidence).length
      ? el("details", {}, el("summary", {}, "evidence"), el("pre", {}, JSON.stringify(f.evidence, null, 2)))
      : null;
    return el("div", { class: `finding ${f.severity}` },
      el("div", { class: "title" }, el("span", { class: `pill ${f.severity}` }, f.severity), f.title),
      el("div", { class: "detail" }, f.detail), action, evidence);
  }));
}

/* ------------------------------------------------- budgets / events */

function renderBudgets(budgets) {
  const table = $("budget-table");
  if (!budgets.length) { table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No projects yet. Add one under Setup.")))); return; }
  table.replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "Project"), el("th", { class: "num" }, "Month to date"), el("th", { class: "num" }, "Budget"), el("th", {}, "Used"))),
    el("tbody", {}, ...budgets.map((b) => el("tr", {},
      el("td", {}, b.project),
      el("td", { class: "num" }, usd(b.month_to_date_usd)),
      el("td", { class: "num" }, b.monthly_budget_usd ? usd(b.monthly_budget_usd) : DASH),
      el("td", { class: "bar-cell" }, b.used_pct == null
        ? el("span", { class: "muted" }, "no budget set")
        : [el("span", { class: "bar", style: `width:${Math.min(b.used_pct, 100)}%;background:${b.over_budget ? "var(--high)" : "var(--good)"};opacity:.22` }),
           el("span", { class: "text" }, b.used_pct.toFixed(0) + "%")])))));
}

function renderEvents(events) {
  const table = $("events-table");
  $("events-count").textContent = events.length ? `latest ${events.length}` : "";
  if (!events.length) { table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No calls yet.")))); return; }
  table.replaceChildren(
    el("thead", {}, el("tr", {},
      ...["When", "Project", "Use case", "Provider", "Model", "Key"].map((h) => el("th", {}, h)),
      ...["Tokens", "ms", "Cost"].map((h) => el("th", { class: "num" }, h)),
      el("th", {}, "Status"))),
    el("tbody", {}, ...events.map((e) => el("tr", {},
      el("td", { class: "muted", title: new Date(e.created_at).toLocaleString() }, fmtWhen(e.created_at)),
      el("td", {}, e.project || el("span", { class: "pill warn" }, "untagged")),
      el("td", {}, e.use_case || el("span", { class: "muted" }, DASH)),
      el("td", {}, e.provider || DASH),
      el("td", {}, e.model || el("span", { class: "muted" }, DASH)),
      el("td", { class: "muted" }, e.credential || DASH),
      el("td", { class: "num" }, compact(e.input_tokens + e.output_tokens)),
      el("td", { class: "num" }, num(e.latency_ms)),
      el("td", { class: "num" }, e.cost_source === "unknown" ? el("span", { class: "pill warn", title: "no price known" }, "unpriced") : usd(e.cost_usd)),
      el("td", {}, el("span", { class: `pill ${e.ok ? "ok" : "high"}` }, String(e.status_code)))))));
}

/* ------------------------------------------------------------ setup */

const SNIPPETS = {
  python: (base) => [
    "from openai import OpenAI",
    "",
    "client = OpenAI(",
    `    base_url="${base}/v1",                 # was the provider's URL`,
    '    api_key=os.environ["OPENMETRIC_KEY"],  # was the provider\'s key',
    ")",
    "",
    "client.chat.completions.create(",
    '    model="openai/gpt-4o-mini",',
    '    messages=[{"role": "user", "content": "hi"}],',
    ")",
  ].join("\n"),
  node: (base) => [
    'import OpenAI from "openai";',
    "",
    "const client = new OpenAI({",
    `  baseURL: "${base}/v1",`,
    "  apiKey: process.env.OPENMETRIC_KEY,",
    '  defaultHeaders: { "X-OpenMetric-Project": "my-app" },',
    "});",
    "",
    "await client.chat.completions.create({",
    '  model: "openai/gpt-4o-mini",',
    '  messages: [{ role: "user", content: "hi" }],',
    "});",
  ].join("\n"),
  curl: (base) => [
    `curl ${base}/v1/chat/completions \\`,
    '  -H "Authorization: Bearer $OPENMETRIC_KEY" \\',
    '  -H "X-OpenMetric-Project: my-app" \\',
    '  -H "X-OpenMetric-Use-Case: llm-summary" \\',
    '  -H "Content-Type: application/json" \\',
    '  -d \'{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}\'',
  ].join("\n"),
  env: (base) => [
    "# Drop into any project that uses the OpenAI SDK. No code change at all.",
    `OPENAI_BASE_URL=${base}/v1`,
    "OPENAI_API_KEY=om_live_...   # a virtual key from the Setup tab",
  ].join("\n"),
};

function baseUrl() {
  if (location.origin && location.origin !== "null") return location.origin;
  return `http://${state.gateway.host}:${state.gateway.port}`;
}

function wireSnippets(tabsId, preId, copyId) {
  const render = (lang) => { $(preId).textContent = SNIPPETS[lang](baseUrl()); };
  $(tabsId).addEventListener("click", (e) => {
    const button = e.target.closest("button[data-lang]");
    if (!button) return;
    for (const b of $(tabsId).querySelectorAll("button")) b.classList.toggle("active", b === button);
    render(button.dataset.lang);
  });
  $(copyId).addEventListener("click", () => copyText($(preId).textContent));
  render("python");
}

function renderSetup(setup, projects, credentials, vkeys, providers) {
  state.setup = setup;
  const done = setup.steps.filter((s) => s.done).length;
  $("setup-progress").style.width = `${(done / setup.steps.length) * 100}%`;
  $("setup-progress-label").textContent = `${done}/${setup.steps.length}`;
  $("checklist").replaceChildren(...setup.steps.map((s) =>
    el("div", { class: `step ${s.done ? "done" : ""}` },
      el("div", { class: "tick" }, s.done ? "✓" : ""),
      el("div", {}, el("div", { class: "t" }, s.title), el("div", { class: "h" }, s.done ? "" : s.hint)))));
  $("setup-gateway").replaceChildren(
    `Gateway: ${baseUrl()}  ·  `,
    setup.admin_token_set
      ? el("span", { class: "pill ok" }, "admin token set")
      : el("span", { class: "pill muted", title: "Fine on localhost. Set OPENMETRIC_ADMIN_TOKEN before exposing this port." }, "no admin token"));

  $("cred-project").replaceChildren(el("option", { value: "" }, "shared"), ...projects.map((p) => el("option", { value: p.slug }, p.slug)));
  $("vkey-project").replaceChildren(el("option", { value: "" }, "none"), ...projects.map((p) => el("option", { value: p.slug }, p.slug)));
  const ordered = [...providers].sort((a, b) => (a.slug === "openrouter" ? -1 : b.slug === "openrouter" ? 1 : a.kind === b.kind ? a.slug.localeCompare(b.slug) : a.kind === "llm" ? -1 : 1));
  $("cred-provider").replaceChildren(...ordered.map((p) => el("option", { value: p.slug }, `${p.name} (${p.kind})`)));
  $("vkey-credential").replaceChildren(el("option", { value: "" }, "auto by provider"),
    ...credentials.filter((c) => c.active).map((c) => el("option", { value: c.label }, `${c.label} · ${c.provider}`)));

  const emptyRow = (cols, text) => el("tbody", {}, el("tr", {}, el("td", { class: "empty", colspan: String(cols) }, text)));

  $("projects-table").replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "Slug"), el("th", {}, "Name"), el("th", { class: "num" }, "Budget"), el("th", {}, ""))),
    projects.length ? el("tbody", {}, ...projects.map((p) => el("tr", {},
      el("td", {}, p.slug), el("td", { class: "muted" }, p.name),
      el("td", { class: "num" }, p.monthly_budget_usd ? usd(p.monthly_budget_usd) : DASH),
      el("td", {}, el("button", { class: "small ghost danger", onclick: async () => {
        if (!confirm(`Archive ${p.slug}? Its history is kept.`)) return;
        await del(`/api/projects/${encodeURIComponent(p.slug)}`); toast("Archived"); refresh();
      } }, "archive")))))
    : emptyRow(4, "No projects yet. They are also created automatically the first time a request names one."));

  $("credentials-table").replaceChildren(
    el("thead", {}, el("tr", {}, ...["Label", "Provider", "Project", "Key", "Last used", ""].map((h) => el("th", {}, h)))),
    credentials.length ? el("tbody", {}, ...credentials.map((c) => el("tr", {},
      el("td", {}, c.label), el("td", {}, c.provider), el("td", { class: "muted" }, c.project || "shared"),
      el("td", { class: "mono" }, c.key_hint),
      el("td", { class: "muted" }, c.last_used_at ? fmtWhen(c.last_used_at) : el("span", { class: "pill warn" }, "never")),
      el("td", {}, c.active
        ? el("button", { class: "small ghost danger", onclick: async () => {
          if (!confirm(`Deactivate ${c.label}? Calls will stop routing through it.`)) return;
          await post(`/api/credentials/${encodeURIComponent(c.label)}/deactivate`, {}); toast("Deactivated"); refresh();
        } }, "deactivate")
        : el("span", { class: "pill muted" }, "inactive")))))
    : emptyRow(6, "No provider keys stored yet."));

  $("vkeys-table").replaceChildren(
    el("thead", {}, el("tr", {}, ...["Name", "Token", "Project", "Use case", "Last used", ""].map((h) => el("th", {}, h)))),
    vkeys.length ? el("tbody", {}, ...vkeys.map((k) => el("tr", {},
      el("td", { title: k.credential ? `pinned to ${k.credential}` : "routes by provider" }, k.name),
      el("td", { class: "mono" }, k.token_hint), el("td", {}, k.project || DASH), el("td", {}, k.use_case || DASH),
      el("td", { class: "muted" }, k.last_used_at ? fmtWhen(k.last_used_at) : "never"),
      el("td", {}, k.active
        ? el("button", { class: "small ghost danger", onclick: async () => {
          if (!confirm(`Revoke ${k.name}?`)) return;
          await del(`/api/virtual-keys/${k.id}`); toast("Revoked"); refresh();
        } }, "revoke")
        : el("span", { class: "pill muted" }, "revoked")))))
    : emptyRow(6, "No virtual keys yet. Issue one above."));
}

function showTokenModal(created) {
  const envText = `OPENAI_BASE_URL=${baseUrl()}/v1\nOPENAI_API_KEY=${created.token}`;
  const modal = el("div", { class: "modal-bg", onclick: (e) => { if (e.target === modal) modal.remove(); } },
    el("div", { class: "modal" },
      el("h3", {}, `Virtual key "${created.name}"`),
      el("p", { class: "muted" }, "Copy it now. It is stored hashed and cannot be shown again."),
      el("div", { class: "token" }, created.token),
      el("p", { class: "muted", style: "font-size:12px" }, "Use it exactly where the provider key used to go:"),
      el("pre", { class: "snippet" }, envText),
      el("div", { class: "actions" },
        el("button", { onclick: () => copyText(envText) }, "Copy .env lines"),
        el("button", { class: "primary", onclick: () => copyText(created.token) }, "Copy token"),
        el("button", { class: "ghost", onclick: () => modal.remove() }, "Done"))));
  $("modal-root").replaceChildren(modal);
}

function wireForms() {
  const formData = (form) => Object.fromEntries([...new FormData(form).entries()].map(([k, v]) => [k, typeof v === "string" ? v.trim() : v]));

  $("form-project").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formData(e.target);
    try {
      await post("/api/projects", { slug: data.slug, monthly_budget_usd: data.monthly_budget_usd ? Number(data.monthly_budget_usd) : null });
      e.target.reset(); toast(`Project ${data.slug} added`); refresh();
    } catch (err) { toast(err.message); }
  });

  $("form-credential").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formData(e.target);
    try {
      await post("/api/credentials", { provider: data.provider, label: data.label, api_key: data.api_key, project: data.project || null });
      e.target.reset(); toast(`Stored ${data.label}`); refresh();
    } catch (err) { toast(err.message); }
  });

  $("form-vkey").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formData(e.target);
    try {
      const created = await post("/api/virtual-keys", { name: data.name, project: data.project || null, use_case: data.use_case || null, credential_label: data.credential_label || null });
      e.target.reset(); showTokenModal(created); refresh();
    } catch (err) { toast(err.message); }
  });
}

/* --------------------------------------------------------- first run */

function startFirstRunPolling() {
  if (state.firstRunPoll) return;
  state.firstRunPoll = setInterval(async () => {
    try {
      const health = await apiRaw("/api/health");
      if (health.events > 0) {
        clearInterval(state.firstRunPoll); state.firstRunPoll = null;
        toast("First request received");
        await loadFilterOptions();
        refresh();
      }
    } catch { /* gateway restarting; keep listening */ }
  }, 3000);
}

/* ------------------------------------------------------------- views */

function showView(view) {
  state.view = view;
  for (const section of document.querySelectorAll("section.view")) section.hidden = section.id !== `view-${view}`;
  for (const a of $("tabs").querySelectorAll("a")) a.classList.toggle("active", a.dataset.view === view);
  $("filterbar").hidden = view === "setup";
  writeUrl();
}

async function loadFilterOptions() {
  const [projects, useCases, providers] = await Promise.all([
    apiRaw("/api/analytics/group", { by: "project", limit: 200, days: 3650 }),
    apiRaw("/api/analytics/group", { by: "use_case", limit: 200, days: 3650 }),
    apiRaw("/api/analytics/group", { by: "provider", limit: 200, days: 3650 }),
  ]);
  const fill = (id, rows, keep) => {
    const select = $(id), current = select.value;
    select.replaceChildren(el("option", { value: "" }, keep),
      ...rows.rows.filter((r) => r.key != null && r.key !== "").map((r) => el("option", { value: r.label }, r.label)));
    select.value = current;
  };
  fill("f-project", projects, "All projects");
  fill("f-use-case", useCases, "All use cases");
  fill("f-provider", providers, "All providers");
}

async function refresh() {
  writeUrl();
  $("main").classList.add("loading");
  try {
    if (state.view === "setup") {
      const [setup, projects, credentials, vkeys, providers] = await Promise.all([
        apiRaw("/api/setup"), apiRaw("/api/projects"), apiRaw("/api/credentials"), apiRaw("/api/virtual-keys"), apiRaw("/api/providers"),
      ]);
      state.gateway = setup.gateway;
      renderSetup(setup, projects, credentials, vkeys, providers);
      return;
    }
    if (state.view === "overview") {
      const [setup, summary, spots, series, budgets] = await Promise.all([
        apiRaw("/api/setup"),
        api("/api/analytics/summary", { compare: "true" }),
        api("/api/blindspots"),
        api("/api/analytics/timeseries", { bucket: $("ts-bucket").value, by: $("ts-dimension").value, limit: SLOTS }),
        apiRaw("/api/analytics/budgets"),
      ]);
      state.gateway = setup.gateway;
      const firstRun = setup.counts.events === 0;
      $("first-run").hidden = !firstRun;
      if (firstRun) startFirstRunPolling();
      renderKpis(summary, spots);
      renderChart(series, $("ts-metric").value);
      renderBlindspots(spots);
      renderBudgets(budgets.budgets);
      return;
    }
    const [groups, matrix, events] = await Promise.all([
      api("/api/analytics/group", { by: $("group-dimension").value, order_by: $("group-order").value, limit: 25 }),
      api("/api/analytics/matrix", { rows_by: $("matrix-rows").value, cols_by: $("matrix-cols").value, metric: $("matrix-metric").value }),
      api("/api/analytics/events", { limit: 40 }),
    ]);
    renderGroup(groups.rows, $("group-dimension").value);
    renderMatrix(matrix);
    renderEvents(events.events);
  } catch (error) {
    console.error(error);
    toast(String(error.message || error));
  } finally {
    $("main").classList.remove("loading");
  }
}

/* --------------------------------------------------------------- boot */

function wireControls() {
  $("f-days").addEventListener("change", () => setFilter("days", $("f-days").value));
  $("f-project").addEventListener("change", () => setFilter("project", $("f-project").value));
  $("f-use-case").addEventListener("change", () => setFilter("use_case", $("f-use-case").value));
  $("f-provider").addEventListener("change", () => setFilter("provider", $("f-provider").value));
  for (const id of VIEW_CONTROLS) $(id).addEventListener("change", refresh);
  $("ts-table-toggle").addEventListener("click", () => {
    chartTableVisible = !chartTableVisible;
    $("chart-table").hidden = !chartTableVisible;
    $("ts-table-toggle").textContent = chartTableVisible ? "Hide table" : "Table";
  });
  $("refresh").addEventListener("click", async () => { await loadFilterOptions(); refresh(); });
  $("export").addEventListener("click", () => { window.location = "/api/export.csv?" + params({ limit: 200000 }); });

  let timer = null;
  $("auto-refresh").addEventListener("change", () => {
    clearInterval(timer);
    const seconds = Number($("auto-refresh").value);
    if (seconds) timer = setInterval(refresh, seconds * 1000);
  });

  $("theme").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const current = root.dataset.theme || (dark ? "dark" : "light");
    root.dataset.theme = current === "dark" ? "light" : "dark";
    try { localStorage.setItem("openmetric_theme", root.dataset.theme); } catch { /* storage blocked */ }
  });
  try {
    const saved = localStorage.getItem("openmetric_theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch { /* storage blocked */ }

  $("tabs").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-view]");
    if (!a) return;
    e.preventDefault();
    showView(a.dataset.view); refresh();
  });
  window.addEventListener("hashchange", () => { readUrl(); syncFilterControls(); showView(state.view); refresh(); });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "r") refresh();
    if (e.key === "1") { showView("overview"); refresh(); }
    if (e.key === "2") { showView("explore"); refresh(); }
    if (e.key === "3") { showView("setup"); refresh(); }
    if (e.key === "Escape") $("modal-root").replaceChildren();
  });
}

(async function boot() {
  readUrl();
  wireControls();
  wireForms();
  wireSnippets("snippet-tabs", "snippet", "copy-snippet");
  wireSnippets("snippet-tabs-2", "snippet-2", "copy-snippet-2");
  showView(state.view);
  try { await loadFilterOptions(); } catch (e) { console.error(e); }
  syncFilterControls();
  refresh();
})();
