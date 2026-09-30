// 📊 One small tooltip for all chart marks with data-tip="Titel|Zeile;Zeile" (hover and keyboard focus).
// Text is set with textContent only.
(function () {
  "use strict";
  var tip = document.createElement("div");
  tip.className = "ov-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);

  function show(mark, x, y) {
    var parts = mark.dataset.tip.split("|");
    tip.textContent = "";
    var head = document.createElement("div");
    head.className = "ov-tip-head";
    head.textContent = parts[0];
    tip.appendChild(head);
    (parts[1] || "").split(";").forEach(function (line) {
      var row = document.createElement("div");
      row.textContent = line;
      tip.appendChild(row);
    });
    tip.hidden = false;
    var box = tip.getBoundingClientRect();
    var left = Math.min(window.innerWidth - box.width - 8, Math.max(8, x - box.width / 2));
    var top = y - box.height - 12;
    tip.style.left = left + "px";
    tip.style.top = (top < 8 ? y + 16 : top) + "px";
  }
  function hide() { tip.hidden = true; }

  document.addEventListener("pointermove", function (event) {
    var mark = event.target.closest && event.target.closest("[data-tip]");
    if (mark) { show(mark, event.clientX, event.clientY); } else { hide(); }
  });
  document.addEventListener("focusin", function (event) {
    var mark = event.target.closest && event.target.closest("[data-tip]");
    if (!mark) { return; }
    var box = mark.getBoundingClientRect();
    show(mark, box.left + box.width / 2, box.top);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("scroll", hide, true);
})();
