// slice VC01-02: VERCON opens only after sign-in. A classic script, loaded first in <head>: without a portal
// session in this tab the page goes to the portal sign-in at once. The real check is on the server -- every
// /api/vercon/* call needs the session, the unit and the VR right; this only saves the visit.
(function () {
  // Only the local preview response carries this server-generated marker.
  if (document.documentElement.dataset.vcPreview === 'true' || document.documentElement.dataset.vcLocalSsh === 'true') return;
  var token = '';
  try { token = window.sessionStorage.getItem('kbot-portal-token') || ''; } catch (err) { console.error('[vercon] sessionStorage is blocked', err); }
  if (!token) window.location.replace('/portal?next=' + encodeURIComponent('/vercon'));
})();
