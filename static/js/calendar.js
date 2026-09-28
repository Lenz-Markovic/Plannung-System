/*
 * Calendar page (planning/calendar.html) - the only JavaScript of the
 * planning pages. FullCalendar needs it; everything else is done by the
 * server.
 *
 * What it does:
 * 1. Shows the tours as events (JSON from /planung/kalender/termine/).
 * 2. Filter "Person": reloads the events for that person.
 * 3. Click on a tour: loads the side panel with HTMX (/planung/fahrplan/<id>/).
 * 4. Drag & drop to another day: asks the server to make a DRAFT for the new
 *    day and opens the preview ("Fahrplan prüfen"). Nothing is saved before
 *    the user confirms there, so the event jumps back meanwhile.
 */
document.addEventListener("DOMContentLoaded", function () {
  var element = document.getElementById("calendar");
  var personSelect = document.getElementById("cal-person");
  var csrfToken = element.dataset.csrf;

  var calendar = new FullCalendar.Calendar(element, {
    locale: "de",
    firstDay: 1,                       // weeks start on Monday
    initialView: "dayGridMonth",
    initialDate: element.dataset.initialDate || undefined,
    headerToolbar: { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,listWeek" },
    buttonText: { today: "heute", month: "Monat", week: "Woche", list: "Liste" },
    slotMinTime: "06:00:00",
    slotMaxTime: "20:00:00",
    height: "auto",
    dayMaxEvents: 4,                   // "+2 mehr" when a day is full
    eventDisplay: "block",             // coloured bars (not dots) in the month view
    eventTimeFormat: { hour: "2-digit", minute: "2-digit" },  // "08:00" instead of "08 Uhr"
    displayEventEnd: false,
    views: { dayGridMonth: { displayEventTime: false } },  // month cells are narrow: name is enough
    editable: element.dataset.editable === "1",
    eventDurationEditable: false,      // only the day can change, not the length

    // 1 + 2: events from the server, with the chosen person
    events: {
      url: element.dataset.feedUrl,
      extraParams: function () { return { person: personSelect.value }; }
    },

    // 3: side panel
    eventClick: function (info) {
      if (!info.event.extendedProps.detailUrl) return;
      htmx.ajax("GET", info.event.extendedProps.detailUrl, "#cal-side");
    },

    // 4: drag & drop -> draft for the new day -> preview
    eventDrop: function (info) {
      var body = new URLSearchParams({
        date: info.event.startStr.slice(0, 10),
        version: info.event.extendedProps.version
      });
      info.revert();  // stays on the old day until confirmed in the preview
      fetch("/planung/fahrplan/" + info.event.id + "/verschieben/", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
        body: body
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (data.redirect) { window.location.href = data.redirect; }
          else { alert(data.message); calendar.refetchEvents(); }
        });
    }
  });
  calendar.render();

  personSelect.addEventListener("change", function () { calendar.refetchEvents(); });
  // The side panel opens/closes -> the calendar gets narrower/wider.
  document.getElementById("cal-side").addEventListener("htmx:afterSwap", function () { calendar.updateSize(); });

  // After deleting a tour in the side panel the server sends "calendar-changed".
  document.body.addEventListener("calendar-changed", function () {
    calendar.refetchEvents();
    document.getElementById("cal-side").innerHTML = "";
    calendar.updateSize();
  });

  // Near-live: fetch the tours again every 60 s (only while the tab is visible),
  // so tours planned or moved by colleagues - or stops ticked off by readers - show up.
  setInterval(function () {
    if (document.visibilityState === "visible") { calendar.refetchEvents(); }
  }, 60000);
});
