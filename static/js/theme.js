/* Runs before first paint (loaded in <head>) to avoid a light/dark or sidebar flash. */
(function () {
  var root = document.documentElement;
  try {
    var theme = localStorage.getItem("ps.theme");
    if (!theme) {
      theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    root.setAttribute("data-bs-theme", theme);
    if (localStorage.getItem("ps.sidebar") === "collapsed") root.classList.add("sidebar-collapsed");
  } catch (e) {}
})();
