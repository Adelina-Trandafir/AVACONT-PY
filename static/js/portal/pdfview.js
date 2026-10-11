// Slice 0110-08 -- the read-only PDF viewer of the web area, on pdf.js (vendored in static/vendor/pdfjs).
//
//   const view = createPdfView(container);
//   await view.open(arrayBuffer);   // shows the document
//   view.clear();
//
// pdf.js is loaded the first time a document is opened, not with the page (about 2 MB).
// `enableXfa` is ON: a LiveCycle form that carries only the «Please wait...» page is drawn by the
// XFA engine of pdf.js; a PDF that Adobe saved after signing carries its pages and is drawn
// normally (slice 0110-07 measured both). Form fields are made inert in CSS (css/pdfview.css) -
// this is a viewer, nothing typed here goes anywhere. pdf.js does not run the form's scripts and
// does not check signatures; the page that hosts the viewer says so.

const VENDOR = '/static/vendor/pdfjs';

let libs = null;

async function loadLibs() {
  if (libs) return libs;
  libs = (async () => {
    const pdfjsLib = await import(`${VENDOR}/pdf.min.js`);
    // pdf_viewer.min.js looks for the library on globalThis.pdfjsLib, which pdf.min.js sets.
    if (!globalThis.pdfjsLib) globalThis.pdfjsLib = pdfjsLib;
    const pdfjsViewer = await import(`${VENDOR}/pdf_viewer.min.js`);
    pdfjsLib.GlobalWorkerOptions.workerSrc = `${VENDOR}/pdf.worker.min.js`;
    return { pdfjsLib, pdfjsViewer };
  })();
  try {
    return await libs;
  } catch (err) {
    libs = null; // the next attempt tries again
    throw err;
  }
}

export function createPdfView(container) {
  container.classList.add('pdfview');
  const scroll = document.createElement('div');
  scroll.className = 'pdfview__scroll';
  const pages = document.createElement('div');
  pages.className = 'pdfViewer';
  scroll.appendChild(pages);
  container.appendChild(scroll);

  let viewer = null;
  let current = null; // the pdf.js document now shown
  let seq = 0;

  async function release() {
    const doc = current;
    current = null;
    if (viewer) {
      viewer.setDocument(null);
      viewer = null;
    }
    pages.textContent = '';
    if (doc) {
      try {
        await doc.destroy();
      } catch (err) {
        console.error('[pdfview] destroying the previous document failed', err);
      }
    }
  }

  return {
    /** Shows the document in `data` (ArrayBuffer or Uint8Array). Rejects with a sentence the operator can read. */
    async open(data) {
      const mine = (seq += 1);
      await release();
      let l;
      try {
        l = await loadLibs();
      } catch (err) {
        console.error('[pdfview] pdf.js could not be loaded', err);
        throw new Error('Vizualizatorul de PDF nu a putut fi încărcat. Reîncărcați pagina.');
      }
      if (mine !== seq) return;
      const eventBus = new l.pdfjsViewer.EventBus();
      const linkService = new l.pdfjsViewer.PDFLinkService({ eventBus });
      const v = new l.pdfjsViewer.PDFViewer({ container: scroll, viewer: pages, eventBus, linkService, enableXfa: true });
      linkService.setViewer(v);
      eventBus.on('pagesinit', () => { v.currentScaleValue = 'page-width'; });
      let doc;
      try {
        // Copy: pdf.js takes ownership of (transfers) the buffer it is given.
        doc = await l.pdfjsLib.getDocument({ data: new Uint8Array(data).slice(), enableXfa: true, isEvalSupported: false }).promise;
      } catch (err) {
        console.error('[pdfview] the document could not be read', err);
        throw new Error('Documentul nu a putut fi afișat. Îl puteți descărca și deschide în K-BOT.');
      }
      if (mine !== seq) {
        await doc.destroy();
        return;
      }
      viewer = v;
      current = doc;
      v.setDocument(doc);
      linkService.setDocument(doc, null);
    },

    clear() {
      seq += 1;
      return release();
    },
  };
}
