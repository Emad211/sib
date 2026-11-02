/**
 * Sib Shift - phone handover wizard (SHF-2 slice 5).
 *
 * The screen a tired receptionist or nurse uses at 2 am: one job per screen (R-10), big type,
 * big tap targets, no dense tables. It drives the slice-4 API and shows the answer of a blind
 * count as a status word only — never a reference, an expected figure, a comparison or a delta
 * (R-02, ADR-011).
 *
 * Each person signs on their own phone, so a second phone joins the flow the first one opened
 * (`GET /current`) and the sign screen offers only the caller's own signature (S03 §3, H-04).
 * The checklists and the parcels in transit are recorded on their own steps instead of being
 * walked past, and the checklists step leaves only when every item has an answer.
 *
 * Handover calls go straight to `fetch` and never into the IndexedDB outbox (ADR-016): a flow
 * needs the clinic server, and offline the screen says so instead of pretending.
 */

const API = "/api/v1/handover";
const ALREADY_OPEN = "این تحویل از قبل برای این شیفت باز شده است";
const STORAGE_KEY = "shift:handover";
const FLOWS = ["reception", "nursing"];

const FLOW_LABELS = { reception: "پذیرش", nursing: "پرستاری" };
// The role that answers for each flow: a person with exactly one of them goes straight into that handover.
const FLOW_ROLE = { reception: "reception", nursing: "nurse" };

