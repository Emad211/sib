/**
 * Sib Shift — the staff phone screen for shift events (SHF-3, S01 §3, R-10).
 *
 * One job on this page: pick a big button, answer the kind's questions one at a time, then let the
 * outbox carry the event (ADR-016). The vocabulary arrives as one JSON blob built by the registry,
 * so the phone never draws a button the API would refuse. The event itself is never rendered here:
 * approvals are fetched live from the pending endpoint and dropped whenever the server cannot answer,
 * because the shell is served offline and must not show another person's work.
 *
 * Every server value reaches the DOM through `textContent`, never `innerHTML`.
 */
(function () {
  "use strict";

  var GROUP_LABELS = {
    money: "پول",
    stock: "انبار",
    care: "مراقبت",
    equipment: "تجهیزات",
  };
  var GROUP_ORDER = ["money", "stock", "care", "equipment"];
  var SUMMARY_LABELS = {
    amount: "مبلغ",
    quantity: "تعداد",
    note: "یادداشت",
    device: "تجهیز",
    admission_no: "شماره پذیرش",
    reason: "دلیل",
    waste_kind: "نوع ضایعات",
    source: "منبع",
    reference: "مرجع",
  };
  var PENDING_OFFLINE = "برای دیدن تأییدهای در انتظار، اتصال به سرور لازم است.";
  var OPEN_OFFLINE = "برای دیدن موردهای باز (خرابی، امانت)، اتصال به سرور لازم است.";
  var OPEN_EMPTY = "مورد بازی برای این کار نیست.";
  var REQUIRED = "این سؤال را پاسخ دهید.";
  var BAD_INTEGER = "عدد صحیح و صفر یا بیشتر وارد کنید.";
  var PHOTO_FAILED = "عکس خوانده نشد؛ دوباره تلاش کنید.";
  var PHOTO_READY = "عکس آماده است و همراه رویداد فرستاده می‌شود.";
  var PHOTO_MAX_EDGE = 1280;
  var PHOTO_MAX_BYTES = 400 * 1024;

  var catalog = null;
  var state = { kind: null, fields: [], index: 0, values: {}, ref: null };
  // The reading a follow-up was asked for, chosen for the nurse when the open list arrives (E1).
  var pendingRef = null;
  var FOLLOWUP_QUEUED = "بعد از ثبت همین عدد، مسیر پیگیری باز می‌شود";
  var openEntries = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function readCatalog() {
    var blob = byId("shift-event-catalog");
    if (!blob) return null;
    try {
      return JSON.parse(blob.textContent);
    } catch (error) {
      return null;
    }
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

  /** The shared status line app.js owns: this page never invents its own success message. */
  function setFeedback(text, badge) {
    var feedback = byId("submission-feedback");
    if (!feedback) return;
    feedback.textContent = text;
    feedback.className = "badge " + badge;
    feedback.classList.remove("hidden");
  }

  // ---------------------------------------------------------------- the group grid

  function renderGroups() {
    var container = byId("shift-event-groups");
    if (!container || !catalog) return;
    container.textContent = "";
    // A decision needs the reference the approval card owns, never the wizard's blank start.
    var decisions = [catalog.approved_type, catalog.rejected_type];
    GROUP_ORDER.forEach(function (group) {
      var kinds = catalog.kinds.filter(function (kind) {
        return kind.group === group && decisions.indexOf(kind.type) === -1;
      });
      if (kinds.length === 0) return;
      var section = el("section", "se-group");
      section.setAttribute("aria-label", GROUP_LABELS[group] || group);
      section.appendChild(el("h2", "se-group-title", GROUP_LABELS[group] || group));
      var grid = el("div", "se-kind-grid");
      kinds.forEach(function (kind) {
        var button = el("button", "se-kind");
        button.type = "button";
        button.dataset.kind = kind.type;
        button.appendChild(el("span", "se-kind-code", kind.code));
        button.appendChild(el("span", "se-kind-label", kind.label));
        grid.appendChild(button);
      });
      section.appendChild(grid);
      container.appendChild(section);
    });
  }

  // ---------------------------------------------------------------- the open record (F2)

  /**
   * The wizard asks which open record this event closes before it asks its own questions.
   * «تعمیر شد», «ارجاع خرابی» and «تحویل امانت بیمار» point at a record of any shift, so the list comes
   * from the server live and is never cached on the phone: an offline list could show another shift's
   * patient a day later (R-01).
   */
  function openReference(kind) {
    byId("shift-event-groups").classList.add("hidden");
    byId("shift-event-step").classList.add("hidden");
    byId("shift-event-review").classList.add("hidden");
    byId("shift-event-reference").classList.remove("hidden");
    byId("shift-ref-title").textContent = "کدام مورد؟ — " + kind.label;
    byId("shift-ref-next").disabled = true;
    byId("shift-ref-list").textContent = "";
    state.ref = null;
    var note = byId("shift-ref-note");
    note.textContent = "در حال خواندن موردهای باز…";
    note.classList.remove("hidden");
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      note.textContent = OPEN_OFFLINE;
      return;
    }
    fetch(catalog.open_url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("open unavailable");
        return response.json();
      })
      .then(function (entries) {
        openEntries = entries || [];
        renderOpen(state.kind);
      })
      .catch(function () {
        note.textContent = OPEN_OFFLINE;
      });
  }

  function renderOpen(kind) {
    var list = byId("shift-ref-list");
    var note = byId("shift-ref-note");
    list.textContent = "";
    var fit = (openEntries || []).filter(function (entry) {
      return Array.isArray(entry.actions) && entry.actions.indexOf(kind.type) !== -1;
    });
    if (fit.length === 0) {
      note.textContent = OPEN_EMPTY;
      note.classList.remove("hidden");
      return;
    }
    note.textContent = "";
    note.classList.add("hidden");
    fit.forEach(function (entry) {
      var card = el("li", "se-ref-card");
      var button = el("button", "se-ref-choice");
      button.type = "button";
      button.dataset.eventId = entry.event_id;
      var title = el("p", "se-ref-title");
      if (entry.code) title.appendChild(el("span", "se-pending-code", entry.code));
      title.appendChild(el("span", null, entry.label || entry.type || ""));
      button.appendChild(title);
      var meta = [actorName(entry), faTime(entry.at_tehran)].filter(function (part) {
        return part !== "";
      });
      button.appendChild(el("p", "se-ref-meta", "ثبت‌کننده: " + meta.join(" · ")));
      var summary = summaryText(entry.summary);
      if (summary) button.appendChild(el("p", "se-ref-summary", summary));
      if (pendingRef && entry.event_id === pendingRef) {
        pendingRef = null;
        window.setTimeout(function () {
          button.click();
        }, 0);
      }
      button.addEventListener("click", function () {
        state.ref = entry;
        Array.prototype.forEach.call(list.querySelectorAll("[data-event-id]"), function (other) {
          other.classList.toggle("is-selected", other.dataset.eventId === entry.event_id);
        });
        byId("shift-ref-next").disabled = false;
      });
      card.appendChild(button);
      list.appendChild(card);
    });
  }

  // ---------------------------------------------------------------- the wizard

  function resetPanels() {
    byId("shift-event-step").classList.add("hidden");
    byId("shift-event-review").classList.add("hidden");
    byId("shift-event-reference").classList.add("hidden");
    byId("shift-event-groups").classList.remove("hidden");
  }

  function startKind(kind) {
    state = { kind: kind, fields: kind.fields || [], index: 0, values: {}, ref: null };
    if (kind.needs_reference) {
      openReference(kind);
      return;
    }
    beginFields();
  }

  function beginFields() {
    byId("shift-event-groups").classList.add("hidden");
    byId("shift-event-reference").classList.add("hidden");
    byId("shift-event-review").classList.add("hidden");
    byId("shift-event-step").classList.remove("hidden");
    if (state.fields.length === 0) {
      openReview();
      return;
    }
    renderStep();
  }

  function currentField() {
    return state.fields[state.index];
  }

  function renderStep() {
    var field = currentField();
    var container = byId("shift-step-field");
    var progress = faNumber(state.index + 1) + " از " + faNumber(state.fields.length);
    byId("shift-step-progress").textContent = "سؤال " + progress;
    byId("shift-step-question").textContent = field.label;
    byId("shift-step-error").classList.add("hidden");
    container.textContent = "";
    container.appendChild(buildControl(field));
    suggestFollowup(field, container);
    byId("shift-step-back").textContent = state.index === 0 ? "بازگشت" : "قبلی";
    byId("shift-step-next").textContent = state.index === state.fields.length - 1 ? "بازبینی" : "بعدی";
  }

  /** The reading keys whose threshold the manager may set, and the setting each one reads. */
  var THRESHOLD_KEYS = { sugar: "sugar", systolic: "systolic", diastolic: "diastolic" };

  /**
   * E1: with a threshold set, an above-threshold reading offers one button into the follow-up path.
   * With no number set the box never appears, so the app suggests nothing (owner decision 11).
   */
  function suggestFollowup(field, container) {
    if (!THRESHOLD_KEYS[field.key]) return;
    var limit = catalog && catalog.thresholds ? catalog.thresholds[THRESHOLD_KEYS[field.key]] : null;
    if (limit === null || limit === undefined) return;
    var input = container.querySelector("input[data-field='" + field.key + "']");
    if (!input) return;
    var box = el("div", "se-suggest hidden");
    box.appendChild(el("p", "se-hint", "این عدد از آستانه‌ای که مدیر گذاشته بالاتر است."));
    var button = el("button", "btn btn-primary", state.followup ? FOLLOWUP_QUEUED : "باز کردن مسیر پیگیری");
    button.type = "button";
    button.disabled = Boolean(state.followup);
    // The follow-up points at the saved reading, so the reading is recorded first: the button only asks
    // for the path to open once this record has reached the server, and the typed value stays put.
    button.addEventListener("click", function () {
      state.followup = true;
      button.textContent = FOLLOWUP_QUEUED;
      button.disabled = true;
    });
    box.appendChild(button);
    container.appendChild(box);
    function refresh() {
      var above = input.value !== "" && Number(input.value) > Number(limit);
      box.classList.toggle("hidden", !above);
    }
    input.addEventListener("input", refresh);
    refresh();
  }

  function optionValue(raw) {
    return Array.isArray(raw) ? raw[0] : raw.value;
  }

  function optionLabel(raw) {
    return Array.isArray(raw) ? raw[1] : raw.label;
  }

  function selectControl(field, choices, placeholder) {
    var select = el("select", "form-control");
    select.dataset.field = field.key;
    var empty = el("option", null, placeholder || "انتخاب کنید…");
    empty.value = "";
    select.appendChild(empty);
    choices.forEach(function (choice) {
      var option = el("option", null, optionLabel(choice));
      option.value = optionValue(choice);
      select.appendChild(option);
    });
    select.value = state.values[field.key] || "";
    select.addEventListener("change", function () {
      state.values[field.key] = select.value;
    });
    return select;
  }

  function itemChoices() {
    var counted = catalog.counted_items || null;
    return (catalog.items || [])
      .filter(function (item) {
        return !counted || counted.indexOf(item.id) !== -1;
      })
      .map(function (item) {
        return [item.id, item.name];
      });
  }

  function campaignChoices() {
    return (catalog.campaigns || []).map(function (campaign) {
      var amount = Number(campaign.amount || 0).toLocaleString("fa-IR");
      return [campaign.id, campaign.name + " — " + amount + " تومان"];
    });
  }

  function staffChoices() {
    return (catalog.staff || []).map(function (person) {
      return [person.id, person.name];
    });
  }

  function locationChoices() {
    return (catalog.locations || []).map(function (location) {
      return [location.id, location.name];
    });
  }

  function locationField() {
    return state.fields.filter(function (field) {
      return field.kind === "location";
    })[0];
  }

  function multiChoices(field) {
    if (field.options && field.options.length > 0) return field.options;
    // A checklist of catalog items, narrowed to the step's location when it named one.
    var place = locationField();
    var items = catalog.items || [];
    if (place && state.values[place.key]) {
      items = items.filter(function (item) {
        return item.location_id === state.values[place.key];
      });
    }
    return items.map(function (item) {
      return [item.id, item.name];
    });
  }

  // ---------------------------------------------------------------- the receipt photo (SHF-7)

  var PHOTO_NEEDS_HTTPS = "برای گرفتن عکس، اتصال امن (HTTPS) لازم است.";

  function canvasBlob(canvas, quality) {
    return new Promise(function (resolve) {
      canvas.toBlob(
        function (blob) {
          resolve(blob);
        },
        "image/jpeg",
        quality
      );
    });
  }

  /**
   * Downscale on the phone before anything leaves it: the clinic's Wi-Fi is the bottleneck and the
   * file is evidence for the accountant, not something to print. The longest edge is capped and the
   * quality steps down until the blob fits, so one photo stays a few hundred kilobytes.
   */
  function downscaled(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var image = new Image();
      image.onload = function () {
        URL.revokeObjectURL(url);
        var scale = Math.min(1, PHOTO_MAX_EDGE / Math.max(image.width, image.height));
        var canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        var context = canvas.getContext("2d");
        if (!context) {
          reject(new Error(PHOTO_FAILED));
          return;
        }
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        var qualities = [0.72, 0.64, 0.56, 0.48, 0.4];
        function tryQuality(index) {
          canvasBlob(canvas, qualities[index]).then(function (blob) {
            if (!blob) {
              reject(new Error(PHOTO_FAILED));
              return;
            }
            if (blob.size <= PHOTO_MAX_BYTES || index === qualities.length - 1) {
              resolve(blob);
              return;
            }
            tryQuality(index + 1);
          });
        }
        tryQuality(0);
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error(PHOTO_FAILED));
      };
      image.src = url;
    });
  }

  function sha256Hex(buffer) {
    if (typeof crypto === "undefined" || !crypto.subtle) {
      return Promise.reject(new Error(PHOTO_NEEDS_HTTPS));
    }
    return crypto.subtle.digest("SHA-256", buffer).then(function (digest) {
      return Array.prototype.map
        .call(new Uint8Array(digest), function (byte) {
          return ("0" + byte.toString(16)).slice(-2);
        })
        .join("");
    });
  }

  /** The optional receipt: the blob waits in the phone's queue, the payload carries its hash. */
  function photoControl(field) {
    var box = el("div", "se-photo");
    var input = el("input", "se-photo-input");
    input.type = "file";
    input.accept = "image/*";
    input.capture = "environment";
    input.id = "photo-" + field.key;
    var pick = el("label", "btn btn-secondary se-photo-pick", "گرفتن عکس");
    pick.setAttribute("for", input.id);
    var preview = el("img", "se-photo-preview hidden");
    preview.alt = "پیش‌نمایش عکس";
    var status = el("p", "se-hint se-photo-status", state.values[field.key] ? "عکس ثبت شد." : "");
    var clear = el("button", "btn btn-secondary btn-compact se-photo-clear", "حذف عکس");
    clear.type = "button";
    clear.classList.toggle("hidden", !state.values[field.key]);
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      status.textContent = "در حال آماده‌سازی عکس…";
      downscaled(file)
        .then(function (blob) {
          return blob.arrayBuffer().then(function (buffer) {
            return sha256Hex(buffer).then(function (sha) {
              if (!window.ShiftOutbox || !window.ShiftOutbox.savePhotoBlob) {
                throw new Error(PHOTO_FAILED);
              }
              return window.ShiftOutbox.savePhotoBlob(sha, blob).then(function () {
                return { sha: sha, blob: blob };
              });
            });
          });
        })
        .then(function (prepared) {
          state.values[field.key] = prepared.sha;
          preview.src = URL.createObjectURL(prepared.blob);
          preview.classList.remove("hidden");
          clear.classList.remove("hidden");
          status.textContent = PHOTO_READY;
        })
        .catch(function (error) {
          status.textContent = (error && error.message) || PHOTO_FAILED;
        });
    });
    clear.addEventListener("click", function () {
      var sha = state.values[field.key];
      state.values[field.key] = "";
      input.value = "";
      preview.classList.add("hidden");
      clear.classList.add("hidden");
      status.textContent = "";
      if (sha && window.ShiftOutbox && window.ShiftOutbox.deletePhotoBlob) {
        window.ShiftOutbox.deletePhotoBlob(sha);
      }
    });
    box.appendChild(input);
    box.appendChild(pick);
    box.appendChild(preview);
    box.appendChild(clear);
    box.appendChild(status);
    return box;
  }

  function buildControl(field) {
    var value = state.values[field.key];
    var control;
    switch (field.kind) {
      case "text":
        control = el("input", "form-control");
        control.type = "text";
        control.maxLength = field.max_length || 200;
        control.autocomplete = "off";
        control.value = value || "";
        control.dataset.field = field.key;
        control.addEventListener("input", function () {
          state.values[field.key] = control.value;
        });
        return control;
      case "amount":
      case "quantity":
        control = el("input", "form-control");
        control.type = "number";
        control.min = String(field.minimum === null || field.minimum === undefined ? 0 : field.minimum);
        control.step = "1";
        control.inputMode = "numeric";
        if (field.maximum !== null && field.maximum !== undefined) control.max = String(field.maximum);
        control.value = value === undefined ? "" : String(value);
        control.dataset.field = field.key;
        control.addEventListener("input", function () {
          state.values[field.key] = control.value;
        });
        return control;
      case "choice":
        return selectControl(field, field.key === "campaign_id" ? campaignChoices() : field.options || []);
      case "item":
        control = selectControl(field, itemChoices(), "کالا را انتخاب کنید…");
        control.addEventListener("change", function () {
          fillLocationFromItem(control.value);
        });
        return control;
      case "location":
        return selectControl(field, locationChoices(), "محل را انتخاب کنید…");
      case "staff":
        return selectControl(field, staffChoices(), "نفر را انتخاب کنید…");
      case "date":
        // The Jalali picker attaches when this field reaches the page and owns the ISO value.
        control = el("input", "form-control");
        control.type = "text";
        control.name = field.key;
        control.setAttribute("data-jalali-date", "");
        control.autocomplete = "off";
        control.value = value || "";
        control.dataset.field = field.key;
        control.addEventListener("change", function () {
          state.values[field.key] = window.ShiftJalaliDatepicker.getIso(control);
        });
        return control;
      case "boolean":
        control = el("input");
        control.type = "checkbox";
        control.checked = value === true;
        control.dataset.field = field.key;
        control.addEventListener("change", function () {
          state.values[field.key] = control.checked;
        });
        var booleanLabel = el("label", "se-check");
        booleanLabel.appendChild(control);
        booleanLabel.appendChild(el("span", null, field.label));
        var booleanBox = el("div", "se-field-box");
        booleanBox.appendChild(booleanLabel);
        return booleanBox;
      case "multi":
        var box = el("div", "se-check-list");        box.dataset.field = field.key;
        var chosen = Array.isArray(value) ? value.slice() : [];
        multiChoices(field).forEach(function (choice) {
          var id = optionValue(choice);
          var item = el("input");
          item.type = "checkbox";
          item.value = id;
          item.checked = chosen.indexOf(id) !== -1;
          item.addEventListener("change", function () {
            var next = Array.isArray(state.values[field.key]) ? state.values[field.key].slice() : [];
            var at = next.indexOf(id);
            if (item.checked && at === -1) next.push(id);
            if (!item.checked && at !== -1) next.splice(at, 1);
            state.values[field.key] = next;
          });
          var row = el("label", "se-check");
          row.appendChild(item);
          row.appendChild(el("span", null, optionLabel(choice)));
          box.appendChild(row);
        });
        return box;
      case "photo":
        return photoControl(field);
      default:
        control = el("input", "form-control");
        control.type = "text";
        control.dataset.field = field.key;
        control.value = value || "";
        return control;
    }
  }

  /** The catalog knows each item's shelf, so the payload's location never needs typing. */
  function fillLocationFromItem(itemId) {
    var place = locationField();
    if (!place) return;
    var item = (catalog.items || []).filter(function (candidate) {
      return candidate.id === itemId;
    })[0];
    if (item) state.values[place.key] = item.location_id || "";
  }

  function capture() {
    var field = currentField();
    var control = document.querySelector("#shift-step-field [data-field='" + field.key + "']");
    if (!control) return;
    if (field.kind === "multi") {
      if (!Array.isArray(state.values[field.key])) state.values[field.key] = [];
      return;
    }
    if (field.kind === "boolean") {
      var box = control.querySelector("input[type=checkbox]") || control;
      state.values[field.key] = box.checked;
      return;
    }
    state.values[field.key] = control.value;
  }

  function validate(field) {
    var value = state.values[field.key];
    var empty =
      value === undefined ||
      value === null ||
      value === "" ||
      (Array.isArray(value) && value.length === 0);
    if (field.kind === "boolean") return "";
    if (empty) return field.required ? REQUIRED : "";
    if (field.kind === "amount" || field.kind === "quantity") {
      var number = Number(value);
      var least = field.minimum === null || field.minimum === undefined ? 0 : field.minimum;
      if (!isFinite(number) || Math.floor(number) !== number || number < least) return BAD_INTEGER;
      if (field.maximum !== null && field.maximum !== undefined && number > field.maximum) {
        return "عدد واردشده از حد مجاز بیشتر است.";
      }
      return "";
    }
    if (field.kind === "text" && field.max_length && String(value).length > field.max_length) {
      return "پاسخ طولانی‌تر از حد مجاز است.";
    }
    return "";
  }

  function showFieldError(message) {
    var box = byId("shift-step-error");
    box.textContent = message;
    box.classList.toggle("hidden", !message);
  }

  function next() {
    capture();
    var message = validate(currentField());
    if (message) {
      showFieldError(message);
      return;
    }
    if (state.index === state.fields.length - 1) {
      openReview();
      return;
    }
    state.index += 1;
    renderStep();
  }

  function back() {
    if (state.index === 0) {
      resetPanels();
      return;
    }
    state.index -= 1;
    renderStep();
  }

  // ---------------------------------------------------------------- review and submit

  function displayValue(field, value) {
    if (field.kind === "boolean") return value === true ? "بله" : "خیر";
    if (field.kind === "photo") return "عکس پیوست شد";
    if (field.kind === "amount") return faNumber(value) + " تومان";
    if (field.kind === "quantity") return faNumber(value);
    var choices = [];
    if (field.kind === "choice") choices = field.options || [];
    if (field.kind === "item") choices = itemChoices();
    if (field.kind === "location") choices = locationChoices();
    if (field.kind === "staff") choices = staffChoices();
    if (field.kind === "multi") choices = multiChoices(field);
    var values = Array.isArray(value) ? value : [value];
    var labels = values.map(function (raw) {
      var match = choices.filter(function (choice) {
        return optionValue(choice) === raw;
      })[0];
      return match ? optionLabel(match) : String(raw);
    });
    return labels.join("، ");
  }

  function openReview() {
    capture();
    byId("shift-review-title").textContent = "بازبینی رویداد — " + state.kind.label;
    var list = byId("shift-review-list");
    list.textContent = "";
    if (state.ref) {
      // The record this event closes is the one fact the person cannot re-read on the phone.
      var refRow = el("div", "se-review-row");
      refRow.appendChild(el("dt", "se-review-label", "مورد باز"));
      refRow.appendChild(
        el("dd", "se-review-value", (state.ref.label || state.ref.type) + " · " + faTime(state.ref.at_tehran))
      );
      list.appendChild(refRow);
    }
    state.fields.forEach(function (field) {
      var value = state.values[field.key];
      var empty = value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
      if (empty && field.kind !== "boolean") return;
      var row = el("div", "se-review-row");
      row.appendChild(el("dt", "se-review-label", field.label));
      row.appendChild(el("dd", "se-review-value", displayValue(field, value)));
      list.appendChild(row);
    });
    byId("shift-event-step").classList.add("hidden");
    byId("shift-event-groups").classList.add("hidden");
    byId("shift-event-review").classList.remove("hidden");
  }

  function payloadFromState() {
    var payload = {};
    state.fields.forEach(function (field) {
      var value = state.values[field.key];
      if (field.kind === "boolean") {
        payload[field.key] = value === true;
        return;
      }
      var empty = value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
      if (empty) return;
      if (field.kind === "amount" || field.kind === "quantity") {
        payload[field.key] = Number(value);
        return;
      }
      if (field.kind === "multi") {
        payload[field.key] = value.slice();
        return;
      }
      payload[field.key] = typeof value === "string" ? value.trim() : value;
    });
    return payload;
  }

  function submit() {
    if (!window.ShiftOutbox || !window.submitShiftEvent) {
      setFeedback("صف ارسال در دسترس نیست.", "badge-failed");
      return;
    }
    var ulid = window.ShiftOutbox.mintUlid();
    var event = {
      type: state.kind.type,
      payload: payloadFromState(),
      client_event_id: ulid,
      idempotency_key: ulid,
      client_at: new Date().toISOString(),
      work_date: window.ShiftOutbox.tehranBusinessDate(),
    };
    if (state.ref) {
      event.ref_event_id = state.ref.event_id;
    }
    if (state.followup) followAfterSent(ulid);
    // `submitShiftEvent` owns the queued/sent answer: this page never claims success itself.
    window.submitShiftEvent(event);
    state = { kind: null, fields: [], index: 0, values: {}, ref: null };
    resetPanels();
  }

  /**
   * E1: once the reading the nurse asked a follow-up for has reached the server, open the follow-up
   * kind with that very reading already chosen. Offline, the reading waits in the outbox and the path
   * opens when it is sent; a wizard already in use is never interrupted.
   */
  function followAfterSent(eventId) {
    function onSent(event) {
      var detail = event.detail || {};
      if (detail.idempotency_key !== eventId) return;
      window.removeEventListener("outbox:sent", onSent);
      if (state.kind) return;
      var kind = (catalog.kinds || []).filter(function (candidate) {
        return candidate.type === "vitals.followup_opened";
      })[0];
      if (!kind) return;
      pendingRef = eventId;
      startKind(kind);
    }
    window.addEventListener("outbox:sent", onSent);
  }

  // ---------------------------------------------------------------- approvals

  function pendingSection() {
    return byId("shift-pending-list") ? byId("shift-pending-list").closest("section") : null;
  }

  function showPendingNote(text) {
    var note = byId("shift-pending-note");
    if (note) {
      note.textContent = text;
      note.classList.toggle("hidden", !text);
    }
  }

  function clearPending() {
    var list = byId("shift-pending-list");
    if (list) list.textContent = "";
  }

  function showPendingOffline() {
    clearPending();
    showPendingNote(PENDING_OFFLINE);
  }

  /** The pending entry names its author as `{id, name}`; a person who left keeps the id. */
  function actorName(entry) {
    var actor = entry.actor || {};
    return actor.name || actor.id || entry.actor_name || entry.actor_id || "";
  }

  function summaryText(summary) {
    if (!summary || typeof summary !== "object") return "";
    return Object.keys(summary)
      .map(function (key) {
        // A receipt photo is a link on the card, never a 64-character hash in the text (SHF-7).
        if (key.endsWith("_sha256")) return "";
        var label = SUMMARY_LABELS[key] || key;
        var value = summary[key];
        if (value === null || value === undefined || value === "") return "";
        if (typeof value === "number") {
          return label + ": " + faNumber(value) + (key === "amount" ? " تومان" : "");
        }
        if (Array.isArray(value)) return label + ": " + value.join("، ");
        return label + ": " + String(value);
      })
      .filter(function (part) {
        return part !== "";
      })
      .join(" · ");
  }

  function decisionButton(entry, type, label, className) {
    var button = el("button", "btn " + className, label);
    button.type = "button";
    button.dataset.decision = type;
    button.addEventListener("click", function () {
      decide(entry, type, button);
    });
    return button;
  }

  function renderPending(entries) {
    var list = byId("shift-pending-list");
    if (!list) return;
    list.textContent = "";
    if (!entries || entries.length === 0) {
      showPendingNote("تأییدی در انتظار شما نیست.");
      return;
    }
    showPendingNote("");
    entries.forEach(function (entry) {
      var card = el("li", "se-pending-card");
      card.dataset.eventId = entry.event_id;
      var title = el("p", "se-pending-title");
      if (entry.code) title.appendChild(el("span", "se-pending-code", entry.code));
      title.appendChild(el("span", null, entry.label || entry.type || ""));
      card.appendChild(title);
      var meta = [actorName(entry), faTime(entry.at_tehran)].filter(function (part) {
        return part !== "";
      });
      card.appendChild(el("p", "se-pending-meta", "ثبت‌کننده: " + meta.join(" · ")));
      var summary = summaryText(entry.summary);
      if (summary) card.appendChild(el("p", "se-pending-summary", summary));
      if (entry.summary && entry.summary.photo_sha256) {
        // The approver decides the record from this phone, so the receipt must open from here too
        // (the photo route admits exactly the manager and the approvers).
        var photo = el("a", "se-pending-photo", "عکس رسید");
        photo.href = "/api/v1/photos/" + entry.summary.photo_sha256;
        photo.target = "_blank";
        photo.rel = "noopener";
        card.appendChild(photo);
      }
      var actions = el("div", "se-pending-actions");
      actions.appendChild(decisionButton(entry, catalog.approved_type, "تأیید", "btn-primary"));
      actions.appendChild(decisionButton(entry, catalog.rejected_type, "رد", "btn-danger"));
      card.appendChild(actions);
      list.appendChild(card);
    });
  }

  function loadPending() {
    if (!catalog) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      showPendingOffline();
      return;
    }
    fetch(catalog.pending_url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (response.status === 403) {
          // This person may record events but not decide them: no approvals card at all (R-07).
          clearPending();
          var section = pendingSection();
          if (section) section.classList.add("hidden");
          return null;
        }
        if (!response.ok) throw new Error("pending unavailable");
        return response.json();
      })
      .then(function (entries) {
        if (entries !== null) renderPending(entries);
      })
      .catch(function () {
        showPendingOffline();
      });
  }

  function decide(entry, type, button) {
    if (!window.ShiftOutbox || !window.submitShiftEvent) {
      setFeedback("صف ارسال در دسترس نیست.", "badge-failed");
      return;
    }
    var note = window.prompt("یادداشت (اختیاری) برای این تصمیم:", "");
    if (note === null) return;
    var card = button.closest(".se-pending-card");
    if (card) {
      Array.prototype.forEach.call(card.querySelectorAll("button[data-decision]"), function (control) {
        control.disabled = true;
      });
    }
    var ulid = window.ShiftOutbox.mintUlid();
    var trimmed = String(note).trim();
    window.submitShiftEvent({
      type: type,
      ref_event_id: entry.event_id,
      payload: trimmed ? { note: trimmed } : {},
      client_event_id: ulid,
      idempotency_key: ulid,
      client_at: new Date().toISOString(),
      work_date: window.ShiftOutbox.tehranBusinessDate(),
    });
  }

  // ---------------------------------------------------------------- wiring

  document.addEventListener("DOMContentLoaded", function () {
    catalog = readCatalog();
    if (!catalog) return;

    renderGroups();

    // The accountant's queue sends her straight to the one question she came for (?kind=cash.deposited).
    var wanted = new URLSearchParams(window.location.search).get("kind");
    var chosen = catalog.kinds.filter(function (candidate) {
      return candidate.type === wanted;
    })[0];
    if (chosen) startKind(chosen);

    byId("shift-event-groups").addEventListener("click", function (event) {
      var button = event.target.closest("[data-kind]");
      if (!button) return;
      var kind = catalog.kinds.filter(function (candidate) {
        return candidate.type === button.dataset.kind;
      })[0];
      if (kind) startKind(kind);
    });
    byId("shift-ref-back").addEventListener("click", resetPanels);
    byId("shift-ref-next").addEventListener("click", function () {
      if (!state.ref) return;
      beginFields();
    });
    byId("shift-step-back").addEventListener("click", back);
    byId("shift-step-next").addEventListener("click", next);
    byId("shift-review-back").addEventListener("click", function () {
      byId("shift-event-review").classList.add("hidden");
      byId("shift-event-step").classList.remove("hidden");
      state.index = Math.max(0, state.fields.length - 1);
      renderStep();
    });
    byId("shift-submit").addEventListener("click", submit);

    loadPending();
    window.addEventListener("online", loadPending);
    window.addEventListener("outbox:sent", loadPending);
  });
})();
