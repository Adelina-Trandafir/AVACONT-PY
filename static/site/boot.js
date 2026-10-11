// Runs before the first paint: a banner the visitor closed earlier must not flash back.
try {
  if (window.localStorage.getItem("kbot-promo-free-2026") === "closed") {
    document.documentElement.classList.add("promo-off");
  }
} catch (e) { /* storage blocked: the banner simply shows */ }
