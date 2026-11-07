(function () {
  "use strict";
  var root = document.getElementById("dash");
  var form = document.getElementById("dash-filters");
  if (!root || !form || !window.Chart) return;

  var overviewUrl = root.dataset.overviewUrl;
  var liveUrl = root.dataset.liveUrl || "";
  var inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
  var PALETTE = ["#3437c9", "#0e9f92", "#ffc845", "#e5484d", "#8b90ff", "#5b6182", "#ff8a3d", "#2fb8e6"];
  var SPECS = {
    revenue: { type: "line", fmt: "money" }, bookings: { type: "bar", fmt: "int" }, overtime: { type: "bar", fmt: "money", stacked: true },
    occupancy: { type: "line", fmt: "pct", max: 100 }, peak_hours: { type: "bar", fmt: "int", label: "Arrivals" },
    status: { type: "doughnut" }, vehicle: { type: "doughnut" },
    parking_revenue: { type: "bar", fmt: "money", horizontal: true, label: "Net earnings" },
    rating: { type: "line", fmt: "rating", max: 5, min: 0 },
    signups: { type: "bar", fmt: "int", stacked: true }, refunds: { type: "bar", fmt: "money" },
    cities: { type: "bar", fmt: "int", horizontal: true, label: "Bookings" },
    top_parkings: { type: "bar", fmt: "money", horizontal: true, label: "Net earnings" },
    top_owners: { type: "bar", fmt: "money", horizontal: true, label: "Net earnings" },
  };
  var charts = {}, last = null, seq = 0, timer;

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function applyTheme() {
    Chart.defaults.color = css("--ps-muted") || "#5b6182";
    Chart.defaults.borderColor = css("--ps-border") || "#e2e5f3";
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  }
  function fmt(kind, v) {
    if (v === null || v === undefined) return "-";
    if (kind === "money") return "₹" + inr.format(v);
    if (kind === "pct") return inr.format(v) + "%";
    if (kind === "rating") return Number(v).toFixed(1);
    return inr.format(v);
  }
  function alpha(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }
  function isEmpty(spec, d) {
    var lists = spec.type === "doughnut" ? [d.data || []] : d.series ? d.series.map(function (s) { return s.data; }) : [d.data || []];
    return !lists.some(function (l) { return l.some(function (v) { return v; }); });
  }

  function render(id, d) {
    var canvas = document.getElementById("ch-" + id);
    var spec = SPECS[id];
    if (!canvas || !spec || !d) return;
    var empty = canvas.parentNode.querySelector(".chart-empty");
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    var none = isEmpty(spec, d);
    empty.hidden = !none;
    canvas.style.visibility = none ? "hidden" : "visible";
    if (none) return;

    var datasets;
    if (spec.type === "doughnut") {
      datasets = [{ data: d.data, backgroundColor: PALETTE, borderWidth: 0 }];
    } else if (d.series) {
      datasets = d.series.map(function (s, i) {
        var c = PALETTE[i % PALETTE.length];
        return { label: s.name, data: s.data, borderColor: c, backgroundColor: spec.type === "line" ? alpha(c, 0.12) : c,
                 fill: spec.type === "line" && i === 0, tension: 0.3, pointRadius: d.labels.length > 40 ? 0 : 3,
                 spanGaps: true, borderRadius: 6, maxBarThickness: 36 };
      });
    } else {
      datasets = [{ label: spec.label || "", data: d.data, backgroundColor: PALETTE[0], borderRadius: 6, maxBarThickness: 36 }];
    }
    var opts = {
      responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
      indexAxis: spec.horizontal ? "y" : "x",
      plugins: {
        legend: { display: spec.type === "doughnut" || (d.series && d.series.length > 1), position: "bottom", labels: { boxWidth: 12, usePointStyle: true } },
        tooltip: { callbacks: { label: function (ctx) { var v = ctx.parsed.y !== undefined && !spec.horizontal ? ctx.parsed.y : (spec.horizontal ? ctx.parsed.x : ctx.parsed);
          return (ctx.dataset.label ? ctx.dataset.label + ": " : ctx.label + ": ") + fmt(spec.fmt, v); } } },
      },
    };
    if (spec.type !== "doughnut") {
      var valueAxis = { beginAtZero: true, stacked: !!spec.stacked, ticks: { callback: function (v) { return spec.fmt === "money" ? "₹" + inr.format(v) : spec.fmt === "pct" ? v + "%" : v; } } };
      if (spec.max) valueAxis.max = spec.max;
      if (spec.min !== undefined) valueAxis.min = spec.min;
      var catAxis = { stacked: !!spec.stacked, grid: { display: false }, ticks: { autoSkip: true, maxRotation: 0 } };
      opts.scales = spec.horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis };
    } else {
      opts.cutout = "62%";
    }
    charts[id] = new Chart(canvas, { type: spec.type === "doughnut" ? "doughnut" : spec.type, data: { labels: d.labels, datasets: datasets }, options: opts });
  }

  function renderKpis(list) {
    var box = document.getElementById("kpis");
    box.innerHTML = "";
    list.forEach(function (k) {
      var col = document.createElement("div"); col.className = "col-6 col-lg-4 col-xl-3";
      var card = document.createElement("div"); card.className = "stat";
      var label = document.createElement("div"); label.className = "label"; label.textContent = k.label;
      var value = document.createElement("div"); value.className = "value";
      value.textContent = k.format === "text" ? k.value : fmt(k.format, k.value);
      card.appendChild(label); card.appendChild(value);
      if (k.delta !== null && k.delta !== undefined) {
        var d = document.createElement("div"); d.className = "small fw-semibold " + (k.delta >= 0 ? "text-success" : "text-danger");
        d.textContent = (k.delta >= 0 ? "▲ " : "▼ ") + Math.abs(k.delta) + "% vs previous period";
        card.appendChild(d);
      } else if (k.hint) {
        var h = document.createElement("div"); h.className = "small text-muted"; h.textContent = k.hint; card.appendChild(h);
      }
      col.appendChild(card); box.appendChild(col);
    });
  }

  // ---- filters
  function syncVisibility() {
    var mode = form.querySelector("input[name=mode]:checked").value;
    form.querySelectorAll("[data-mode-show]").forEach(function (el) {
      el.hidden = el.dataset.modeShow.split(" ").indexOf(mode) === -1;
    });
    return mode;
  }
  function query() {
    var mode = syncVisibility(), q = new URLSearchParams({ mode: mode });
    function put(name) { var el = form.elements[name]; if (el && el.value) q.set(name, el.value); }
    if (mode === "year" || mode === "month") put("year");
    if (mode === "month") put("month");
    if (mode === "week") put("date");
    if (mode === "custom") { put("start"); put("end"); }
    put("group"); put("parking");
    return q.toString();
  }
  function updateExports(qs) {
    form.querySelectorAll("a[data-export]").forEach(function (a) { a.href = a.dataset.base + "?" + qs; });
  }

  function load() {
    var qs = query(), mine = ++seq;
    var s = form.elements.start, e = form.elements.end;
    if (form.querySelector("input[name=mode]:checked").value === "custom" && (!s.value || !e.value || e.value < s.value)) return;
    root.classList.add("is-loading");
    document.getElementById("dash-error").hidden = true;
    history.replaceState(null, "", "?" + qs);
    updateExports(qs);
    fetch(overviewUrl + "?" + qs, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        if (mine !== seq) return;
        last = data;
        renderKpis(data.kpis);
        Object.keys(data.charts).forEach(function (id) { render(id, data.charts[id]); });
        document.getElementById("dash-period-label").textContent = data.meta.label + "  ·  by " + data.meta.group;
        root.classList.remove("is-loading");
      })
      .catch(function () {
        if (mine !== seq) return;
        root.classList.remove("is-loading");
        document.getElementById("dash-error").hidden = false;
      });
  }
  function scheduleLoad() { clearTimeout(timer); timer = setTimeout(load, 250); }

  // ---- "right now" lists (owner)
  var LIVE_TEXT = {
    upcoming: function (r) { return [r.time + "  ·  " + r.plate, r.driver + ", " + r.parking]; },
    overstaying: function (r) { return [r.plate + "  ·  " + r.minutes + " min over", r.driver + ", " + r.parking]; },
    refunds: function (r) { return [r.plate + "  ·  ₹" + inr.format(r.amount), r.driver + ", " + r.parking]; },
    recent: function (r) { return [r.plate + "  ·  " + r.status, r.driver + ", ₹" + inr.format(r.amount)]; },
  };
  function loadLive() {
    if (!liveUrl) return;
    fetch(liveUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        document.querySelectorAll("[data-live]").forEach(function (box) {
          var rows = data[box.dataset.live] || [];
          box.innerHTML = "";
          if (!rows.length) { var p = document.createElement("div"); p.className = "text-muted small py-2"; p.textContent = box.dataset.empty; box.appendChild(p); return; }
          rows.forEach(function (r) {
            var t = LIVE_TEXT[box.dataset.live](r);
            var a = document.createElement("a"); a.href = r.url; a.className = "live-row";
            var main = document.createElement("div"); main.className = "fw-semibold small"; main.textContent = t[0];
            var sub = document.createElement("div"); sub.className = "small text-muted"; sub.textContent = t[1];
            a.appendChild(main); a.appendChild(sub); box.appendChild(a);
          });
        });
      }).catch(function () {});
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); load(); });
  form.addEventListener("change", function () { syncVisibility(); scheduleLoad(); });
  document.getElementById("dash-retry").addEventListener("click", load);
  new MutationObserver(function () { applyTheme(); if (last) Object.keys(last.charts).forEach(function (id) { render(id, last.charts[id]); }); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });

  applyTheme();
  syncVisibility();
  load();
  loadLive();
  setInterval(loadLive, 60000);
})();
