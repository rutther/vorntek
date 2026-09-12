const [webSocketUrl] = process.argv.slice(2);
if (!webSocketUrl) throw new Error('Usage: edge_cdp_keyboard.mjs <webSocketUrl>');

const socket = new WebSocket(webSocketUrl);
let nextId = 1;
const pending = new Map();

function call(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const waiter = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
  else waiter.resolve(message.result);
});

await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, returnByValue: true });
  return result.result.value;
}

async function press(key, code = key) {
  const virtualKey = key === 'Tab' ? 9 : key === 'Enter' ? 13 : key === 'Escape' ? 27 : 0;
  await call('Input.dispatchKeyEvent', {
    type: 'rawKeyDown',
    key,
    code,
    windowsVirtualKeyCode: virtualKey,
    nativeVirtualKeyCode: virtualKey,
  });
  if (key === 'Enter') {
    await call('Input.dispatchKeyEvent', {
      type: 'char',
      key,
      code,
      text: '\r',
      unmodifiedText: '\r',
      windowsVirtualKeyCode: virtualKey,
      nativeVirtualKeyCode: virtualKey,
    });
  }
  await call('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key,
    code,
    windowsVirtualKeyCode: virtualKey,
    nativeVirtualKeyCode: virtualKey,
  });
}

try {
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.reload', { ignoreCache: true });
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (await evaluate('document.readyState === "complete"')) break;
    await wait(100);
  }
  await wait(350);

  const initialized = await evaluate(`(() => {
    const visible = (node) => Boolean(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
    const focusable = [...document.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
      .filter(visible);
    if (!focusable.length) return false;
    focusable[0].focus();
    return true;
  })()`);
  if (!initialized) throw new Error('No visible focusable control was found.');

  const sequence = [];
  let triggerReached = false;
  for (let index = 0; index < 80; index += 1) {
    const active = await evaluate(`(() => {
      const node = document.activeElement;
      return node ? {
        tag: node.tagName,
        id: node.id || '',
        target: node.getAttribute('data-bs-target') || '',
        label: (node.getAttribute('aria-label') || node.textContent || '').trim().slice(0, 40),
      } : null;
    })()`);
    sequence.push(active);
    if (active?.target === '#pool-create-modal') {
      triggerReached = true;
      break;
    }
    await press('Tab');
  }
  if (!triggerReached) throw new Error('The create-customer trigger was not reachable by Tab.');

  await press('Enter');
  let opened = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await wait(100);
    opened = await evaluate(`(() => {
      const modal = document.querySelector('#pool-create-modal');
      return {
        shown: Boolean(modal?.classList.contains('show')),
        activeId: document.activeElement?.id || '',
        activeTag: document.activeElement?.tagName || '',
        bootstrapType: typeof window.bootstrap,
        ariaHidden: modal?.getAttribute('aria-hidden') || '',
      };
    })()`);
    if (opened.shown && opened.activeId === 'create-company') break;
  }
  if (!opened.shown || opened.activeId !== 'create-company') {
    throw new Error(`Modal keyboard open/focus failed: ${JSON.stringify(opened)}`);
  }

  await press('Escape');
  await wait(450);
  const closed = await evaluate(`(() => {
    const modal = document.querySelector('#pool-create-modal');
    const active = document.activeElement;
    return {
      hidden: !modal?.classList.contains('show'),
      focusReturned: active?.getAttribute('data-bs-target') === '#pool-create-modal',
      activeLabel: (active?.textContent || '').trim().slice(0, 40),
    };
  })()`);
  if (!closed.hidden || !closed.focusReturned) {
    throw new Error(`Modal keyboard close/focus restoration failed: ${JSON.stringify(closed)}`);
  }

  process.stdout.write(JSON.stringify({
    passed: true,
    tabStopsInspected: sequence.length,
    triggerReached,
    opened,
    closed,
  }));
} finally {
  socket.close();
}
