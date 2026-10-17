// Editor-local warehouse statements. Pending requests are shared when revisiting a row.
export function statementCache({ lifetime = 0, limit = 128 } = {}) {
  const entries = new Map();
  let duration = Number.isFinite(lifetime) && lifetime > 0 ? lifetime : 0;

  function find(key) {
    const entry = entries.get(key);
    if (!entry) return null;
    if (entry.data && entry.expires <= Date.now()) { entries.delete(key); return null; }
    entries.delete(key); entries.set(key, entry);
    return entry;
  }

  return {
    get(key) { return find(key)?.data ?? null; },
    load(key, loader) {
      if (!duration) return Promise.resolve().then(loader);
      const cached = find(key);
      if (cached) return cached.promise;
      const entry = { data: null, expires: 0, promise: null };
      entry.promise = Promise.resolve().then(loader).then(data => {
        // Clearing/evicting a pending request prevents its response repopulating the cache.
        if (entries.get(key) === entry) { entry.data = data; entry.expires = Date.now() + duration; }
        return data;
      }).catch(error => {
        if (entries.get(key) === entry) entries.delete(key);
        throw error;
      });
      entries.set(key, entry);
      while (entries.size > limit) entries.delete(entries.keys().next().value);
      return entry.promise;
    },
    clear() { entries.clear(); },
    configure({ lifetime = 0 } = {}) {
      duration = Number.isFinite(lifetime) && lifetime > 0 ? lifetime : 0;
      entries.clear();
    },
  };
}
