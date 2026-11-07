(function () {
  "use strict";

  // ---- Vehicle-type formset: add / remove rows
  var form = document.querySelector("[data-vt-form]");
  if (form) {
    var list = document.getElementById("vt-list");
    var tpl = document.getElementById("vt-empty");
    var total = form.querySelector("input[name$='-TOTAL_FORMS']");
    var MAX = 6;
    function liveRows() {
      return Array.prototype.filter.call(list.querySelectorAll("[data-vt-row]"), function (r) { return !r.hidden; });
    }
    function syncAddButton() { document.getElementById("vt-add").disabled = liveRows().length >= MAX; }
    document.getElementById("vt-add").addEventListener("click", function () {
      var index = parseInt(total.value, 10);
      var html = tpl.innerHTML.replace(/__prefix__/g, index);
      var holder = document.createElement("div");
      holder.innerHTML = html.trim();
      var row = holder.firstElementChild;
      list.appendChild(row);
      total.value = index + 1;
      syncAddButton();
      row.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    list.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-vt-remove]");
      if (!btn) return;
      if (liveRows().length <= 1) { alert("Keep at least one vehicle type."); return; }
      var row = btn.closest("[data-vt-row]");
      var del = row.querySelector("input[type=checkbox][name$='-DELETE']");
      if (del) del.checked = true;
      row.hidden = true;
      syncAddButton();
    });
    syncAddButton();
  }

  // ---- Amenities step: enable/disable dependent inputs
  function bindToggle(toggleId, targetsSelector, disableWhenChecked) {
    var t = document.getElementById(toggleId);
    if (!t) return;
    function apply() {
      var off = disableWhenChecked ? t.checked : !t.checked;
      // readOnly (not disabled) so the values are still submitted and validated
      document.querySelectorAll(targetsSelector).forEach(function (el) {
        el.readOnly = off;
        var col = el.closest(".col-md-6");
        if (col) col.classList.toggle("opacity-50", off);
      });
    }
    t.addEventListener("change", apply);
    apply();
  }
  bindToggle("id_is_24x7", "#hours-row input", true);
  bindToggle("id_allow_cancellation", "#cancel-row input", false);

  // ---- Photos step: local previews before upload
  var input = document.getElementById("photos");
  var preview = document.getElementById("photo-preview");
  if (input && preview) {
    input.addEventListener("change", function () {
      preview.innerHTML = "";
      Array.prototype.slice.call(input.files, 0, 5).forEach(function (file) {
        if (!/^image\//.test(file.type)) return;
        var img = document.createElement("img");
        img.src = URL.createObjectURL(file);
        img.alt = file.name;
        img.onload = function () { URL.revokeObjectURL(img.src); };
        preview.appendChild(img);
      });
    });
  }
})();
