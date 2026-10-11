// K-BOT presentation page: section dots, active link, reveal on scroll, side panels, banner.
// No libraries. Every step guards against a missing element, so a change in the markup
// can never stop the rest of the page from working.
(function () {
  "use strict";

  var PROMO_KEY = "kbot-promo-free-2026";

  var root = document.documentElement;
  root.classList.add("js");

  var topbar = document.getElementById("topbar");
  var screens = Array.prototype.slice.call(document.querySelectorAll("[data-screen]"));
  var dots = Array.prototype.slice.call(document.querySelectorAll(".dots a"));
  var navLinks = Array.prototype.slice.call(document.querySelectorAll(".topnav a"));

  // A screen belongs to a group; several screens can share one dot.
  function groupOf(screen) {
    return screen ? screen.getAttribute("data-group") || screen.id : null;
  }

  // Which screen a link such as "#integrare" points at (a link may point inside a screen).
  function screenOf(hash) {
    if (!hash || hash.charAt(0) !== "#") { return null; }
    var el = document.getElementById(hash.slice(1));
    return el ? el.closest("[data-screen]") : null;
  }

  function setActive(screen) {
    var group = groupOf(screen);
    dots.forEach(function (dot) {
      dot.classList.toggle("is-active", dot.getAttribute("data-group") === group);
    });
    navLinks.forEach(function (link) {
      link.classList.toggle("is-active", groupOf(screenOf(link.getAttribute("href"))) === group);
    });
  }

  function onScroll() {
    if (topbar) { topbar.classList.toggle("is-scrolled", window.scrollY > 8); }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  if ("IntersectionObserver" in window) {
    // The screen that covers the middle of the window is the active one.
    var screenObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { setActive(entry.target); }
      });
    }, { rootMargin: "-45% 0px -45% 0px", threshold: 0 });
    screens.forEach(function (screen) { screenObserver.observe(screen); });

    var revealObserver = new IntersectionObserver(function (entries, observer) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    Array.prototype.forEach.call(document.querySelectorAll(".reveal"), function (el) {
      revealObserver.observe(el);
    });
  } else {
    Array.prototype.forEach.call(document.querySelectorAll(".reveal"), function (el) {
      el.classList.add("is-in");
    });
    if (screens.length) { setActive(screens[0]); }
  }

  // Side panels (<dialog>) with the full text of a topic.
  function openPanel(id) {
    var dlg = document.getElementById(id);
    if (!dlg || typeof dlg.showModal !== "function") { return; }
    root.classList.add("is-locked");
    dlg.showModal();
    var body = dlg.querySelector(".drawer__body");
    if (body) { body.scrollTop = 0; }
  }

  function closePanel(dlg) {
    if (dlg && dlg.open) { dlg.close(); }
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-open]"), function (btn) {
    btn.addEventListener("click", function () { openPanel(btn.getAttribute("data-open")); });
  });

  Array.prototype.forEach.call(document.querySelectorAll("dialog.drawer"), function (dlg) {
    dlg.addEventListener("close", function () {
      if (!document.querySelector("dialog.drawer[open]")) { root.classList.remove("is-locked"); }
    });
    // A click on the dimmed area (the dialog element itself) closes the panel.
    dlg.addEventListener("click", function (event) {
      if (event.target === dlg) { closePanel(dlg); }
    });
    var closeBtn = dlg.querySelector("[data-close]");
    if (closeBtn) { closeBtn.addEventListener("click", function () { closePanel(dlg); }); }
  });

  // The banner: closing it is remembered in this browser (boot.js reads the same key).
  var promoClose = document.getElementById("promo-close");
  if (promoClose) {
    promoClose.addEventListener("click", function () {
      root.classList.add("promo-off");
      try { window.localStorage.setItem(PROMO_KEY, "closed"); } catch (e) { /* storage blocked */ }
    });
  }
})();
