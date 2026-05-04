// Vértice — Command Palette: atalho global ⌘K / Ctrl+K
document.addEventListener("keydown", (e) => {
  const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
  if (isCmdK) {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent("open-palette"));
  }
});

// Helper json-enc para HTMX (envia request body como JSON)
htmx.defineExtension("json-enc", {
  onEvent: function (name, evt) {
    if (name === "htmx:configRequest") {
      evt.detail.headers["Content-Type"] = "application/json";
    }
  },
  encodeParameters: function (xhr, parameters, elt) {
    xhr.overrideMimeType("text/json");
    return JSON.stringify(parameters);
  }
});
