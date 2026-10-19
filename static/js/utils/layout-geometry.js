// CSS zoom scales screen rectangles and pointer coordinates, while layout sizes
// (offsetWidth, scrollTop, CSS lengths) stay in application pixels.
export function applicationScale() {
  return Number(document.documentElement.dataset.applicationZoom || 100) / 100;
}

export function layoutRect(element) {
  const rect = element.getBoundingClientRect();
  const scale = applicationScale();
  return Object.fromEntries(['left', 'top', 'right', 'bottom', 'width', 'height', 'x', 'y']
    .map(key => [key, rect[key] / scale]));
}

export function layoutViewport() {
  const scale = applicationScale();
  return { width: window.innerWidth / scale, height: window.innerHeight / scale };
}
