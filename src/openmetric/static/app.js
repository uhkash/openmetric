/* OpenMetric dashboard. No build step, no dependencies, works offline. */

const PALETTE = [
  "#5b8cff", "#3ecf8e", "#f5a623", "#ff5c5c", "#b47cff",
  "#3ec9d6", "#ff8fab", "#9fd356", "#f78c6b", "#7b8cde",
];

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};

const usd = (n) => {
  if (!n) return "$0";
  if (n < 0.01) return "$" + n.toFixed(5);
  if (n < 1000) return "$" + n.toFixed(2);
  return "$" + Math.round(n).toLocaleString();
};
const num = (n) => (n || 0).toLocaleString();
const compact = (n) => {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n || 0));
};

/* The admin token, if one is configured, is kept in this tab only. */
let adminToken = sessionStorage.getItem("openmetric_admin_token") || "";

function filterParams(extra = {}) {
  const params = new URLSearchParams({ days: $("f-days").value, ...extra });
  for (const [key, id] of [["project", "f-project"], ["use_case", "f-use-case"], ["provider", "f-provider"]]) {
    const value = $(id)?.value;
    if (value) params.set(key, value);
  }
  return params;
}

async function api(path, extra = {}) {
  const url = path + (path.includes("?") ? "&" : "?") + filterParams(extra).toString();
  const headers = adminToken ? { "X-OpenMetric-Admin-Token": adminToken } : {};
  const response = await fetch(url, { headers });
  if (response.status === 401) {
    const token = prompt("Admin token required:");
    if (token) {
      adminToken = token;
      sessionStorage.setItem("openmetric_admin_token", token);
      return api(path, extra);
    }
    throw new Error("unauthorized");
  }
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return response.json();
}

/* ----------------------------------------------------------------- KPIs */

function renderKpis(summary, blindspots) {
  const days = summary.window?.days || 30;
  const perDay = summary.cost_usd / Math.max(days, 1);
  const highCount = blindspots?.counts?.high || 0;
  const tiles = [
    { label: "Spend", value: usd(summary.cost_usd), sub: `${usd(perDay)}/day - ${usd(summary.cost_per_request)}/call` },
    { label: "Requests", value: num(summary.requests), sub: `${compact(summary.total_tokens)} tokens` },
    { label: "Errors", value: (summary.error_rate * 100).toFixed(1) + "%", sub: `${num(summary.errors)} failed calls` },
    { label: "Latency p95", value: num(summary.p95_latency_ms) + "ms", sub: `${num(Math.round(summary.avg_latency_ms))}ms average` },
    {
      label: "Unpriced",
      value: num(summary.unpriced_requests),
      sub: summary.unpriced_requests ? "calls counted as $0" : "everything is priced",
    },
    {
      label: "Blindspots",
      value: String((blindspots?.findings || []).length),
      sub: highCount ? `${highCount} need attention` : "nothing urgent",
    },
  ];
  $("kpis").replaceChildren(
    ...tiles.map((tile) =>
      el("div", { class: "kpi" },
        el("div", { class: "label" }, tile.label),
        el("div", { class: "value" }, tile.value),
        el("div", { class: "sub" }, tile.sub))
    )
  );
}

/* --------------------------------------------------------------- Chart */

function renderChart(data, metric) {
  const container = $("chart");
  const series = (data.series || []).filter((s) => s.points.length);
  if (!series.length) {
    container.replaceChildren(el("div", { class: "empty" }, "No calls in this window."));
    $("chart-legend").replaceChildren();
    return;
  }

  const buckets = [...new Set(series.flatMap((s) => s.points.map((p) => p.bucket)))].sort();
  const lookup = series.map((s) => {
    const map = new Map(s.points.map((p) => [p.bucket, p[metric] || 0]));
    return { label: s.label, values: buckets.map((b) => map.get(b) || 0) };
  });
  const totals = buckets.map((_, i) => lookup.reduce((sum, s) => sum + s.values[i], 0));
  const peak = Math.max(...totals, 0.000001);

  const W = 1000, H = 260, padL = 54, padR = 12, padT = 12, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const slot = plotW / buckets.length;
  const barW = Math.max(Math.min(slot * 0.72, 46), 1);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "260");
  const svgEl = (tag, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };

  // gridlines + y axis
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i) / 4;
    svg.append(svgEl("line", {
      x1: padL, x2: W - padR, y1: y, y2: y,
      stroke: "currentColor", "stroke-opacity": 0.09,
    }));
    const value = peak * (1 - i / 4);
    svg.append(svgEl("text", { x: padL - 8, y: y + 3, "text-anchor": "end" },
      metric === "cost_usd" ? usd(value) : compact(value)));
  }

  buckets.forEach((bucket, i) => {
    let cursor = padT + plotH;
    lookup.forEach((s, si) => {
      const value = s.values[i];
      if (!value) return;
      const height = (value / peak) * plotH;
      cursor -= height;
      const rect = svgEl("rect", {
        x: padL + slot * i + (slot - barW) / 2,
        y: cursor,
        width: barW,
        height: Math.max(height, 0.6),
        fill: PALETTE[si % PALETTE.length],
        rx: 1.5,
      });
      rect.append(svgEl("title", {}, `${s.label} - ${bucket}: ${metric === "cost_usd" ? usd(value) : num(value)}`));
      svg.append(rect);
    });

    const step = Math.ceil(buckets.length / 12);
    if (i % step === 0) {
      svg.append(svgEl("text", {
        x: padL + slot * i + slot / 2, y: H - 8, "text-anchor": "middle",
      }, bucket.slice(5)));
    }
  });

  container.replaceChildren(svg);
  $("chart-legend").replaceChildren(
    ...lookup.map((s, i) =>
      el("span", {},
        el("i", { style: `background:${PALETTE[i % PALETTE.length]}` }),
        s.label))
  );
}

