import fs from 'node:fs';

const [webSocketUrl, screenshotPath, widthText, heightText, scaleText] = process.argv.slice(2);
const width = Number(widthText);
const height = Number(heightText);
const deviceScaleFactor = Number(scaleText);
if (!webSocketUrl || !screenshotPath || !width || !height || !deviceScaleFactor) {
  throw new Error('Usage: edge_cdp_capture.mjs <webSocketUrl> <screenshotPath> <width> <height> <scale>');
}

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

try {
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor,
    mobile: false,
    screenWidth: width,
    screenHeight: height,
  });
  await call('Page.reload', { ignoreCache: true });
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const ready = await call('Runtime.evaluate', {
      expression: 'document.readyState === "complete"',
      returnByValue: true,
    });
    if (ready.result.value === true) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  await new Promise((resolve) => setTimeout(resolve, 350));

  const evaluated = await call('Runtime.evaluate', {
    expression: `(() => {
      const root = document.documentElement;
      const body = document.body;
      const tableRegion = document.querySelector('.table-responsive');
      const sidebar = document.querySelector('.nc-sidebar, [data-sidebar], aside.sidebar');
      const rect = (node) => node ? ({
        left: Math.round(node.getBoundingClientRect().left * 100) / 100,
        right: Math.round(node.getBoundingClientRect().right * 100) / 100,
        width: Math.round(node.getBoundingClientRect().width * 100) / 100,
      }) : null;
      return {
        url: location.href,
        title: document.title,
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio,
        rootClientWidth: root.clientWidth,
        rootScrollWidth: root.scrollWidth,
        bodyClientWidth: body.clientWidth,
        bodyScrollWidth: body.scrollWidth,
        pageHorizontalOverflow: root.scrollWidth > root.clientWidth || body.scrollWidth > body.clientWidth,
        tableRegion: tableRegion ? {
          clientWidth: tableRegion.clientWidth,
          scrollWidth: tableRegion.scrollWidth,
          hasOwnHorizontalScroll: tableRegion.scrollWidth > tableRegion.clientWidth,
          rect: rect(tableRegion),
        } : null,
        sidebarRect: rect(sidebar),
        activeElement: document.activeElement ? document.activeElement.tagName : null,
      };
    })()`,
    returnByValue: true,
  });
  const screenshot = await call('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));
  process.stdout.write(JSON.stringify(evaluated.result.value));
} finally {
  socket.close();
}
