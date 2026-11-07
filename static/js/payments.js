(function () {
  "use strict";
  var tokenInput = document.querySelector("input[name=csrfmiddlewaretoken]");
  var csrf = tokenInput ? tokenInput.value : "";

  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, message: "Unexpected response. Please refresh and check your booking." }; });
    });
  }

  function loadCheckout(done) {
    if (window.Razorpay) return done();
    var s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = done;
    s.onerror = function () { alert("Could not load the payment window. Check your connection and try again."); };
    document.head.appendChild(s);
  }

  document.querySelectorAll("[data-pay-url]").forEach(function (btn) {
    var label = btn.innerHTML;
    function reset() { btn.disabled = false; btn.innerHTML = label; }
    btn.addEventListener("click", function () {
      btn.disabled = true;
      btn.textContent = "Opening payment...";
      post(btn.dataset.payUrl).then(function (o) {
        if (!o.ok) { alert(o.message || "Could not start the payment."); reset(); return; }
        loadCheckout(function () {
          var rz = new window.Razorpay({
            key: o.key, amount: o.amount, currency: o.currency, name: o.name, description: o.description,
            order_id: o.order_id, prefill: o.prefill, theme: { color: "#3437c9" },
            modal: { ondismiss: reset },
            handler: function (resp) {
              btn.textContent = "Confirming payment...";
              post(o.verify_url, resp).then(function (v) {
                if (v.ok) { window.location.href = v.redirect || o.next_url; }
                else {
                  alert((v.message || "We could not confirm the payment.") + " If money was deducted, refresh this page in a minute.");
                  reset();
                }
              });
            },
          });
          rz.on("payment.failed", function (r) {
            alert("Payment failed: " + ((r.error && r.error.description) || "please try again."));
            reset();
          });
          rz.open();
        });
      });
    });
  });
})();
