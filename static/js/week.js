// 🗓 Wochenplanung: the "n angekreuzt" count and drag & drop (templates/planning/week.html).
// Dropping only calls the same "+ hierher" action as a click - the office decides, nothing is automatic.
(function () {
  "use strict";
  function picked() { return Array.prototype.map.call(document.querySelectorAll("#week-pool input[name=item]:checked"), function (b) { return b.value; }); }
  function count() {
    var out = document.getElementById("wk-picked");
    if (out) { out.textContent = picked().length + " angekreuzt"; }
  }
  document.addEventListener("change", function (event) { if (event.target.closest("#week-pool")) { count(); } });
  document.addEventListener("htmx:afterSettle", count);
  document.addEventListener("click", function (event) {
    if (!event.target.closest("[data-wk-none]")) { return; }
    document.querySelectorAll("#week-pool input[name=item]:checked").forEach(function (b) { b.checked = false; });
    count();
  });
  count();

  var dragged = null;
  document.addEventListener("dragstart", function (event) {
    var item = event.target.closest && event.target.closest("[data-item]");
    if (!item) { return; }
    dragged = item.dataset.item;
    event.dataTransfer.setData("text/plain", dragged);
    event.dataTransfer.effectAllowed = "move";
  });
  document.addEventListener("dragover", function (event) {
    var cell = event.target.closest && event.target.closest("[data-drop]");
    if (!cell || !dragged) { return; }
    event.preventDefault();
    cell.classList.add("wk-over");
  });
  document.addEventListener("dragleave", function (event) {
    var cell = event.target.closest && event.target.closest("[data-drop]");
    if (cell && !cell.contains(event.relatedTarget)) { cell.classList.remove("wk-over"); }
  });
  document.addEventListener("drop", function (event) {
    var cell = event.target.closest && event.target.closest("[data-drop]");
    if (!cell || !dragged) { return; }
    event.preventDefault();
    cell.classList.remove("wk-over");
    var items = [dragged];
    // dragged from the list: the other ticked objects come along
    if (document.querySelector('#week-pool [data-item="' + dragged + '"]')) {
      picked().forEach(function (v) { if (items.indexOf(v) === -1) { items.push(v); } });
    }
    var params = new FormData(document.getElementById("week-params"));
    var values = { employee: cell.dataset.employee, date: cell.dataset.date, item: items,
                   kw: params.get("kw"), art: params.get("art"), q: params.get("q") || "" };
    htmx.ajax("POST", document.getElementById("week-board").dataset.place, { target: "#week-board", swap: "outerHTML", values: values });
    dragged = null;
  });
  document.addEventListener("dragend", function () { dragged = null; });
})();
