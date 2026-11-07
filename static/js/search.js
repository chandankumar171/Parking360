(function () {
  "use strict";
  var form = document.getElementById("search-form");
  if (!form) return;

  form.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { form.submit(); });
  });

  // Debounced place/parking suggestions
  var input = document.getElementById("q");
  var list = document.getElementById("suggest-list");
  var timer;
  if (input && list) {
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var term = input.value.trim();
      if (term.length < 2) { list.innerHTML = ""; return; }
      timer = setTimeout(function () {
        fetch(input.dataset.suggestUrl + "?q=" + encodeURIComponent(term), { headers: { Accept: "application/json" } })
          .then(function (r) { return r.ok ? r.json() : { results: [] }; })
          .then(function (data) {
            list.innerHTML = "";
            data.results.forEach(function (item) {
              var opt = document.createElement("option");
              opt.value = item.label;
              opt.label = item.kind;
              list.appendChild(opt);
            });
          })
          .catch(function () {});
      }, 250);
    });
  }

  // Near me
  var lat = document.getElementById("geo-lat");
  var lng = document.getElementById("geo-lng");
  var sort = document.getElementById("sort");
  var near = document.getElementById("near-me");
  if (near) {
    near.addEventListener("click", function () {
      if (!navigator.geolocation) { alert("Your browser does not support location."); return; }
      near.disabled = true;
      navigator.geolocation.getCurrentPosition(function (pos) {
        lat.value = pos.coords.latitude.toFixed(6);
        lng.value = pos.coords.longitude.toFixed(6);
        if (sort) sort.value = "distance";
        form.submit();
      }, function () {
        near.disabled = false;
        alert("We could not get your location. Allow location access or search by area instead.");
      }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
    });
  }
  var clear = document.getElementById("clear-geo");
  if (clear) clear.addEventListener("click", function () {
    lat.value = ""; lng.value = "";
    if (sort && sort.value === "distance") sort.value = "relevance";
    form.submit();
  });
})();
