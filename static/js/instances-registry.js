// ========== FILE: /static/js/dashboard/instances-registry.js ==========
// Registry centralizat pentru instanțele principale - ES6 PURE

/**
 * 🎯 INSTANCES REGISTRY - ES6 Future Proof
 * Înlocuiește window globals cu module management curat
 */

// Map pentru instanțe (memory efficient)
const instances = new Map();
const instanceMetadata = new Map();

// Case-insensitive key mapping
const keyMapping = new Map();

// 🎯 DEFINEȘTE debugMode LOCAL - ca să nu depindă de alte module
const debugMode =
  typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location?.hostname);

/**
 * 📝 REGISTER INSTANCE
 */
export function registerInstance(name, instance, metadata = {}) {
  const originalName = name;
  const lowerName = name.toLowerCase();

  if (instances.has(originalName)) {
    log(`⚠️ Overwriting existing instance: ${originalName}`);
  }

  instances.set(originalName, instance);
  keyMapping.set(lowerName, originalName); // Map lowercase → original
  instanceMetadata.set(originalName, {
    registeredAt: Date.now(),
    type: instance.constructor.name,
    version: metadata.version || '1.0.0',
    description: metadata.description || '',
    ...metadata,
  });

  log(`✅ Registered: ${originalName} (${instance.constructor.name})`);
  return instance;
}

/**
 * 🔍 GET INSTANCE (Case-insensitive)
 */
export function getInstance(name) {
  const lowerName = name.toLowerCase();
  const originalName = keyMapping.get(lowerName);

  if (!originalName || !instances.has(originalName)) {
    log.error(
      `❌ Instance '${name}' not found. Available: [${getAvailableInstances().join(', ')}]`
    );
    // throw new Error(
    //   `❌ [Registry] Instance '${name}' not found. Available: [${getAvailableInstances().join(', ')}]`
    // );
  }
  return instances.get(originalName);
}

/**
 * 🗑️ UNREGISTER INSTANCE (Case-insensitive)
 */
export function unregisterInstance(name) {
  const lowerName = name.toLowerCase();
  const originalName = keyMapping.get(lowerName);

  if (!originalName) {
    return false;
  }

  const success = instances.delete(originalName);
  instanceMetadata.delete(originalName);
  keyMapping.delete(lowerName);

  if (success) {
    log(`🗑️ Unregistered: ${originalName}`);
  }

  return success;
}

/**
 * 🧹 CLEAR ALL INSTANCES
 */
export function clearAllInstances() {
  const count = instances.size;
  instances.clear();
  instanceMetadata.clear();
  keyMapping.clear(); // Clear case mapping too
  log(`🧹 Cleared ${count} instances`);
}

/**
 * 📋 GET AVAILABLE INSTANCES
 */
export function getAvailableInstances() {
  return Array.from(instances.keys());
}

/**
 * 📊 GET REGISTRY STATS
 */
export function getRegistryStats() {
  const stats = {
    totalInstances: instances.size,
    estimatedMemory: instances.size * 8, // bytes (pointers)
    instances: {},
  };

  for (const [name, metadata] of instanceMetadata) {
    stats.instances[name] = {
      ...metadata,
      hasInstance: instances.has(name),
    };
  }

  return stats;
}

/**
 * 📝 LOGGING - consistent cu alte clase
 */
const log = (() => {
  const fn = (message, data = null) => {
    if (debugMode) {
      const now = new Date();
      const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
        .getMilliseconds()
        .toString()
        .padStart(3, '0')}`;
      const CPN = 'Registry'.padEnd(15);
      console.log(
        `%c[${ts}] [${CPN}] ${message}`,
        'color: #8eb2c5ff; font-weight: bold;',
        data ?? ''
      );
    }
  };

  fn.error = (message, data = null) => {
    const now = new Date();
    const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
      .getMilliseconds()
      .toString()
      .padStart(3, '0')}`;
    const CPN = 'Registry'.padEnd(15);
    console.error(
      `%c[${ts}] [${CPN}] ${message}`,
      'color: #ef4444; font-weight: bold;',
      data ?? ''
    );
  };

  return fn;
})();

window.jsCache = {
  getInstance,
  registerInstance,
  unregisterInstance,
  clearAllInstances,
  getAvailableInstances,
  getRegistryStats,
};
