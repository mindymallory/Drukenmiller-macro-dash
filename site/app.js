/* Druckenmiller macro map: renders site/data/dashboard.json with Plotly. */
(function () {
  "use strict";

  const STATE = {
    tailwind: { icon: "✓", label: "Tailwind" },
    headwind: { icon: "✕", label: "Headwind" },
    neutral: { icon: "–", label: "Neutral" },
    unavailable: { icon: "?", label: "No data" },
  };
  const TREND = { up: "Above 200-day", down: "Below 200-day", flat: "At 200-day" };
  const STANCE_ICON = { bullish: "✓", lean_bull: "✓", bearish: "✕", lean_bear: "✕", flat: "–" };

  let DATA = null;
  let rangeYears = 5;
  let chartsBuilt = false;
  const plots = []; // {el, build}

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const signed = (v, dg) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(dg == null ? 2 : dg);
  const pct = (v) => Math.round(v * 100) + "%";

  /* ---------------------------------------------------------- colors -- */
  function hex2rgb(h) {
    h = h.replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    const n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mix(a, b, t) {
    const A = hex2rgb(a), B = hex2rgb(b);
    return "rgb(" + A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(",") + ")";
  }
  function tileColor(s) {
    if (s === null || s === undefined) return css("--na");
    const t = Math.min(1, Math.abs(s));
    return s >= 0 ? mix(css("--nt"), css("--tw"), t) : mix(css("--nt"), css("--hw"), t);
  }

  /* ------------------------------------------------------------ tabs -- */
  function setBarHeight() {
    document.documentElement.style.setProperty("--bar-h", $(".bar").offsetHeight + "px");
  }
  function showTab(name, opts) {
    opts = opts || {};
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".tab").forEach((t) => { t.hidden = t.id !== "tab-" + name; });
    if (!opts.keepHash) history.replaceState(null, "", "#" + name);
    if (name === "charts" && DATA && !chartsBuilt) buildCharts();
    if (name === "charts") plots.forEach(({ el }) => Plotly.Plots.resize(el));
    if (name === "map") sizeMap();
    if (!opts.keepScroll) window.scrollTo(0, 0);
  }
  function goGuide(id) {
    showTab("guide", { keepHash: true, keepScroll: true });
    history.replaceState(null, "", "#g-" + id);
    const el = document.getElementById("g-" + id);
    if (el) {
      el.scrollIntoView({ block: "start" });
      el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
    }
  }
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

  /* ------------------------------------------------------------- map -- */
  function sizeMap() {
    const el = $("#map");
    if (!el || $("#tab-map").hidden) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const h = Math.max(360, window.innerHeight - top - 40);
    el.style.height = h + "px";
    if (el.data) Plotly.Plots.resize(el);
  }

  function renderStance(d) {
    const n = d.now, a = d.year_ago, root = $("#stance");
    if (!n) { root.innerHTML = '<div class="verdict">Not enough data this run</div>'; return; }
    root.className = "stance " + n.stance;
    const counts = { tailwind: 0, neutral: 0, headwind: 0 };
    d.tiles.forEach((t) => { if (counts[t.state] !== undefined) counts[t.state]++; });
    root.innerHTML =
      '<div class="lead">If Druckenmiller saw this today:</div>' +
      `<div class="verdict"><span class="ico" aria-hidden="true">${STANCE_ICON[n.stance]}</span>${esc(n.label)}</div>` +
      `<div class="line">${esc(n.line)}</div>` +
      `<div class="facts">Overall ${signed(n.score)} on a −1 to +1 scale · ` +
      `${pct(n.tail_w)} of the weight is tailwind, ${pct(n.head_w)} headwind ` +
      `(${counts.tailwind} ✓, ${counts.headwind} ✕, ${counts.neutral} neutral)` +
      (a ? ` · A year ago: ${esc(a.label)} (${signed(a.score)})` : "") +
      ` · <a href="#g-overall" data-guide="overall">How this is decided</a></div>`;
    $("[data-guide]", root).addEventListener("click", (e) => { e.preventDefault(); goGuide("overall"); });
  }

  function buildMap() {
    const el = $("#map");
    const tiles = DATA.tiles;
    const groups = [];
    tiles.forEach((t) => { if (!groups.includes(t.group)) groups.push(t.group); });
    const small = window.innerWidth < 640;

    const ids = [], labels = [], parents = [], values = [], colors = [], text = [], custom = [], fontc = [];
    groups.forEach((g) => {
      const members = tiles.filter((t) => t.group === g);
      ids.push("grp:" + g); labels.push(g); parents.push("");
      values.push(members.reduce((s, t) => s + t.weight, 0));
      colors.push(css("--page")); fontc.push(css("--text-secondary"));
      text.push(`<b>${esc(g.toUpperCase())}</b>`); custom.push(["", "", ""]);
    });
    tiles.forEach((t) => {
      const st = STATE[t.state];
      ids.push(t.id); labels.push(t.name); parents.push("grp:" + t.group); values.push(t.weight);
      colors.push(tileColor(t.s)); fontc.push(t.state === "unavailable" ? css("--text-secondary") : css("--tile-text"));
      text.push(`<b>${st.icon} ${esc(t.name)}</b><br>${st.label}`);
      custom.push([esc(t.reading), t.s === null ? "n/a" : signed(t.s), String(t.weight)]);
    });

    const trace = {
      type: "treemap", ids, labels, parents, values, branchvalues: "total", sort: false,
      text, texttemplate: "%{text}", textposition: "middle center",
      textfont: { color: fontc, size: small ? 12 : 16, family: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
      marker: { colors, line: { color: css("--tile-gap"), width: 2 }, pad: { t: small ? 18 : 22, l: 2, r: 2, b: 2 } },
      tiling: { pad: 2 }, pathbar: { visible: false }, maxdepth: 2,
      customdata: custom,
      hovertemplate: "<b>%{label}</b><br>%{customdata[0]}<br>Score %{customdata[1]} · weight %{customdata[2]}<extra></extra>",
    };
    const layout = {
      margin: { l: 0, r: 0, t: 0, b: 0 }, paper_bgcolor: "rgba(0,0,0,0)",
      uniformtext: { minsize: 10, mode: "hide" },
      hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--axis"), font: { color: css("--text-primary"), size: 13 }, align: "left" },
    };
    Plotly.react(el, [trace], layout, { displayModeBar: false, responsive: true });
    if (!el._clickBound) {
      el.on("plotly_treemapclick", (ev) => {
        const id = ev.points && ev.points[0] && ev.points[0].id;
        if (id && !String(id).startsWith("grp:")) goGuide(id);
        return false; // no zooming into groups
      });
      el._clickBound = true;
    }
  }

  function chip(state, extra) {
    const st = STATE[state] || STATE.unavailable;
    return `<span class="chip ${state}"><span class="dot" aria-hidden="true"></span>` +
      `<span aria-hidden="true">${st.icon}</span>${st.label}${extra ? " " + esc(extra) : ""}</span>`;
  }

  function renderMapList() {
    const rows = DATA.tiles.map((t) =>
      `<tr><td><a href="#g-${t.id}" data-guide="${t.id}">${esc(t.name)}</a></td><td>${chip(t.state)}</td>` +
      `<td class="ago">${chip(t.state_ago)}</td><td class="r">${esc(t.reading)}</td></tr>`).join("");
    $("#maplist").innerHTML = "<h2>Every signal</h2>" +
      '<table class="tiletable"><thead><tr><th>Signal</th><th>Now</th><th class="ago">Year ago</th><th>Reading</th></tr></thead>' +
      `<tbody>${rows}</tbody></table>`;
    $$("#maplist [data-guide]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); goGuide(a.dataset.guide); }));
  }

  /* ----------------------------------------------------------- guide -- */
  function renderGuide() {
    DATA.tiles.forEach((t) => {
      const art = document.getElementById("g-" + t.id);
      if (!art) return;
      const h = t.history;
      const split = h ? `<div class="split" aria-hidden="true">` +
        `<span style="width:${h.tailwind * 100}%;background:var(--tw)"></span>` +
        `<span style="width:${h.neutral * 100}%;background:var(--nt)"></span>` +
        `<span style="width:${h.headwind * 100}%;background:var(--hw)"></span></div>` +
        `<div class="split-label">Last ${h.months} month-ends: tailwind ${pct(h.tailwind)}, neutral ${pct(h.neutral)}, headwind ${pct(h.headwind)}</div>` : "";
      const box = document.createElement("div");
      box.className = "now";
      box.innerHTML = `${chip(t.state)}<span>Score ${t.s === null ? "n/a" : signed(t.s)} · weight ${t.weight}</span>` +
        `<span class="reading">${esc(t.reading)}</span>${split}`;
      const old = $(".now", art);
      if (old) old.remove();
      art.querySelector("h2").after(box);
    });
  }

  /* ---------------------------------------------------------- charts -- */
  function fmt(v, digits, unit) {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    const n = Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    if (unit === "%") return n + "%";
    if (unit === "pts") return (v > 0 ? "+" : "") + n + " pts";
    return n;
  }
  function fmtDate(iso) {
    return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  function statHTML(s) {
    let ago = "";
    if (s.year_ago !== undefined) {
      const ch = s.now - s.year_ago;
      const chTxt = (s.unit === "%" || s.unit === "pts")
        ? (ch >= 0 ? "+" : "") + ch.toFixed(s.digits) + " pts"
        : (s.year_ago !== 0 ? ((ch / Math.abs(s.year_ago)) * 100 >= 0 ? "+" : "") + ((ch / Math.abs(s.year_ago)) * 100).toFixed(1) + "%" : "");
      ago = `<div class="stat-ago">Year ago ${fmt(s.year_ago, s.digits, s.unit)} (${chTxt})</div>`;
    }
    return `<div class="stat"><div class="stat-label">${esc(s.label)}, ${fmtDate(s.now_date)}</div>` +
      `<div class="stat-now">${fmt(s.now, s.digits, s.unit)}</div>${ago}</div>`;
  }

  function rangeStart() {
    if (!rangeYears) return null;
    const d = new Date();
    d.setFullYear(d.getFullYear() - rangeYears);
    return d.toISOString().slice(0, 10);
  }
  function yRangeFor(traces, start) {
    let lo = Infinity, hi = -Infinity;
    traces.forEach((t) => t.x.forEach((x, k) => {
      const v = t.y[k];
      if (v === null || (start && x < start)) return;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }));
    if (!isFinite(lo)) return undefined;
    const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.05 || 1;
    return [lo - pad, hi + pad];
  }
  function baseLayout(traces, unit, yr) {
    const start = rangeStart();
    const today = new Date().toISOString().slice(0, 10);
    return {
      margin: { l: 48, r: 12, t: traces.length > 1 ? 28 : 8, b: 28 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: css("--text-secondary") },
      hovermode: "x unified",
      hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--axis"), font: { color: css("--text-primary") } },
      xaxis: {
        type: "date", range: start ? [start, today] : undefined, autorange: !start,
        gridcolor: css("--grid"), linecolor: css("--axis"), tickcolor: css("--axis"), zeroline: false,
        showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: css("--text-muted"), spikedash: "solid",
      },
      yaxis: { range: yr, autorange: !yr, gridcolor: css("--grid"), zeroline: false, ticksuffix: unit === "%" ? "%" : "", linecolor: css("--axis") },
      legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 12, color: css("--text-secondary") } },
      showlegend: traces.length > 1, shapes: [], annotations: [],
    };
  }
  function traceFor(s, i, n) {
    const colors = [css("--series-1"), css("--series-2"), css("--series-3")];
    const ref = s.style === "dash";
    return {
      x: s.x, y: s.y, name: s.name, type: "scatter", mode: "lines",
      line: { color: ref ? css("--text-muted") : colors[i % 3], width: ref ? 1.5 : 2, dash: ref ? "dash" : s.style === "dot" ? "dot" : "solid" },
      hovertemplate: "%{y}<extra>" + esc(s.name) + "</extra>", showlegend: n > 1,
    };
  }
  function addPlot(container, buildFn) {
    const el = document.createElement("div");
    el.className = "plot";
    container.appendChild(el);
    const { traces, lay } = buildFn();
    Plotly.newPlot(el, traces, lay, { displayModeBar: false, responsive: true });
    plots.push({ el, build: buildFn });
  }
  function seriesPlot(series, refs, unit) {
    return () => {
      const traces = series.map((s, i) => traceFor(s, i, series.length));
      const lay = baseLayout(traces, unit, yRangeFor(traces, rangeStart()));
      (refs || []).forEach((r) => {
        lay.shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: r.y, y1: r.y,
          line: { color: css("--axis"), width: 1, dash: r.label ? "dash" : "solid" } });
        if (r.label) lay.annotations.push({ xref: "paper", x: 0, y: r.y, text: r.label, showarrow: false,
          xanchor: "left", yanchor: "bottom", font: { size: 11, color: css("--text-muted") } });
      });
      return { traces, lay };
    };
  }
  function historyPlot() {
    return () => {
      const h = DATA.history;
      const traces = [{
        x: h.map((p) => p.date), y: h.map((p) => p.score), type: "scatter", mode: "lines+markers",
        name: "Overall score", line: { color: css("--series-1"), width: 2 }, marker: { size: 4 },
        customdata: h.map((p) => p.stance), showlegend: false,
        hovertemplate: "%{y:+.2f}<extra>Overall</extra>",
      }];
      const lay = baseLayout(traces, "", [-1, 1]);
      lay.xaxis.range = undefined; lay.xaxis.autorange = true;
      const band = (y0, y1, c) => ({ type: "rect", xref: "paper", x0: 0, x1: 1, y0, y1, fillcolor: c, opacity: 0.12, line: { width: 0 }, layer: "below" });
      lay.shapes.push(band(0.35, 1, css("--tw")), band(-1, -0.35, css("--hw")));
      [0.15, -0.15].forEach((y) => lay.shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { color: css("--axis"), width: 1, dash: "dot" } }));
      lay.annotations.push(
        { xref: "paper", x: 0, y: 0.95, text: "Bullish zone", showarrow: false, xanchor: "left", font: { size: 11, color: css("--text-muted") } },
        { xref: "paper", x: 0, y: -0.95, text: "Bearish zone", showarrow: false, xanchor: "left", font: { size: 11, color: css("--text-muted") } });
      return { traces, lay };
    };
  }
  function tableHTML(seriesList) {
    const rows = [];
    seriesList.forEach((s) => {
      const n = s.x.length;
      for (let k = Math.max(0, n - 6); k < n; k++) rows.push([s.name, s.x[k], s.y[k]]);
    });
    if (!rows.length) return "";
    return '<details><summary>Recent values</summary><div class="table-wrap"><table class="datatable">' +
      "<thead><tr><th>Series</th><th>Date</th><th>Value</th></tr></thead><tbody>" +
      rows.map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${r[2] === null ? "–" : r[2]}</td></tr>`).join("") +
      "</tbody></table></div></details>";
  }

  function buildCharts() {
    chartsBuilt = true;
    const root = $("#charts");
    root.innerHTML = "";
    const tileById = {};
    DATA.tiles.forEach((t) => { tileById[t.id] = t; });

    if (DATA.history && DATA.history.length) {
      root.insertAdjacentHTML("beforeend", '<h2 class="section-title">The overall read, month by month</h2>' +
        '<p class="section-note">Recomputed at each month-end using only data published by then. Above +0.35 with broad agreement is the bullish zone; below −0.35 the bearish zone.</p>');
      const g = document.createElement("div"); g.className = "grid"; root.appendChild(g);
      const card = document.createElement("article"); card.className = "card wide"; g.appendChild(card);
      card.innerHTML = '<div class="card-head"><h3>Overall score</h3></div>';
      addPlot(card, historyPlot());
    }

    DATA.sections.forEach((sec) => {
      root.insertAdjacentHTML("beforeend", `<h2 class="section-title">${esc(sec.title)}</h2>` +
        (sec.note ? `<p class="section-note">${esc(sec.note)}</p>` : ""));
      const grid = document.createElement("div"); grid.className = "grid"; root.appendChild(grid);
      sec.charts.forEach((c) => {
        const t = c.tile ? tileById[c.tile] : null;
        const card = document.createElement("article");
        card.className = "card"; card.id = "c-" + c.id;
        let badge = "";
        if (t) badge = `<a class="chip ${t.state}" href="#g-${t.id}" data-guide="${t.id}"><span class="dot" aria-hidden="true"></span>` +
          `<span aria-hidden="true">${STATE[t.state].icon}</span>${STATE[t.state].label}</a>`;
        else if (c.trend) badge = `<span class="chip ${c.trend.state}"><span class="dot" aria-hidden="true"></span>` +
          `${TREND[c.trend.state]} (${signed(c.trend.dev, 1)}%)</span>`;
        card.innerHTML = `<div class="card-head"><h3>${esc(c.title)}</h3>${badge}</div>` +
          (t ? `<p class="reading">${esc(t.reading)}</p>` : "") +
          `<div class="stats">${(c.stats || []).map(statHTML).join("")}</div>`;
        grid.appendChild(card);
        if (c.series && c.series.length) addPlot(card, seriesPlot(c.series, c.refs, c.unit));
        else card.insertAdjacentHTML("beforeend", '<div class="unavail">Data unavailable this run.</div>');
        if (c.sep && c.sep.length) card.insertAdjacentHTML("beforeend",
          '<p class="meta">FOMC median dots: ' + c.sep.map((d) => `${esc(d.year)} ${Number(d.median).toFixed(2)}%`).join(" · ") + "</p>");
        if (c.note) card.insertAdjacentHTML("beforeend", `<p class="meta">${esc(c.note)}</p>`);
        card.insertAdjacentHTML("beforeend", tableHTML(c.series || []));
      });
    });
    $$("#charts [data-guide]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); goGuide(a.dataset.guide); }));
  }

  function redrawCharts() {
    plots.forEach(({ el, build }) => {
      const { traces, lay } = build();
      Plotly.react(el, traces, lay, { displayModeBar: false, responsive: true });
    });
  }

  $$(".controls button").forEach((b) => b.addEventListener("click", () => {
    $$(".controls button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    rangeYears = Number(b.dataset.range);
    redrawCharts();
  }));

  /* ------------------------------------------------------------ boot -- */
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { setBarHeight(); sizeMap(); if (DATA) buildMap(); }, 150);
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (DATA) { buildMap(); redrawCharts(); } });
  }

  function route() {
    const h = (location.hash || "").slice(1);
    if (h.startsWith("g-")) goGuide(h.slice(2));
    else if (["charts", "guide", "about"].includes(h)) showTab(h, { keepHash: true });
    else showTab("map", { keepHash: true });
  }

  setBarHeight();
  fetch("data/dashboard.json", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((d) => {
      DATA = d;
      const gen = new Date(d.generated);
      $("#updated").textContent = "Data as of " + gen.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) +
        (d.errors && d.errors.length ? ` · ${d.errors.length} series failed` : "");
      if (d.errors && d.errors.length) $("#errors").textContent = "Series that failed on the last refresh: " + d.errors.join("; ");
      renderStance(d);
      renderMapList();
      renderGuide();
      route();
      sizeMap();
      buildMap();
    })
    .catch((e) => {
      $("#updated").textContent = "Could not load data (" + e.message + ").";
      $("#stance").innerHTML = '<div class="verdict">Data unavailable</div>';
    });
  window.addEventListener("hashchange", () => { if (DATA) route(); });
})();
