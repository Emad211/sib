/**
 * CSRF Protection for Sib Shift PWA.
 * Extracts token from <meta name="csrf-token"> or shift_csrf cookie.
 * Attaches X-CSRF-Token to same-origin state-changing fetch and XHR requests.
 */
(function () {
  "use strict";

  var SAFE = /^(GET|HEAD|OPTIONS|TRACE)$/i;

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute("content")) {
      return meta.getAttribute("content");
    }
    // Fallback: read pre-auth or session csrf cookie
    var match = document.cookie.match(/(?:^|;\s*)(?:shift_csrf|csrf_token)=([^;]+)/);
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
      var url = typeof input === "string" ? input : (input && input.url);
      var token = getCsrfToken();

      if (!SAFE.test(method) && isSameOrigin(url) && token) {
        var headers = new Headers(init.headers || (input && input.headers) || {});
        if (!headers.has("X-CSRF-Token")) {
          headers.set("X-CSRF-Token", token);
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
        this.setRequestHeader("X-CSRF-Token", token);
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

  document.addEventListener("submit", function (e) {
    if (e.target && e.target.tagName === "FORM") {
      addFormField(e.target);
    }
  }, true);

  if (typeof window !== "undefined") {
    window.getCsrfToken = getCsrfToken;
  }
})();
