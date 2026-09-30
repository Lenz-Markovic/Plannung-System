// ☰ Side menu (templates/core/_side_menu.html): open/close, "📌 anheften", the current page marked,
// pop-ups on/off. Settings are kept in the browser (localStorage) - wrapped in try/catch.
(function () {
  var root = document.documentElement;
  var menu = document.getElementById("side-menu");
  if (!menu) { return; }

  function store(key, value) { try { if (value === null) { localStorage.removeItem(key); } else { localStorage.setItem(key, value); } } catch (e) {} }
  function read(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

  function setOpen(open) {
    root.classList.toggle("menu-open", open);
    menu.setAttribute("aria-hidden", open || root.classList.contains("menu-pinned") ? "false" : "true");
  }
  function setPinned(pinned) {
    root.classList.toggle("menu-pinned", pinned);
    store("menuPinned", pinned ? "1" : null);
    menu.querySelectorAll("[data-menu-pin]").forEach(function (b) { b.textContent = pinned ? "📌 lösen" : "📌 anheften"; });
    setOpen(false);
  }
  setPinned(read("menuPinned") === "1");

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-menu-toggle]")) { setOpen(!root.classList.contains("menu-open")); return; }
    if (event.target.closest("[data-menu-pin]")) { setPinned(!root.classList.contains("menu-pinned")); return; }
    if (event.target.closest("[data-menu-close]")) { setOpen(false); }
    if (event.target.closest("[data-popups-toggle]")) {
      var off = !root.classList.contains("no-rm-popups");
      root.classList.toggle("no-rm-popups", off);
      store("rmPopupsOff", off ? "1" : null);
      showPopupState();
    }
  });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") { setOpen(false); } });

  function showPopupState() {
    menu.querySelectorAll("[data-popups-state]").forEach(function (b) {
      b.textContent = root.classList.contains("no-rm-popups") ? "aus" : "an";
    });
  }
  showPopupState();

  // the button of the page you are on (same path and the same quick filter)
  var here = window.location.pathname + window.location.search;
  var best = null;
  menu.querySelectorAll("a.sm-btn").forEach(function (a) {
    var url = new URL(a.href, window.location.href);
    var target = url.pathname + url.search;
    if (target === here) { best = a; }
    else if (!best && !url.search && url.pathname === window.location.pathname) { best = a; }
  });
  if (best) { best.classList.add("on"); best.setAttribute("aria-current", "page"); }
})();
