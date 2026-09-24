// z-max.js
window.ZIndexManager = (() => {
  let maxZIndex = 1000;

  // Map pentru tracking rapid: element -> z-index
  const trackedElements = new Map();

  // Array sortat descrescător pentru acces rapid la max
  let sortedZIndices = [];

  const EXCEPTIONS = new Set([
    'permanent-header',
    'permanent-footer',
    'system-notification',
    'row-action-buttons',
    'loading',
    'notification',
  ]);

  const FLOATING_KEYWORDS = [
    'modal',
    'dialog',
    'overlay',
    'panel',
    'floating',
    'popup',
    'dropdown',
    'tooltip',
  ];

  const EXCLUDED_SELECTORS =
    'table, tr, td, th, tbody, thead, tfoot, input, label, span, a, form, button, img, svg, canvas, video, audio';

  function getZIndex(element) {
    const zIndex = parseInt(window.getComputedStyle(element).zIndex, 10);
    return isNaN(zIndex) ? 0 : zIndex;
  }

  function isException(element) {
    if (EXCEPTIONS.has(element.id)) return true;
    for (const className of element.classList) {
      if (EXCEPTIONS.has(className)) return true;
    }
    return element.hasAttribute('data-z-index-ignore');
  }

  function isFloatingElement(element) {
    if (!element.matches) return false;
    if (element.matches(EXCLUDED_SELECTORS)) return false;

    const hasFloatingClass = Array.from(element.classList).some((cls) =>
      FLOATING_KEYWORDS.some((keyword) => cls.toLowerCase().includes(keyword))
    );

    const styles = window.getComputedStyle(element);
    const hasFloatingStyle =
      (styles.position === 'fixed' || styles.position === 'absolute') && styles.zIndex !== 'auto';

    return hasFloatingClass || hasFloatingStyle;
  }

  function binaryInsert(entry) {
    let low = 0,
      high = sortedZIndices.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (sortedZIndices[mid].zIndex < entry.zIndex) {
        high = mid;
      } else {
        low = mid + 1;
      }
    }
    sortedZIndices.splice(low, 0, entry);
  }

  function addToTracked(element, zIndex) {
    if (trackedElements.has(element)) {
      removeFromTracked(element);
    }

    trackedElements.set(element, zIndex);

    const entry = { element, zIndex };
    binaryInsert(entry);

    if (sortedZIndices.length > 0) {
      maxZIndex = Math.max(maxZIndex, sortedZIndices[0].zIndex);
    }
  }

  function removeFromTracked(element) {
    const oldZIndex = trackedElements.get(element);
    if (oldZIndex === undefined) return;

    trackedElements.delete(element);

    const index = sortedZIndices.findIndex((item) => item.element === element);
    if (index !== -1) {
      sortedZIndices.splice(index, 1);
    }

    if (sortedZIndices.length > 0) {
      maxZIndex = sortedZIndices[0].zIndex;
    } else {
      maxZIndex = 1000;
    }
  }

  function updateElement(element) {
    if (isException(element)) {
      removeFromTracked(element);
      return;
    }

    if (!isFloatingElement(element)) {
      removeFromTracked(element);
      return;
    }

    const currentZIndex = getZIndex(element);

    if (currentZIndex > 0) {
      if (trackedElements.get(element) !== currentZIndex) {
        addToTracked(element, currentZIndex);
      }
    } else {
      removeFromTracked(element);
    }
  }

  function scanExisting() {
    const potentialElements = document.querySelectorAll('div, section, aside, nav, header');
    potentialElements.forEach((element) => {
      if (isFloatingElement(element) && !isException(element)) {
        const zIndex = getZIndex(element);
        if (zIndex > 0) {
          addToTracked(element, zIndex);
        }
      }
    });
  }

  // Debounce pentru observer
  let mutationQueue = new Set();
  let scheduled = false;

  function scheduleUpdate(element) {
    mutationQueue.add(element);
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(() => {
        mutationQueue.forEach(updateElement);
        mutationQueue.clear();
        scheduled = false;
      });
    }
  }

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === 1) {
            scheduleUpdate(node);
            if (node.querySelectorAll) {
              const children = node.querySelectorAll('div, section, aside, nav, header');
              children.forEach(scheduleUpdate);
            }
          }
        });

        mutation.removedNodes.forEach((node) => {
          if (node.nodeType === 1) {
            removeFromTracked(node);
            if (node.querySelectorAll) {
              const children = node.querySelectorAll('*');
              children.forEach(removeFromTracked);
            }
          }
        });
      }

      if (
        mutation.type === 'attributes' &&
        (mutation.attributeName === 'style' || mutation.attributeName === 'class')
      ) {
        scheduleUpdate(mutation.target);
      }
    }
  });

  function init() {
    scanExisting();
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class'],
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Public API
  return {
    getMax: () => maxZIndex,
    getElement: () => (sortedZIndices.length > 0 ? sortedZIndices[0].element : null),
    getNext: () => Math.floor(maxZIndex + 1),
    setMin: (value) => {
      maxZIndex = Math.max(maxZIndex, value);
    },
    addException: (idOrClass) => EXCEPTIONS.add(idOrClass),
    removeException: (idOrClass) => EXCEPTIONS.delete(idOrClass),
    addKeyword: (keyword) => {
      keyword = keyword.toLowerCase();
      if (!FLOATING_KEYWORDS.includes(keyword)) {
        FLOATING_KEYWORDS.push(keyword);
      }
    },
    rescan: () => {
      trackedElements.clear();
      sortedZIndices = [];
      maxZIndex = -Infinity;
      scanExisting();
      if (sortedZIndices.length === 0) maxZIndex = 1000;
    },
    getElementsForZIndex: (zIndex) => {
      return sortedZIndices.filter((item) => item.zIndex === zIndex).map((item) => item.element);
    },
    getTrackedCount: () => trackedElements.size,
    getBuffer: () =>
      sortedZIndices.map((item) => ({
        element: item.element,
        zIndex: item.zIndex,
        tag: item.element.tagName,
        id: item.element.id,
        classes: Array.from(item.element.classList),
      })),
  };
})();
