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
    if (name === "map") { sizeMap(); if (DATA && mapDirty) buildMap(); }
    if (name === "backtest" && DATA) buildBacktest();
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
  const isMobile = () => window.innerWidth < 700;
  let mapDirty = true;
  function sizeMap() {
    const el = $("#map");
    if (!el || $("#tab-map").hidden) return;
    if (isMobile()) { el.style.height = "auto"; return; }
    const top = el.getBoundingClientRect().top + window.scrollY;
    el.style.height = Math.max(520, window.innerHeight - top - 40) + "px";
  }

  /* Squarified treemap layout (Bruls, Huizing & van Wijk 2000). */
  function squarify(items, x, y, w, h) {
    const total = items.reduce((s, it) => s + it.w, 0);
    const scale = (w * h) / total;
    const nodes = items.map((it) => ({ it, a: it.w * scale }));
    const out = [];
    let row = [];
    const worst = (r, side) => {
      const s = r.reduce((a, n) => a + n.a, 0);
      let mx = 0, mn = Infinity;
      r.forEach((n) => { mx = Math.max(mx, n.a); mn = Math.min(mn, n.a); });
      return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
    };
    const layoutRow = (r) => {
      const s = r.reduce((a, n) => a + n.a, 0);
      if (w >= h) {
        const cw = s / h; let cy = y;
        r.forEach((n) => { const ch = n.a / cw; out.push({ it: n.it, x, y: cy, w: cw, h: ch }); cy += ch; });
        x += cw; w -= cw;
      } else {
        const ch = s / w; let cx = x;
        r.forEach((n) => { const c = n.a / ch; out.push({ it: n.it, x: cx, y, w: c, h: ch }); cx += c; });
        y += ch; h -= ch;
      }
    };
    nodes.forEach((n) => {
      const side = Math.min(w, h);
      if (!row.length || worst(row.concat([n]), side) <= worst(row, side)) row.push(n);
      else { layoutRow(row); row = [n]; }
    });
    if (row.length) layoutRow(row);
    return out;
  }

  function tint(t) {
    if (t.s === null || t.s === undefined) return css("--surface-1");
    if (t.state === "neutral") return mix(css("--surface-1"), css("--nt"), 0.22);
    const c = t.s >= 0 ? css("--tw") : css("--hw");
    return mix(css("--surface-1"), c, 0.2 + 0.3 * Math.min(1, Math.abs(t.s)));
  }
  function place(el, x, y, w, h) {
    el.style.left = x + "px"; el.style.top = y + "px";
    el.style.width = Math.max(0, w) + "px"; el.style.height = Math.max(0, h) + "px";
  }
  function tileEl(t) {
    const st = STATE[t.state];
    const el = document.createElement("div");
    el.className = "tile " + t.state;
    el.style.background = tint(t);
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", `${t.name}: ${st.label}. ${t.reading}. Open the guide.`);
    el.title = t.reading;
    el.dataset.id = t.id;
    el.innerHTML = `<div class="tile-h"><span class="tile-name"><span aria-hidden="true">${st.icon}</span> ${esc(t.name)}</span>` +
      `<span class="tile-val">${t.spark ? esc(t.spark.value) : ""}</span></div><div class="tile-plot"></div>`;
    el.addEventListener("click", () => goGuide(t.id));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goGuide(t.id); } });
    return el;
  }
  function drawSpark(te, t) {
    const pd = $(".tile-plot", te);
    const sp = t.spark;
    if (!sp || !sp.series.length) { pd.innerHTML = '<div class="tile-na">No data this run</div>'; return; }
    const tiny = pd.clientHeight < 80 || pd.clientWidth < 140;
    const traces = sp.series.map((s) => ({
      x: s.x, y: s.y, name: s.name, type: "scatter", mode: "lines", showlegend: false,
      line: s.style === "dash" ? { color: css("--text-secondary"), width: 1, dash: "dash" } : { color: css("--text-primary"), width: 1.6 },
      hovertemplate: "%{x|%b %d, %Y}: %{y}<extra>" + esc(s.name) + "</extra>",
    }));
    const ax = { showgrid: false, zeroline: false, fixedrange: true, showticklabels: !tiny,
      tickfont: { size: 9, color: css("--text-secondary") }, linecolor: "rgba(0,0,0,0)" };
    const layout = {
      margin: tiny ? { l: 2, r: 2, t: 2, b: 2 } : { l: 36, r: 4, t: 2, b: 16 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", hovermode: "x", dragmode: false,
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
      hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--axis"), font: { color: css("--text-primary"), size: 12 } },
      xaxis: Object.assign({ type: "date", nticks: 4 }, ax),
      yaxis: Object.assign({ nticks: 3 }, ax, { showgrid: !tiny, gridcolor: "rgba(127,127,127,0.22)" }),
      shapes: sp.ref === null || sp.ref === undefined ? [] : [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: sp.ref, y1: sp.ref,
        line: { color: css("--text-secondary"), width: 1, dash: "dot" } }],
    };
    Plotly.newPlot(pd, traces, layout, { displayModeBar: false, responsive: true });
  }

  function buildMap() {
    const el = $("#map");
    if ($("#tab-map").hidden) { mapDirty = true; return; }
    $$(".tile-plot", el).forEach((p) => { if (p.data) Plotly.purge(p); });
    el.innerHTML = "";
    const mobile = isMobile();
    el.classList.toggle("stacked", mobile);
    const groups = [];
    DATA.tiles.forEach((t) => {
      let g = groups.find((x) => x.name === t.group);
      if (!g) groups.push((g = { name: t.group, tiles: [], w: 0 }));
      g.tiles.push(t); g.w += t.weight;
    });
    groups.sort((a, b) => b.w - a.w);
    groups.forEach((g) => g.tiles.sort((a, b) => b.weight - a.weight));
    const made = [];
    if (mobile) {
      groups.forEach((g) => {
        const gd = document.createElement("div"); gd.className = "grp";
        gd.innerHTML = `<div class="grp-h">${esc(g.name)}</div><div class="grp-grid"></div>`;
        g.tiles.forEach((t) => { const te = tileEl(t); $(".grp-grid", gd).appendChild(te); made.push([te, t]); });
        el.appendChild(gd);
      });
    } else {
      const W = el.clientWidth, H = el.clientHeight, GAP = 3, HEAD = 20;
      squarify(groups.map((g) => ({ w: g.w, g })), 0, 0, W, H).forEach((r) => {
        const gd = document.createElement("div"); gd.className = "grp abs";
        place(gd, r.x + 2, r.y + 2, r.w - 4, r.h - 4);
        gd.innerHTML = `<div class="grp-h">${esc(r.it.g.name)}</div>`;
        el.appendChild(gd);
        squarify(r.it.g.tiles.map((t) => ({ w: t.weight, t })), 0, HEAD, r.w - 4, r.h - 4 - HEAD).forEach((q) => {
          const te = tileEl(q.it.t); te.classList.add("abs");
          place(te, q.x + GAP / 2, q.y + GAP / 2, q.w - GAP, q.h - GAP);
          gd.appendChild(te); made.push([te, q.it.t]);
        });
      });
    }
    made.forEach(([te, t]) => drawSpark(te, t));
    mapDirty = false;
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
        `<div class="split-label">${h.months} month-ends since ${h.since}: tailwind ${pct(h.tailwind)}, neutral ${pct(h.neutral)}, headwind ${pct(h.headwind)}</div>` : "";
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
        x: h.map((p) => p.date), y: h.map((p) => p.score), type: "scatter", mode: "lines",
        name: "Overall score", line: { color: css("--series-1"), width: 2 }, showlegend: false,
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

  $$("#tab-charts .controls button").forEach((b) => b.addEventListener("click", () => {
    $$("#tab-charts .controls button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    rangeYears = Number(b.dataset.range);
    redrawCharts();
  }));

  /* -------------------------------------------------------- backtest -- */
  /* Mirrors bt_scores / bt_summary in fetch/fetch_data.py. All monthly. */
  const BT = { h: 12, split: null, nb: 5, P: null, P0: null, built: false, plots: {}, raf: 0 };
  const clip1 = (x) => Math.max(-1, Math.min(1, x));
  const ymOf = (iso) => Number(iso.slice(0, 4)) * 12 + Number(iso.slice(5, 7)) - 1;
  const btIds = () => DATA.backtest.params.map((q) => q.id);

  function btDefaults() {
    const o = {};
    DATA.backtest.params.forEach((q) => { o[q.id] = { w: q.weight, c: q.center, k: q.scale }; });
    return o;
  }
  function btScoresWith(P, rows) {
    const B = DATA.backtest, ids = btIds();
    const tot = ids.reduce((s, id) => s + P[id].w, 0);
    const one = (i) => {
      let num = 0, den = 0;
      ids.forEach((id) => {
        const m = B.metrics[id][i], q = P[id];
        if (m === null || !(q.w > 0)) return;
        num += q.w * clip1((m - q.c) / q.k);
        den += q.w;
      });
      return den > 0 && den >= 0.5 * tot ? num / den : null;
    };
    if (rows) { const out = new Array(B.dates.length).fill(null); rows.forEach((i) => { out[i] = one(i); }); return out; }
    return B.dates.map((_, i) => one(i));
  }
  function mean(a) { return a.reduce((s, v) => s + v, 0) / a.length; }
  function pearson(x, y) {
    const n = x.length;
    if (n < 3) return NaN;
    const mx = mean(x), my = mean(y);
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) { const a = x[i] - mx, b = y[i] - my; sxy += a * b; sxx += a * a; syy += b * b; }
    return sxx > 0 && syy > 0 ? sxy / Math.sqrt(sxx * syy) : NaN;
  }
  function ranks(a) {
    const o = a.map((v, i) => [v, i]).sort((p, q) => p[0] - q[0]);
    const r = new Array(a.length);
    for (let i = 0; i < o.length;) {
      let j = i;
      while (j + 1 < o.length && o[j + 1][0] === o[i][0]) j++;
      for (let k = i; k <= j; k++) r[o[k][1]] = (i + j) / 2 + 1;
      i = j + 1;
    }
    return r;
  }
  /* t-stat of b in y = a + b x with Newey-West (Bartlett) standard errors. */
  function nwT(x, y, L) {
    const n = x.length;
    if (n < 10) return NaN;
    let sx = 0, sxx = 0, sy = 0, sxy = 0;
    for (let i = 0; i < n; i++) { sx += x[i]; sxx += x[i] * x[i]; sy += y[i]; sxy += x[i] * y[i]; }
    const det = n * sxx - sx * sx;
    if (!(det > 0)) return NaN;
    const inv = [[sxx / det, -sx / det], [-sx / det, n / det]];
    const b1 = (n * sxy - sx * sy) / det, b0 = (sy - b1 * sx) / n;
    const u = x.map((xi, i) => { const e = y[i] - b0 - b1 * xi; return [e, e * xi]; });
    const S = [[0, 0], [0, 0]];
    const add = (l, w) => {
      for (let t = l; t < n; t++) for (let a = 0; a < 2; a++) for (let b = 0; b < 2; b++) {
        const g = u[t][a] * u[t - l][b];
        S[a][b] += l === 0 ? g : w * g;
        if (l > 0) S[b][a] += w * g;
      }
    };
    add(0, 1);
    for (let l = 1; l <= L; l++) add(l, 1 - l / (L + 1));
    const m = (A, B) => [[A[0][0] * B[0][0] + A[0][1] * B[1][0], A[0][0] * B[0][1] + A[0][1] * B[1][1]],
      [A[1][0] * B[0][0] + A[1][1] * B[1][0], A[1][0] * B[0][1] + A[1][1] * B[1][1]]];
    const V = m(m(inv, S), inv);
    return V[1][1] > 0 ? b1 / Math.sqrt(V[1][1]) : NaN;
  }

  function btRows(sc, h) {
    const f = DATA.backtest["fwd" + h];
    return sc.map((v, i) => i).filter((i) => sc[i] !== null && f[i] !== null);
  }
  const splitYm = () => BT.split * 12;
  function trainRows(idx, h) { return idx.filter((i) => ymOf(DATA.backtest.dates[i]) + h < splitYm()); }
  function testRows(idx) { return idx.filter((i) => ymOf(DATA.backtest.dates[i]) >= splitYm()); }

  /* Weight each signal by its own (positive) correlation with forward returns on `rows`. */
  function icWeights(P, rows, h) {
    const B = DATA.backtest, f = B["fwd" + h], w = {};
    btIds().forEach((id) => {
      const q = P[id], xs = [], ys = [];
      rows.forEach((i) => {
        const m = B.metrics[id][i];
        if (m !== null && f[i] !== null) { xs.push(clip1((m - q.c) / q.k)); ys.push(f[i]); }
      });
      const c = xs.length >= 24 ? pearson(xs, ys) : NaN;
      w[id] = isFinite(c) ? Math.max(0, c) : 0;
    });
    const tot = Object.values(w).reduce((s, v) => s + v, 0);
    const out = {};
    btIds().forEach((id) => { out[id] = { w: tot ? (w[id] / tot) * 100 : 100 / btIds().length, c: P[id].c, k: P[id].k }; });
    return out;
  }
  /* Expanding window: refit each January on months whose forward window had already closed. */
  function walkForward(P, h, idx) {
    const B = DATA.backtest, out = new Array(B.dates.length).fill(null);
    let cur = null;
    B.dates.forEach((d, i) => {
      if (d.slice(5, 7) === "01" || cur === null) {
        const ym = ymOf(d);
        const rows = idx.filter((j) => ymOf(B.dates[j]) + h <= ym);
        cur = rows.length >= 60 ? icWeights(P, rows, h) : null;
      }
      if (cur) out[i] = btScoresWith(cur, [i])[i];
    });
    return out;
  }

  function btStats(sc, h, idx) {
    const f = DATA.backtest["fwd" + h];
    const rows = idx.filter((i) => sc[i] !== null);
    const xs = rows.map((i) => sc[i]), ys = rows.map((i) => f[i]);
    const sub = (ii) => { const r = ii.filter((i) => sc[i] !== null); return r.length > 5 ? pearson(r.map((i) => sc[i]), r.map((i) => f[i])) : NaN; };
    const non = rows.filter((_, k) => k % h === 0);
    return {
      n: rows.length, corr: pearson(xs, ys), ric: pearson(ranks(xs), ranks(ys)), t: nwT(xs, ys, h - 1),
      non: sub(non), nonN: non.length, train: sub(trainRows(rows, h)), test: sub(testRows(rows)),
    };
  }

  function sameP(a, b) { return btIds().every((id) => a[id].w === b[id].w && a[id].c === b[id].c && a[id].k === b[id].k); }
  const f2 = (v) => (isFinite(v) ? signed(v, 2) : "–");

  function btCompute() {
    const h = BT.h;
    const s0 = btScoresWith(BT.P0), s1 = btScoresWith(BT.P);
    const idx0 = btRows(s0, h), idx1 = btRows(s1, h);
    const fitP = icWeights(BT.P, trainRows(idx1, h), h);
    const sf = btScoresWith(fitP), wf = walkForward(BT.P, h, idx1);
    return {
      s0, s1, idx1, fitP, wf,
      rows: [
        { name: "Default settings", st: btStats(s0, h, idx0) },
        { name: "Your settings", st: btStats(s1, h, idx1), mine: true },
        { name: "Weights fitted before the split", st: btStats(sf, h, idx1), fit: true },
        { name: "Walk-forward (refit each January)", st: btStats(wf, h, idx1), wf: true },
      ],
    };
  }

  function btTable(R) {
    const yr = BT.split;
    const head = `<tr><th>Score</th><th>Corr.</th><th>Rank IC</th><th>Newey-West t</th><th>Non-overlapping corr. (n)</th>` +
      `<th>Before ${yr}</th><th>${yr} on</th></tr>`;
    const body = R.rows.map((r) => {
      const s = r.st;
      const tr = r.wf ? '<td class="na">n/a</td>' : `<td${r.fit ? ' class="ins"' : ""}>${f2(s.train)}</td>`;
      return `<tr${r.mine ? ' class="mine"' : ""}><td>${esc(r.name)}</td><td>${f2(s.corr)}</td><td>${f2(s.ric)}</td>` +
        `<td>${isFinite(s.t) ? signed(s.t, 1) : "–"}</td><td>${f2(s.non)} (${s.nonN})</td>${tr}<td class="oos">${f2(s.test)}</td></tr>`;
    }).join("");
    return `<div class="table-wrap"><table class="datatable bt-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  }

  function btLead(R) {
    const d = R.rows[0].st, B = DATA.backtest;
    if (!R.idx1.length) return '<p class="bt-lead">Your settings leave too little weight to score any month. Raise some weights or press Reset.</p>';
    const first = B.dates[R.idx1[0]], last = B.dates[R.idx1[R.idx1.length - 1]];
    const mo = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { year: "numeric", month: "short" });
    return `<p class="bt-lead">With the default settings, the score's correlation with the next ${BT.h} months of returns is ` +
      `<b>${f2(d.train)}</b> before ${BT.split} and <b>${f2(d.test)}</b> from ${BT.split} on (${f2(d.corr)} over the whole sample).</p>` +
      `<p class="caveat">${esc(B.ret_name)}, month-ends ${mo(first)} to ${mo(last)}, ${R.rows[1].st.n} months. ` +
      `Overlapping ${BT.h}-month windows leave only about ${Math.round(R.rows[1].st.n / BT.h)} independent observations, ` +
      `so a correlation needs to be roughly ±${(2 / Math.sqrt(Math.max(4, R.rows[1].st.n / BT.h))).toFixed(2)} or larger to stand out from noise.</p>`;
  }

  function btLayoutBase() {
    return {
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: css("--text-secondary") },
      hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--axis"), font: { color: css("--text-primary") } },
      legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom" }, shapes: [], annotations: [],
    };
  }
  const ax = (o) => Object.assign({ gridcolor: css("--grid"), linecolor: css("--axis"), zeroline: false }, o || {});

  function btTimePlot(R) {
    const B = DATA.backtest, f = B["fwd" + BT.h], same = sameP(BT.P, BT.P0);
    const tr = [];
    if (!same) tr.push({ x: B.dates, y: R.s0, name: "Default score", type: "scatter", mode: "lines",
      line: { color: css("--text-muted"), width: 1.2 }, hovertemplate: "%{y:+.2f}<extra>Default</extra>" });
    tr.push({ x: B.dates, y: R.s1, name: same ? "Score" : "Your score", type: "scatter", mode: "lines",
      line: { color: css("--series-1"), width: 2 }, hovertemplate: "%{y:+.2f}<extra>Score</extra>" });
    tr.push({ x: B.dates, y: f, name: `Next ${BT.h} months' return`, type: "scatter", mode: "lines", yaxis: "y2",
      line: { color: css("--series-2"), width: 1.6 }, hovertemplate: "%{y:.1f}%<extra>Next " + BT.h + "m</extra>" });
    const L = btLayoutBase();
    Object.assign(L, {
      margin: { l: 52, r: 12, t: 28, b: 28 }, hovermode: "x unified",
      xaxis: ax({ type: "date" }),
      yaxis: ax({ domain: [0.54, 1], range: [-1, 1], title: { text: "Score", font: { size: 11 } } }),
      yaxis2: ax({ domain: [0, 0.44], anchor: "x", ticksuffix: "%", title: { text: "Fwd return", font: { size: 11 } } }),
    });
    L.shapes.push({ type: "rect", xref: "x", yref: "paper", x0: BT.split + "-01-01", x1: B.dates[B.dates.length - 1], y0: 0, y1: 1,
      fillcolor: css("--text-muted"), opacity: 0.1, line: { width: 0 }, layer: "below" });
    [["y", 0], ["y2", 0]].forEach(([y]) => L.shapes.push({ type: "line", xref: "paper", yref: y, x0: 0, x1: 1, y0: 0, y1: 0, line: { color: css("--axis"), width: 1 } }));
    L.annotations.push({ xref: "x", yref: "paper", x: BT.split + "-01-01", y: 1, text: " Test period →", showarrow: false,
      xanchor: "left", yanchor: "top", font: { size: 11, color: css("--text-muted") } });
    return { tr, L };
  }

  function btScatter(R) {
    const B = DATA.backtest, f = B["fwd" + BT.h], sc = R.s1;
    const train = new Set(trainRows(R.idx1, BT.h)), test = new Set(testRows(R.idx1));
    const grp = (name, keep, color) => {
      const ii = R.idx1.filter(keep);
      return { x: ii.map((i) => sc[i]), y: ii.map((i) => f[i]), text: ii.map((i) => B.dates[i].slice(0, 7)), name, type: "scatter", mode: "markers",
        marker: { color, size: 6, opacity: 0.75 }, hovertemplate: "%{text}: score %{x:+.2f}, next " + BT.h + "m %{y:.1f}%<extra></extra>" };
    };
    const tr = [grp(`Before ${BT.split}`, (i) => train.has(i), css("--series-1")),
      grp(`${BT.split} on`, (i) => test.has(i), css("--series-2"))];
    const gap = R.idx1.filter((i) => !train.has(i) && !test.has(i));
    if (gap.length) tr.push(Object.assign(grp("Gap (window spans the split)", (i) => !train.has(i) && !test.has(i), css("--text-muted")), {}));
    const xs = R.idx1.map((i) => sc[i]), ys = R.idx1.map((i) => f[i]);
    if (xs.length > 3) {
      const mx = mean(xs), my = mean(ys);
      let sxy = 0, sxx = 0;
      xs.forEach((x, k) => { sxy += (x - mx) * (ys[k] - my); sxx += (x - mx) ** 2; });
      const b = sxx ? sxy / sxx : 0, lo = Math.min(...xs), hi = Math.max(...xs);
      tr.push({ x: [lo, hi], y: [my + b * (lo - mx), my + b * (hi - mx)], name: "Fit, whole sample", type: "scatter", mode: "lines",
        line: { color: css("--text-primary"), width: 1.5, dash: "dash" }, hoverinfo: "skip" });
    }
    const L = btLayoutBase();
    Object.assign(L, { margin: { l: 52, r: 12, t: 28, b: 40 }, hovermode: "closest",
      xaxis: ax({ title: { text: "Score at month-end", font: { size: 11 } }, range: [-1.05, 1.05] }),
      yaxis: ax({ ticksuffix: "%", title: { text: `Next ${BT.h} months`, font: { size: 11 } } }) });
    return { tr, L };
  }

  function bucketMeans(ii, sc, f, nb) {
    const o = ii.slice().sort((a, b) => sc[a] - sc[b]);
    const out = [];
    for (let b = 0; b < nb; b++) {
      const part = o.slice(Math.floor((b * o.length) / nb), Math.floor(((b + 1) * o.length) / nb));
      out.push(part.length ? { m: mean(part.map((i) => f[i])), n: part.length, lo: sc[part[0]], hi: sc[part[part.length - 1]] } : null);
    }
    return out;
  }
  function btBuckets(R) {
    const f = DATA.backtest["fwd" + BT.h], sc = R.s1, nb = BT.nb;
    const labs = nb === 3 ? ["Low third", "Middle third", "High third"] : ["Lowest fifth", "2nd", "3rd", "4th", "Highest fifth"];
    const mk = (name, ii, color) => {
      const bm = bucketMeans(ii, sc, f, nb);
      return { x: labs, y: bm.map((b) => (b ? b.m : null)), name, type: "bar", marker: { color },
        customdata: bm.map((b) => (b ? [b.n, b.lo, b.hi] : [0, 0, 0])),
        hovertemplate: "%{y:.1f}% avg, %{customdata[0]} months, scores %{customdata[1]:+.2f} to %{customdata[2]:+.2f}<extra>" + name + "</extra>" };
    };
    const tr = [mk("Whole sample", R.idx1, css("--series-1")), mk(`${BT.split} on`, testRows(R.idx1), css("--series-2"))];
    const L = btLayoutBase();
    Object.assign(L, { margin: { l: 52, r: 12, t: 28, b: 40 }, barmode: "group", hovermode: "closest",
      xaxis: ax({ title: { text: "Months grouped by score", font: { size: 11 } } }),
      yaxis: ax({ ticksuffix: "%", title: { text: `Avg next ${BT.h} months`, font: { size: 11 } } }) });
    if (R.idx1.length) {
      const m = mean(R.idx1.map((i) => f[i]));
      L.shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: m, y1: m, line: { color: css("--text-muted"), width: 1, dash: "dot" } });
    }
    return { tr, L };
  }

  function btDraw() {
    const R = btCompute();
    BT.last = R;
    $("#bt-lead").innerHTML = btLead(R);
    $("#bt-stats").innerHTML = btTable(R);
    const cfg = { displayModeBar: false, responsive: true };
    [["ts", btTimePlot], ["sc", btScatter], ["bk", btBuckets]].forEach(([k, fn]) => {
      const { tr, L } = fn(R);
      const el = $("#bt-" + k);
      if (BT.plots[k]) Plotly.react(el, tr, L, cfg); else { Plotly.newPlot(el, tr, L, cfg); BT.plots[k] = true; }
    });
    $("#bt-fitnote").textContent = "Weights fitted before " + BT.split + ": " +
      btIds().map((id) => [id, R.fitP[id].w]).filter((p) => p[1] >= 0.5).sort((a, b) => b[1] - a[1])
        .map(([id, w]) => DATA.backtest.params.find((q) => q.id === id).name + " " + Math.round(w)).join(", ") + ".";
  }
  function btSchedule() {
    if (BT.raf) return;
    BT.raf = requestAnimationFrame(() => { BT.raf = 0; btDraw(); });
  }

  function stepFor(lo, hi) {
    const s = (hi - lo) / 100;
    const p = Math.pow(10, Math.floor(Math.log10(s)));
    return [1, 2, 5, 10].map((m) => m * p).find((v) => v >= s);
  }
  const decs = (step) => Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  function unitTxt(v, u, dg) {
    const n = Number(v).toFixed(dg);
    return u === "%" ? n + "%" : u === "bp" ? n + " bp" : u === "pts" ? n + " pts" : u === "sd" ? n + " sd" : u === "days" ? n + " days" : n;
  }
  function btSliders() {
    const root = $("#bt-sliders");
    root.innerHTML = "";
    DATA.backtest.params.forEach((q) => {
      const cs = stepFor(q.center_range[0], q.center_range[1]), ks = stepFor(q.scale_range[0], q.scale_range[1]);
      const dir = q.scale > 0 ? "higher = tailwind" : "higher = headwind";
      const el = document.createElement("div");
      el.className = "sl"; el.dataset.id = q.id;
      el.innerHTML = `<div class="sl-h"><a href="#g-${q.id}" data-guide="${q.id}">${esc(q.name)}</a>` +
        `<span>${q.first ? "data from " + q.first.slice(0, 4) : "no history"} · ${dir}</span></div>` +
        `<label><span>Weight</span><input type="range" data-k="w" min="0" max="30" step="1"><output></output></label>` +
        `<label><span>Neutral at</span><input type="range" data-k="c" min="${q.center_range[0]}" max="${q.center_range[1]}" step="${cs}"><output></output></label>` +
        `<label><span>Full score at ±</span><input type="range" data-k="k" min="${q.scale_range[0]}" max="${q.scale_range[1]}" step="${ks}"><output></output></label>`;
      $$("input", el).forEach((inp) => {
        inp.setAttribute("aria-label", q.name + " " + inp.previousElementSibling.textContent.toLowerCase());
        inp.addEventListener("input", () => {
          const v = Number(inp.value), p = BT.P[q.id];
          if (inp.dataset.k === "w") p.w = v;
          else if (inp.dataset.k === "c") p.c = v;
          else p.k = Math.sign(q.scale) * v;
          btSyncOne(q.id);
          btSchedule();
        });
      });
      root.appendChild(el);
    });
    $$("#bt-sliders [data-guide]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); goGuide(a.dataset.guide); }));
    btSyncAll();
  }
  function btSyncOne(id) {
    const q = DATA.backtest.params.find((x) => x.id === id), p = BT.P[id], el = $(`#bt-sliders .sl[data-id="${id}"]`);
    if (!el) return;
    const cs = stepFor(q.center_range[0], q.center_range[1]), ks = stepFor(q.scale_range[0], q.scale_range[1]);
    const set = (k, v, txt) => { const i = $(`input[data-k="${k}"]`, el); i.value = v; i.nextElementSibling.textContent = txt; };
    set("w", p.w, String(Math.round(p.w)));
    set("c", p.c, unitTxt(p.c, q.unit, decs(cs)));
    set("k", Math.abs(p.k), unitTxt(Math.abs(p.k), q.unit, decs(ks)) + " away");
    const dflt = BT.P0[id];
    el.classList.toggle("changed", p.w !== dflt.w || p.c !== dflt.c || p.k !== dflt.k);
  }
  function btSyncAll() { btIds().forEach(btSyncOne); }

  function buildBacktest() {
    if (BT.built) { btDraw(); return; }
    const B = DATA.backtest;
    if (!B || !B.dates || !B.params) { $("#bt-lead").textContent = "No backtest data in this build."; return; }
    BT.built = true;
    BT.P0 = btDefaults();
    BT.P = btDefaults();
    const sm = B.summary && (B.summary["12"] || B.summary["6"]);
    BT.split = sm ? Number(sm.split.slice(0, 4)) : 2013;
    const sp = $("#bt-split");
    sp.value = BT.split; $("#bt-split-out").textContent = BT.split;
    sp.addEventListener("input", () => { BT.split = Number(sp.value); $("#bt-split-out").textContent = BT.split; btSchedule(); });
    $$("[data-h]").forEach((b) => b.addEventListener("click", () => {
      BT.h = Number(b.dataset.h);
      $$("[data-h]").forEach((x) => x.classList.toggle("on", x === b));
      btSchedule();
    }));
    $$("[data-nb]").forEach((b) => b.addEventListener("click", () => {
      BT.nb = Number(b.dataset.nb);
      $$("[data-nb]").forEach((x) => x.classList.toggle("on", x === b));
      btSchedule();
    }));
    $("#bt-reset").addEventListener("click", () => { BT.P = btDefaults(); btSyncAll(); btSchedule(); });
    $("#bt-fit").addEventListener("click", () => {
      const fit = BT.last ? BT.last.fitP : null;
      if (!fit) return;
      btIds().forEach((id) => { BT.P[id].w = Math.round(fit[id].w); });
      btSyncAll(); btSchedule();
    });
    btSliders();
    btDraw();
  }

  /* ------------------------------------------------------------ boot -- */
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { setBarHeight(); sizeMap(); if (DATA) buildMap(); }, 150);
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (DATA) { buildMap(); redrawCharts(); if (BT.built) btDraw(); } });
  }

  function route() {
    const h = (location.hash || "").slice(1);
    if (h.startsWith("g-")) goGuide(h.slice(2));
    else if (["charts", "backtest", "guide", "about"].includes(h)) showTab(h, { keepHash: true });
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
    })
    .catch((e) => {
      $("#updated").textContent = "Could not load data (" + e.message + ").";
      $("#stance").innerHTML = '<div class="verdict">Data unavailable</div>';
    });
  window.addEventListener("hashchange", () => { if (DATA) route(); });
})();
