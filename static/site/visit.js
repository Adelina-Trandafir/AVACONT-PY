// Slice 0110-09 -- how long the presentation page is read, and which section.
// Sends small totals to POST /api/vizita (routes/landing/vizite.py): no cookie, no storage, no
// library, nothing that identifies the person beyond the address the server sees anyway.
//
// Time counts only while the tab is visible AND the person did something (scroll, move, key,
// touch) in the last IDLE_SECONDS; a page left open in a background tab adds nothing. A section
// is the `[data-screen]` block that crosses the middle line of the window.
// Every message carries the totals so far, so a lost message costs nothing.
(function () {
  "use strict";

  var ENDPOINT = "/api/vizita";
  var IDLE_SECONDS = 30;
  var SEND_EVERY = 15;      // seconds, and only when something changed
  var MAX_SENDS = 200;      // a page open for hours stops talking after this

  var screens = Array.prototype.slice.call(document.querySelectorAll("[data-screen]"));
  var total = 0;
  var perSection = {};
  var lastActive = Date.now();
  var lastSentTotal = -1;
  var sends = 0;

  function newId() {
    var bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < 16; i++) { bytes[i] = Math.floor(Math.random() * 256); }
    }
    return Array.prototype.map.call(bytes, function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
  }

  var visitId = newId();

  function touched() { lastActive = Date.now(); }
  ["scroll", "mousemove", "keydown", "touchstart", "click", "wheel"].forEach(function (name) {
    window.addEventListener(name, touched, { passive: true });
  });

  function currentSection() {
    var middle = window.innerHeight / 2;
    for (var i = 0; i < screens.length; i++) {
      var box = screens[i].getBoundingClientRect();
      if (box.top <= middle && box.bottom > middle) { return screens[i].id || ""; }
    }
    return "";
  }

  function referrerHost() {
    try {
      var host = document.referrer ? new window.URL(document.referrer).hostname : "";
      return host && host !== window.location.hostname ? host : "";
    } catch (err) {
      return "";
    }
  }

  function payload() {
    var body = {
      v: visitId,
      t: total,
      s: perSection,
      w: window.screen ? window.screen.width : 0,
      h: window.screen ? window.screen.height : 0,
      m: !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches),
      l: navigator.language || "",
      r: referrerHost()
    };
    return JSON.stringify(body);
  }

  function send(final) {
    if (sends >= MAX_SENDS || total === lastSentTotal) { return; }
    sends += 1;
    lastSentTotal = total;
    var text = payload();
    if (final && navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([text], { type: "text/plain" }));
      return;
    }
    if (window.fetch) {
      window.fetch(ENDPOINT, { method: "POST", body: text, keepalive: true, headers: { "Content-Type": "text/plain" } })
        .catch(function () { /* the page does not depend on being counted */ });
    }
  }

  window.setInterval(function () {
    var active = document.visibilityState === "visible" && Date.now() - lastActive < IDLE_SECONDS * 1000;
    if (!active) { return; }
    total += 1;
    var section = currentSection();
    if (section) { perSection[section] = (perSection[section] || 0) + 1; }
    if (total % SEND_EVERY === 0) { send(false); }
  }, 1000);

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { send(true); }
  });
  window.addEventListener("pagehide", function () { send(true); });

  send(false);   // the visit exists from the first moment, even if the person leaves at once
})();
