/* Macro checklist dashboard: renders site/data/dashboard.json with Plotly. */
(function () {
  "use strict";

  const STATE_LABEL = {
    supportive: "Supportive", mixed: "Mixed", headwind: "Headwind",
    context: "Context", unavailable: "No data",
  };
  const STATE_ICON = { supportive: "✓", mixed: "~", headwind: "✕", context: "i", unavailable: "?" };
  const plots = []; // {el, traces, layoutFn}
  let DATA = null;
  let rangeYears = 5;

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function chip(sig, prefix) {
    const st = sig ? sig.state : "unavailable";
    return `<span class="chip ${st}${prefix ? " chip-small" : ""}" title="${esc(sig ? sig.text : "")}">` +
      `<span class="dot" aria-hidden="true"></span><span class="ico" aria-hidden="true">${STATE_ICON[st]}</span>` +
      `${prefix ? esc(prefix) + " " : ""}${STATE_LABEL[st]}</span>`;
  }

  function fmt(v, digits, unit) {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    const n = Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    if (unit === "%") return n + "%";
    if (unit === "pts") return (v > 0 ? "+" : "") + n + " pts";
    if (unit === "$") return "$" + n;
    if (unit === "$/lb") return "$" + n + "/lb";
    return n;
  }
  function fmtDate(iso) {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
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

  function traceFor(s, i, n) {
    const colors = [css("--series-1"), css("--series-2"), css("--series-3")];
    const ref = s.style === "dash";
    const color = ref ? css("--text-muted") : colors[i % 3];
    return {
      x: s.x, y: s.y, name: s.name, type: "scatter", mode: "lines",
      line: { color, width: ref ? 1.5 : 2, dash: ref ? "dash" : s.style === "dot" ? "dot" : "solid" },
      hovertemplate: "%{y}<extra>" + esc(s.name) + "</extra>",
      showlegend: n > 1,
    };
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
      if (v < lo) lo = v; if (v > hi) hi = v;
    }));
    if (!isFinite(lo)) return undefined;
    const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.05 || 1;
    return [lo - pad, hi + pad];
  }

  function layoutFor(traces, refs, unit) {
    const start = rangeStart();
    const today = new Date().toISOString().slice(0, 10);
    const yr = yRangeFor(traces, start);
    const shapes = (refs || []).map((r) => ({
      type: "line", xref: "paper", x0: 0, x1: 1, y0: r.y, y1: r.y,
      line: { color: css("--axis"), width: 1, dash: r.label ? "dash" : "solid" },
    }));
    const annotations = (refs || []).filter((r) => r.label).map((r) => ({
      xref: "paper", x: 0, y: r.y, text: r.label, showarrow: false, xanchor: "left", yanchor: "bottom",
      font: { size: 11, color: css("--text-muted") },
    }));
    return {
      margin: { l: 48, r: 12, t: 8, b: 28 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: css("--text-secondary") },
      hovermode: "x unified",
      hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--axis"), font: { color: css("--text-primary") } },
      xaxis: {
        type: "date", range: start ? [start, today] : undefined, autorange: !start,
        gridcolor: css("--grid"), linecolor: css("--axis"), tickcolor: css("--axis"), zeroline: false,
        showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: css("--text-muted"), spikedash: "solid",
      },
      yaxis: {
        range: yr, autorange: yr ? false : true, gridcolor: css("--grid"), zeroline: false,
        ticksuffix: unit === "%" ? "%" : "", linecolor: css("--axis"),
      },
      legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 12, color: css("--text-secondary") } },
      shapes, annotations,
      showlegend: traces.length > 1,
    };
  }

  function addPlot(container, series, refs, unit, small) {
    if (!series || !series.length) {
      container.insertAdjacentHTML("beforeend", '<div class="unavail">Data unavailable this run.</div>');
      return;
    }
    const el = document.createElement("div");
    el.className = "plot" + (small ? " small" : "");
    container.appendChild(el);
    const build = () => {
      const traces = series.map((s, i) => traceFor(s, i, series.length));
      const lay = layoutFor(traces, refs, unit);
      if (series.length > 1) lay.margin.t = 28;
      return { traces, lay };
    };
    const { traces, lay } = build();
    Plotly.newPlot(el, traces, lay, { displayModeBar: false, responsive: true });
    plots.push({ el, build });
  }

  function tableHTML(seriesList) {
    const rows = [];
    seriesList.forEach((s) => {
      const n = s.x.length;
      for (let k = Math.max(0, n - 6); k < n; k++) rows.push([s.name, s.x[k], s.y[k]]);
    });
    if (!rows.length) return "";
    return '<details><summary>Show recent values</summary><div class="table-wrap"><table class="datatable">' +
      "<thead><tr><th>Series</th><th>Date</th><th>Value</th></tr></thead><tbody>" +
      rows.map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${r[2] === null ? "–" : r[2]}</td></tr>`).join("") +
      "</tbody></table></div></details>";
  }

  function renderScore(panels) {
    const scored = panels.filter((p) => p.scored);
    const count = (key, st) => scored.filter((p) => p[key] && p[key].state === st).length;
    const nowS = count("signal_now", "supportive"), nowH = count("signal_now", "headwind");
    const agoS = count("signal_ago", "supportive"), agoH = count("signal_ago", "headwind");
    const rows = scored.map((p) =>
      `<tr><td><a href="#${p.id}">${esc(p.title)}</a></td><td>${chip(p.signal_now)}</td>` +
      `<td>${chip(p.signal_ago)}</td><td class="why">${esc(p.signal_now ? p.signal_now.text : "")}</td></tr>`).join("");
    document.getElementById("score").innerHTML =
      '<div class="score-head">' +
      `<div><div class="score-big">${nowS} / ${scored.length}</div><div class="score-label">supportive now, ${nowH} headwinds</div></div>` +
      `<div><div class="score-big" style="color:var(--text-secondary)">${agoS} / ${scored.length}</div><div class="score-label">supportive a year ago, ${agoH} headwinds</div></div>` +
      "</div>" +
      '<table class="score-table"><thead><tr><th>Signal</th><th>Now</th><th>Year ago</th><th class="why">Reading</th></tr></thead>' +
      `<tbody>${rows}</tbody></table>`;
  }

  function renderPanels(panels) {
    const root = document.getElementById("panels");
    root.innerHTML = "";
    panels.forEach((p) => {
      const card = document.createElement("article");
      card.className = "card" + (p.subplots && p.subplots.length > 2 ? " wide" : "");
      card.id = p.id;
      const chips = p.scored
        ? `<div class="chips">${chip(p.signal_now, "Now")}${chip(p.signal_ago, "Year ago")}</div>`
        : `<div class="chips">${chip(p.signal_now)}</div>`;
      card.innerHTML =
        `<div class="card-head"><div><div class="group">${esc(p.group)}</div><h2>${esc(p.title)}</h2></div>${chips}</div>` +
        `<p class="read"><b>His read:</b> ${esc(p.read)}</p>` +
        (p.scored && p.signal_now ? `<p class="sigtext">${esc(p.signal_now.text)}</p>` : "") +
        `<div class="stats">${(p.stats || []).map(statHTML).join("")}</div>`;
      root.appendChild(card);

      let allSeries = [];
      if (p.subplots) {
        p.subplots.forEach((sp) => {
          card.insertAdjacentHTML("beforeend", `<p class="sub-title">${esc(sp.title)}</p>`);
          addPlot(card, sp.series, sp.refs, sp.unit, true);
          allSeries = allSeries.concat(sp.series);
        });
      } else {
        addPlot(card, p.series, p.refs, p.unit, false);
        allSeries = p.series || [];
      }
      if (p.sep && p.sep.length) {
        card.insertAdjacentHTML("beforeend",
          `<p class="sep">FOMC median dots (latest Summary of Economic Projections): ` +
          p.sep.map((d) => `${esc(d.year)}: ${Number(d.median).toFixed(2)}%`).join(" · ") + "</p>");
      }
      card.insertAdjacentHTML("beforeend",
        (p.rule ? `<p class="meta">Rule: ${esc(p.rule)}</p>` : "") +
        (p.note ? `<p class="meta">${esc(p.note)}</p>` : "") +
        `<p class="meta">Source: ${esc(p.source)}</p>` + tableHTML(allSeries));
    });
  }

  function redrawAll() {
    plots.forEach(({ el, build }) => {
      const { traces, lay } = build();
      Plotly.react(el, traces, lay, { displayModeBar: false, responsive: true });
    });
  }

  document.querySelectorAll(".controls button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".controls button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      rangeYears = Number(b.dataset.range);
      redrawAll();
    });
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawAll);
  }

  fetch("data/dashboard.json", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((d) => {
      DATA = d;
      const gen = new Date(d.generated);
      document.getElementById("updated").textContent =
        "Data refreshed " + gen.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
      renderScore(d.panels);
      renderPanels(d.panels);
      if (d.errors && d.errors.length) {
        document.getElementById("errors").textContent =
          "Series that failed on the last refresh: " + d.errors.join("; ");
      }
    })
    .catch((e) => {
      document.getElementById("updated").textContent = "Could not load data (" + e.message + ").";
    });
})();
