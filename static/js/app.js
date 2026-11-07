(function () {
  "use strict";
  var root = document.documentElement;
  var desktop = window.matchMedia("(min-width: 992px)");

  function syncThemeIcons() {
    var dark = root.getAttribute("data-bs-theme") === "dark";
    document.querySelectorAll("[data-theme-toggle] i").forEach(function (i) {
      i.className = dark ? "bi bi-sun" : "bi bi-moon-stars";
    });
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-theme-toggle], [data-sidebar-toggle], [data-toggle-password], [data-sidebar-close]");
    if (!t) return;

    if (t.hasAttribute("data-theme-toggle")) {
      var next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-bs-theme", next);
      try { localStorage.setItem("ps.theme", next); } catch (err) {}
      syncThemeIcons();
    } else if (t.hasAttribute("data-sidebar-toggle")) {
      if (desktop.matches) {
        var collapsed = root.classList.toggle("sidebar-collapsed");
        try { localStorage.setItem("ps.sidebar", collapsed ? "collapsed" : "expanded"); } catch (err) {}
        t.setAttribute("aria-expanded", String(!collapsed));
      } else {
        root.classList.toggle("sidebar-open");
      }
    } else if (t.hasAttribute("data-sidebar-close")) {
      root.classList.remove("sidebar-open");
    } else if (t.hasAttribute("data-toggle-password")) {
      var input = t.closest(".input-group").querySelector("input");
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      t.setAttribute("aria-label", show ? "Hide password" : "Show password");
      t.querySelector("i").className = show ? "bi bi-eye-slash" : "bi bi-eye";
    }
  });

  // Confirmation modal for destructive forms: <form data-confirm="Delete this?">
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-confirm]") || form.dataset.confirmed === "1") return;
    var modalEl = document.getElementById("confirmModal");
    if (!modalEl || !window.bootstrap) return;
    e.preventDefault();
    modalEl.querySelector("[data-confirm-message]").textContent = form.getAttribute("data-confirm");
    var modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    modalEl.querySelector("[data-confirm-accept]").onclick = function () {
      form.dataset.confirmed = "1";
      modal.hide();
      form.requestSubmit();
    };
    modal.show();
  });

  function initRolePicker() {
    var radios = document.querySelectorAll("input.role-input");
    if (!radios.length) return;
    function apply() {
      var checked = document.querySelector("input.role-input:checked");
      var owner = checked && checked.value === "OWNER";
      document.querySelectorAll("[data-owner-only]").forEach(function (el) { el.hidden = !owner; });
    }
    radios.forEach(function (r) { r.addEventListener("change", apply); });
    apply();
  }

  document.addEventListener("DOMContentLoaded", function () {
    syncThemeIcons();
    initRolePicker();
    if (window.bootstrap) {
      document.querySelectorAll(".toast").forEach(function (el) {
        window.bootstrap.Toast.getOrCreateInstance(el, { delay: 6000 }).show();
      });
    }
  });
})();
