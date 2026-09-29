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
 * 5. Click on a person: only their plans, and their overview in the side panel.
 *    Buttons "alle / Ablesung / Montage / beides": only plans of that kind.
 * 6. Mouse over a plan: its stops as a tooltip.
 * 7. Click on an empty part of a day: that day in the side panel (who is free).
 * 8. "🗓 Erste freie Tage": green markers on everybody's first free day; a click
 *    on a marker opens the planning panel with suggestions for that person and day.
 */
document.addEventListener("DOMContentLoaded", function () {
  var element = document.getElementById("calendar");
  var personSelect = document.getElementById("cal-person");
  var csrfToken = element.dataset.csrf;
  var kind = "";  // "" = all plans, "reading", "installation", "mixed"
  var showFree = false;  // 8: green markers on everybody's first free day

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
      extraParams: function () { return { person: personSelect.value, art: kind, frei: showFree ? "1" : "" }; }
    },

    // 6: the stops of a plan when the mouse is over it
    eventDidMount: function (info) {
      if (info.event.extendedProps.tooltip) { info.el.title = info.event.extendedProps.tooltip; }
    },

    // 7: click on a day: who is planned, who is still free (office only)
    dateClick: element.dataset.dayUrl ? function (info) {
      htmx.ajax("GET", element.dataset.dayUrl + "?datum=" + info.dateStr.slice(0, 10), "#cal-side");
    } : undefined,

    // 3: side panel
    eventClick: function (info) {
      if (info.event.extendedProps.freeUrl) {  // 8: green marker -> plan this free day
        htmx.ajax("GET", info.event.extendedProps.freeUrl, "#cal-side");
        return;
      }
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

  // 5: people and kinds
  var pills = document.querySelectorAll(".cal-pill[data-person]");
  function choosePerson(id) {
    personSelect.value = id;
    pills.forEach(function (pill) { pill.classList.toggle("on", pill.dataset.person === id); });
    calendar.refetchEvents();
    var pill = document.querySelector('.cal-pill[data-person="' + id + '"]');
    if (id && pill) { htmx.ajax("GET", pill.dataset.overview, "#cal-side"); }
    else { document.getElementById("cal-side").innerHTML = ""; calendar.updateSize(); }
  }
  pills.forEach(function (pill) {
    pill.addEventListener("click", function () {
      // a second click on the chosen person shows everybody again
      choosePerson(personSelect.value === pill.dataset.person ? "" : pill.dataset.person);
    });
  });
  personSelect.addEventListener("change", function () { choosePerson(personSelect.value); });

  document.querySelectorAll(".cal-kinds [data-kind]").forEach(function (button) {
    button.addEventListener("click", function () {
      kind = button.dataset.kind;
      document.querySelectorAll(".cal-kinds [data-kind]").forEach(function (b) { b.classList.toggle("an", b === button); });
      calendar.refetchEvents();
      if (showFree) { loadFreeList(); }
    });
  });

  // 8: "🗓 Erste freie Tage" on/off: green markers in the calendar + the list on the right
  var freeButton = document.getElementById("cal-free");
  function loadFreeList() {
    var art = kind === "mixed" ? "" : kind;
    htmx.ajax("GET", freeButton.dataset.listUrl + (art ? "?art=" + art : ""), "#cal-side");
  }
  if (freeButton) {
    freeButton.addEventListener("click", function () {
      showFree = !showFree;
      freeButton.classList.toggle("primary", showFree);
      freeButton.setAttribute("aria-pressed", showFree ? "true" : "false");
      calendar.refetchEvents();
      if (showFree) { loadFreeList(); } else { document.getElementById("cal-side").innerHTML = ""; calendar.updateSize(); }
    });
  }

  // Planning panel of a free day: live sum of the ticked work minutes
  document.getElementById("cal-side").addEventListener("change", function (event) {
    if (!event.target.matches("input[data-minutes]")) { return; }
    var sum = 0;
    event.target.form.querySelectorAll("input[data-minutes]:checked").forEach(function (box) { sum += Number(box.dataset.minutes); });
    var out = document.getElementById("free-minutes");
    if (out) { out.textContent = sum; }
  });

  // In the person overview: a click on a plan also jumps the calendar to its day
  document.getElementById("cal-side").addEventListener("click", function (event) {
    var target = event.target.closest("[data-goto]");
    if (target) { calendar.gotoDate(target.dataset.goto); }
    if (event.target.closest("[data-person-close]")) { personSelect.value = ""; pills.forEach(function (p) { p.classList.remove("on"); }); calendar.refetchEvents(); }
  });
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