/* ----------------------------------------------------------- Breakdown */

function renderGroup(rows) {
  const table = $("group-table");
  if (!rows.length) {
    table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No data."))));
    return;
  }
  const peak = Math.max(...rows.map((r) => r.cost_usd), 0.000001);
  table.replaceChildren(
    el("thead", {}, el("tr", {},
      el("th", {}, "Name"),
      el("th", { class: "num" }, "Cost"),
      el("th", { class: "num" }, "Calls"),
      el("th", { class: "num" }, "Tokens"),
      el("th", { class: "num" }, "Err"),
      el("th", { class: "num" }, "Avg ms"))),
    el("tbody", {}, ...rows.map((row) =>
      el("tr", {},
        el("td", { class: "bar-cell" },
          el("span", { class: "bar", style: `width:${(row.cost_usd / peak) * 100}%` }),
          el("span", { class: "text" }, row.label)),
        el("td", { class: "num" }, usd(row.cost_usd)),
        el("td", { class: "num" }, num(row.requests)),
        el("td", { class: "num" }, compact(row.total_tokens)),
        el("td", { class: "num" }, row.errors ? String(row.errors) : "-"),
        el("td", { class: "num" }, Math.round(row.avg_latency_ms)))))
  );
}

/* -------------------------------------------------------------- Matrix */

function renderMatrix(data) {
  const table = $("matrix-table");
  if (!data.rows.length) {
    table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No data."))));
    return;
  }
  const peak = Math.max(...data.cells.flat(), 0.000001);
  const format = (v) =>
    data.metric === "cost_usd" ? usd(v) : data.metric === "avg_latency_ms" ? Math.round(v) : compact(v);

  table.replaceChildren(
    el("thead", {}, el("tr", {},
      el("th", {}, ""),
      ...data.cols.map((c) => el("th", { class: "num" }, c)),
      el("th", { class: "num" }, "Total"))),
    el("tbody", {}, ...data.rows.map((rowName, r) =>
      el("tr", {},
        el("td", {}, rowName),
        ...data.cells[r].map((value) =>
          el("td", {
            class: "cell",
            style: `background:rgba(91,140,255,${(value / peak) * 0.4})`,
          }, value ? format(value) : "-")),
        el("td", { class: "num" }, format(data.row_totals[r]))))),
    el("tfoot", {}, el("tr", {},
      el("th", {}, "Total"),
      ...data.col_totals.map((t) => el("th", { class: "num" }, format(t))),
      el("th", { class: "num" }, format(data.grand_total))))
  );
}

/* ---------------------------------------------------------- Blindspots */

function renderBlindspots(data) {
  const findings = data.findings || [];
  const counts = data.counts || {};
  $("blindspot-count").replaceChildren(
    findings.length
      ? el("span", { class: `pill ${counts.high ? "high" : counts.warn ? "warn" : "info"}` },
          `${counts.high || 0} high / ${counts.warn || 0} warn`)
      : el("span", { class: "pill ok" }, "all clear")
  );
  if (!findings.length) {
    $("blindspots").replaceChildren(
      el("div", { class: "empty" }, "Everything is tagged and priced. Nothing hiding.")
    );
    return;
  }
  $("blindspots").replaceChildren(
    ...findings.map((f) => {
      const action = el("div", { class: "action" });
      action.append("Fix: ");
      // Split on backticks so command snippets render as code without innerHTML.
      f.action.split("`").forEach((part, i) =>
        action.append(i % 2 ? el("code", {}, part) : document.createTextNode(part)));
      return el("div", { class: `finding ${f.severity}` },
        el("div", { class: "title" }, el("span", { class: `pill ${f.severity}` }, f.severity), " ", f.title),
        el("div", { class: "detail" }, f.detail),
        action);
    })
  );
}

/* ------------------------------------------------------ Budgets/events */

