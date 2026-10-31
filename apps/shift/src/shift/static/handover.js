/**
 * Sib Shift - phone handover wizard (SHF-2 slice 5).
 *
 * The screen a tired receptionist or nurse uses at 2 am: one job per screen (R-10), big type,
 * big tap targets, no dense tables. It drives the slice-4 API and shows the answer of a blind
 * count as a status word only — never a reference, an expected figure, a comparison or a delta
 * (R-02, ADR-011). The phone is the shared surface of a handover: the status
 * «گوشی را به نفر خروجی بدهید» is what hands it to the other person, so both signatures are
 * tapped on the phone that opened the flow.
 *
 * Handover calls go straight to `fetch` and never into the IndexedDB outbox (ADR-016): a flow
 * needs the clinic server, and offline the screen says so instead of pretending.
 */

const API = "/api/v1/handover";
const ALREADY_OPEN = "این تحویل از قبل برای این شیفت باز شده است";
const STORAGE_KEY = "shift:handover";
const FLOWS = ["reception", "nursing"];

const STEP_LABELS = {
  cash: "صندوق",
  shelf: "قفسهٔ پذیرش",
  cpr_room: "اتاق CPR",
  trolley: "ترالی",
  checklists: "چک‌لیست",
  parcels: "بسته‌های در راه",
  dual_sign: "امضا",
};

// What a count tells the operator, and the only thing it tells them (R-02). The five statuses
// named in the owner's rule are exact; `incoming` names the action for the second person.
const STATUS_LABELS = {
  incoming: "شمارش ثبت شد؛ نفر دوم هم بشمارد",
  recount: "دوباره بشمارید",
  outgoing: "گوشی را به نفر خروجی بدهید",
  joint: "با هم بشمارید",
  matched: "درست است",
  settled: "ثبت شد",
};

const DONE_STATUSES = new Set(["matched", "settled"]);
const PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
const PERSIAN_RANGE = /[۰-۹٠-٩]/g;
const ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";

const $ = (id) => document.getElementById(id);

const screens = {
  offline: $("handover-offline"),
  start: $("handover-start"),
  exists: $("handover-exists"),
  wizard: $("handover-wizard"),
  waiting: $("handover-waiting"),
  done: $("handover-done"),
};
const wizard = screens.wizard;
const stepContainer = $("handover-step");
const messageElement = $("handover-message");
const refusalElement = $("handover-refusal");
const managerPicker = $("handover-manager-picker");
const managerSelect = $("handover-manager");
const managerMessage = $("handover-manager-message");

let state = null;
let targets = null;
let lastStep = null;
let busy = false;

class OfflineLeft extends Error {}
class Refusal extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/* ---------- screens and messages ---------- */

function showScreen(name) {
  for (const [key, element] of Object.entries(screens)) {
    element.classList.toggle("hidden", key !== name);
  }
}

function showOffline() {
  showScreen("offline");
}

function showMessage(text, kind) {
  if (!text) {
    clearMessage();
    return;
  }
  messageElement.textContent = text;
  messageElement.className = `ho-message is-${kind || "info"}`;
}

function clearMessage() {
  messageElement.textContent = "";
  messageElement.className = "ho-message hidden";
}

function showRefusal(text) {
  refusalElement.textContent = text || "";
  refusalElement.classList.toggle("hidden", !text);
}

function showManagerMessage(text) {
  managerMessage.textContent = text || "";
  managerMessage.classList.toggle("hidden", !text);
}

function showExists(message) {
  $("handover-exists-message").textContent = message;
  showScreen("exists");
}

/* ---------- storage: which flow this phone opened ---------- */

function readStorage() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    return stored && typeof stored === "object" ? stored : {};
  } catch (error) {
    return {};
  }
}

function writeStorage(all) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch (error) {
    // Private mode: the flow still runs, it just cannot be resumed after a reload.
  }
}

function remember(flowName, handoverId) {
  const all = readStorage();
  all[flowName] = handoverId;
  writeStorage(all);
}

function forget(flowName) {
  const all = readStorage();
  delete all[flowName];
  writeStorage(all);
}

/* ---------- the live server ---------- */

