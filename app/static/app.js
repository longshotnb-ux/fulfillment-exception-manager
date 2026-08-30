const state = {
  status: "",
  region: "",
  search: "",
  items: new Map(),
  selectedOrderId: null,
};

const elements = {
  activeExceptions: document.querySelector("#active-exceptions"),
  activeContext: document.querySelector("#active-context"),
  revenueRisk: document.querySelector("#revenue-risk"),
  lateRate: document.querySelector("#late-rate"),
  lateRateContext: document.querySelector("#late-rate-context"),
  averageDelay: document.querySelector("#average-delay"),
  lastSync: document.querySelector("#last-sync"),
  rows: document.querySelector("#exception-rows"),
  emptyState: document.querySelector("#empty-state"),
  resultCount: document.querySelector("#result-count"),
  regionFilter: document.querySelector("#region-filter"),
  search: document.querySelector("#search"),
  csvFile: document.querySelector("#csv-file"),
  dialog: document.querySelector("#manage-dialog"),
  exceptionForm: document.querySelector("#exception-form"),
  dialogOrderId: document.querySelector("#dialog-order-id"),
  orderContext: document.querySelector("#order-context"),
  dialogStatus: document.querySelector("#dialog-status"),
  dialogNotes: document.querySelector("#dialog-notes"),
  noteCount: document.querySelector("#note-count"),
  saveException: document.querySelector("#save-exception"),
  toast: document.querySelector("#toast"),
};

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

let toastTimer;
let searchTimer;

async function requestJSON(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new Error(payload?.detail || `Request failed with status ${response.status}.`);
  }
  return payload;
}

function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("visible");
  toastTimer = setTimeout(() => elements.toast.classList.remove("visible"), 3500);
}

function setText(selector, value) {
  document.querySelector(selector).textContent = value;
}

function renderSummary(summary) {
  elements.activeExceptions.textContent = summary.active_exceptions.toLocaleString();
  elements.activeContext.textContent = `${summary.resolved_exceptions} resolved · ${summary.workflow.Investigating} investigating`;
  elements.revenueRisk.textContent = currency.format(summary.revenue_at_risk);
  elements.lateRate.textContent = `${summary.late_rate_pct}%`;
  elements.lateRateContext.textContent = `${summary.late_orders} of ${summary.total_orders} orders`;
  elements.averageDelay.textContent = `${summary.average_delay_days} days`;
  setText("#count-all", summary.late_orders);
  setText("#count-open", summary.workflow.Open);
  setText("#count-investigating", summary.workflow.Investigating);
  setText("#count-resolved", summary.workflow.Resolved);

  const currentRegions = new Set(
    Array.from(elements.regionFilter.options).map((option) => option.value),
  );
  summary.regions.forEach((region) => {
    if (!currentRegions.has(region)) {
      const option = document.createElement("option");
      option.value = region;
      option.textContent = region;
      elements.regionFilter.append(option);
    }
  });
}

function createCell(primary, secondary = "", className = "") {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  const primaryElement = document.createElement("span");
  primaryElement.className = "primary-cell";
  primaryElement.textContent = primary;
  cell.append(primaryElement);
  if (secondary) {
    const secondaryElement = document.createElement("span");
    secondaryElement.className = "secondary-cell";
    secondaryElement.textContent = secondary;
    cell.append(secondaryElement);
  }
  return cell;
}

function renderRows(payload) {
  elements.rows.replaceChildren();
  state.items.clear();
  payload.items.forEach((item) => state.items.set(String(item.order_id), item));
  elements.emptyState.hidden = payload.items.length !== 0;
  elements.rows.closest(".table-wrap").hidden = payload.items.length === 0;
  elements.resultCount.textContent = `${payload.total} exception${payload.total === 1 ? "" : "s"}`;

  payload.items.forEach((item) => {
    const row = document.createElement("tr");
    row.append(createCell(`#${item.order_id}`, item.order_date));
    row.append(createCell(item.fulfillment_center, `${item.region} · ${item.category}`));

    const delayCell = document.createElement("td");
    delayCell.className = "delay-cell";
    delayCell.textContent = `+${item.delay_days} day${item.delay_days === 1 ? "" : "s"}`;
    row.append(delayCell);

    const valueCell = document.createElement("td");
    valueCell.textContent = currency.format(item.revenue);
    row.append(valueCell);

    const signalsCell = document.createElement("td");
    const signals = document.createElement("div");
    signals.className = "signal-list";
    if (item.defect) signals.append(makeSignal("Defect", true));
    if (item.returned) signals.append(makeSignal("Returned", true));
    if (!item.defect && !item.returned) signals.append(makeSignal("Delay only", false));
    signalsCell.append(signals);
    row.append(signalsCell);

    const statusCell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `status-pill status-${item.status.toLowerCase()}`;
    pill.textContent = item.status;
    statusCell.append(pill);
    row.append(statusCell);

    const actionCell = document.createElement("td");
    const manage = document.createElement("button");
    manage.type = "button";
    manage.className = "manage-button";
    manage.dataset.orderId = item.order_id;
    manage.textContent = "Manage";
    actionCell.append(manage);
    row.append(actionCell);
    elements.rows.append(row);
  });
}

