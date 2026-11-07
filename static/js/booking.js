(function () {
  "use strict";

  // ---- Hold countdown
  document.querySelectorAll("[data-countdown]").forEach(function (el) {
    var left = parseInt(el.getAttribute("data-countdown"), 10);
    function tick() {
      if (left <= 0) { window.location.reload(); return; }
      var m = Math.floor(left / 60), s = left % 60;
      el.textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
      left -= 1;
      setTimeout(tick, 1000);
    }
    tick();
  });

  // ---- Booking box
  var form = document.getElementById("booking-form");
  if (!form) return;
  var box = document.getElementById("quote-box");
  var typeSel = document.getElementById("id_vehicle_type");
  var dateIn = document.getElementById("id_date");
  var timeIn = document.getElementById("id_time");
  var slotsIn = document.getElementById("id_slots");
  var hint = document.getElementById("slot-hint");
  var btn = document.getElementById("book-btn");
  var inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
  var timer, seq = 0;

  function duration(min) {
    var h = Math.floor(min / 60), r = min % 60;
    return h && r ? h + " h " + r + " min" : h ? h + " h" : r + " min";
  }
  function line(text, cls) {
    var d = document.createElement("div");
    if (cls) d.className = cls;
    d.textContent = text;
    return d;
  }
  function syncSlotLimits() {
    var opt = typeSel.options[typeSel.selectedIndex];
    if (!opt) return;
    slotsIn.min = opt.dataset.min; slotsIn.max = opt.dataset.max;
    var v = parseInt(slotsIn.value, 10);
    if (!v || v < +opt.dataset.min) slotsIn.value = opt.dataset.min;
    if (v > +opt.dataset.max) slotsIn.value = opt.dataset.max;
    hint.textContent = "Each slot is " + duration(+opt.dataset.slot) + ". Choose " + opt.dataset.min + " to " + opt.dataset.max + ".";
  }
  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (!dateIn.value || !timeIn.value || !slotsIn.value) return;
      var mine = ++seq;
      var qs = new URLSearchParams({ vehicle_type: typeSel.value, date: dateIn.value, time: timeIn.value, slots: slotsIn.value });
      fetch(form.dataset.quoteUrl + "?" + qs.toString(), { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (q) {
          if (mine !== seq) return; // a newer request is in flight
          box.innerHTML = "";
          if (q.ok) {
            box.appendChild(line("Available · " + q.spots_left + " bay" + (q.spots_left === 1 ? "" : "s") + " left", "text-success fw-semibold"));
            box.appendChild(line(q.start + " to " + q.end + " (" + duration(q.duration_minutes) + ")", "small text-muted"));
            box.appendChild(line("Total ₹" + inr.format(parseFloat(q.price)), "quote-total"));
          } else {
            box.appendChild(line(q.message || "Not available for this selection.", "text-danger"));
          }
          btn.disabled = !q.ok && q.message && q.message.indexOf("booked") !== -1;
        })
        .catch(function () {});
    }, 300);
  }

  typeSel.addEventListener("change", function () { syncSlotLimits(); refresh(); });
  [dateIn, timeIn, slotsIn].forEach(function (el) { el.addEventListener("input", refresh); el.addEventListener("change", refresh); });

  var saved = document.getElementById("saved-vehicle");
  if (saved) saved.addEventListener("change", function () {
    var o = saved.options[saved.selectedIndex];
    if (!o || !o.dataset.plate) return;
    document.getElementById("id_vehicle_number_plate").value = o.dataset.plate;
    document.getElementById("id_vehicle_brand_model").value = o.dataset.brand;
    for (var i = 0; i < typeSel.options.length; i++) {
      if (typeSel.options[i].dataset.category === o.dataset.category) { typeSel.selectedIndex = i; break; }
    }
    syncSlotLimits(); refresh();
  });

  syncSlotLimits();
  refresh();
})();
