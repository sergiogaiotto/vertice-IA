// Vértice — toast notifications + helpers HTTP

(function () {
  function ensureContainer() {
    let c = document.getElementById("vertice-toasts");
    if (!c) {
      c = document.createElement("div");
      c.id = "vertice-toasts";
      c.className = "fixed top-16 right-4 z-[60] flex flex-col gap-2 pointer-events-none";
      document.body.appendChild(c);
    }
    return c;
  }

  window.toast = function (message, type = "info", durationMs = 3500) {
    const container = ensureContainer();
    const colors = {
      success: "border-brand-400/40 bg-brand-50 text-brand-800",
      error:   "border-risk-400/40 bg-risk-50 text-risk-800",
      info:    "border-neutral-200 bg-white text-neutral-800",
      warn:    "border-amber-400/40 bg-amber-50 text-amber-800",
    };
    const el = document.createElement("div");
    el.className = `pointer-events-auto px-4 py-2.5 rounded-lg border text-sm shadow-lg
                    transform translate-x-2 opacity-0 transition-all duration-200
                    ${colors[type] || colors.info}`;
    el.textContent = message;
    container.appendChild(el);
    requestAnimationFrame(() => {
      el.classList.remove("translate-x-2", "opacity-0");
    });
    setTimeout(() => {
      el.classList.add("translate-x-2", "opacity-0");
      setTimeout(() => el.remove(), 220);
    }, durationMs);
  };

  // Helper para chamadas JSON simples
  window.api = async function (method, url, body) {
    const opts = {
      method,
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!resp.ok) {
      const msg = (data && data.detail) || resp.statusText || "erro";
      const err = new Error(msg);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  };

  // Confirmação inline (não-modal, mais rápida)
  window.confirmInline = function (msg) {
    return new Promise((resolve) => {
      const ok = window.confirm(msg);
      resolve(ok);
    });
  };

  // Listener global para erros HTMX → toast
  document.body.addEventListener("htmx:responseError", (e) => {
    const status = e.detail.xhr.status;
    let msg = "erro " + status;
    try {
      const json = JSON.parse(e.detail.xhr.responseText);
      if (json.detail) msg = json.detail;
    } catch (_) {}
    window.toast(msg, "error");
  });
})();
