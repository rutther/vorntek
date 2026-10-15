export class ApiError extends Error {
  constructor(message, { status = 0, code = "network", current = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.current = current;
  }
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
  return value;
}

function currentCsrfToken(fallback) {
  try {
    const cookie = globalThis.document?.cookie;
    const entry = typeof cookie === "string"
      ? cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("csrftoken="))
      : null;
    if (!entry) return fallback;
    const value = decodeURIComponent(entry.slice("csrftoken=".length));
    return value && !/[\u0000-\u0020\u007f]/.test(value) ? value : fallback;
  } catch {
    // Cookie access or decoding can fail. The server-rendered token remains
    // usable when no current cookie is readable (for example HttpOnly setups).
    return fallback;
  }
}

export function createApiClient({ apiUrl, csrfToken, timeoutMs = 30000 }, fetcher = globalThis.fetch) {
  if (!apiUrl?.startsWith("/") || apiUrl.startsWith("//")) throw Error("CRM 接口地址无效。");
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw Error("CRM 请求超时设置无效。");
  const base = apiUrl.endsWith("/") ? apiUrl : `${apiUrl}/`;
  const requests = new Map();
  async function request(url, init) {
    const controller = new AbortController();
    let timer;
    // One deadline covers both headers and body. Racing also releases callers
    // when an intermediary never settles even after aborting the transport.
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        reject(new ApiError("请求超时，结果尚未确认。输入仍保留，请重试同一操作确认结果；不要重复创建或改填内容。", {code:"timeout"}));
        controller.abort();
      }, timeoutMs);
    });
    try { return await Promise.race([readResponse(url, {...init, signal:controller.signal}), deadline]); }
    finally { clearTimeout(timer); }
  }
  async function readResponse(url, init) {
    let response;
    try {
      response = await fetcher(url, { credentials: "same-origin", ...init });
    } catch {
      throw new ApiError("网络连接中断，保存结果尚未确认。输入仍保留，请重试同一操作。");
    }
    if (response.redirected || !response.headers.get("content-type")?.includes("application/json")) {
      throw new ApiError("登录可能已过期，或服务暂不可用。请在新标签页重新登录后返回重试，当前输入仍保留。", { status: response.status, code: "session" });
    }
    let body;
    try { body = await response.json(); }
    catch { throw new ApiError("服务器返回内容无法读取。输入仍保留，请稍后重试。", { status: response.status, code: "response" }); }
    if (!response.ok) {
      const fallback = response.status === 403
        ? "当前账号无权保存，或登录已过期。请核对权限后重试。"
        : "暂时无法保存。输入仍保留，请稍后重试。";
      throw new ApiError(body.error || fallback, { status: response.status, code: body.code, current: body.current });
    }
    return body;
  }
  return {
    runtimeLoad: (id, checks = false) => request(`${base}${encodeURIComponent(id)}/runtime/${checks ? 'checks/' : ''}`, {method:'GET',headers:{Accept:'application/json'},cache:'no-store'}),
    runtimeCommand(id, payload, checks = false) {
      const url = `${base}${encodeURIComponent(id)}/runtime/${checks ? 'checks/' : ''}`;
      const key = JSON.stringify([url, stable(payload)]);
      let record = requests.get(key);
      if (record?.promise) return record.promise;
      if (!record) { record={requestId:crypto.randomUUID(),promise:null}; requests.set(key,record); }
      record.promise = request(url,{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json','X-CSRFToken':currentCsrfToken(csrfToken)},
        body:JSON.stringify(checks ? payload : {...payload,requestId:record.requestId})
      }).then(body=>{ requests.delete(key); return body; }).finally(()=>{ record.promise=null; });
      return record.promise;
    },
    load: () => request(base, { method: "GET", headers: { Accept: "application/json" }, cache: "no-store" }),
    command(action, loop, values = {}) {
      const payload = { ...values, action, ...(loop ? { expectedVersion: loop.version } : {}) };
      const url = loop ? `${base}${encodeURIComponent(loop.id)}/` : base;
      const key = JSON.stringify([url, stable(payload)]);
      let record = requests.get(key);
      if (record?.promise) return record.promise;
      if (!record) {
        record = { requestId: crypto.randomUUID(), promise: null };
        requests.set(key, record);
      }
      record.promise = request(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": currentCsrfToken(csrfToken) },
        body: JSON.stringify({ ...payload, requestId: record.requestId }),
      }).then((body) => {
        if (!body.loop?.id) throw new ApiError("服务器未返回保存记录。请重试确认结果。", { code: "response" });
        requests.delete(key);
        return body;
      }).finally(() => { record.promise = null; });
      return record.promise;
    },
  };
}
