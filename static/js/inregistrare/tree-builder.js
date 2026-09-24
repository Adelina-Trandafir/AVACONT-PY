// The flat code list becomes a tree on the client: three levels of two digits each
// (operator, 22.09.2026). Same for ClsfF and ClsfE.
//
//   650000  ->  65                 the NAME of level 1 `65`; never selectable (cerere.py)
//   650100  ->  65 > 01            a leaf when nothing hangs under `6501`, else the name of `6501`
//   650101  ->  65 > 01 > 01       a leaf, always
//
// That is the same rule cerere.py uses to decide what may be chosen, so every leaf here
// is a code the server accepts and every node with children is one it refuses.
//
// Group ids are the prefixes (`65`, `6501`); leaf ids are the six-digit codes the
// server wants back. The two cannot collide: codes are always six characters.
//
// A group's name comes from its own row (`650000`, `650100`) or, failing that, from
// `groupCaptions` (ClsfE: DefaTitlu / DefaArticol, see nomenclatoare.py). A level-2
// group with no name shows its two digits alone. A level-1 group with no name at all
// is dropped with everything under it (D14: a leaf with no root is ignored).

export function buildClassificationTree(rows, groupCaptions = {}) {
  const captions = new Map();
  for (const row of rows || []) {
    const code = String(row.cod ?? '').trim();
    if (code.length !== 6 || captions.has(code)) continue;
    captions.set(code, String(row.denumire ?? '').trim());
  }

  // A level-2 stem that something hangs under -- `6501` when `650101` exists.
  const stemsWithChildren = new Set();
  for (const code of captions.keys()) {
    if (!code.endsWith('00')) stemsWithChildren.add(code.slice(0, 4));
  }

  const nameOf = (prefix) => {
    const own = captions.get(prefix.padEnd(6, '0'));
    if (own) return own;
    const given = groupCaptions && groupCaptions[prefix];
    return given ? String(given).trim() : '';
  };

  const tops = new Map();
  const mids = new Map();
  const top = (prefix) => {
    if (!tops.has(prefix)) tops.set(prefix, node(prefix, prefix, nameOf(prefix), prefix));
    return tops.get(prefix);
  };
  const mid = (stem) => {
    if (!mids.has(stem)) {
      const group = node(stem, stem.slice(2), nameOf(stem), stem);
      mids.set(stem, group);
      top(stem.slice(0, 2)).children.push(group);
    }
    return mids.get(stem);
  };

  for (const [code, caption] of captions) {
    if (code.endsWith('0000')) continue; // a level-1 name, never a choice
    const stem = code.slice(0, 4);
    if (code.endsWith('00')) {
      // `650100`: a choice only while nothing hangs under `6501`.
      if (!stemsWithChildren.has(stem)) {
        top(code.slice(0, 2)).children.push(node(code, code.slice(2, 4), caption, code));
      }
      continue;
    }
    mid(stem).children.push(node(code, code.slice(4), caption, code));
  }

  const kept = [...tops.values()].filter((t) => nameOf(t.id) && t.children.length > 0);
  const byKey = (a, b) => (a.sortKey < b.sortKey ? -1 : a.sortKey > b.sortKey ? 1 : 0);
  const tidy = (list) => {
    list.sort(byKey);
    for (const n of list) tidy(n.children);
    return list;
  };
  return tidy(kept);
}

// `sortKey` pads a group to six digits so `6501` sorts where `650100` would.
function node(id, digits, caption, sortSource) {
  return {
    id,
    label: caption ? `${digits} · ${caption}` : digits,
    sortKey: sortSource.padEnd(6, '0'),
    children: [],
  };
}

export function countLeaves(tree) {
  let total = 0;
  const walk = (list) => {
    for (const n of list) {
      if (n.children.length === 0) total++;
      else walk(n.children);
    }
  };
  walk(tree);
  return total;
}
