const state = {
  status: "",
  region: "",
  search: "",
  items: new Map(),
  selectedOrderId: null,
  offset: 0,
  limit: 25,
  historyOffset: 0,
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
  pageCount: document.querySelector("#page-count"),
  previousPage: document.querySelector("#previous-page"),
  nextPage: document.querySelector("#next-page"),
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
  historyList: document.querySelector("#history-list"),
  historyStatus: document.querySelector("#history-status"),
  moreHistory: document.querySelector("#more-history"),
  toast: document.querySelector("#toast"),
};

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

let toastTimer;
let searchTimer;
let dashboardRequestId = 0;
let historyRequestId = 0;

async function requestJSON(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(Array.isArray(detail)
      ? detail.map((issue) => issue.msg).join("; ")
      : detail || `Request failed with status ${response.status}.`);
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

  elements.regionFilter.replaceChildren(new Option("All regions", ""));
  summary.regions.forEach((region) => {
    elements.regionFilter.append(new Option(region, region));
  });
  // Keep a selected region visible even when its last order was re-imported elsewhere.
  if (state.region && !summary.regions.includes(state.region)) {
    elements.regionFilter.append(new Option(state.region, state.region));
  }
  elements.regionFilter.value = state.region;
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
  const first = payload.items.length ? payload.offset + 1 : 0;
  const last = payload.offset + payload.items.length;
  elements.resultCount.textContent = payload.total
    ? `${first}–${last} of ${payload.total} exceptions`
    : "0 exceptions";
  elements.pageCount.textContent = `Page ${Math.floor(payload.offset / payload.limit) + 1} of ${Math.max(1, Math.ceil(payload.total / payload.limit))}`;
  elements.previousPage.disabled = payload.offset === 0;
  elements.nextPage.disabled = last >= payload.total;

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
  params.set("limit", state.limit);
  params.set("offset", state.offset);
  if (state.status) params.set("status", state.status);
  if (state.region) params.set("region", state.region);
  if (state.search) params.set("q", state.search);
  const query = params.toString();
  return `/api/exceptions${query ? `?${query}` : ""}`;
}

async function loadDashboard() {
  const requestId = ++dashboardRequestId;
  elements.previousPage.disabled = true;
  elements.nextPage.disabled = true;
  try {
    const [summary, exceptions] = await Promise.all([
      requestJSON("/api/summary"),
      requestJSON(exceptionURL()),
    ]);
    if (requestId !== dashboardRequestId) return false;
    if (state.offset > 0 && state.offset >= exceptions.total) {
      state.offset = Math.max(0, Math.ceil(exceptions.total / state.limit) - 1) * state.limit;
      return loadDashboard();
    }
    renderSummary(summary);
    renderRows(exceptions);
    elements.lastSync.textContent = `Updated ${new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date())}`;
    return true;
  } catch (error) {
    if (requestId !== dashboardRequestId) return false;
    showToast(error.message, true);
    state.items.clear();
    elements.emptyState.hidden = true;
    elements.rows.closest(".table-wrap").hidden = false;
    elements.resultCount.textContent = "Queue unavailable";
    elements.rows.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "loading-cell";
    cell.colSpan = 7;
    cell.textContent = "Could not load the exception queue.";
    row.append(cell);
    elements.rows.append(row);
    return false;
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
  state.historyOffset = 0;
  historyRequestId += 1;
  elements.historyList.replaceChildren();
  elements.moreHistory.hidden = true;
  elements.dialog.showModal();
  loadHistory();
}

async function loadHistory() {
  const orderId = state.selectedOrderId;
  const requestId = historyRequestId;
  elements.historyStatus.textContent = "Loading history…";
  elements.moreHistory.disabled = true;
  try {
    const history = await requestJSON(`/api/exceptions/${orderId}/history?offset=${state.historyOffset}`);
    if (requestId !== historyRequestId || orderId !== state.selectedOrderId) return;
    history.items.forEach((change) => {
      const entry = document.createElement("li");
      const time = document.createElement("time");
      time.dateTime = change.created_at;
      time.textContent = new Date(change.created_at).toLocaleString();
      const details = [];
      if (change.previous_status !== change.status) {
        details.push(`${change.previous_status} → ${change.status}`);
      }
      if (change.previous_notes !== change.notes) {
        details.push(`Notes: ${change.previous_notes || "(empty)"} → ${change.notes || "(cleared)"}`);
      }
      entry.append(time, document.createTextNode(details.join("\n")));
      elements.historyList.append(entry);
    });
    state.historyOffset += history.items.length;
    elements.historyStatus.textContent = history.total
      ? `${state.historyOffset} of ${history.total} changes · newest first`
      : "No changes recorded yet.";
    elements.moreHistory.hidden = state.historyOffset >= history.total;
  } catch (error) {
    if (requestId !== historyRequestId || orderId !== state.selectedOrderId) return;
    elements.historyStatus.textContent = `Could not load history: ${error.message}`;
    elements.moreHistory.hidden = false;
  } finally {
    if (requestId === historyRequestId) elements.moreHistory.disabled = false;
  }
}

function closeDialog() {
  elements.dialog.close();
  state.selectedOrderId = null;
  historyRequestId += 1;
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
    const refreshed = await loadDashboard();
    showToast(refreshed ? "Exception updated." : "Exception saved. Refresh to reload the queue.", !refreshed);
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
    state.offset = 0;
    const refreshed = await loadDashboard();
    showToast(
      `Imported ${result.imported_rows} rows: ${result.inserted_rows} new, ${result.updated_rows} updated.${refreshed ? "" : " Refresh to reload the queue."}`,
      !refreshed,
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
    state.offset = 0;
    loadDashboard();
  });
});

elements.regionFilter.addEventListener("change", () => {
  state.region = elements.regionFilter.value;
  state.offset = 0;
  loadDashboard();
});

elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.search = elements.search.value.trim();
    state.offset = 0;
    loadDashboard();
  }, 250);
});

elements.rows.addEventListener("click", (event) => {
  const button = event.target.closest(".manage-button");
  if (button) openDialog(button.dataset.orderId);
});

elements.csvFile.addEventListener("change", () => importCSV(elements.csvFile.files[0]));
document.querySelector("#import-button").addEventListener("click", () => elements.csvFile.click());
elements.previousPage.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadDashboard();
});
elements.nextPage.addEventListener("click", () => {
  state.offset += state.limit;
  loadDashboard();
});
elements.moreHistory.addEventListener("click", loadHistory);
elements.dialog.addEventListener("close", () => {
  state.selectedOrderId = null;
  historyRequestId += 1;
});
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
