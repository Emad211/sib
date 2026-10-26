/* CSRF token for every state-changing request (SEC-05, ACC-2.1).
 * The server injects <meta name="csrf-token"> and this file into every HTML page.
 * Same-origin fetch/XHR get an X-CSRFToken header; every submitted form gets a hidden
 * csrf_token field (including programmatic form.submit()). Nothing else changes. */
(function () {
  "use strict";
  var meta = document.querySelector('meta[name="csrf-token"]');
  if (!meta) return;
  var token = meta.getAttribute("content");
  var SAFE = /^(GET|HEAD|OPTIONS|TRACE)$/i;

  function sameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  var originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function (input, init) {
      init = init || {};
      var method = (init.method || (input && input.method) || "GET").toUpperCase();
      var url = typeof input === "string" ? input : input && input.url;
      if (!SAFE.test(method) && sameOrigin(url)) {
        var headers = new Headers(init.headers || (input && input.headers) || {});
        if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
        init = Object.assign({}, init, { headers: headers });
      }
      return originalFetch.call(this, input, init);
    };
  }

  var open = XMLHttpRequest.prototype.open;
  var send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__csrf = !SAFE.test(method) && sameOrigin(url);
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    if (this.__csrf) this.setRequestHeader("X-CSRFToken", token);
    return send.apply(this, arguments);
  };

  function addField(form) {
    if (SAFE.test(form.method || "GET") || !sameOrigin(form.action || window.location.href)) return;
    var field = form.querySelector('input[name="csrf_token"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "csrf_token";
      form.appendChild(field);
    }
    field.value = token;
  }

  document.addEventListener("submit", function (e) { addField(e.target); }, true);
  var nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    addField(this);
    return nativeSubmit.apply(this, arguments);
  };
})();