const STEP_LABELS = {
  cash: "جمع صندوق",
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
  recount: "دوباره بشمارید",
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
// G2: the picture that stands for an item, so a tired person knows the row before reading its name.
const iconFor = (name) => (window.itemIcons ? window.itemIcons.for(name) : "");
let lastStep = null;
let busy = false;
// Which checklist items this phone has answered for the open flow; the step leaves only when
// every item on the list is in here, so nobody walks past H-04's checklist step.
const answeredChecklists = new Set();

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

// The two phones share one flow. A phone that is behind its colleague (they settled the joint count, they
// moved to the next step) gets a refusal for something that is already done. That is not an error for the
// person: the screen follows the flow and says so, instead of leaving a dead end.
function flowSignature(flowState) {
  return JSON.stringify([
    flowState.step,
    flowState.cash_status,
    flowState.counts,
    flowState.signed,
    flowState.closed,
    flowState.early_left,
  ]);
}

async function followColleague() {
  if (!state) return false;
  const before = flowSignature(state);
  try {
    const next = await request(`${API}/${state.handover_id}`);
    if (flowSignature(next) === before) return false;
    await adopt(next);
    return true;
  } catch (error) {
    return false;
  }
}

async function guard(work) {
  if (busy) return;
  busy = true;
  try {
    await work();
  } catch (error) {
    if (error instanceof OfflineLeft) return;
    if (error.status === 409 && (await followColleague())) {
      showMessage("همکار شما جلوتر رفته بود؛ صفحه تازه شد.", "info");
    } else {
      showMessage(error.message || "درخواست انجام نشد؛ دوباره تلاش کنید.", "error");
    }
  } finally {
    busy = false;
  }
}

// Every few seconds the wizard asks where the flow is and follows it, so the other person's phone never
// shows a step that is already over. What somebody is typing is never wiped.
function watchFlow() {
  window.setInterval(async () => {
    if (document.hidden || busy || !state || screens.wizard.classList.contains("hidden")) return;
    const active = document.activeElement;
    if (active && active.tagName === "INPUT" && active.value) return;
    try {
      const next = await request(`${API}/${state.handover_id}`);
      if (flowSignature(next) !== flowSignature(state)) await adopt(next);
    } catch (error) {
      /* offline or refused: the next tick tries again */
    }
  }, 5000);
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

function decimalNumber(raw) {
  // A checklist reading is a gauge value, never a negative: the event's own schema refuses one.
  const text = foldDigits(raw).trim();
  if (!text) return { error: "عدد را وارد کنید." };
  if (!/^[0-9]+(\.[0-9]+)?$/.test(text)) return { error: "فقط عدد وارد کنید." };
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

function textField(className, id, label) {
  const input = document.createElement("input");
  input.className = className;
  input.type = "text";
  input.autocomplete = "off";
  input.dir = "ltr";
  input.id = id;
  input.setAttribute("aria-label", label);
  return input;
}

function countField(target) {
  const input = document.createElement("input");
  input.className = "ho-number";
  input.type = "text";
  input.inputMode = "numeric";
  input.autocomplete = "off";
  input.dir = "ltr";
  input.dataset.scope = target.scope;
  input.setAttribute("aria-label", target.scope === "cash" ? "جمع کل پول صندوق" : "شمارش این قلم");
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
  showMessage(statusText(result.status), DONE_STATUSES.has(result.status) ? "ok" : "info");
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
    hint:
      target.scope === "cash"
        ? "هر دو نفر با هم همهٔ پول نقد داخل صندوق را بشمارید و جمع کل را وارد کنید."
        : "هر دو نفر با هم بشمارید و عدد توافق‌شده را وارد کنید.",
    inputId: "handover-joint-input",
    submitId: "handover-joint-submit",
    submitLabel: "ثبت شمارش مشترک",
    endpoint: "joint",
  });
}

/* ---------- step renders ---------- */

async function advanceFlow() {
  // Say which step this phone is leaving: if the colleague's phone already moved on, nothing is skipped.
  await adopt(await request(`${API}/${state.handover_id}/advance`, "POST", { from: state.step }));
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

const SIDE_LABELS = { incoming: "نفر ورودی", outgoing: "نفر خروجی" };

// Screens name the person («پذیرش یک»), not the side; the side word is only a fallback.
function sideName(side) {
  const name = state && (side === "incoming" ? state.incoming_name : state.outgoing_name);
  return name || SIDE_LABELS[side] || "همکار شما";
}

function otherName() {
  return sideName(state && state.my_side === "incoming" ? "outgoing" : "incoming");
}

function statusText(status) {
  if (status === "incoming") return `شمارش ثبت شد؛ ${otherName()} با گوشی خودش بشمارد`;
  if (status === "outgoing") return `نوبت ${sideName("outgoing")} است؛ با گوشی خودش بشمارد`;
  return STATUS_LABELS[status] || "";
}
const WAIT_POLL_MS = 4000;
let waitTimer = null;

// A4: every count belongs to one side and is taken on that person's own phone. The other side sees
// whose turn it is and the screen follows the flow by itself, so nobody has to pass a phone around.
function turnOf(scope, status) {
  if (state.emergency) return "incoming"; // the outgoing person left: the incoming one counts alone
  if (scope === "cash") return status === "first" ? "outgoing" : "incoming";
  return status === "outgoing" ? "outgoing" : "incoming";
}

function isOtherSidesTurn(scope, status) {
  return Boolean(state.my_side) && turnOf(scope, status) !== state.my_side;
}

function waitingJob(scope, status) {
  const side = turnOf(scope, status);
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.id = "handover-turn";
  job.appendChild(element("h2", "ho-job-title", `نوبت ${sideName(side)} است`));
  job.appendChild(element("p", "ho-hint", "این شمارش با گوشی خودِ او انجام می‌شود؛ این صفحه خودکار تازه می‌شود."));
  stepContainer.appendChild(job);
  window.clearTimeout(waitTimer);
  waitTimer = window.setTimeout(() => guard(refresh), WAIT_POLL_MS);
}

function renderCash() {
  if (state.cash_status === "joint") {
    jointJob({ scope: "cash" });
    return;
  }
  if (DONE_STATUSES.has(state.cash_status)) {
    advanceJob("جمع صندوق ثبت و تأیید شد.", "ادامه");
    return;
  }
  if (isOtherSidesTurn("cash", state.cash_status)) {
    waitingJob("cash", state.cash_status);
    return;
  }
  numberJob(
    { scope: "cash" },
    {
      title: "جمع کل پول صندوق",
      hint: "همهٔ پول نقد داخل صندوق را با هم جمع بزنید و جمع کل را وارد کنید.",
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
  if (status !== "joint" && isOtherSidesTurn("item", status)) {
    waitingJob("item", status);
    return;
  }
  if (status === "joint") {
    jointJob(target);
    return;
  }
  numberJob(target, {
    title: `${iconFor(info.icon)} ${info.name}`,
    kicker: kicker,
    hint: `واحد: ${info.unit}`,
    inputId: "handover-count-input",
    submitId: "handover-count-submit",
    submitLabel: "ثبت شمارش",
    endpoint: "counts",
  });
}

function checklistRow(row) {
  const item = element("li", "ho-checklist-item");
  item.dataset.itemId = row.item_id;
  item.appendChild(element("p", "ho-checklist-name", `${iconFor(row.icon)} ${row.name}`));
  if (answeredChecklists.has(row.item_id)) {
    item.appendChild(element("p", "ho-answered", "پاسخ ثبت شد."));
    return item;
  }
  const actions = element("div", "ho-checklist-actions");
  if (row.kind === "reading") {
    const input = textField("ho-reading", `handover-reading-${row.item_id}`, `مقدار ${row.name}`);
    input.inputMode = "decimal";
    const submit = element("button", "ho-action ho-action-primary", "ثبت");
    submit.id = `handover-checklist-${row.item_id}-submit`;
    submit.type = "button";
    submit.addEventListener("click", () => guard(() => answerChecklist(row.item_id, true, input)));
    actions.append(input, submit);
  } else {
    const yes = element("button", "ho-action ho-action-primary", "بله");
    yes.id = `handover-checklist-${row.item_id}-yes`;
    yes.type = "button";
    yes.addEventListener("click", () => guard(() => answerChecklist(row.item_id, true)));
    const no = element("button", "ho-action", "خیر");
    no.id = `handover-checklist-${row.item_id}-no`;
    no.type = "button";
    no.addEventListener("click", () => guard(() => answerChecklist(row.item_id, false)));
    actions.append(yes, no);
  }
  item.appendChild(actions);
  return item;
}

function renderChecklists() {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("h2", "ho-job-title", "چک‌لیست"));
  job.appendChild(element("p", "ho-hint", "هر مورد را پاسخ بدهید؛ تا پاسخ همهٔ موارد، ادامه فعال نمی‌شود."));
  const list = element("ul", "ho-checklist");
  list.id = "handover-checklist-list";
  const rows = (targets && targets.checklists) || [];
  for (const row of rows) list.appendChild(checklistRow(row));
  job.appendChild(list);
  if (!rows.length) {
    job.appendChild(element("p", "ho-text", "چک‌لیستی برای این تحویل ثبت نشده است."));
    job.appendChild(advanceButton("ادامه"));
  } else if (rows.every((row) => answeredChecklists.has(row.item_id))) {
    job.appendChild(advanceButton("ادامه"));
  }
  stepContainer.appendChild(job);
}

async function answerChecklist(itemId, ok, input) {
  const body = { item_id: itemId, ok: ok };
  if (input) {
    const answer = decimalNumber(input.value);
    if (answer.error) {
      showMessage(answer.error, "error");
      return;
    }
    body.reading = answer.value;
  }
  await request(`${API}/${state.handover_id}/checklists`, "POST", body);
  answeredChecklists.add(itemId);
  await refresh();
  showMessage("پاسخ ثبت شد.", "ok");
}

function renderParcels() {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("h2", "ho-job-title", "بسته‌های در راه"));
  const known = state.parcels_in_transit || [];
  if (known.length) {
    // SHF-4: a parcel the store has not taken yet is a real delivery, so the screen offers its own id
    // instead of asking a tired person to type a number nobody can check later (P-12).
    job.appendChild(element("p", "ho-text", "بستهٔ در راه را از فهرست انتخاب کنید؛ شناسهٔ آن همان دریافت پذیرش است."));
    const selectLabel = element("label", "ho-label", "بستهٔ در راه");
    selectLabel.htmlFor = "handover-parcel-select";
    const select = element("select", "ho-text-input");
    select.id = "handover-parcel-select";
    for (const parcel of known) {
      const option = element(
        "option",
        "",
        `${parcel.invoice_reference}${parcel.packages ? ` — ${parcel.packages} بسته` : ""}`
      );
      option.value = parcel.parcel_id;
      select.appendChild(option);
    }
    const submit = element("button", "ho-action ho-action-primary", "ثبت بسته");
    submit.id = "handover-parcel-submit";
    submit.type = "button";
    submit.addEventListener("click", () => guard(submitKnownParcel));
    job.append(selectLabel, select, submit);
    job.appendChild(advanceButton("بستهٔ دیگری در راه نیست، ادامه"));
    stepContainer.appendChild(job);
    return;
  }
  job.appendChild(
    element(
      "p",
      "ho-text",
      "اگر بستهٔ در راهی هست، شناسهٔ آن و تعداد بسته‌ها را ثبت کنید. اگر بسته‌ای در راه نیست، ادامه بدهید."
    )
  );
  const idLabel = element("label", "ho-label", "شناسهٔ بسته");
  idLabel.htmlFor = "handover-parcel-id";
  const idInput = textField("ho-text-input", "handover-parcel-id", "شناسهٔ بسته");
  const countLabel = element("label", "ho-label", "تعداد بسته");
  countLabel.htmlFor = "handover-parcel-packages";
  const countInput = textField("ho-number ho-number-small", "handover-parcel-packages", "تعداد بسته");
  countInput.inputMode = "numeric";
  const submit = element("button", "ho-action ho-action-primary", "ثبت بسته");
  submit.id = "handover-parcel-submit";
  submit.type = "button";
  submit.addEventListener("click", () => guard(submitParcel));
  job.append(idLabel, idInput, countLabel, countInput, submit);
  job.appendChild(advanceButton("بسته‌ای در راه نیست، ادامه"));
  stepContainer.appendChild(job);
}

async function submitKnownParcel() {
  const select = $("handover-parcel-select");
  const parcel = (state.parcels_in_transit || []).find((entry) => entry.parcel_id === select.value);
  if (!parcel) {
    showMessage("بستهٔ در راه را انتخاب کنید.", "error");
    return;
  }
  await request(`${API}/${state.handover_id}/parcels`, "POST", {
    parcel_id: parcel.parcel_id,
    packages: parcel.packages || 1,
  });
  await refresh();
  showMessage("بسته ثبت شد.", "ok");
}

async function submitParcel() {
  const parcelId = $("handover-parcel-id").value.trim();
  if (!parcelId) {
    showMessage("شناسهٔ بسته را وارد کنید.", "error");
    return;
  }
  const packages = wholeNumber($("handover-parcel-packages").value);
  if (packages.error) {
    showMessage(packages.error, "error");
    return;
  }
  await request(`${API}/${state.handover_id}/parcels`, "POST", {
    parcel_id: parcelId,
    packages: packages.value,
  });
  await refresh();
  showMessage("بسته ثبت شد.", "ok");
}

function renderSign() {
  stepContainer.replaceChildren();
  const job = element("div", "ho-job");
  job.appendChild(element("h2", "ho-job-title", "امضای دو طرف"));
  job.appendChild(element("p", "ho-hint", "هر نفر امضای خودش را روی گوشی خودش ثبت می‌کند."));
  if (state.emergency) {
    job.appendChild(element("p", "ho-hint", "نفر خروجی حاضر نیست؛ فقط نفر ورودی امضا می‌کند."));
  }
  const signed = new Set(state.signed || []);
  const list = element("ul", "ho-signed");
  list.id = "handover-signed";
  for (const [key, label] of [
    ["incoming", sideName("incoming")],
    ["outgoing", sideName("outgoing")],
  ]) {
    if (signed.has(key)) list.appendChild(element("li", null, `${label} — امضا شد`));
  }
  job.appendChild(list);
  const mine = element("button", "ho-action ho-action-primary", "ثبت امضای من");
  mine.id = "handover-sign-own";
  mine.type = "button";
  mine.addEventListener("click", () => guard(signOwn));
  job.appendChild(mine);
  job.appendChild(
    element("p", "ho-hint", "اگر نفر دیگر هنوز امضا نکرده است، بعد از امضای او «تازه‌سازی وضعیت» را بزنید.")
  );
  stepContainer.appendChild(job);
}

async function signOwn() {
  // The server matches the session's person against the flow's two people (S03 §3), so the
  // request names no signer: this phone can only ever sign its own side.
  const next = await request(`${API}/${state.handover_id}/sign`, "POST");
  await adopt(next);
  if (!next.closed) {
    showMessage("امضای شما ثبت شد؛ منتظر امضای نفر دیگر باشید.", "ok");
  }
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
  // A4: only the state read carries the caller's side; an answer to a write is read once more.
  if (next && next.handover_id && !next.my_side) next = await request(`${API}/${next.handover_id}`);
  if (state !== null && state.handover_id !== next.handover_id) answeredChecklists.clear();
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

async function loadOpenFlows() {
  const flows = await request(`${API}/current`);
  const list = $("handover-open-list");
  list.replaceChildren();
  for (const flow of flows) {
    const row = element("li", "ho-open-row");
    const join = element("button", "ho-action", `پیوستن به تحویل ${FLOW_LABELS[flow.flow] || flow.flow}`);
    join.id = `handover-join-${flow.flow}`;
    join.type = "button";
    join.addEventListener("click", () => guard(() => joinFlow(flow.handover_id)));
    const where = STEP_LABELS[flow.step] || "";
    const note = flow.early_left ? `${where} — نفر خروجی رفته است` : where;
    row.append(join, element("p", "ho-hint", `مرحلهٔ جاری: ${note}`));
    list.appendChild(row);
  }
  $("handover-open").classList.toggle("hidden", flows.length === 0);
  return flows;
}

function showChooser() {
  $("handover-choose").classList.remove("hidden");
  $("handover-await").classList.add("hidden");
}

// Owner rule: «تحویل شیفت» takes a receptionist into the reception handover and a nurse into the nursing
// one, with no menu. The person who is not on shift yet starts it; the one who holds the shift joins it
// as soon as it exists (their phone is told on any page, and this screen waits and follows by itself).
async function goStraightIn(open) {
  let me;
  try {
    me = await request("/api/v1/auth/me");
  } catch (error) {
    if (error instanceof OfflineLeft) return;
    showChooser();
    return;
  }
  const own = FLOWS.filter((flow) => (me.roles || []).includes(FLOW_ROLE[flow]));
  if (own.length !== 1) {
    showChooser();
    return;
  }
  const flow = own[0];
  const running = open.find((entry) => entry.flow === flow);
  if (running) {
    await joinFlow(running.handover_id);
    return;
  }
  if (!(me.on_shift || []).includes(FLOW_ROLE[flow])) {
    await startFlow(flow);
    return;
  }
  $("handover-await-text").textContent = `منتظر بمانید تا همکارتان تحویل ${FLOW_LABELS[flow]} را شروع کند؛ همین‌جا خودکار وارد می‌شوید.`;
  $("handover-await").classList.remove("hidden");
  window.clearTimeout(waitTimer);
  waitTimer = window.setTimeout(() => guard(boot), WAIT_POLL_MS);
}

async function joinFlow(handoverId) {
  await adopt(await request(`${API}/${handoverId}`));
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

function comingBack() {
  const entry = window.performance && performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
  return Boolean(entry) && (entry.type === "reload" || entry.type === "back_forward");
}

async function boot() {
  if (!navigator.onLine) {
    showOffline();
    return;
  }
  // A reload or the back button goes back into the flow this phone was in; opening the page from home
  // asks which handover, and the open one is offered under «تحویل باز» instead of being forced on the person.
  const stored = comingBack() ? readStorage() : {};
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
    answeredChecklists.clear();
    const joinId = new URLSearchParams(window.location.search).get("join");
    if (joinId) {
      try {
        await joinFlow(joinId);
        return;
      } catch (error) {
        if (error instanceof OfflineLeft) return;
      }
    }
    showScreen("start");
    await goStraightIn(await loadOpenFlows());
  }
}

/* ---------- wiring (no inline handlers: CSP allows 'self' only) ---------- */

watchFlow();
$("handover-retry").addEventListener("click", () => guard(boot));
$("handover-start-reception").addEventListener("click", () => guard(() => startFlow("reception")));
$("handover-start-nursing").addEventListener("click", () => guard(() => startFlow("nursing")));
$("handover-exists-back").addEventListener("click", () =>
  guard(async () => {
    state = null;
    targets = null;
    lastStep = null;
    answeredChecklists.clear();
    showRefusal("");
    showScreen("start");
    await loadOpenFlows();
    showChooser();
  })
);
$("handover-refresh").addEventListener("click", () => guard(refresh));
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
