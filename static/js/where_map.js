// 🛰 Wer ist wo? (templates/planning/where.html): the time slider, ▶ Abspielen and the map.
// The positions are worked out on the server (planning/rules/whereabouts.py); this script only
// moves the markers to what #where-data says after every update of the list.
// The map images come from OUR server (/planung/karte/...), the TomTom key never reaches the browser.
(function () {
  "use strict";
  var slider = document.getElementById("where-time");
  var hidden = document.getElementById("where-zeit");
  var clockOut = document.getElementById("where-clock");
  var mapEl = document.getElementById("where-map");
  var map = null, markers = {}, bounds = null, fitted = false;
  var STATES = ["before", "commute", "at", "pause", "driving", "waiting", "home", "done"];

  function clock(minutes) {
    var h = Math.floor(minutes / 60), m = minutes % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }
  function data() {
    var el = document.getElementById("where-data");
    return el ? JSON.parse(el.textContent) : { people: [] };
  }

  // --- slider: show the time at once, the list follows (hx-trigger on the form) ---
  if (slider) {
    slider.addEventListener("input", function () {
      var text = clock(Number(slider.value));
      clockOut.textContent = text;
      hidden.value = text;
    });
  }
  var nowButton = document.getElementById("where-now");
  if (nowButton) {
    nowButton.addEventListener("click", function () {
      var parts = nowButton.dataset.now.split(":");
      slider.value = Number(parts[0]) * 60 + Number(parts[1]);
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  // --- ▶ Abspielen: the day in fast motion (5 minutes per step) ---
  var play = document.getElementById("where-play"), timer = null;
  function stop() { clearInterval(timer); timer = null; play.textContent = "▶ Abspielen"; }
  if (play) {
    play.addEventListener("click", function () {
      if (timer) { stop(); return; }
      if (Number(slider.value) >= Number(slider.max)) { slider.value = slider.min; }
      play.textContent = "⏸ Anhalten";
      timer = setInterval(function () {
        var next = Number(slider.value) + 5;
        if (next > Number(slider.max)) { stop(); return; }
        slider.value = next;
        slider.dispatchEvent(new Event("input", { bubbles: true }));
      }, 450);
    });
  }

  // --- the map ---
  function style(tileUrl) {
    return { version: 8, sources: { tomtom: { type: "raster", tiles: [tileUrl], tileSize: 256, maxzoom: 18, attribution: "© TomTom" } },
             layers: [{ id: "tomtom", type: "raster", source: "tomtom" }] };
  }
  function personElement(p) {
    var el = document.createElement("div");
    el.className = "where-pin " + p.state;
    el.style.background = p.colour;
    el.textContent = p.short;
    el.title = p.label;
    return el;
  }
  function stopFeatures(people) {
    var features = [];
    people.forEach(function (p) {
      if (p.line.length > 1) {
        features.push({ type: "Feature", properties: { colour: p.colour, kind: "line" }, geometry: { type: "LineString", coordinates: p.line } });
      }
      p.stops.forEach(function (s) {
        features.push({ type: "Feature", properties: { colour: p.colour, kind: "stop", done: s.done ? 1 : 0 },
                        geometry: { type: "Point", coordinates: [s.lon, s.lat] } });
      });
    });
    return { type: "FeatureCollection", features: features };
  }
  function draw() {
    if (!map) { return; }
    var people = data().people, seen = {};
    bounds = new tt.LngLatBounds();
    people.forEach(function (p) {
      seen[p.id] = true;
      var popup = "<b>" + p.label + "</b><br>" + p.stateLabel + "<br>" + p.text;
      if (p.behind.length) { popup += "<br>⚠ Stopp " + p.behind.join(", ") + " noch nicht gemeldet"; }
      if (!markers[p.id]) {
        markers[p.id] = new tt.Marker({ element: personElement(p) }).setLngLat([p.lon, p.lat])
          .setPopup(new tt.Popup({ offset: 18, closeButton: false })).addTo(map);
      }
      var m = markers[p.id];
      m.setLngLat([p.lon, p.lat]);
      var pin = m.getElement();  // keep the map's own classes (they place the marker) - only swap the state
      STATES.forEach(function (state) { pin.classList.toggle(state, state === p.state); });
      m.getPopup().setHTML(popup);
      p.stops.forEach(function (s) { bounds.extend([s.lon, s.lat]); });
      bounds.extend([p.lon, p.lat]);
    });
    Object.keys(markers).forEach(function (id) { if (!seen[id]) { markers[id].remove(); delete markers[id]; } });
    var source = map.getSource("plans");
    if (source) { source.setData(stopFeatures(people)); }
    if (!fitted && people.length) { map.fitBounds(bounds, { padding: 50, maxZoom: 13, animate: false }); fitted = true; }
  }
  if (mapEl && window.tt) {
    var start = data().people[0];
    map = tt.map({ key: "server-side", container: mapEl, style: style(mapEl.dataset.tiles.replace("/0/0/0.png", "/{z}/{x}/{y}.png")),
                   center: start ? [start.lon, start.lat] : [9.0, 48.7], zoom: 9 });
    map.addControl(new tt.NavigationControl({ showCompass: false }));
    map.on("load", function () {
      map.addSource("plans", { type: "geojson", data: stopFeatures(data().people) });
      map.addLayer({ id: "plan-lines", type: "line", source: "plans", filter: ["==", ["get", "kind"], "line"],
                     paint: { "line-color": ["get", "colour"], "line-width": 2.5, "line-opacity": 0.55, "line-dasharray": [2, 1.5] } });
      map.addLayer({ id: "plan-stops", type: "circle", source: "plans", filter: ["==", ["get", "kind"], "stop"],
                     paint: { "circle-radius": 5, "circle-color": ["case", ["==", ["get", "done"], 1], "#ffffff", ["get", "colour"]],
                              "circle-stroke-color": ["get", "colour"], "circle-stroke-width": 2 } });
    });
    draw();
  }
  document.addEventListener("htmx:afterSettle", function (event) {
    if (event.target.id === "where-list" || event.target.querySelector && event.target.querySelector("#where-data")) { draw(); }
  });
  // a click on a person in the list: fly there and open the box
  document.addEventListener("click", function (event) {
    var row = event.target.closest("[data-where-id]");
    if (!row || !map) { return; }
    var m = markers[row.dataset.whereId];
    if (!m) { return; }
    map.flyTo({ center: m.getLngLat(), zoom: Math.max(map.getZoom(), 12) });
    if (!m.getPopup().isOpen()) { m.togglePopup(); }
  });
})();
