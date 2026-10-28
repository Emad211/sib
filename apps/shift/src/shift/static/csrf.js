/**
 * CSRF Protection for Sib Shift PWA.
 * Reads the server-issued token from <meta name="csrf-token"> (primary) or the
 * shift_csrf cookie (fallback) and adds the same X-CSRFToken header that the
 * accounting app validates. The server also performs double-submit validation
 * of the header against the shift_csrf cookie.
 */
(function () {
  "use strict";

  var SAFE = /^(GET|HEAD|OPTIONS|TRACE)$/i;
  var HEADER = "X-CSRFToken";
  var COOKIE = "shift_csrf";

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute("content")) {
      return meta.getAttribute("content");
    }
    var pattern = new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]+)");
    var match = document.cookie.match(pattern);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function isSameOrigin(url) {
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
      var token = getCsrfToken();

      if (!SAFE.test(method) && isSameOrigin(url) && token) {
        var headers = new Headers(init.headers || (input && input.headers) || {});
        if (!headers.has(HEADER)) {
          headers.set(HEADER, token);
        }
        init = Object.assign({}, init, { headers: headers });
      }
      return originalFetch.call(this, input, init);
    };
  }

  var origOpen = XMLHttpRequest.prototype.open;
  var origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__csrf = !SAFE.test(method) && isSameOrigin(url);
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    if (this.__csrf) {
      var token = getCsrfToken();
      if (token) {
        this.setRequestHeader(HEADER, token);
      }
    }
    return origSend.apply(this, arguments);
  };

  function addFormField(form) {
    if (SAFE.test(form.method || "GET") || !isSameOrigin(form.action || window.location.href)) {
      return;
    }
    var token = getCsrfToken();
    if (!token) return;
    var field = form.querySelector('input[name="csrf_token"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "csrf_token";
      form.appendChild(field);
    }
    field.value = token;
  }

  document.addEventListener(
    "submit",
    function (e) {
      if (e.target && e.target.tagName === "FORM") {
        addFormField(e.target);
      }
    },
    true
  );

  window.getCsrfToken = getCsrfToken;
})();
