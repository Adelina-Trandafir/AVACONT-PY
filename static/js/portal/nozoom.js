// Phone: the page itself neither zooms nor scrolls (portal.css holds it still); only the PDF viewer
// keeps pinch zoom. Browsers that ignore the viewport hints (iOS) are stopped here instead.
const insidePdf = (t) => t instanceof Element && !!t.closest('.pdfview');

const isPhone = () => window.matchMedia('(max-width: 900px)').matches;

document.addEventListener('touchmove', (e) => {
  if (e.touches.length > 1 && isPhone() && !insidePdf(e.target)) e.preventDefault();
}, { passive: false });

['gesturestart', 'gesturechange', 'gestureend'].forEach((n) => {
  document.addEventListener(n, (e) => { if (isPhone() && !insidePdf(e.target)) e.preventDefault(); }, { passive: false });
});
