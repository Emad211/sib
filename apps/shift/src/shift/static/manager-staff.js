/**
 * Sib Shift — the manager's staff screen (ADR-022).
 *
 * Adds a person, switches a role on or off for the next shift and deactivates an account. Every
 * change goes through the staff API, which is what login and permissions read, so a saved change is
 * the truth the next sign-in sees. `csrf.js` patches `fetch`; a change reloads the page.
 */
(function () {
  "use strict";

  function reason(payload) {
    var message = payload && payload.error && payload.error.message;
    return message ? "انجام نشد: " + message : "انجام نشد؛ دوباره تلاش کنید.";
  }

  function send(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (payload) {
          if (!response.ok) throw new Error(reason(payload));
          return payload;
        });
    });
  }

  function say(node, text, kind) {
    var status = node.querySelector(".mg-status");
    if (!status) return;
    status.textContent = text;
    status.className = "mg-status" + (kind ? " is-" + kind : "");
  }

  var add = document.getElementById("staff-add");
  if (add) {
    add.addEventListener("submit", function (event) {
      event.preventDefault();
      var roles = Array.prototype.map.call(add.querySelectorAll("input[name=roles]:checked"), function (box) {
        return box.value;
      });
      if (!roles.length) {
        say(add, "دست‌کم یک نقش را انتخاب کنید.", "error");
        return;
      }
      var button = add.querySelector("button[type=submit]");
      button.disabled = true;
      send("POST", "/api/v1/staff", {
        full_name: add.elements.full_name.value,
        mobile: add.elements.mobile.value,
        pin: add.elements.pin.value,
        roles: roles,
      })
        .then(function () {
          window.location.reload();
        })
        .catch(function (error) {
          say(add, error.message, "error");
          button.disabled = false;
        });
    });
  }

  document.querySelectorAll("[data-staff]").forEach(function (row) {
    var id = row.dataset.staff;
    row.querySelectorAll("input[data-role]").forEach(function (box) {
      box.addEventListener("change", function () {
        send("PUT", "/api/v1/staff/" + encodeURIComponent(id) + "/roles/" + encodeURIComponent(box.dataset.role), {
          granted: box.checked,
        })
          .then(function () {
            window.location.reload();
          })
          .catch(function (error) {
            box.checked = !box.checked;
            say(row, error.message, "error");
          });
      });
    });
    row.querySelectorAll("button[data-active]").forEach(function (button) {
      button.addEventListener("click", function () {
        button.disabled = true;
        send("POST", "/api/v1/staff/" + encodeURIComponent(id) + "/active", {
          active: button.dataset.active === "true",
        })
          .then(function () {
            window.location.reload();
          })
          .catch(function (error) {
            say(row, error.message, "error");
            button.disabled = false;
          });
      });
    });
  });
})();
