// V/A reproduce the blue marks from the existing application selector; K uses its existing logo.
export const APPLICATIONS = Object.freeze({
  V: { label: 'VERCON', icon: '/static/js/components/chat/icons/v.svg' },
  A: { label: 'ADECHIT', icon: '/static/js/components/chat/icons/a.svg' },
  K: { label: 'K-BOT', icon: '/static/site/img/art/logo-64.png' },
});

export function applicationIcon(code) {
  const app = APPLICATIONS[code];
  const image = document.createElement('img');
  image.className = 'kchat__app-icon';
  image.src = app.icon;
  image.alt = code;
  image.title = app.label;
  image.width = 24;
  image.height = 24;
  return image;
}
