// Slice 0110-02 -- the «Cere mai multe detalii» form. Plain script, no inline code (CSP script-src 'self').
// The server decides everything; this only keeps the person from sending an obviously empty form
// and shows the server's sentence when it refuses.
(function () {
  "use strict";

  var form = document.getElementById("detalii-form");
  if (!form) { return; }

  var msg = document.getElementById("form-msg");
  var send = document.getElementById("btn-send");
  var area = document.getElementById("f-mesaj");
  var counter = document.getElementById("mesaj-count");
  var cardForm = document.getElementById("card-form");
  var cardDone = document.getElementById("card-done");

  var EMAIL = /^[^@\s<>,;:"']+@[^@\s<>,;:"']+\.[^@\s<>,;:"']{2,}$/;

  function showMessage(text) {
    msg.textContent = text;
    msg.hidden = !text;
  }

  function mark(id, bad) {
    var el = document.getElementById(id);
    var box = el && (el.closest(".field") || el.closest(".check"));
    if (box) { box.classList.toggle("is-bad", !!bad); }
  }

  function clearMarks() {
    ["f-nume", "f-institutie", "f-email", "f-acord"].forEach(function (id) { mark(id, false); });
  }

  function updateCounter() {
    counter.textContent = area.value.length + " / " + area.maxLength;
  }
  area.addEventListener("input", updateCounter);
  updateCounter();

  function valueOf(name) { return (form.elements[name].value || "").trim(); }

  // First problem found, as {id, text}; null when the form looks fine.
  function firstProblem() {
    if (valueOf("nume").length < 2) { return { id: "f-nume", text: "Scrieți numele dumneavoastră." }; }
    if (valueOf("institutie").length < 2) { return { id: "f-institutie", text: "Scrieți numele instituției." }; }
    if (!EMAIL.test(valueOf("email"))) { return { id: "f-email", text: "Adresa de e-mail nu pare corectă." }; }
    if (!form.elements.acord.checked) { return { id: "f-acord", text: "Bifați acordul pentru prelucrarea datelor, ca să putem răspunde." }; }
    return null;
  }

  // The server's reason code tells which field to point at.
  var FIELD_OF_REASON = { NUME: "f-nume", INSTITUTIE: "f-institutie", EMAIL: "f-email", CF: "f-cf", TELEFON: "f-telefon", ACORD: "f-acord" };

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    clearMarks();
    showMessage("");

    var problem = firstProblem();
    if (problem) {
      mark(problem.id, true);
      showMessage(problem.text);
      document.getElementById(problem.id).focus();
      return;
    }

    var payload = {
      nume: valueOf("nume"),
      institutie: valueOf("institutie"),
      email: valueOf("email"),
      telefon: valueOf("telefon"),
      cf: valueOf("cf"),
      mesaj: valueOf("mesaj"),
      acord: true,
      website: form.elements.website.value,
      t: form.getAttribute("data-token")
    };

    send.disabled = true;
    fetch("/api/detalii", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (r) {
        if (r.ok && r.data.ok) {
          cardForm.hidden = true;
          cardDone.hidden = false;
          cardDone.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
        var reason = r.data && r.data.reason;
        if (FIELD_OF_REASON[reason]) { mark(FIELD_OF_REASON[reason], true); }
        showMessage((r.data && r.data.error) || "Cererea nu a putut fi trimisă. Reîncercați.");
      })
      .catch(function () {
        showMessage("Nu s-a putut face legătura cu serverul. Verificați conexiunea și reîncercați.");
      })
      .then(function () { send.disabled = false; });
  });
})();
