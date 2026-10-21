// Shared entry point for the three real authenticated web pages. Demos stay isolated.
import { mountChat } from './client.js';

const root = document.documentElement;
const preview = root.dataset.vcPreview === 'true' || root.dataset.adePreview === 'true' || root.dataset.vcLocalSsh === 'true';
const application = document.body.classList.contains('vc-page') ? 'V'
  : document.body.classList.contains('ade-page') ? 'A' : 'K';
const token = () => {
  try { return sessionStorage.getItem('kbot-portal-token') || ''; }
  catch { return ''; }
};
let chat = null, rejectedToken = '', observer = null;

function sync() {
  const value = token();
  const visible = application !== 'K' || document.getElementById('user-info')?.hidden === false;
  if (!value || !visible) { chat?.destroy(); chat = null; return; }
  if (!chat && value !== rejectedToken) {
    chat = mountChat({ application, token, onUnauthorized: () => {
      rejectedToken = value;
      chat = null;
    } });
  }
}

if (!preview) {
  sync();
  const user = document.getElementById('user-info');
  if (application === 'K' && user) {
    observer = new MutationObserver(sync);
    observer.observe(user, { attributes: true, attributeFilter: ['hidden'] });
  }
  const stop = () => { observer?.disconnect(); chat?.destroy(); chat = null; };
  window.addEventListener('pagehide', stop, { once: true });
  window.addEventListener('app:session-expired', stop, { once: true });
}
