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
    })
    .catch((e) => {
      $("#updated").textContent = "Could not load data (" + e.message + ").";
      $("#stance").innerHTML = '<div class="verdict">Data unavailable</div>';
    });
  window.addEventListener("hashchange", () => { if (DATA) route(); });
})();
