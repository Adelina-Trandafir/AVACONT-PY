// slice AD10-01: ADECHIT opens only after sign-in. A classic script, loaded first in <head>:
// without a portal session in this tab the page goes to the portal sign-in at once and comes
// back here afterwards (/portal?next=/adechit). The real check is on the server -- every
// /api/adechit/* call needs the session and the AD right; this only saves the visit.
(function () {
  // Only the local preview response carries this server-generated marker.
  if (document.documentElement.dataset.adePreview === 'true') return;
  var token = '';
  try { token = window.sessionStorage.getItem('kbot-portal-token') || ''; } catch (err) { console.error('[adechit] sessionStorage is blocked', err); }
  if (!token) window.location.replace('/portal?next=' + encodeURIComponent('/adechit'));
})();
