/**
 * Sib Shift — the flow's own initial count (owner decision 9, ADR-022 item 5).
 *
 * The receptionist counts the reception flow (box included) and the nurse the nursing flow, one task
 * per screen on their own phone, and the count is recorded under their own name with them becoming the
 * flow's custodian. The manager's /manager/baseline tool stays the fallback.
 *
 * Blind: nothing here shows an expected quantity — the person types only what they counted, and the
 * flow's own blind count compares against it afterwards. It needs the clinic server (the count is one
 * event, never an offline record), so no answer is kept on the phone.
 */
(function () {
  "use strict";

  var API = "/api/v1/handover/baseline";
  var SCREENS = ["baseline-none", "baseline-pick", "baseline-wizard", "baseline-done"];
  var PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  var ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";

  var data = { flows: [] };
  try {
    data = JSON.parse(document.getElementById("baseline-data").textContent);
  } catch (error) {
    data = { flows: [] };
  }
  var flows = data.flows || [];
  var stepBox = document.getElementById("baseline-step");
  var progress = document.getElementById("baseline-progress");
  var message = document.getElementById("baseline-message");
  var state = null;

  function fa(value) {
    return Number(value).toLocaleString("fa-IR");
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function showScreen(id) {
    SCREENS.forEach(function (name) {
      document.getElementById(name).classList.toggle("hidden", name !== id);
    });
  }

  function setMessage(text, kind) {
    message.textContent = text;
    message.className = "ho-message" + (kind ? " is-" + kind : "");
  }

  function clearMessage() {
    message.textContent = "";
    message.className = "ho-message hidden";
  }

  /** Persian and Arabic digits, separators and spaces folded to a plain ASCII number. */
  function digits(raw) {
    return String(raw === undefined || raw === null ? "" : raw)
      .replace(/[,\u066B\u066C\s]/g, "")
      .replace(/[۰-۹]/g, function (digit) {
        return String(PERSIAN_DIGITS.indexOf(digit));
      })
      .replace(/[٠-٩]/g, function (digit) {
        return String(ARABIC_DIGITS.indexOf(digit));
      });
  }

  function wholeNumber(raw) {
    var value = digits(raw);
    if (value === "") return { error: "عدد را وارد کنید؛ اگر قفسه خالی است صفر بزنید." };
    if (!/^\d+$/.test(value)) return { error: "فقط عدد درست است؛ دوباره وارد کنید." };
    return { value: Number(value) };
  }

  function countField(value) {
    var input = document.createElement("input");
    input.className = "ho-number";
    input.type = "text";
    input.inputMode = "numeric";
    input.autocomplete = "off";
    input.dir = "ltr";
    if (value !== null && value !== undefined) input.value = String(value);
    return input;
  }

  function action(label, onClick, primary) {
    var button = element("button", "ho-action" + (primary ? " ho-action-primary" : ""), label);
    button.type = "button";
    button.addEventListener("click", onClick);
    return button;
  }

  function stepsOf(flow) {
    var steps = [];
    if (flow.cash) steps.push({ kind: "cash" });
    flow.locations.forEach(function (location) {
      steps.push({ kind: "location", location: location });
    });
    steps.push({ kind: "review" });
    return steps;
  }

  function start(flow) {
    state = { flow: flow, steps: stepsOf(flow), index: 0, cash: null, counts: {} };
    showScreen("baseline-wizard");
    render();
  }

  function go(delta) {
    state.index += delta;
    render();
  }

  function render() {
    clearMessage();
    var step = state.steps[state.index];
    progress.textContent = "گام " + fa(state.index + 1) + " از " + fa(state.steps.length);
    stepBox.replaceChildren();
    if (step.kind === "cash") renderCash();
    else if (step.kind === "location") renderLocation(step.location);
    else renderReview();
    if (state.index > 0) {
      stepBox.appendChild(action("مرحلهٔ قبل", function () { go(-1); }));
    }
  }

  function renderCash() {
    var job = element("div", "ho-job");
    job.appendChild(element("h2", "ho-job-title", "شمارش صندوق"));
    job.appendChild(element("p", "ho-hint", "صندوق را بشمارید و عدد را خودتان وارد کنید."));
    var input = countField(state.cash);
    input.id = "baseline-cash-input";
    input.setAttribute("aria-label", "شمارش صندوق");
    job.appendChild(input);
    var next = action("ادامه", function () {
      var answer = wholeNumber(input.value);
      if (answer.error) {
        setMessage(answer.error, "error");
        return;
      }
      state.cash = answer.value;
      go(1);
    }, true);
    next.id = "baseline-cash-next";
    job.appendChild(next);
    stepBox.appendChild(job);
    input.focus();
  }

  function renderLocation(location) {
    var job = element("div", "ho-job");
    job.appendChild(element("h2", "ho-job-title", location.name));
    job.appendChild(element("p", "ho-hint", "هر قلم را بشمارید؛ عدد را خودتان وارد می‌کنید."));
    var fields = location.items.map(function (item) {
      var row = element("div", "ho-checklist-item");
      row.appendChild(
        element("p", "ho-checklist-name", (window.itemIcons ? window.itemIcons.for(item.icon) : "") + " " + item.name)
      );
      row.appendChild(element("p", "ho-hint", item.unit));
      var key = item.item_id + "|" + location.location_id;
      var input = countField(state.counts[key]);
      input.dataset.itemId = item.item_id;
      input.dataset.locationId = location.location_id;
      input.dataset.key = key;
      input.setAttribute("aria-label", "شمارش " + item.name);
      row.appendChild(input);
      job.appendChild(row);
      return input;
    });
    var next = action("ادامه", function () {
      var inputs = Array.prototype.slice.call(stepBox.querySelectorAll("input[data-key]"));
      var answers = {};
      for (var index = 0; index < inputs.length; index += 1) {
        var answer = wholeNumber(inputs[index].value);
        if (answer.error) {
          setMessage(answer.error, "error");
          return;
        }
        answers[inputs[index].dataset.key] = answer.value;
      }
      Object.keys(answers).forEach(function (key) {
        state.counts[key] = answers[key];
      });
      go(1);
    }, true);
    next.id = "baseline-items-next";
    job.appendChild(next);
    stepBox.appendChild(job);
    if (fields.length > 0) fields[0].focus();
  }

  function itemLabel(flow, key) {
    var parts = key.split("|");
    var name = parts[0];
    flow.locations.forEach(function (location) {
      location.items.forEach(function (item) {
        if (item.item_id === parts[0] && location.location_id === parts[1]) name = item.name;
      });
    });
    return name;
  }

  function renderReview() {
    var job = element("div", "ho-job");
    job.appendChild(element("h2", "ho-job-title", "مرور شمارش"));
    job.appendChild(element("p", "ho-hint", "این‌ها عددهایی است که خودتان شمردید."));
    if (state.cash !== null) {
      job.appendChild(element("p", "ho-text", "صندوق: " + fa(state.cash) + " تومان"));
    }
    Object.keys(state.counts).forEach(function (key) {
      job.appendChild(element("p", "ho-text", itemLabel(state.flow, key) + ": " + fa(state.counts[key])));
    });
    var submit = action("ثبت شمارش اولیه", function () { submitBaseline(submit); }, true);
    submit.id = "baseline-submit";
    job.appendChild(submit);
    stepBox.appendChild(job);
  }

  function submitBaseline(button) {
    var items = Object.keys(state.counts).map(function (key) {
      var parts = key.split("|");
      return { item_id: parts[0], location_id: parts[1], quantity: state.counts[key] };
    });
    var body = {
      flow: state.flow.id,
      opening_cash: state.flow.cash ? state.cash : null,
      items: items,
    };
    button.disabled = true;
    setMessage("در حال ثبت…", "");
    fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (payload) {
            if (!response.ok) {
              throw new Error((payload && payload.error && payload.error.message) || "ثبت نشد؛ دوباره تلاش کنید.");
            }
            return payload;
          });
      })
      .then(function () {
        showScreen("baseline-done");
      })
      .catch(function (error) {
        setMessage(error.message, "error");
        button.disabled = false;
      });
  }

  function boot() {
    if (flows.length === 0) {
      showScreen("baseline-none");
      return;
    }
    if (flows.length === 1) {
      start(flows[0]);
      return;
    }
    var box = document.getElementById("baseline-pick-buttons");
    flows.forEach(function (flow) {
      box.appendChild(action(flow.label, function () { start(flow); }, true));
    });
    showScreen("baseline-pick");
  }

  boot();
})();
