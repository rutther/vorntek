export function observeCanvasSize(element, fit, {
  ResizeObserverImpl = globalThis.ResizeObserver,
  schedule = globalThis.setTimeout,
  cancel = globalThis.clearTimeout,
  delay = 120,
} = {}) {
  if (!element || !ResizeObserverImpl) return () => {};
  let active = true, timer = null, lastSize = "";
  const clear = () => {
    if (timer !== null) cancel(timer);
    timer = null;
  };
  const observer = new ResizeObserverImpl((entries) => {
    if (!active) return;
    const entry = entries.find((item) => item.target === element);
    if (!entry) return;
    const { width, height } = entry.contentRect;
    if (!(width > 0 && height > 0)) {
      clear();
      lastSize = "";
      return;
    }
    const size = `${width}:${height}`;
    if (size === lastSize) return;
    lastSize = size;
    clear();
    timer = schedule(() => {
      timer = null;
      if (active) fit();
    }, delay);
  });
  observer.observe(element);
  return () => {
    active = false;
    clear();
    observer.disconnect();
  };
}