function makeSignal(label, isAlert) {
  const signal = document.createElement("span");
  signal.className = `signal${isAlert ? " alert" : ""}`;
  signal.textContent = label;
  return signal;
}

function exceptionURL() {
  const params = new URLSearchParams();
  if (state.status) params.set("status", state.status);
  if (state.region) params.set("region", state.region);
  if (state.search) params.set("q", state.search);
  const query = params.toString();
  return `/api/exceptions${query ? `?${query}` : ""}`;
}

async function loadDashboard() {
  try {
    const [summary, exceptions] = await Promise.all([
      requestJSON("/api/summary"),
      requestJSON(exceptionURL()),
    ]);
    renderSummary(summary);
    renderRows(exceptions);
    elements.lastSync.textContent = `Updated ${new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date())}`;
  } catch (error) {
    showToast(error.message, true);
    elements.rows.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "loading-cell";
    cell.colSpan = 7;
    cell.textContent = "Could not load the exception queue.";
    row.append(cell);
    elements.rows.append(row);
  }
}

function openDialog(orderId) {
  const item = state.items.get(String(orderId));
  if (!item) return;
  state.selectedOrderId = item.order_id;
  elements.dialogOrderId.textContent = `#${item.order_id}`;
  elements.orderContext.textContent = `${item.fulfillment_center} · ${item.region} · ${item.category} · ${currency.format(item.revenue)} · ${item.delay_days} day${item.delay_days === 1 ? "" : "s"} late`;
  elements.dialogStatus.value = item.status;
  elements.dialogNotes.value = item.notes;
  elements.noteCount.textContent = item.notes.length;
  elements.dialog.showModal();
}

function closeDialog() {
  elements.dialog.close();
  state.selectedOrderId = null;
}

async function saveException(event) {
  event.preventDefault();
  if (!state.selectedOrderId) return;
  elements.saveException.disabled = true;
  elements.saveException.textContent = "Saving…";
  try {
    await requestJSON(`/api/exceptions/${state.selectedOrderId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: elements.dialogStatus.value,
        notes: elements.dialogNotes.value,
      }),
    });
    closeDialog();
    await loadDashboard();
    showToast("Exception updated.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.saveException.disabled = false;
    elements.saveException.textContent = "Save changes";
  }
}

async function importCSV(file) {
  if (!file) return;
  if (file.size > 1_000_000) {
    showToast("CSV files must be 1 MB or smaller.", true);
    return;
  }
  try {
    const result = await requestJSON("/api/import", {
      method: "POST",
      headers: { "Content-Type": "text/csv" },
      body: await file.text(),
    });
    await loadDashboard();
    showToast(
      `Imported ${result.imported_rows} rows: ${result.inserted_rows} new, ${result.updated_rows} updated.`,
    );
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.csvFile.value = "";
  }
}

document.querySelectorAll(".status-tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".status-tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    state.status = button.dataset.status;
    loadDashboard();
  });
});

elements.regionFilter.addEventListener("change", () => {
  state.region = elements.regionFilter.value;
  loadDashboard();
});

elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.search = elements.search.value.trim();
    loadDashboard();
  }, 250);
});

elements.rows.addEventListener("click", (event) => {
  const button = event.target.closest(".manage-button");
  if (button) openDialog(button.dataset.orderId);
});

elements.csvFile.addEventListener("change", () => importCSV(elements.csvFile.files[0]));
elements.dialogNotes.addEventListener("input", () => {
  elements.noteCount.textContent = elements.dialogNotes.value.length;
});
elements.exceptionForm.addEventListener("submit", saveException);
document.querySelector("#close-dialog").addEventListener("click", closeDialog);
document.querySelector("#cancel-dialog").addEventListener("click", closeDialog);
elements.dialog.addEventListener("click", (event) => {
  if (event.target === elements.dialog) closeDialog();
});

loadDashboard();