async function request(path, method = "GET", body) {
  if (!navigator.onLine) {
    showOffline();
    throw new OfflineLeft();
  }
  let response;
  try {
    response = await fetch(path, {
      method: method,
      cache: "no-store",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    // A failed request is the same teaching moment as a dead network: the flow needs the server.
    showOffline();
    throw new OfflineLeft();
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const refusal = (payload.error && payload.error.message) || "درخواست انجام نشد؛ دوباره تلاش کنید.";
    throw new Refusal(refusal, response.status);
  }
  return payload;
}

async function guard(work) {
  if (busy) return;
  busy = true;
  try {
    await work();
  } catch (error) {
    if (!(error instanceof OfflineLeft)) {
      showMessage(error.message || "درخواست انجام نشد؛ دوباره تلاش کنید.", "error");
    }
  } finally {
    busy = false;
  }
}

/* ---------- counting ---------- */

function foldDigits(value) {
  return String(value).replace(PERSIAN_RANGE, (digit) => {
    const persian = PERSIAN_DIGITS.indexOf(digit);
    return persian >= 0 ? String(persian) : String(ARABIC_DIGITS.indexOf(digit));
  });
}

function wholeNumber(raw) {
  const text = foldDigits(raw).trim();
  if (!text) return { error: "شمارش را وارد کنید." };
  if (!/^[0-9]+$/.test(text)) return { error: "فقط عدد کامل را وارد کنید." };
  return { value: Number(text) };
}

function fa(value) {
  return Number(value).toLocaleString("fa-IR");
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function countField(target) {
  const input = document.createElement("input");
  input.className = "ho-number";
  input.type = "text";
  input.inputMode = "numeric";
  input.autocomplete = "off";
  input.dir = "ltr";
  input.dataset.scope = target.scope;
  input.setAttribute("aria-label", target.scope === "cash" ? "شمارش صندوق" : "شمارش این قلم");
  if (target.scope === "item") {
    input.dataset.itemId = target.item_id;
    input.dataset.locationId = target.location_id;
  }
  return input;
}

function countBody(target, counted) {
  const body = { scope: target.scope, counted: counted };
  if (target.scope === "item") {
    body.item_id = target.item_id;
    body.location_id = target.location_id;
  }
  return body;
}

async function submitNumber(target, input, endpoint) {
  const answer = wholeNumber(input.value);
  if (answer.error) {
    showMessage(answer.error, "error");
    return;
  }
  const result = await request(`${API}/${state.handover_id}/${endpoint}`, "POST", countBody(target, answer.value));
  input.value = "";
  await refresh();
  showMessage(STATUS_LABELS[result.status] || "", DONE_STATUSES.has(result.status) ? "ok" : "info");
}

function numberJob(target, { title, kicker, hint, inputId, submitId, submitLabel, endpoint }) {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.id = target.scope === "cash" ? "handover-cash" : "handover-item";
  if (kicker) job.appendChild(element("p", "ho-kicker", kicker));
  job.appendChild(element("h2", "ho-job-title", title));
  if (hint) job.appendChild(element("p", "ho-hint", hint));
  const input = countField(target);
  input.id = inputId;
  const button = element("button", "ho-action ho-action-primary", submitLabel);
  button.id = submitId;
  button.type = "button";
  button.addEventListener("click", () => guard(() => submitNumber(target, input, endpoint)));
  job.append(input, button);
  stepContainer.appendChild(job);
  input.focus();
}

function jointJob(target) {
  numberJob(target, {
    title: "با هم بشمارید",
    kicker: target.kicker,
    hint: "هر دو نفر با هم بشمارید و عدد توافق‌شده را وارد کنید.",
    inputId: "handover-joint-input",
    submitId: "handover-joint-submit",
    submitLabel: "ثبت شمارش مشترک",
    endpoint: "joint",
  });
}

/* ---------- step renders ---------- */

async function advanceFlow() {
  await adopt(await request(`${API}/${state.handover_id}/advance`, "POST"));
}

function advanceButton(label) {
  const button = element("button", "ho-action ho-action-primary", label);
  button.id = "handover-advance";
  button.type = "button";
  button.addEventListener("click", () => guard(advanceFlow));
  return button;
}

function advanceJob(text, label) {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("p", "ho-text", text));
  job.appendChild(advanceButton(label));
  stepContainer.appendChild(job);
}

function renderCash() {
  if (state.cash_status === "joint") {
    jointJob({ scope: "cash" });
    return;
  }
  if (DONE_STATUSES.has(state.cash_status)) {
    advanceJob("صندوق تسویه شد.", "ادامه");
    return;
  }
  numberJob(
    { scope: "cash" },
    {
      title: "شمارش صندوق",
      hint: "صندوق را بشمارید و عدد را وارد کنید.",
      inputId: "handover-count-input",
      submitId: "handover-count-submit",
      submitLabel: "ثبت شمارش",
      endpoint: "counts",
    }
  );
}

function itemInfo(itemId) {
  const rows = (targets && targets.items) || [];
  return rows.find((row) => row.item_id === itemId) || null;
}

function renderItems() {
  const counted = Object.entries(state.counts || {});
  const pending = counted.findIndex(([, status]) => !DONE_STATUSES.has(status));
  if (pending < 0) {
    advanceJob("همهٔ اقلام این مرحله ثبت شد.", "ادامه");
    return;
  }
  const [itemId, status] = counted[pending];
  const info = itemInfo(itemId);
  if (!info) {
    stepContainer.replaceChildren(
      element("p", "ho-text", "این قلم در فهرست اقلام این مرحله نیست؛ با مدیر تماس بگیرید.")
    );
    return;
  }
  const kicker = `قلم ${fa(pending + 1)} از ${fa(counted.length)}`;
  const target = { scope: "item", item_id: itemId, location_id: info.location_id, kicker: kicker };
  if (status === "joint") {
    jointJob(target);
    return;
  }
  numberJob(target, {
    title: info.name,
    kicker: kicker,
    hint: `واحد: ${info.unit}`,
    inputId: "handover-count-input",
    submitId: "handover-count-submit",
    submitLabel: "ثبت شمارش",
    endpoint: "counts",
  });
}

function renderChecklists() {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("h2", "ho-job-title", "چک‌لیست"));
  job.appendChild(element("p", "ho-hint", "این موارد را ببینید و بعد ادامه بدهید."));
  const list = element("ul", "ho-checklist");
  list.id = "handover-checklist-list";
  for (const row of (targets && targets.checklists) || []) {
    list.appendChild(element("li", null, row.name));
  }
  job.appendChild(list);
  job.appendChild(advanceButton("بررسی کردم، ادامه"));
  stepContainer.appendChild(job);
}

function renderParcels() {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("h2", "ho-job-title", "بسته‌های در راه"));
  job.appendChild(element("p", "ho-text", "اگر بستهٔ در راهی هست، آن را به نفر بعدی تحویل بدهید."));
  job.appendChild(advanceButton("تحویل شد، ادامه"));
  stepContainer.appendChild(job);
}

