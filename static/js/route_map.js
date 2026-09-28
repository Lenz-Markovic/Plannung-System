// TomTom map of a route: plan preview, calendar side panel and "Mein Tag".
//
// Security: the real TomTom key stays on the server. The map style below
// loads the map images from OUR Django backend (/planung/karte/z/x/y.png),
// which fetches them from TomTom with the key. The SDK insists on a "key"
// option, so it gets a placeholder that is never used for anything.
//
// Every element <div class="route-map" data-source="map-data" data-tiles="..."> is
// turned into a map - also after HTMX swapped new content into the page.
(function () {
  "use strict";

  // The SDK's copyright box asks TomTom for its text - with the placeholder key
  // that can only fail (and it would retry forever). So that one request is
  // answered right here in the browser; nothing is sent to TomTom.
  var COPYRIGHT_URL = "api.tomtom.com/map/2/copyrights/caption";
  var realFetch = window.fetch;
  window.fetch = function (url) {
    if (String(url && url.url || url).indexOf(COPYRIGHT_URL) !== -1) {
      var caption = { copyrightsCaption: "© 1992 - " + new Date().getFullYear() + " TomTom." };
      return Promise.resolve(new Response(JSON.stringify(caption), { headers: { "Content-Type": "application/json" } }));
    }
    return realFetch.apply(this, arguments);
  };

  function style(tileUrl) {
    return {
      version: 8,
      sources: {
        tomtom: { type: "raster", tiles: [tileUrl], tileSize: 256, maxzoom: 18, attribution: "© TomTom" },
      },
      layers: [{ id: "tomtom", type: "raster", source: "tomtom" }],
    };
  }

  function pinElement(pin) {
    var el = document.createElement("div");
    el.className = "map-pin" + (pin.done ? " done" : "");
    el.style.background = pin.colour;
    el.textContent = pin.n;
    return el;
  }

  function init(el) {
    el.dataset.ready = "1";
    if (!window.tt || el.offsetWidth === 0) { el.dataset.ready = ""; return; } // hidden: try again when shown
    var data = JSON.parse(document.getElementById(el.dataset.source).textContent);
    if (!data.pins.length) { el.hidden = true; return; }
    var tiles = el.dataset.tiles.replace("/0/0/0.png", "/{z}/{x}/{y}.png");

    var map = tt.map({
      key: "server-side", // placeholder - see the comment at the top
      container: el,
      style: style(tiles),
      center: [data.pins[0].lon, data.pins[0].lat],
      zoom: 10,
    });
    map.addControl(new tt.NavigationControl({ showCompass: false }));

    // Numbered pins with a small info box (street, time)
    var bounds = new tt.LngLatBounds();
    el.markers = {};
    data.pins.forEach(function (pin) {
      var popup = new tt.Popup({ offset: 16, closeButton: false }).setText(
        pin.n + ". " + pin.label + (pin.time ? " · " + pin.time : ""));
      el.markers[pin.n] = new tt.Marker({ element: pinElement(pin) })
        .setLngLat([pin.lon, pin.lat]).setPopup(popup).addTo(map);
      bounds.extend([pin.lon, pin.lat]);
    });
    data.lines.forEach(function (line) { line.forEach(function (p) { bounds.extend(p); }); });
    map.fitBounds(bounds, { padding: 40, maxZoom: 14, animate: false });
    el.bounds = bounds;

    // The route: road geometry from TomTom, or straight lines
    map.on("load", function () {
      map.addSource("route", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: data.lines.map(function (line) {
            return { type: "Feature", geometry: { type: "LineString", coordinates: line } };
          }),
        },
      });
      map.addLayer({
        id: "route", type: "line", source: "route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#2a78d6", "line-width": 4, "line-opacity": 0.8 },
      });
    });
    el.map = map;
  }

  function initAll(root) {
    (root || document).querySelectorAll(".route-map:not([data-ready='1'])").forEach(init);
  }

  // Click on a stop number in the list -> show that stop on the map
  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-map-pin]");
    var el = document.querySelector(".route-map[data-ready='1']");
    if (!link || !el || !el.map) { return; }
    var marker = el.markers[link.dataset.mapPin];
    if (!marker) { return; }
    el.map.flyTo({ center: marker.getLngLat(), zoom: Math.max(el.map.getZoom(), 13) });
    if (!marker.getPopup().isOpen()) { marker.togglePopup(); }
  });

  document.addEventListener("DOMContentLoaded", function () { initAll(document); });
  document.addEventListener("htmx:afterSettle", function (event) { initAll(event.target); });
  // A map inside <details> can only be drawn once it is opened
  document.addEventListener("toggle", function (event) {
    if (!event.target.open) { return; }
    initAll(event.target);
    event.target.querySelectorAll(".route-map[data-ready='1']").forEach(function (el) {
      if (el.map) { el.map.resize(); el.map.fitBounds(el.bounds, { padding: 40, maxZoom: 14, animate: false }); }
    });
  }, true);
})();
