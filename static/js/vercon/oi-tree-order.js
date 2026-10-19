import { documentTreeOrder } from './document-tree-order.js';

// PTC uses the same grouping switch and leaf presentation as receipts/issues.
export function oiTreeOrder() {
  return documentTreeOrder({ party: 'holder', rootTotals: true });
}