function renderSign() {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("h2", "ho-job-title", "امضای دو طرف"));
  job.appendChild(element("p", "ho-hint", "هر نفر امضای خودش را روی همین گوشی ثبت می‌کند."));
  if (state.emergency) {
    job.appendChild(element("p", "ho-hint", "نفر خروجی حاضر نیست؛ فقط نفر ورودی امضا می‌کند."));
  }
  const signed = new Set(state.signed || []);
  const list = element("ul", "ho-signed");
  list.id = "handover-signed";
  for (const [key, label] of [
    ["incoming", "نفر ورودی"],
    ["outgoing", "نفر خروجی"],
  ]) {
    if (signed.has(key)) list.appendChild(element("li", null, `${label} — امضا شد`));
  }
  job.appendChild(list);
  if (!signed.has("incoming")) {
    job.appendChild(signButton("incoming", "امضای نفر ورودی"));
  }
  if (!state.emergency && !signed.has("outgoing")) {
    job.appendChild(signButton("outgoing", "امضای نفر خروجی"));
  }
  stepContainer.appendChild(job);
}

function signButton(signer, label) {
  const button = element("button", "ho-action ho-action-primary", label);
  button.id = `handover-sign-${signer}`;
  button.type = "button";
  button.addEventListener("click", () =>
    guard(async () => adopt(await request(`${API}/${state.handover_id}/sign`, "POST", { signer: signer })))
  );
  return button;
}

function renderStep(step) {
  if (step === "cash") return renderCash();
  if (step === "shelf" || step === "cpr_room" || step === "trolley") return renderItems();
  if (step === "checklists") return renderChecklists();
  if (step === "parcels") return renderParcels();
  if (step === "dual_sign") return renderSign();
  stepContainer.replaceChildren();
}

function renderProgress() {
  const steps = state.steps || [];
  const index = steps.indexOf(state.step);
  $("handover-progress").textContent = `مرحلهٔ ${fa(index + 1)} از ${fa(steps.length)} — ${STEP_LABELS[state.step] || ""}`;
  const list = $("handover-steps");
  list.replaceChildren();
  steps.forEach((name, position) => {
    const mark = element("li", "ho-step-mark", STEP_LABELS[name] || "");
    if (position < index) mark.classList.add("is-done");
    if (position === index) {
      mark.classList.add("is-now");
      mark.setAttribute("aria-current", "step");
    }
    list.appendChild(mark);
  });
}

