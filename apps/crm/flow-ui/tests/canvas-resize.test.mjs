import test from "node:test";
import assert from "node:assert/strict";
import { observeCanvasSize } from "../src/canvasResize.mjs";

function harness() {
  const element = {};
  const pending = new Map();
  let observer, serial = 0, fits = 0;
  class FakeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; this.disconnected = false; observer = this; }
    observe(target) { this.targets.push(target); }
    disconnect() { this.disconnected = true; }
  }
  const stop = observeCanvasSize(element, () => { fits++; }, {
    ResizeObserverImpl: FakeObserver,
    schedule: (callback, delay) => { const id = ++serial; pending.set(id, { callback, delay }); return id; },
    cancel: (id) => pending.delete(id),
    delay: 120,
  });
  return { element, observer, stop, pending,
    emit: (width, height) => observer.callback([{ target: element, contentRect: { width, height } }]),
    flush: () => { const tasks = [...pending.values()]; pending.clear(); tasks.forEach(({ callback }) => callback()); },
    fits: () => fits,
  };
}

test("observes the canvas and waits for its first nonzero size", () => {
  const h = harness();
  assert.deepEqual(h.observer.targets, [h.element]);
  h.emit(0, 0); h.emit(900, 0); h.flush();
  assert.equal(h.fits(), 0);
  h.emit(900, 600);
  assert.equal(h.pending.size, 1);
  assert.equal([...h.pending.values()][0].delay, 120);
  h.flush();
  assert.equal(h.fits(), 1);
  h.stop();
});

test("debounces actual viewport/sidebar/inspector resizing but ignores unchanged dimensions", () => {
  const h = harness();
  h.emit(1100, 600); h.flush();
  h.emit(1000, 600); h.emit(850, 600); h.emit(720, 600);
  assert.equal(h.pending.size, 1);
  assert.equal(h.fits(), 1);
  h.flush();
  assert.equal(h.fits(), 2);
  // Data and selection changes must not reset a manually chosen zoom.
  h.emit(720, 600); h.emit(720, 600); h.flush();
  assert.equal(h.fits(), 2);
  h.emit(1005, 600); h.flush();
  assert.equal(h.fits(), 3);
  h.stop();
});

test("hidden canvas cancels a scheduled fit and refits only when visible again", () => {
  const h = harness();
  h.emit(900, 600); h.emit(0, 0); h.flush();
  assert.equal(h.fits(), 0);
  h.emit(900, 600); h.flush();
  assert.equal(h.fits(), 1);
  h.stop();
});

test("cleanup disconnects the observer and cancels queued and late callbacks", () => {
  const h = harness();
  h.emit(900, 600);
  const queued = [...h.pending.values()][0].callback;
  h.stop();
  assert.equal(h.observer.disconnected, true);
  assert.equal(h.pending.size, 0);
  queued(); h.emit(720, 600); h.flush();
  assert.equal(h.fits(), 0);
  h.stop();
});
