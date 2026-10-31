/**
 * Sib Shift — the purchasing and warehouse screen (SHF-4, S01 §4, R-10).
 *
 * One screen, one job per role: the storekeeper drafts and counts, the manager decides, the purchaser
 * hands the order to a vendor, reception takes the parcel in, the accountant types the invoice and the
 * payments. The page carries no clinic data of its own: it asks `GET /api/v1/purchase/state`, which the
 * service already cuts down to the caller's permissions, and posts one named action at a time to
 * `POST /api/v1/purchase/action`. A section the answer does not carry is never drawn, so nobody without
 * `purchase.payment.record` ever receives a price (R-01, R-07).
 *
 * The weekly count is blind (P-11): the answer carries no projected quantity, so the quantity boxes
 * start empty and nothing writes a current figure back beside them.
 *
 * Every server value reaches the DOM through `textContent`, never `innerHTML`.
 */
(function () {
  "use strict";

  var STATE_URL = "/api/v1/purchase/state";
  var ACTION_URL = "/api/v1/purchase/action";
  var MAX_LINES = 5;
  // The main store's id, pinned the way the service pins it (`services/purchase.MAIN_STORE`): the read
  // model does not name the store yet, and every shelf charge starts there (P-10).
  var STORE = "main_store";

  var PROBLEMS = [
    ["short", "کم"],
    ["extra", "زیاد"],
    ["wrong", "اشتباه"],
    ["broken", "شکسته"],
  ];
  var METHODS = [
    ["cash", "نقد"],
    ["card", "کارت"],
    ["transfer", "انتقال"],
  ];
  // The states S03 §4 names, in the clinic's own words.
  var ORDER_LABELS = {
    draft: "پیش‌نویس",
    submitted: "منتظر تصمیم مدیر",
    approved: "تأییدشده",
    rejected: "ردشده",
    placed: "سفارش داده شد",
  };
  var DELIVERY_LABELS = {
    received: "دریافت‌شده",
    flagged: "بار مشکل‌دار",
    in_transit: "در راه",
    with_storekeeper: "نزد انباردار",
    returned: "مرجوع",
    digitized: "فاکتور ثبت‌شده",
    paid: "پرداخت‌شده",
  };
  var WEEKLY_LABELS = {
    done: "شمارش هفتگی این هفته انجام شده است.",
    due: "شمارش هفتگی این هفته هنوز انجام نشده است.",
    overdue: "شمارش هفتگی این هفته عقب افتاده است.",
  };
  // What the service accepts an invoice for: taken in and not closed by a return. A problem flag does
  // not close the price entry — it locks the payment until the manager decides (P-06, P-07).
  var RECEIVABLE = ["received", "flagged", "in_transit", "with_storekeeper"];
  var MESSAGES = {
    waiting: "در حال ثبت…",
    failure: "ثبت نشد: ",
    draft: "سفارش ثبت شد.",
    decide: "تصمیم ثبت شد.",
    place: "سفارش به فروشنده داده شد.",
    receive: "دریافت بار ثبت شد.",
    transfer: "بار به انباردار تحویل شد.",
    flag: "برچسب مشکل ثبت شد.",
    digitize: "فاکتور ثبت شد.",
    payment: "پرداخت ثبت شد.",
    vendorReturn: "مرجوعی ثبت شد.",
    credit: "بستانکاری ثبت شد.",
    restock: "شارژ قفسه ثبت شد.",
    offer: "شارژ قفسه برای تأیید فرستاده شد.",
    confirm: "تأیید ثبت شد.",
    parcelCount: "شمارش بار ثبت شد.",
    count: "شمارش هفتگی ثبت شد.",
    sample: "شمارش نمونه ثبت شد.",
    unreadable: "وضعیت خرید و انبار خوانده نشد؛ صفحه را تازه کنید.",
  };
  var EMPTY = {
    decide: "سفارشی در انتظار تصمیم مدیر نیست.",
    place: "سفارش تأییدشده‌ای برای سفارش دادن نیست.",
    transit: "باری در راه نیست.",
    invoice: "فاکتوری برای پرداخت نیست.",
    vendorReturn: "باری برای مرجوعی نیست.",
    credit: "مرجوعی‌ای برای بستانکاری نیست.",
    restock: "قلمی در انبار برای شارژ قفسه نیست.",
    count: "قلمی در انبار برای شمارش نیست.",
  };
  var LINE_INCOMPLETE = "ردیف‌های ناقص را کامل کنید.";
  var LINE_MISSING = "دست‌کم یک ردیف کالا لازم است.";
  var NO_ITEM = "کالای هر ردیف را انتخاب کنید.";
  var NO_NUMBER = "تعداد شمارش‌شده را درست وارد کنید.";
  var NO_COUNT = "دست‌کم یک قلم را بشمارید.";
  var NO_INVOICE_REFERENCE = "شمارهٔ فاکتور را وارد کنید.";
  var NO_VENDOR = "نام فروشنده را وارد کنید.";
  var NO_RECIPIENT = "شناسهٔ کارمند انباردار گیرنده را وارد کنید.";
  var NO_REASON = "دلیل را بنویسید.";
  var NO_AMOUNT = "مبلغ را درست وارد کنید.";
  var NO_METHOD = "روش پرداخت را انتخاب کنید.";
  var NO_QUANTITY = "تعداد را درست وارد کنید.";
  // F1: the invoice photo is mandatory at the door, the payment receipt's is optional (decision 6).
  var NO_INVOICE_PHOTO = "عکس فاکتور را بگیرید.";
  var PHOTO_FAILED = "عکس ثبت نشد؛ دوباره بگیرید.";

  var clinic = null;
  var busy = false;

  // ---------------------------------------------------------------- little helpers

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function faNumber(value) {
    return Number(value).toLocaleString("fa-IR");
  }

  function faTime(value) {
    var moment = new Date(value);
    if (isNaN(moment.getTime())) return String(value || "");
    return moment.toLocaleString("fa-IR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Tehran",
    });
  }

  function faDate(value) {
    var moment = new Date(value);
    if (isNaN(moment.getTime())) return String(value || "");
    return moment.toLocaleDateString("fa-IR", { timeZone: "Asia/Tehran" });
  }

  function may(permission) {
    return Boolean(clinic) && clinic.permissions.indexOf(permission) !== -1;
  }

  function setStatus(text, kind) {
    var status = byId("purchase-status");
    if (!status) return;
    status.textContent = text;
    status.className = "badge feedback-badge " + (kind || "badge-online");
    status.classList.remove("hidden");
  }

  function fail(error) {
    setStatus(MESSAGES.failure + error.message, "badge-failed");
    return false;
  }

  // ---------------------------------------------------------------- talking to the server

  /** One named action through the one write door; a refusal is the service's own Persian message. */
  function post(action, data) {
    return fetch(ACTION_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ action: action, data: data || {} }),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (payload) {
          if (!response.ok) {
            var message = payload && payload.error && payload.error.message;
            throw new Error(message || "پاسخ " + response.status);
          }
          return payload;
        });
    });
  }

  /** F1: a photo is stored under its own content hash, so the record names a file the app really holds. */
  function uploadPhoto(file, required) {
    if (!file) {
      return required ? Promise.reject(new Error(NO_INVOICE_PHOTO)) : Promise.resolve(null);
    }
    return file.arrayBuffer().then(function (bytes) {
      return crypto.subtle.digest("SHA-256", bytes).then(function (digest) {
        var hash = Array.prototype.map
          .call(new Uint8Array(digest), function (byte) {
            return ("0" + byte.toString(16)).slice(-2);
          })
          .join("");
        return fetch("/api/v1/photos/" + hash, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "image/jpeg" },
          body: file,
        }).then(function (response) {
          if (!response.ok) throw new Error(PHOTO_FAILED);
          return hash;
        });
      });
    });
  }
  /** Steps run one after another: a plain `{action, data}`, or a function given the last answer. */
  function chain(steps) {
    return steps.reduce(function (previous, step) {
      return previous.then(function (answer) {
        if (typeof step === "function") return step(answer);
        return post(step.action, step.data);
      });
    }, Promise.resolve());
  }

  /** Send the steps, redraw from the server, then say in Persian what happened. */
  function run(steps, message) {
    var list = Array.isArray(steps) ? steps : [steps];
    busy = true;
    setStatus(MESSAGES.waiting, "badge-queued");
    chain(list)
      .then(reload)
      .then(function () {
        busy = false;
        setStatus(message, "badge-online");
      })
      .catch(function (error) {
        busy = false;
        fail(error);
      });
    return true;
  }

  function reload() {
    return fetch(STATE_URL, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("پاسخ " + response.status);
        return response.json();
      })
      .then(function (payload) {
        clinic = payload;
        render();
      });
  }

  // ---------------------------------------------------------------- controls

  function field(text, control, extra) {
    var box = el("div", "pu-field" + (extra ? " " + extra : ""));
    box.appendChild(el("span", "pu-field-label", text));
    box.appendChild(control);
    return box;
  }

  function textInput(placeholder) {
    var input = el("input", "form-control");
    input.type = "text";
    input.autocomplete = "off";
    input.maxLength = 200;
    if (placeholder) input.placeholder = placeholder;
    return input;
  }

  function idInput(placeholder) {
    var input = textInput(placeholder);
    input.dir = "ltr";
    input.maxLength = 128;
    return input;
  }

  function numberInput(minimum) {
    var input = el("input", "form-control");
    input.type = "number";
    input.step = "1";
    input.inputMode = "numeric";
    input.min = String(minimum === undefined ? 1 : minimum);
    return input;
  }

  function dateInput() {
    var input = el("input", "form-control");
    input.type = "text";
    input.setAttribute("data-jalali-date", "");
    return input;
  }

  function selectControl(choices, placeholder) {
    var select = el("select", "form-control");
    var empty = el("option", null, placeholder);
    empty.value = "";
    select.appendChild(empty);
    choices.forEach(function (choice) {
      var option = el("option", null, choice[1]);
      option.value = choice[0];
      select.appendChild(option);
    });
    return select;
  }

  /** The item of a line: a catalog choice when the answer carries the store's items, else its id. */
  function itemControl(items) {
    var control;
    if (items && items.length > 0) {
      control = selectControl(
        items.map(function (item) {
          return [item.item_id, item.unit ? item.name + " — " + item.unit : item.name];
        }),
        "کالا را انتخاب کنید…"
      );
      items.forEach(function (item) {
        var option = control.querySelector('option[value="' + item.item_id + '"]');
        if (option) option.dataset.home = item.location_id;
      });
    } else {
      control = idInput("شناسهٔ کالا");
    }
    control.dataset.item = "";
    return control;
  }

  /** The place a line belongs to: a catalog choice when known, else the shelf's own id. */
  function locationControl(locations) {
    var control;
    if (locations && locations.length > 0) {
      control = selectControl(
        locations.map(function (place) {
          return [place.id, place.name];
        }),
        "محل را انتخاب کنید…"
      );
    } else {
      control = idInput("شناسهٔ محل");
    }
    control.dataset.location = "";
    return control;
  }

  /** One button of a card; `do` names the action it posts, so a test can find it without the prose. */
  function actionButton(text, handler, className, action) {
    var button = el("button", "btn " + (className || "btn-primary"), text);
    button.type = "button";
    if (action) button.dataset.do = action;
    button.addEventListener("click", function () {
      if (busy || button.disabled) return;
      if (handler(button) === false) return;
      button.disabled = true;
    });
    return button;
  }

  function card(title, meta) {
    var box = el("div", "pu-card");
    var head = el("div", "pu-card-head");
    head.appendChild(el("h3", "pu-card-title", title));
    if (meta) head.appendChild(el("p", "pu-meta", meta));
    box.appendChild(head);
    return box;
  }

  function emptyLine(text) {
    return el("p", "pu-hint", text);
  }

  /** Every item the answer names: the store's own items plus the below-minimum suggestions (P-01, M-21). */
  function knownItems() {
    var pool = [];
    function add(item) {
      if (!item || !item.item_id) return;
      var seen = pool.some(function (candidate) {
        return candidate.item_id === item.item_id;
      });
      if (!seen) pool.push(item);
    }
    (clinic && clinic.warehouse ? clinic.warehouse.items : []).forEach(add);
    (clinic && clinic.below_minimum ? clinic.below_minimum : []).forEach(add);
    return pool;
  }

  function itemName(itemId) {
    var items = knownItems();
    var match = items.filter(function (item) {
      return item.item_id === itemId;
    })[0];
    return match ? match.name : itemId;
  }

  function locationName(locationId) {
    var places = clinic && clinic.warehouse ? clinic.warehouse.locations : [];
    var match = places.filter(function (place) {
      return place.id === locationId;
    })[0];
    return match ? match.name : locationId;
  }

  function problemLabel(reason) {
    var match = PROBLEMS.filter(function (problem) {
      return problem[0] === reason;
    })[0];
    return match ? match[1] : reason;
  }

  /** One order or parcel line as a person reads it: the item, how many, and where it belongs. */
  function lineText(line) {
    var parts = [itemName(line.item_id), faNumber(line.quantity)];
    if (line.location_id) parts.push(locationName(line.location_id));
    return parts.join(" × ");
  }

  function lineList(lines) {
    var list = el("ul", "pu-lines");
    lines.forEach(function (line) {
      list.appendChild(el("li", null, lineText(line)));
    });
    return list;
  }

  // ---------------------------------------------------------------- one line of a form

  function lineRow(spec) {
    var row = el("div", "pu-line");
    if (spec.priced) row.classList.add("is-priced");
    else if (!spec.located) row.classList.add("is-plain");
    var item = itemControl(spec.items);
    row.appendChild(field("کالا", item, "is-item"));
    var place = null;
    if (spec.located) {
      place = locationControl(spec.locations);
      row.appendChild(field("محل", place));
    }
    var quantity = numberInput(1);
    quantity.dataset.quantity = "";
    row.appendChild(field("تعداد", quantity));
    if (spec.priced) {
      var price = numberInput(0);
      price.dataset.price = "";
      row.appendChild(field("نرخ واحد (تومان)", price));
    }
    // The catalog knows each item's home, so picking an item fills the place it belongs to (P-10).
    if (place && item.tagName === "SELECT") {
      item.addEventListener("change", function () {
        var chosen = item.options[item.selectedIndex];
        var home = chosen ? chosen.dataset.home : "";
        if (home) place.value = home;
      });
    }
    return row;
  }

  function lineRows(spec) {
    var box = el("div", "pu-lines-box");
    var rows = [];
    for (var index = 0; index < MAX_LINES; index += 1) {
      var row = lineRow(spec);
      rows.push(row);
      box.appendChild(row);
    }
    return { box: box, rows: rows };
  }

  function valueOf(row, selector) {
    var control = row.querySelector(selector);
    return control ? String(control.value).trim() : "";
  }

  /** The filled rows of a line box, validated the way the payload would validate them. */
  function readLines(box, spec) {
    var lines = [];
    var incomplete = false;
    var itemMissing = false;
    Array.prototype.forEach.call(box.querySelectorAll(".pu-line"), function (row) {
      var item = valueOf(row, "[data-item]");
      var quantity = valueOf(row, "[data-quantity]");
      var price = spec.priced ? valueOf(row, "[data-price]") : "";
      if (item === "" && quantity === "" && price === "") return;
      var line = { item_id: item, quantity: Number(quantity) };
      if (spec.located) line.location_id = valueOf(row, "[data-location]");
      if (spec.priced) line.unit_price_toman = Number(price);
      var broken =
        item === "" ||
        !Number.isInteger(line.quantity) ||
        line.quantity < 1 ||
        (spec.located && line.location_id === "") ||
        (spec.priced && (!Number.isInteger(line.unit_price_toman) || line.unit_price_toman < 0));
      if (broken) {
        if (item === "") itemMissing = true;
        incomplete = true;
        return;
      }
      lines.push(line);
    });
    if (incomplete) throw new Error(itemMissing ? NO_ITEM : LINE_INCOMPLETE);
    if (lines.length === 0) throw new Error(LINE_MISSING);
    return lines;
  }

  // ---------------------------------------------------------------- the sections

  function sectionBody(id) {
    var section = byId(id);
    return section ? section.querySelector("[data-body]") : null;
  }

  function showSection(id, visible) {
    var section = byId(id);
    if (section) section.hidden = !visible;
  }

  /** The manager's decisions: approve, reject, or send the order back with a short note (P-02). */
  function renderDecide() {
    var orders = clinic.orders;
    var visible = orders !== null && may("purchase.order.decide");
    showSection("purchase-decide", visible);
    if (!visible) return;
    var box = sectionBody("purchase-decide");
    box.textContent = "";
    var waiting = orders.filter(function (order) {
      return order.status === "submitted";
    });
    if (waiting.length === 0) {
      box.appendChild(emptyLine(EMPTY.decide));
      return;
    }
    waiting.forEach(function (order) {
      var cardBox = card("سفارش " + faTime(order.at_tehran), ORDER_LABELS[order.status] || order.status);
      cardBox.appendChild(lineList(order.lines));
      if (order.note) cardBox.appendChild(el("p", "pu-note-text", "یادداشت: " + order.note));
      var note = textInput("یادداشت تغییر (اختیاری)");
      note.dataset.note = "";
      cardBox.appendChild(field("یادداشت تغییر", note));
      var actions = el("div", "pu-actions");
      actions.appendChild(
        actionButton(
          "تأیید",
          function () {
            return run(
              { action: "order.decide", data: { order_id: order.order_id, decision: "approved" } },
              MESSAGES.decide
            );
          },
          null,
          "order.decide.approved"
        )
      );
      actions.appendChild(
        actionButton(
          "رد",
          function () {
            return run(
              { action: "order.decide", data: { order_id: order.order_id, decision: "rejected" } },
              MESSAGES.decide
            );
          },
          "btn-danger",
          "order.decide.rejected"
        )
      );
      actions.appendChild(
        actionButton(
          "تغییر",
          function () {
            var data = { order_id: order.order_id, decision: "changed" };
            var text = note.value.trim();
            if (text) data.note = text;
            return run({ action: "order.decide", data: data }, MESSAGES.decide);
          },
          "btn-secondary",
          "order.decide.changed"
        )
      );
      cardBox.appendChild(actions);
      box.appendChild(cardBox);
    });
  }

  /** The purchaser's one job: an approved order goes to a vendor, with its reference (P-03). */
  function renderPlace() {
    var orders = clinic.orders;
    var visible = orders !== null && may("purchase.order.place");
    showSection("purchase-place", visible);
    if (!visible) return;
    var box = sectionBody("purchase-place");
    box.textContent = "";
    var approved = orders.filter(function (order) {
      return order.status === "approved";
    });
    if (approved.length === 0) {
      box.appendChild(emptyLine(EMPTY.place));
      return;
    }
    approved.forEach(function (order) {
      var cardBox = card("سفارش " + faTime(order.at_tehran), ORDER_LABELS[order.status] || order.status);
      cardBox.appendChild(lineList(order.lines));
      var vendor = textInput("نام فروشنده");
      vendor.dataset.vendor = "";
      var reference = textInput("شمارهٔ سفارش یا مرجع (اختیاری)");
      reference.dataset.reference = "";
      cardBox.appendChild(field("فروشنده", vendor));
      cardBox.appendChild(field("مرجع", reference));
      cardBox.appendChild(
        actionButton("سفارش داده شد", function () {
          var name = vendor.value.trim();
          if (!name) return fail(new Error(NO_VENDOR));
          var data = { order_id: order.order_id, vendor: name };
          var text = reference.value.trim();
          if (text) data.reference = text;
          return run({ action: "order.place", data: data }, MESSAGES.place);
        }, null, "order.place")
      );
      box.appendChild(cardBox);
    });
  }

  /** The storekeeper's draft: the below-minimum items are the suggestion, five lines at most (P-01). */
  function renderDraft() {
    var orders = clinic.orders;
    var visible = orders !== null && may("purchase.order.draft");
    showSection("purchase-draft", visible);
    if (!visible) return;
    var box = sectionBody("purchase-draft");
    box.textContent = "";
    var items = knownItems();
    var places = clinic.warehouse ? clinic.warehouse.locations : [];
    var lines = lineRows({ items: items, locations: places, located: true });
    var note = textInput("یادداشت (اختیاری)");
    note.dataset.note = "";
    var suggestions = clinic.below_minimum || [];
    if (suggestions.length > 0) {
      box.appendChild(el("p", "pu-hint", "زیر حداقل انبار (پیشنهاد خرید):"));
      var list = el("ul", "pu-suggestions");
      suggestions.forEach(function (suggestion) {
        var chip = el("button", "pu-suggestion", suggestion.name);
        chip.type = "button";
        chip.addEventListener("click", function () {
          suggest(suggestion, lines.rows);
        });
        list.appendChild(chip);
      });
      box.appendChild(list);
    }
    box.appendChild(lines.box);
    box.appendChild(field("یادداشت", note));
    box.appendChild(
      actionButton("ارسال سفارش برای تصمیم", function () {
        var payload;
        try {
          payload = { lines: readLines(lines.box, { located: true }) };
        } catch (error) {
          return fail(error);
        }
        var text = note.value.trim();
        if (text) payload.note = text;
        return run({ action: "order.draft", data: payload }, MESSAGES.draft);
      }, null, "order.draft")
    );
    // An order the manager sent back keeps its id: the fresh draft returns under the same one (P-02).
    orders
      .filter(function (order) {
        return order.status === "draft";
      })
      .forEach(function (order) {
        var cardBox = card("پیش‌نویس " + faTime(order.at_tehran), ORDER_LABELS.draft);
        cardBox.appendChild(lineList(order.lines));
        if (order.note) cardBox.appendChild(el("p", "pu-note-text", "یادداشت مدیر: " + order.note));
        cardBox.appendChild(
          actionButton("ارسال دوباره برای تصمیم", function () {
            return run(
              {
                action: "order.draft",
                data: {
                  order_id: order.order_id,
                  lines: order.lines.map(function (line) {
                    return { item_id: line.item_id, location_id: line.location_id, quantity: line.quantity };
                  }),
                },
              },
              MESSAGES.draft
            );
          }, null, "order.draft.again")
        );
        box.appendChild(cardBox);
      });
  }

  /** The suggestion chip fills the first empty line, so the quantity stays the person's own guess. */
  function suggest(suggestion, rows) {
    for (var index = 0; index < rows.length; index += 1) {
      var control = rows[index].querySelector("[data-item]");
      if (!control || control.value) continue;
      control.value = suggestion.item_id;
      control.dispatchEvent(new Event("change"));
      return;
    }
  }

  /** Reception takes the parcel in: units only, no price, and a problem label if it has one (P-04/P-06). */
  function renderReceive() {
    var visible = clinic.deliveries !== null && may("purchase.delivery.receive");
    showSection("purchase-receive", visible);
    if (!visible) return;
    var box = sectionBody("purchase-receive");
    box.textContent = "";
    var items = knownItems();
    var places = clinic.warehouse ? clinic.warehouse.locations : [];
    var placed = clinic.orders
      ? clinic.orders.filter(function (order) {
          return order.status === "placed";
        })
      : [];
    var order = null;
    if (placed.length > 0) {
      order = selectControl(
        placed.map(function (candidate) {
          return [
            candidate.order_id,
            faTime(candidate.at_tehran) + " — " + (candidate.vendor || "بدون فروشنده"),
          ];
        }),
        "بدون سفارش"
      );
      box.appendChild(field("سفارش (اختیاری)", order));
    }
    var invoiceReference = textInput("شمارهٔ فاکتور");
    invoiceReference.dataset.invoiceReference = "";
    box.appendChild(field("شمارهٔ فاکتور", invoiceReference));
    var lines = lineRows({ items: items, locations: places, located: true });
    box.appendChild(lines.box);
    if (order) {
      // The counted lines of the chosen order are what the door checks against, so they are offered (P-04).
      order.addEventListener("change", function () {
        fillFromOrder(order.value, lines.rows, placed);
      });
    }
    var packages = numberInput(1);
    packages.dataset.packages = "";
    box.appendChild(field("تعداد بسته (اختیاری)", packages));
    var reason = selectControl(PROBLEMS, "مشکلی نداشت");
    reason.dataset.reason = "";
    box.appendChild(field("مشکل بار (اختیاری)", reason));
    // F1: the vendor's invoice is photographed at the door before the receipt is recorded (decision 6).
    var invoicePhoto = document.createElement("input");
    invoicePhoto.type = "file";
    invoicePhoto.accept = "image/jpeg";
    invoicePhoto.capture = "environment";
    invoicePhoto.dataset.invoicePhoto = "";
    box.appendChild(field("عکس فاکتور (الزامی)", invoicePhoto));
    box.appendChild(
      actionButton("ثبت دریافت بار", function () {
        var payload;
        try {
          payload = readReceive(invoiceReference, lines.box);
        } catch (error) {
          return fail(error);
        }
        if (order && order.value) payload.order_id = order.value;
        var count = Number(packages.value);
        if (Number.isInteger(count) && count > 0) payload.packages = count;
        var steps = [
          function () {
            return uploadPhoto(invoicePhoto.files[0], true).then(function (hash) {
              payload.invoice_photo_sha256 = hash;
              return post("delivery.receive", payload);
            });
          },
        ];
        if (reason.value) {
          // The label names the id the server minted for this receipt, so it follows it (P-06).
          steps.push(function (answer) {
            return post("delivery.flag", { delivery_id: answer.delivery_id, reason: reason.value });
          });
        }
        return run(steps, MESSAGES.receive);
      }, null, "delivery.receive")
    );
    box.appendChild(
      el(
        "p",
        "pu-hint",
        "دریافت بار موجودی قفسه را جابه‌جا نمی‌کند؛ ورود کالا به قفسه، رویداد «و» در صفحهٔ شیفت است."
      )
    );
  }

  function readReceive(invoiceReference, box) {
    var reference = invoiceReference.value.trim();
    if (!reference) throw new Error(NO_INVOICE_REFERENCE);
    return { invoice_reference: reference, lines: readLines(box, { located: true }) };
  }

  function fillFromOrder(orderId, rows, placed) {
    var order = placed.filter(function (candidate) {
      return candidate.order_id === orderId;
    })[0];
    if (!order) return;
    rows.forEach(function (row) {
      row.querySelector("[data-item]").value = "";
      row.querySelector("[data-quantity]").value = "";
      var place = row.querySelector("[data-location]");
      if (place) place.value = "";
    });
    order.lines.slice(0, rows.length).forEach(function (line, index) {
      var row = rows[index];
      row.querySelector("[data-item]").value = line.item_id;
      var place = row.querySelector("[data-location]");
      if (place) place.value = line.location_id;
      row.querySelector("[data-quantity]").value = String(line.quantity);
    });
  }

  /** The parcels still on their way to the storekeeper, and who holds each one (P-12). */
  function renderTransit() {
    var parcels = clinic.deliveries;
    var visible = parcels !== null && (may("parcel.handle") || may("purchase.delivery.receive"));
    showSection("purchase-transit", visible);
    if (!visible) return;
    var box = sectionBody("purchase-transit");
    box.textContent = "";
    var moving = parcels.filter(function (parcel) {
      return parcel.custody !== "storekeeper" && !parcel.returned;
    });
    if (moving.length === 0) {
      box.appendChild(emptyLine(EMPTY.transit));
      return;
    }
    var recipient = null;
    if (may("parcel.handle")) {
      recipient = idInput("شناسهٔ کارمند انباردار");
      box.appendChild(field("گیرندهٔ انباردار", recipient));
    }
    moving.forEach(function (parcel) {
      var meta = (DELIVERY_LABELS[parcel.status] || parcel.status) + " · " + custodyLabel(parcel);
      var cardBox = card("دریافت " + parcel.invoice_reference, meta + " · " + faTime(parcel.at_tehran));
      cardBox.appendChild(lineList(parcel.lines));
      if (parcel.packages) cardBox.appendChild(el("p", "pu-meta", "بسته: " + faNumber(parcel.packages)));
      if (parcel.flagged_reason) {
        cardBox.appendChild(el("p", "pu-flag", "برچسب مشکل: " + problemLabel(parcel.flagged_reason)));
      }
      var actions = el("div", "pu-actions");
      if (may("parcel.handle")) {
        actions.appendChild(
          actionButton("تحویل به انباردار", function () {
            var to = recipient.value.trim();
            if (!to) return fail(new Error(NO_RECIPIENT));
            return run(
              {
                action: "delivery.transfer",
                data: { delivery_id: parcel.delivery_id, to_staff_id: to, to_kind: "storekeeper" },
              },
              MESSAGES.transfer
            );
          }, null, "delivery.transfer")
        );
      }
      if (may("purchase.delivery.receive") && !parcel.flagged_reason) {
        var reason = selectControl(PROBLEMS, "نوع مشکل");
        cardBox.appendChild(field("برچسب مشکل", reason));
        actions.appendChild(
          actionButton(
            "برچسب مشکل",
            function () {
              if (!reason.value) return fail(new Error(NO_REASON));
              return run(
                { action: "delivery.flag", data: { delivery_id: parcel.delivery_id, reason: reason.value } },
                MESSAGES.flag
              );
            },
            "btn-secondary",
            "delivery.flag"
          )
        );
      }
      cardBox.appendChild(actions);
      box.appendChild(cardBox);
    });
  }

  function custodyLabel(parcel) {
    return parcel.custody === "storekeeper" ? "نزد انباردار" : "در دست شیفت";
  }

  /** The accountant's section: the invoice, then the payment; a locked parcel has no payment button. */
  function renderInvoice() {
    var invoices = clinic.invoices;
    showSection("purchase-invoice", invoices !== null);
    if (invoices === null) return;
    var box = sectionBody("purchase-invoice");
    box.textContent = "";
    if (invoices.length === 0) {
      box.appendChild(emptyLine(EMPTY.invoice));
      return;
    }
    invoices.forEach(function (invoice) {
      box.appendChild(invoiceCard(invoice));
    });
  }

  function invoiceCard(invoice) {
    var meta = "وضعیت: " + (DELIVERY_LABELS[invoice.status] || invoice.status);
    var cardBox = card("فاکتور " + invoice.invoice_reference, meta);
    if (invoice.lines.length > 0) {
      var list = el("ul", "pu-lines");
      invoice.lines.forEach(function (line) {
        list.appendChild(
          el(
            "li",
            null,
            itemName(line.item_id) +
              " × " +
              faNumber(line.quantity) +
              " × " +
              faNumber(line.unit_price_toman) +
              " = " +
              faNumber(line.total) +
              " تومان"
          )
        );
      });
      cardBox.appendChild(list);
      cardBox.appendChild(
        el(
          "p",
          "pu-meta",
          "جمع فاکتور: " +
            faNumber(invoice.invoice_total) +
            " تومان · پرداخت‌شده: " +
            faNumber(invoice.paid_total) +
            " تومان · بستانکاری: " +
            faNumber(invoice.credit_total) +
            " تومان"
        )
      );
    }
    if (invoice.returned) {
      cardBox.appendChild(el("p", "pu-meta", "این بار به فروشنده مرجوع شده است؛ پرداخت روی آن ثبت نمی‌شود."));
      return cardBox;
    }
    if (invoice.lines.length === 0) {
      if (RECEIVABLE.indexOf(invoice.status) === -1) {
        cardBox.appendChild(el("p", "pu-hint", "این بار هنوز برای فاکتور آماده نیست."));
        return cardBox;
      }
      cardBox.appendChild(digitizeForm(invoice));
    } else if (invoice.payment_locked) {
      cardBox.appendChild(lockLine());
      return cardBox;
    } else if (invoice.remaining <= 0) {
      cardBox.appendChild(el("p", "pu-paid", "پرداخت‌شده"));
      return cardBox;
    } else {
      cardBox.appendChild(paymentForm(invoice));
      return cardBox;
    }
    // The flag is still open: the prices may be typed, but no payment button is offered at all (P-06).
    if (invoice.payment_locked) cardBox.appendChild(lockLine());
    return cardBox;
  }

  function lockLine() {
    return el("p", "pu-locked", "پرداخت تا تصمیم مدیر قفل است");
  }

  function digitizeForm(invoice) {
    var box = el("div", "pu-form");
    box.appendChild(el("p", "pu-hint", "خطوط فاکتور را با نرخ واحد ثبت کنید؛ جمع را سرور حساب می‌کند."));
    var items = knownItems();
    var lines = lineRows({ items: items, located: false, priced: true });
    box.appendChild(lines.box);
    var reference = textInput("مرجع فاکتور (اختیاری)");
    box.appendChild(field("مرجع فاکتور", reference));
    box.appendChild(
      actionButton("ثبت فاکتور", function () {
        var payload;
        try {
          payload = { delivery_id: invoice.delivery_id, lines: readLines(lines.box, { priced: true }) };
        } catch (error) {
          return fail(error);
        }
        var text = reference.value.trim();
        if (text) payload.reference = text;
        return run({ action: "invoice.digitize", data: payload }, MESSAGES.digitize);
      }, null, "invoice.digitize")
    );
    return box;
  }

  function paymentForm(invoice) {
    var box = el("div", "pu-form");
    box.appendChild(el("p", "pu-hint", "ماندهٔ فاکتور: " + faNumber(invoice.remaining) + " تومان"));
    var amount = numberInput(1);
    amount.dataset.amount = "";
    box.appendChild(field("مبلغ (تومان)", amount));
    var method = selectControl(METHODS, "روش پرداخت");
    method.dataset.method = "";
    box.appendChild(field("روش پرداخت", method));
    var paidOn = dateInput();
    paidOn.dataset.paidOn = "";
    box.appendChild(field("تاریخ پرداخت (اختیاری)", paidOn));
    var receipt = textInput("شمارهٔ رسید (اختیاری)");
    receipt.dataset.receipt = "";
    box.appendChild(field("شمارهٔ رسید", receipt));
    // Decision 6: the payment receipt's photo is optional; a phone without a camera records the rest.
    var receiptPhoto = document.createElement("input");
    receiptPhoto.type = "file";
    receiptPhoto.accept = "image/jpeg";
    receiptPhoto.capture = "environment";
    receiptPhoto.dataset.receiptPhoto = "";
    box.appendChild(field("عکس رسید (اختیاری)", receiptPhoto));
    box.appendChild(
      actionButton("ثبت پرداخت", function () {
        if (!method.value) return fail(new Error(NO_METHOD));
        var value = Number(amount.value);
        if (!Number.isInteger(value) || value < 1) return fail(new Error(NO_AMOUNT));
        var data = { delivery_id: invoice.delivery_id, amount_toman: value, method: method.value };
        var paidIso = window.ShiftJalaliDatepicker.getIso(paidOn);
        if (paidIso) data.paid_on = paidIso;
        var text = receipt.value.trim();
        if (text) data.receipt_reference = text;
        var file = receiptPhoto.files[0];
        return run(
          [
            function () {
              return uploadPhoto(file, false).then(function (hash) {
                if (hash) data.receipt_photo_sha256 = hash;
              });
            },
            { action: "payment.record", data: data },
          ],
          MESSAGES.payment
        );
      }, null, "payment.record")
    );
    return box;
  }

  /** The storekeeper's vendor return, and the accountant's credit note for it (P-09). */
  function renderReturn() {
    var canReturn = clinic.deliveries !== null && clinic.is_storekeeper === true;
    var canCredit = clinic.invoices !== null && may("purchase.payment.record");
    showSection("purchase-return", canReturn || canCredit);
    if (!canReturn && !canCredit) return;
    var box = sectionBody("purchase-return");
    box.textContent = "";
    if (canReturn) {
      var parcels = clinic.deliveries.filter(function (parcel) {
        return !parcel.returned;
      });
      if (parcels.length === 0) box.appendChild(emptyLine(EMPTY.vendorReturn));
      parcels.forEach(function (parcel) {
        var cardBox = card("مرجوعی دریافت " + parcel.invoice_reference, faTime(parcel.at_tehran));
        cardBox.appendChild(lineList(parcel.lines));
        // A return names the parcel's own lines, so the picker offers exactly what arrived (P-09).
        var arrived = parcel.lines.map(function (line) {
          return { item_id: line.item_id, name: itemName(line.item_id) };
        });
        var lines = lineRows({ items: arrived, located: false });
        cardBox.appendChild(lines.box);
        var reason = textInput("دلیل مرجوعی");
        reason.dataset.returnReason = "";
        cardBox.appendChild(field("دلیل", reason));
        cardBox.appendChild(
          actionButton(
            "ثبت مرجوعی",
            function () {
              var text = reason.value.trim();
              if (!text) return fail(new Error(NO_REASON));
              var payload;
              try {
                payload = { delivery_id: parcel.delivery_id, lines: readLines(lines.box), reason: text };
              } catch (error) {
                return fail(error);
              }
              return run({ action: "return.record", data: payload }, MESSAGES.vendorReturn);
            },
            "btn-secondary",
            "return.record"
          )
        );
        box.appendChild(cardBox);
      });
    }
    if (canCredit) {
      var returned = clinic.invoices.filter(function (invoice) {
        return invoice.returned;
      });
      if (returned.length === 0 && !canReturn) box.appendChild(emptyLine(EMPTY.credit));
      returned.forEach(function (invoice) {
        var cardBox = card(
          "بستانکاری دریافت " + invoice.invoice_reference,
          "جمع فاکتور: " +
            faNumber(invoice.invoice_total) +
            " تومان · بستانکاری تا امروز: " +
            faNumber(invoice.credit_total) +
            " تومان"
        );
        var amount = numberInput(1);
        cardBox.appendChild(field("مبلغ بستانکاری (تومان)", amount));
        var note = textInput("یادداشت (اختیاری)");
        cardBox.appendChild(field("یادداشت", note));
        cardBox.appendChild(
          actionButton("ثبت بستانکاری", function () {
            var value = Number(amount.value);
            if (!Number.isInteger(value) || value < 1) return fail(new Error(NO_AMOUNT));
            var data = { delivery_id: invoice.delivery_id, amount_toman: value };
            var text = note.value.trim();
            if (text) data.note = text;
            return run({ action: "credit.record", data: data }, MESSAGES.credit);
          }, null, "credit.record")
        );
        box.appendChild(cardBox);
      });
    }
  }

  /** G3: what waits for this very person — a parcel in hand to count, a shelf charge to confirm. */
  function renderInbox() {
    var inbox = clinic.inbox;
    var waiting = inbox !== null ? inbox.parcels_to_count.length + inbox.restock_to_confirm.length : 0;
    showSection("purchase-inbox", waiting > 0);
    if (waiting === 0) return;
    var box = sectionBody("purchase-inbox");
    box.textContent = "";
    var names = {};
    knownItems().forEach(function (item) {
      names[item.item_id] = item.name;
    });
    inbox.parcels_to_count.forEach(function (parcel) {
      var card = el("div", "pu-card");
      card.appendChild(el("h3", null, "بار " + parcel.invoice_reference));
      card.appendChild(el("p", "pu-hint", "این بار به شما تحویل شده است؛ آنچه روی قفسه گذاشتید را بشمارید."));
      var rows = el("div", "pu-count-box");
      (parcel.items || []).forEach(function (line) {
        var itemId = line.item_id;
        var row = el("div", "pu-count-row");
        row.dataset.item = itemId;
        row.appendChild(el("span", null, line.name || names[itemId] || "کالا"));
        var counted = numberInput(0);
        counted.dataset.received = "";
        row.appendChild(field("شمارش", counted));
        rows.appendChild(row);
      });
      card.appendChild(rows);
      card.appendChild(
        actionButton("ثبت شمارش بار", function () {
          var lines = [];
          var bad = false;
          Array.prototype.forEach.call(rows.querySelectorAll(".pu-count-row"), function (row) {
            var value = Number(row.querySelector("[data-received]").value);
            if (!Number.isInteger(value) || value < 0) {
              bad = true;
              return;
            }
            lines.push({ item_id: row.dataset.item, quantity: value });
          });
          if (bad || lines.length === 0) return fail(new Error(NO_QUANTITY));
          return run(
            { action: "delivery.confirm", data: { delivery_id: parcel.delivery_id, lines: lines } },
            MESSAGES.parcelCount
          );
        }, null, "delivery.confirm")
      );
      box.appendChild(card);
    });
    inbox.restock_to_confirm.forEach(function (offer) {
      var card = el("div", "pu-card");
      card.appendChild(el("h3", null, offer.item_name || names[offer.item_id] || "کالا"));
      card.appendChild(
        el(
          "p",
          "pu-meta",
          "از " + (offer.from_location_name || "انبار اصلی") + " به " + (offer.to_location_name || "قفسه")
        )
      );
      var counted = numberInput(0);
      counted.dataset.offerCount = "";
      card.appendChild(field("شمارش", counted));
      card.appendChild(
        actionButton("تأیید شارژ قفسه", function () {
          var value = Number(counted.value);
          if (!Number.isInteger(value) || value < 0) return fail(new Error(NO_QUANTITY));
          return run(
            { action: "restock.confirm", data: { offer_id: offer.offer_id, quantity: value } },
            MESSAGES.confirm
          );
        }, null, "restock.confirm")
      );
      box.appendChild(card);
    });
  }

  function renderRestock() {
    var warehouse = clinic.warehouse;
    showSection("purchase-restock", warehouse !== null);
    if (warehouse === null) return;
    var box = sectionBody("purchase-restock");
    box.textContent = "";
    if (warehouse.restock.length > 0) {
      var list = el("ul", "pu-lines");
      warehouse.restock.forEach(function (record) {
        list.appendChild(
          el(
            "li",
            null,
            itemName(record.item_id) +
              " · " +
              faNumber(record.quantity) +
              " · از " +
              locationName(record.from_location_id) +
              " به " +
              locationName(record.to_location_id) +
              " · " +
              faTime(record.at_tehran)
          )
        );
      });
      box.appendChild(list);
    }
    var chargeable = knownItems().filter(function (item) {
      return item.location_id && item.location_id !== STORE;
    });
    if (chargeable.length === 0) {
      box.appendChild(emptyLine(EMPTY.restock));
      return;
    }
    var item = itemControl(chargeable);
    item.dataset.restockItem = "";
    box.appendChild(field("کالا", item, "is-item"));
    box.appendChild(el("p", "pu-meta", "مبدأ: " + locationName(STORE)));
    var to = el("p", "pu-meta", "مقصد: —");
    box.appendChild(to);
    item.addEventListener("change", function () {
      var chosen = chargeable.filter(function (candidate) {
        return candidate.item_id === item.value;
      })[0];
      to.textContent = "مقصد: " + (chosen ? locationName(chosen.location_id) : "—");
    });
    var quantity = numberInput(1);
    quantity.dataset.restockQuantity = "";
    box.appendChild(field("تعداد", quantity));
    // G3: both sides confirm. The giver names the person who will count what arrives on the shelf, and
    // nothing leaves the store until that person's own count matches (owner decision, «هر دو طرف تأیید»).
    var confirmer = document.createElement("select");
    confirmer.dataset.confirmer = "";
    (clinic.directory || []).forEach(function (person) {
      var option = document.createElement("option");
      option.value = person.staff_id;
      option.textContent = person.name + (person.role ? " · " + person.role : "");
      confirmer.appendChild(option);
    });
    if (confirmer.options.length === 0) {
      box.appendChild(el("p", "pu-hint", "کسی برای تأیید شارژ در فهرست کارمندان نیست."));
      return;
    }
    box.appendChild(field("تحویل‌گیرنده (تأیید با شمارش او)", confirmer));
    var pending = clinic.inbox ? clinic.inbox.restock_waiting : [];
    if (pending.length > 0) {
      var list = el("ul", "pu-lines");
      pending.forEach(function (offer) {
        list.appendChild(el("li", null, itemName(offer.item_id) + " · در انتظار تأیید تحویل‌گیرنده"));
      });
      box.appendChild(list);
    }
    box.appendChild(
      actionButton("ثبت شارژ قفسه", function () {
        var chosen = chargeable.filter(function (candidate) {
          return candidate.item_id === item.value;
        })[0];
        if (!chosen) return fail(new Error(NO_ITEM));
        var count = Number(quantity.value);
        if (!Number.isInteger(count) || count < 1) return fail(new Error(NO_QUANTITY));
        if (!confirmer.value) return fail(new Error(NO_RECIPIENT));
        return run(
          {
            action: "restock.record",
            data: {
              item_id: chosen.item_id,
              from_location_id: STORE,
              to_location_id: chosen.location_id,
              quantity: count,
              confirmer_id: confirmer.value,
            },
          },
          MESSAGES.offer
        );
      }, null, "restock.record")
    );
  }

  /** The blind weekly count of the store: one row an item, the box empty and no current figure (P-11). */
  function renderCount() {
    var warehouse = clinic.warehouse;
    // The warehouse opens with the shelf charge's permission; counting is its own permission (P-11).
    var visible = warehouse !== null && may("stock.count.submit");
    showSection("purchase-count", visible);
    if (!visible) return;
    var box = sectionBody("purchase-count");
    box.textContent = "";
    box.appendChild(el("p", "pu-meta", WEEKLY_LABELS[warehouse.weekly.state] || warehouse.weekly.state));
    box.appendChild(
      el(
        "p",
        "pu-hint",
        "بازهٔ شمارش: " + faDate(warehouse.weekly.window_start) + " تا " + faDate(warehouse.weekly.window_end)
      )
    );
    if (warehouse.items.length === 0) {
      box.appendChild(emptyLine(EMPTY.count));
      return;
    }
    var rows = el("div", "pu-count-box");
    warehouse.items.forEach(function (item, index) {
      rows.appendChild(countRow(item, index));
    });
    box.appendChild(rows);
    box.appendChild(
      actionButton("ثبت شمارش هفتگی", function () {
        var entries;
        try {
          entries = readCount(rows, warehouse.items);
        } catch (error) {
          return fail(error);
        }
        return run(
          entries.map(function (entry) {
            return { action: "weekly.count", data: entry };
          }),
          MESSAGES.count
        );
      }, null, "weekly.count")
    );
  }

  function countRow(item, index) {
    var row = el("div", "pu-count-row");
    row.dataset.index = String(index);
    var name = el("div", "pu-count-name");
    name.appendChild(el("span", null, item.name));
    name.appendChild(el("span", "pu-count-unit", " " + item.unit));
    row.appendChild(name);
    var quantity = numberInput(0);
    quantity.dataset.count = "";
    row.appendChild(field("شمارش", quantity));
    var near = el("label", "pu-check");
    var tick = document.createElement("input");
    tick.type = "checkbox";
    tick.dataset.near = "";
    near.appendChild(tick);
    near.appendChild(el("span", null, "نزدیک انقضا"));
    row.appendChild(near);
    return row;
  }

  function readCount(rows, items) {
    var entries = [];
    var broken = false;
    Array.prototype.forEach.call(rows.querySelectorAll(".pu-count-row"), function (row) {
      var quantity = row.querySelector("[data-count]");
      var text = quantity ? quantity.value.trim() : "";
      if (text === "") return;
      var counted = Number(text);
      var item = items[Number(row.dataset.index)];
      var tick = row.querySelector("[data-near]");
      if (!item || !Number.isInteger(counted) || counted < 0) {
        broken = true;
        return;
      }
      entries.push({
        item_id: item.item_id,
        // The count is the main store's, whatever shelf the item's catalog home is (decision 2).
        location_id: STORE,
        counted: counted,
        near_expiry: Boolean(tick && tick.checked),
      });
    });
    if (broken) throw new Error(NO_NUMBER);
    if (entries.length === 0) throw new Error(NO_COUNT);
    return entries;
  }

  /** B1 (decision 2): the accountant's five sampled items a month, blind — names and units only. */
  function renderSample() {
    var sample = clinic.sample;
    showSection("purchase-sample", sample !== null);
    if (sample === null) return;
    var box = sectionBody("purchase-sample");
    box.textContent = "";
    box.appendChild(el("p", "pu-meta", "نمونهٔ ماه " + sample.month + " — پنج قلم تصادفی، بدون عدد فعلی انبار."));
    if (sample.items.length === 0) {
      box.appendChild(emptyLine(EMPTY.sample));
      return;
    }
    sample.items.forEach(function (item) {
      if (item.done) {
        box.appendChild(el("p", "pu-paid", item.name + " (" + item.unit + ") — شمرده شد."));
        return;
      }
      var row = el("div", "pu-form");
      var quantity = numberInput(0);
      quantity.dataset.sample = "";
      row.appendChild(field(item.name + " (" + item.unit + ")", quantity));
      row.appendChild(
        actionButton("ثبت شمارش", function () {
          var counted = Number(quantity.value);
          if (!Number.isInteger(counted) || counted < 0) return fail(new Error(NO_NUMBER));
          return run(
            {
              action: "sample.count",
              data: { item_id: item.item_id, location_id: STORE, counted: counted },
            },
            MESSAGES.sample
          );
        }, null, "sample.count")
      );
      box.appendChild(row);
    });
  }

  // ---------------------------------------------------------------- the page

  function render() {
    var loading = byId("purchase-loading");
    if (loading) loading.classList.add("hidden");
    renderDecide();
    renderPlace();
    renderDraft();
    renderReceive();
    renderTransit();
    renderInvoice();
    renderReturn();
    renderInbox();
    renderRestock();
    renderCount();
    renderSample();
  }

  document.addEventListener("DOMContentLoaded", function () {
    reload().catch(function () {
      var loading = byId("purchase-loading");
      if (loading) loading.classList.add("hidden");
      setStatus(MESSAGES.unreadable, "badge-failed");
    });
  });
})();
