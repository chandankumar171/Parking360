(function () {
  "use strict";

  // Owner: code box (auto-uppercase) and optional camera QR scan
  var code = document.getElementById("code-input");
  if (code) {
    code.addEventListener("input", function () {
      code.value = code.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
    });
    code.focus();
  }
  var scanBtn = document.getElementById("scan-btn");
  var reader = document.getElementById("qr-reader");
  if (scanBtn && reader) {
    var scanner = null;
    function start() {
      reader.hidden = false;
      scanner = new window.Html5Qrcode("qr-reader");
      scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 220 }, function (text) {
        code.value = String(text).toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
        scanner.stop().then(function () { code.form.submit(); });
      }, function () {}).catch(function () {
        reader.hidden = true;
        alert("Could not open the camera. Allow camera access or type the code instead.");
      });
    }
    scanBtn.addEventListener("click", function () {
      if (window.Html5Qrcode) return start();
      var s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js";
      s.onload = start;
      s.onerror = function () { alert("Could not load the scanner. Type the code instead."); };
      document.head.appendChild(s);
    });
  }

  // Owner: wait for the driver to pay overtime, then reload
  var poll = document.querySelector("[data-poll-url]");
  if (poll) {
    var timer = setInterval(function () {
      fetch(poll.dataset.pollUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d.overtime_paid || d.status !== "CHECKED_IN") { clearInterval(timer); window.location.reload(); } })
        .catch(function () {});
    }, 5000);
  }
})();
