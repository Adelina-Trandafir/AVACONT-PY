# pdf.js (vendored)

Apache-2.0 (see LICENSE). pdfjs-dist **4.10.38**, taken from
`https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/` on 05.10.2026:

| here | upstream |
|---|---|
| `pdf.min.js` | `build/pdf.min.mjs` |
| `pdf.worker.min.js` | `build/pdf.worker.min.mjs` |
| `pdf_viewer.min.js` | `web/pdf_viewer.min.mjs` (source-map comment removed) |
| `pdf_viewer.min.css` | `web/pdf_viewer.min.css` |

The files are ES modules; they carry the `.js` extension only so every server sends a JavaScript
MIME type. Used by `static/js/portal/pdfview.js` (slice 0116) so the portal pages can keep
`script-src 'self'`. To update: download the same four files of the new version over these and
re-check the viewer on a signed DDF, an ORD and a note (`SLICE-0115` lists what to look at).