function renderBudgets(budgets) {
  const table = $("budget-table");
  if (!budgets.length) {
    table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No projects yet."))));
    return;
  }
  table.replaceChildren(
    el("thead", {}, el("tr", {},
      el("th", {}, "Project"),
      el("th", { class: "num" }, "Month to date"),
      el("th", { class: "num" }, "Budget"),
      el("th", {}, "Used"))),
    el("tbody", {}, ...budgets.map((b) =>
      el("tr", {},
        el("td", {}, b.project),
        el("td", { class: "num" }, usd(b.month_to_date_usd)),
        el("td", { class: "num" }, b.monthly_budget_usd ? usd(b.monthly_budget_usd) : "-"),
        el("td", { class: "bar-cell" },
          b.used_pct == null
            ? el("span", { class: "muted" }, "no budget set")
            : [
                el("span", {
                  class: "bar",
                  style: `width:${Math.min(b.used_pct, 100)}%;background:${b.over_budget ? "var(--high)" : "var(--good)"}`,
                }),
                el("span", { class: "text" }, b.used_pct.toFixed(0) + "%"),
              ])))),
  );
}

function renderEvents(events) {
  const table = $("events-table");
  if (!events.length) {
    table.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "empty" }, "No calls yet."))));
    return;
  }
  table.replaceChildren(
    el("thead", {}, el("tr", {},
      el("th", {}, "When"),
      el("th", {}, "Project"),
      el("th", {}, "Use case"),
      el("th", {}, "Provider"),
      el("th", {}, "Model"),
      el("th", {}, "Key"),
      el("th", { class: "num" }, "Tokens"),
      el("th", { class: "num" }, "ms"),
      el("th", { class: "num" }, "Cost"),
      el("th", {}, "Status"))),
    el("tbody", {}, ...events.slice(0, 25).map((e) =>
      el("tr", {},
        el("td", { class: "muted" }, new Date(e.created_at).toLocaleString()),
        el("td", {}, e.project || el("span", { class: "muted" }, "untagged")),
        el("td", {}, e.use_case || el("span", { class: "muted" }, "-")),
        el("td", {}, e.provider || "-"),
        el("td", {}, e.model || el("span", { class: "muted" }, "-")),
        el("td", { class: "muted" }, e.credential || "-"),
        el("td", { class: "num" }, compact(e.input_tokens + e.output_tokens)),
        el("td", { class: "num" }, num(e.latency_ms)),
        el("td", { class: "num" },
          e.cost_source === "unknown"
            ? el("span", { class: "pill warn", title: "no price known" }, "unpriced")
            : usd(e.cost_usd)),
        el("td", {}, el("span", { class: `pill ${e.ok ? "ok" : "high"}` }, String(e.status_code))))))
  );
}

/* --------------------------------------------------------------- Boot */

async function loadFilterOptions() {
  const [projects, useCases, providers] = await Promise.all([
    api("/api/analytics/group?by=project&limit=100"),
    api("/api/analytics/group?by=use_case&limit=100"),
    api("/api/analytics/group?by=provider&limit=100"),
  ]);
  const fill = (id, rows, keep) => {
    const select = $(id);
    const current = select.value;
    select.replaceChildren(
      el("option", { value: "" }, keep),
      ...rows.rows.filter((r) => r.key != null).map((r) => el("option", { value: r.label }, r.label))
    );
    select.value = current;
  };
  fill("f-project", projects, "All projects");
  fill("f-use-case", useCases, "All use cases");
  fill("f-provider", providers, "All providers");
}

async function refresh() {
  try {
    const [summary, spots, series, groups, matrix, budgets, events] = await Promise.all([
      api("/api/analytics/summary"),
      api("/api/blindspots"),
      api("/api/analytics/timeseries", { bucket: $("ts-bucket").value, by: $("ts-dimension").value }),
      api("/api/analytics/group", { by: $("group-dimension").value, order_by: $("group-order").value, limit: 15 }),
      api("/api/analytics/matrix", {
        rows_by: $("matrix-rows").value,
        cols_by: $("matrix-cols").value,
        metric: $("matrix-metric").value,
      }),
      api("/api/analytics/budgets"),
      api("/api/analytics/events", { limit: 25 }),
    ]);
    renderKpis(summary, spots);
    renderBlindspots(spots);
    renderChart(series, $("ts-metric").value);
    renderGroup(groups.rows);
    renderMatrix(matrix);
    renderBudgets(budgets.budgets);
    renderEvents(events.events);
  } catch (error) {
    console.error(error);
    $("kpis").replaceChildren(
      el("div", { class: "kpi" },
        el("div", { class: "label" }, "Error"),
        el("div", { class: "value" }, "!"),
        el("div", { class: "sub" }, String(error.message || error)))
    );
  }
}

for (const id of [
  "f-days", "f-project", "f-use-case", "f-provider",
  "ts-dimension", "ts-bucket", "ts-metric",
  "group-dimension", "group-order",
  "matrix-rows", "matrix-cols", "matrix-metric",
]) {
  $(id).addEventListener("change", refresh);
}
$("refresh").addEventListener("click", async () => {
  await loadFilterOptions();
  refresh();
});
$("export").addEventListener("click", () => {
  window.location = "/api/export.csv?" + filterParams().toString();
});

loadFilterOptions().then(refresh);