function render() {
  if (!state) {
    showScreen("start");
    return;
  }
  if (state.closed) {
    forget(state.flow);
    showScreen("done");
    return;
  }
  if (state.early_left && !state.emergency) {
    // H-08: nothing continues until the manager's phone confirmation is recorded.
    showScreen("waiting");
    return;
  }
  showScreen("wizard");
  if (state.step !== lastStep) {
    lastStep = state.step;
    clearMessage();
  }
  wizard.dataset.step = state.step;
  wizard.dataset.flow = state.flow;
  $("handover-early-leave").classList.toggle("hidden", Boolean(state.early_left));
  $("handover-early-leave-confirm").classList.add("hidden");
  renderProgress();
  renderStep(state.step);
}

/* ---------- flow lifecycle ---------- */

async function adopt(next) {
  state = next;
  remember(next.flow, next.handover_id);
  if (next.closed) {
    forget(next.flow);
    showScreen("done");
    return;
  }
  if (!targets || targets.handover_id !== next.handover_id) {
    targets = await request(`${API}/${next.handover_id}/targets`);
  }
  render();
}

async function refresh() {
  state = await request(`${API}/${state.handover_id}`);
  if (state.closed) {
    forget(state.flow);
    showScreen("done");
    return;
  }
  render();
}

async function startFlow(flowName) {
  showRefusal("");
  try {
    await adopt(await request(`${API}/start`, "POST", { flow: flowName }));
  } catch (error) {
    if (error instanceof OfflineLeft) return;
    if (error.status === 409 && String(error.message).includes(ALREADY_OPEN)) {
      showExists(error.message);
      return;
    }
    showRefusal(error.message);
  }
}

async function reportEarlyLeave() {
  state = await request(`${API}/${state.handover_id}/early-leave`, "POST");
  managerPicker.classList.add("hidden");
  $("handover-manager-button").classList.remove("hidden");
  showManagerMessage("");
  showScreen("waiting");
}

async function openManagers() {
  showManagerMessage("");
  const managers = await request(`${API}/managers`);
  managerSelect.replaceChildren();
  for (const entry of managers) {
    const option = document.createElement("option");
    option.value = entry.id;
    option.textContent = entry.name;
    managerSelect.appendChild(option);
  }
  if (!managers.length) {
    showManagerMessage("مدیری در فهرست نیست؛ با مدیر تماس بگیرید.");
    return;
  }
  $("handover-manager-button").classList.add("hidden");
  managerPicker.classList.remove("hidden");
}

async function confirmManager() {
  if (!managerSelect.value) {
    showManagerMessage("نام مدیر را انتخاب کنید.");
    return;
  }
  const confirmed = await request(`${API}/${state.handover_id}/emergency-start`, "POST", {
    manager_id: managerSelect.value,
  });
  await adopt(confirmed);
}

async function boot() {
  if (!navigator.onLine) {
    showOffline();
    return;
  }
  const stored = readStorage();
  let resumed = false;
  for (const flowName of FLOWS) {
    if (!stored[flowName]) continue;
    try {
      const next = await request(`${API}/${stored[flowName]}`);
      if (next.closed) {
        forget(flowName);
        continue;
      }
      await adopt(next);
      resumed = true;
      break;
    } catch (error) {
      if (error instanceof OfflineLeft) return;
      forget(flowName);
    }
  }
  if (!resumed) {
    state = null;
    targets = null;
    lastStep = null;
    showScreen("start");
  }
}

/* ---------- wiring (no inline handlers: CSP allows 'self' only) ---------- */

$("handover-retry").addEventListener("click", () => guard(boot));
$("handover-start-reception").addEventListener("click", () => guard(() => startFlow("reception")));
$("handover-start-nursing").addEventListener("click", () => guard(() => startFlow("nursing")));
$("handover-exists-back").addEventListener("click", () => {
  state = null;
  targets = null;
  lastStep = null;
  showRefusal("");
  showScreen("start");
});
$("handover-early-leave").addEventListener("click", () => {
  // H-08 blocks the flow until a manager confirms, so a stray tap needs one deliberate second tap.
  $("handover-early-leave").classList.add("hidden");
  $("handover-early-leave-confirm").classList.remove("hidden");
});
$("handover-early-leave-no").addEventListener("click", () => {
  $("handover-early-leave-confirm").classList.add("hidden");
  $("handover-early-leave").classList.toggle("hidden", Boolean(state && state.early_left));
});
$("handover-early-leave-yes").addEventListener("click", () => guard(reportEarlyLeave));
$("handover-manager-button").addEventListener("click", () => guard(openManagers));
$("handover-manager-confirm").addEventListener("click", () => guard(confirmManager));

window.addEventListener("offline", () => showOffline());
window.addEventListener("online", () => guard(boot));

guard(boot);
