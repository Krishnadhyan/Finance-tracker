/**
 * AI Finance Tracker Dashboard - App Engine JavaScript
 */

const API_BASE = "";

// State
let state = {
  overview: null,
  transactions: [],
  filteredTransactions: [],
  visibleTxnLimit: 10,
  targetAmb: 10000,
  anchorBalance: 10000,
  activeTab: "tab-overview",
  selectedTxn: null
};

// DOM Elements
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initListeners();
  loadDashboardData();
  loadTransactionsData();
});

// Programmatic Tab Switcher
function switchTab(targetPaneId) {
  if (!targetPaneId) return;

  // Update tab buttons active state
  const tabs = document.querySelectorAll(".nav-tab");
  tabs.forEach(tab => {
    if (tab.dataset.tab === targetPaneId) {
      tab.classList.add("active");
    } else {
      tab.classList.remove("active");
    }
  });

  // Update tab panes visibility
  document.querySelectorAll(".tab-pane").forEach(pane => {
    if (pane.id === targetPaneId) {
      pane.classList.add("active");
      pane.style.display = "block";
    } else {
      pane.classList.remove("active");
      pane.style.display = "none";
    }
  });

  state.activeTab = targetPaneId;
  window.scrollTo({ top: 0, behavior: 'smooth' });

  // Trigger re-render of canvas charts when active tab changes
  if (targetPaneId === "tab-overview" && state.overview) {
    setTimeout(() => {
      renderChart(state.overview.amb_forecast);
      renderPieChart(state.overview.category_breakdown, state.overview.total_debit);
    }, 50);
  } else if (targetPaneId === "tab-amb") {
    runSimulatorForecast();
  }
}

// Make switchTab globally accessible for HTML onclick attributes
window.switchTab = switchTab;

function initTabs() {
  const tabs = document.querySelectorAll(".nav-tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      switchTab(tab.dataset.tab);
    });
  });
}

// Event Listeners
function initListeners() {
  // Simulator sliders & inputs
  const slider = document.getElementById("sim-target-slider");
  const inputAmb = document.getElementById("sim-target-amb");

  slider.addEventListener("input", (e) => {
    inputAmb.value = e.target.value;
    runSimulatorForecast();
  });

  inputAmb.addEventListener("change", (e) => {
    slider.value = e.target.value;
    runSimulatorForecast();
  });

  document.getElementById("sim-anchor-balance").addEventListener("change", runSimulatorForecast);
  document.getElementById("sim-deposit").addEventListener("input", runSimulatorForecast);
  document.getElementById("sim-expense").addEventListener("input", runSimulatorForecast);
  document.getElementById("btn-recalculate-sim").addEventListener("click", runSimulatorForecast);

  // Send ntfy alert button
  document.getElementById("btn-trigger-ntfy").addEventListener("click", triggerNtfyAlert);

  // Save Anchor button
  document.getElementById("btn-save-anchor").addEventListener("click", saveMonthlyAnchor);

  // Transactions search & filters
  document.getElementById("txn-search").addEventListener("input", filterTransactions);
  document.getElementById("filter-category").addEventListener("change", filterTransactions);
  document.getElementById("filter-type").addEventListener("change", filterTransactions);

  // Load More Transactions button
  const btnLoadMore = document.getElementById("btn-load-more");
  if (btnLoadMore) {
    btnLoadMore.addEventListener("click", () => {
      state.visibleTxnLimit += 10;
      renderTransactionsTable(state.filteredTransactions);
    });
  }

  // Vendor Tagging tester
  document.getElementById("btn-test-tag").addEventListener("click", testVendorTagging);

  // Cloud sync
  document.getElementById("btn-sync-cloud").addEventListener("click", syncCloudDatabase);

  // Modal close
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("txn-modal").addEventListener("click", (e) => {
    if (e.target.id === "txn-modal") closeModal();
  });

  // Window resize handler for responsive charts
  window.addEventListener("resize", () => {
    if (state.activeTab === "tab-overview" && state.overview) {
      renderChart(state.overview.amb_forecast);
      renderPieChart(state.overview.category_breakdown, state.overview.total_debit);
    }
  });
}

// Fetch Overview Data
async function loadDashboardData() {
  try {
    const res = await fetch(`${API_BASE}/api/overview`);
    const data = await res.json();
    state.overview = data;
    renderOverview(data);
    renderChart(data.amb_forecast);
    
    // Update Cloud Status Header
    const cloudBadgeText = document.getElementById("cloud-status-text");
    if (data.supabase_configured) {
      cloudBadgeText.textContent = "Supabase Cloud Connected";
      document.getElementById("sync-url").textContent = "Connected to Supabase PostgreSQL";
    } else {
      cloudBadgeText.textContent = "Local Mode (.env config needed)";
      document.getElementById("sync-url").textContent = "Not configured in .env";
    }

    document.getElementById("checkpoint-text").textContent = `Msg Checkpoint: ${data.checkpoint_msg_id || "None"}`;
    document.getElementById("sync-msg-id").textContent = data.checkpoint_msg_id || "None";

  } catch (err) {
    console.error("Failed loading overview:", err);
  }
}

// Render Overview & KPIs
function renderOverview(data) {
  const fc = data.amb_forecast;

  document.getElementById("kpi-balance").textContent = `₹${(fc.current_available_balance || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  document.getElementById("kpi-anchor-info").textContent = `Anchor Start: ₹${(fc.anchor_balance || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

  document.getElementById("kpi-current-amb").textContent = `₹${(fc.current_amb || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  document.getElementById("kpi-target-amb").textContent = `Target: ₹${(fc.target_amb || 10000).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

  document.getElementById("kpi-req-daily").textContent = `₹${(fc.required_daily_balance || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  document.getElementById("kpi-days-left").textContent = `Remaining Days: ${fc.days_remaining} of ${fc.total_days_in_month}`;

  const netFlow = data.net_flow || 0;
  const netFlowEl = document.getElementById("kpi-net-flow");
  netFlowEl.textContent = `₹${netFlow.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  netFlowEl.style.color = netFlow >= 0 ? "#10b981" : "#f43f5e";
  document.getElementById("kpi-flow-sub").textContent = `Credits (₹${data.total_credit.toLocaleString()}) - Debits (₹${data.total_debit.toLocaleString()})`;

  // Render Health Score KPI
  if (data.health_score) {
    const hs = data.health_score;
    const hsEl = document.getElementById("kpi-health-score");
    if (hsEl) {
      hsEl.textContent = `${hs.score}/100`;
      hsEl.style.color = hs.score >= 85 ? "#10b981" : (hs.score >= 70 ? "#f59e0b" : "#f43f5e");
    }
    const hrEl = document.getElementById("kpi-health-rating");
    if (hrEl) hrEl.textContent = `Rating: ${hs.rating}`;
  }

  // Render Alert Banner
  const alertBanner = document.getElementById("amb-alert-banner");
  if (fc.status === "WARNING" || fc.shortfall > 0) {
    alertBanner.classList.remove("hidden", "safe-banner");
    alertBanner.classList.add("warning-banner");
    document.getElementById("alert-title").textContent = `🚨 AMB Shortfall Warning (${fc.month_year})`;
    document.getElementById("alert-message").textContent = `Current AMB is ₹${fc.current_amb.toLocaleString()} (Target: ₹${fc.target_amb.toLocaleString()}). You need to maintain ₹${fc.required_daily_balance.toLocaleString()}/day for remaining ${fc.days_remaining} days. Deposit ₹${fc.shortfall.toLocaleString()} before midnight!`;
  } else {
    alertBanner.classList.remove("hidden", "warning-banner");
    alertBanner.classList.add("safe-banner");
    document.getElementById("alert-title").textContent = `✅ AMB Status Safe (${fc.month_year})`;
    document.getElementById("alert-message").textContent = fc.message;
  }

  // Render Category Bars, Pie Chart & Recurring Subs
  renderCategoryBars(data.category_breakdown, data.total_debit);
  renderPieChart(data.category_breakdown, data.total_debit);
  renderRecurringSubscriptions(data.recurring_subscriptions);
}

// Render Recurring Subscriptions & AutoPays
function renderRecurringSubscriptions(list) {
  const container = document.getElementById("recurring-subs-grid");
  if (!container) return;
  container.innerHTML = "";

  if (!list || list.length === 0) {
    container.innerHTML = `<p class="sub-text">No recurring subscriptions or AutoPay commitments detected yet.</p>`;
    return;
  }

  list.forEach(item => {
    const card = document.createElement("div");
    card.className = "recurring-card";
    card.innerHTML = `
      <div class="recurring-head">
        <span class="sub-chip">${item.category || 'General'}</span>
        <span>${item.count} Txns</span>
      </div>
      <div class="recurring-party">${item.party}</div>
      <div class="recurring-amt">₹${item.last_amount.toLocaleString("en-IN", { minimumFractionDigits: 2 })}</div>
      <div class="sub-text" style="font-size: 11.5px;">Last debit: ${item.last_date || 'recent'}</div>
    `;
    container.appendChild(card);
  });
}

// Export CSV Function
window.exportTransactionsCSV = function() {
  window.location.href = `${API_BASE}/api/export/csv`;
};

// Category Colors for Pie Chart & Bars
const CATEGORY_COLORS = {
  "Food & Dining": "#9333ea",
  "Utilities": "#06b6d4",
  "Bills & Utilities": "#06b6d4",
  "Travel & Transport": "#10b981",
  "Transport": "#10b981",
  "Groceries": "#f59e0b",
  "Groceries & Supplies": "#f59e0b",
  "Shopping": "#f43f5e",
  "Entertainment": "#ec4899",
  "Health & Medical": "#3b82f6",
  "Transfers & Personal": "#8b5cf6",
  "Uncategorized": "#64748b"
};

// Donut / Pie Chart Renderer for Category Spending
function renderPieChart(breakdown, totalDebit) {
  const canvas = document.getElementById("category-pie-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const dpr = window.devicePixelRatio || 1;
  const parent = canvas.parentElement;
  const width = Math.min(parent ? parent.clientWidth : 280, 280);
  const height = 180;

  canvas.width = width * dpr;
  canvas.height = height * dpr;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, width, height);

  const sortedCats = Object.entries(breakdown || {}).sort((a, b) => b[1] - a[1]);
  if (sortedCats.length === 0 || totalDebit <= 0) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "13px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No spending data available", width / 2, height / 2);
    return;
  }

  const centerX = width / 2;
  const centerY = height / 2;
  const outerRadius = 70;
  const innerRadius = 42;

  let startAngle = -Math.PI / 2;

  sortedCats.forEach(([cat, amt]) => {
    const sliceAngle = (amt / totalDebit) * (Math.PI * 2);
    const endAngle = startAngle + sliceAngle;
    const color = CATEGORY_COLORS[cat] || "#64748b";

    ctx.beginPath();
    ctx.arc(centerX, centerY, outerRadius, startAngle, endAngle);
    ctx.arc(centerX, centerY, innerRadius, endAngle, startAngle, true);
    ctx.closePath();

    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#121826";
    ctx.lineWidth = 2;
    ctx.stroke();

    startAngle = endAngle;
  });

  // Donut center text
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 14px Outfit, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("₹" + Math.round(totalDebit).toLocaleString("en-IN"), centerX, centerY - 6);

  ctx.fillStyle = "#94a3b8";
  ctx.font = "11px Inter, sans-serif";
  ctx.fillText("Total Spent", centerX, centerY + 10);
}

// Category Spending Bars
function renderCategoryBars(breakdown, totalDebit) {
  const container = document.getElementById("category-bars");
  container.innerHTML = "";

  const sortedCats = Object.entries(breakdown || {}).sort((a, b) => b[1] - a[1]);

  if (sortedCats.length === 0) {
    container.innerHTML = `<p class="sub-text">No category spending data yet.</p>`;
    return;
  }

  sortedCats.forEach(([cat, amt]) => {
    const pct = totalDebit > 0 ? Math.round((amt / totalDebit) * 100) : 0;
    const item = document.createElement("div");
    item.className = "cat-bar-item";
    const color = CATEGORY_COLORS[cat] || "#7f00ff";

    item.innerHTML = `
      <div class="cat-bar-header">
        <strong><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};margin-right:6px;"></span>${cat}</strong>
        <span>₹${amt.toLocaleString("en-IN", { minimumFractionDigits: 2 })} (${pct}%)</span>
      </div>
      <div class="cat-bar-bg">
        <div class="cat-bar-fill" style="width: ${pct}%; background: ${color};"></div>
      </div>
    `;
    container.appendChild(item);
  });
}

// Canvas EOD Balance Chart
function renderChart(forecast) {
  const canvas = document.getElementById("eod-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const parent = canvas.parentElement;
  const w = parent ? parent.clientWidth : (canvas.getBoundingClientRect().width || 600);
  const h = 220;

  if (w <= 0) return;

  // Adjust canvas pixel density for high DPI / Retina displays
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;

  // Explicitly constrain CSS display size to container width
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;

  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  // Demo EOD balances points (or simulated timeline)
  const daysPassed = forecast.days_passed || 15;
  const totalDays = forecast.total_days_in_month || 31;
  const anchor = forecast.anchor_balance || 10000;
  const currentBal = forecast.current_available_balance || 10000;

  // Generate simulated smooth points from day 1 to daysPassed
  const points = [];
  const paddingX = 40;
  const paddingY = 30;

  for (let i = 1; i <= totalDays; i++) {
    let bal = anchor;
    if (i <= daysPassed) {
      // Interpolate start to current available balance with slight variance
      const ratio = daysPassed > 1 ? (i - 1) / (daysPassed - 1) : 1;
      bal = anchor + (currentBal - anchor) * ratio;
    } else {
      bal = currentBal; // forecast remaining
    }
    points.push({ day: i, balance: bal });
  }

  const maxVal = Math.max(...points.map(p => p.balance), forecast.target_amb * 1.3, 15000);
  const minVal = Math.min(0, Math.min(...points.map(p => p.balance)));

  // Grid lines
  ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
  ctx.lineWidth = 1;
  for (let y = paddingY; y <= h - paddingY; y += 40) {
    ctx.beginPath();
    ctx.moveTo(paddingX, y);
    ctx.lineTo(w - paddingX, y);
    ctx.stroke();
  }

  // Draw Target AMB Line (Dashed Amber)
  const targetY = h - paddingY - ((forecast.target_amb - minVal) / (maxVal - minVal)) * (h - 2 * paddingY);
  ctx.beginPath();
  ctx.setLineDash([5, 5]);
  ctx.strokeStyle = "#f59e0b";
  ctx.lineWidth = 1.5;
  ctx.moveTo(paddingX, targetY);
  ctx.lineTo(w - paddingX, targetY);
  ctx.stroke();
  ctx.setLineDash([]);

  // Label Target AMB
  ctx.fillStyle = "#f59e0b";
  ctx.font = "11px Inter, sans-serif";
  ctx.fillText(`Target AMB (₹${forecast.target_amb.toLocaleString()})`, w - 160, targetY - 6);

  // Draw Balance Line & Gradient Fill
  const getX = (day) => paddingX + ((day - 1) / (totalDays - 1)) * (w - 2 * paddingX);
  const getY = (bal) => h - paddingY - ((bal - minVal) / (maxVal - minVal)) * (h - 2 * paddingY);

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(147, 51, 234, 0.4)");
  grad.addColorStop(1, "rgba(147, 51, 234, 0.0)");

  ctx.beginPath();
  ctx.moveTo(getX(1), getY(points[0].balance));
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(getX(points[i].day), getY(points[i].balance));
  }

  // Close path for fill
  ctx.lineTo(getX(totalDays), h - paddingY);
  ctx.lineTo(getX(1), h - paddingY);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Stroke Line
  ctx.beginPath();
  ctx.moveTo(getX(1), getY(points[0].balance));
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(getX(points[i].day), getY(points[i].balance));
  }
  ctx.strokeStyle = "#00f2fe";
  ctx.lineWidth = 2.5;
  ctx.stroke();

  // Draw Data Dots for passed days
  for (let i = 0; i < daysPassed; i++) {
    const px = getX(points[i].day);
    const py = getY(points[i].balance);

    ctx.beginPath();
    ctx.arc(px, py, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#7f00ff";
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

// Run AMB Forecast Simulator
async function runSimulatorForecast() {
  const targetAmb = parseFloat(document.getElementById("sim-target-amb").value || 10000);
  const anchorBalance = parseFloat(document.getElementById("sim-anchor-balance").value || 10000);
  const simDeposit = parseFloat(document.getElementById("sim-deposit").value || 0);
  const simExpense = parseFloat(document.getElementById("sim-expense").value || 0);

  try {
    const res = await fetch(`${API_BASE}/api/amb/forecast`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_amb: targetAmb,
        anchor_balance: anchorBalance,
        simulated_deposit: simDeposit,
        simulated_expense: simExpense
      })
    });
    const fc = await res.json();
    renderSimulatorResults(fc);
  } catch (err) {
    console.error("Failed simulator calculation:", err);
  }
}

// Render Simulator Output
function renderSimulatorResults(fc) {
  document.getElementById("sim-month-label").textContent = `Month: ${fc.month_year}`;
  const badge = document.getElementById("sim-status-badge");
  if (fc.status === "WARNING" || fc.shortfall > 0) {
    badge.textContent = "WARNING";
    badge.className = "status-badge-lg warning";
  } else {
    badge.textContent = "SAFE";
    badge.className = "status-badge-lg";
  }

  document.getElementById("sim-res-amb").textContent = `₹${fc.current_amb.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  document.getElementById("sim-res-days").textContent = `${fc.days_passed} / ${fc.days_remaining}`;
  document.getElementById("sim-res-target-sum").textContent = `₹${fc.total_target_sum.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  document.getElementById("sim-res-req-daily").textContent = `₹${fc.required_daily_balance.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

  const shortfallEl = document.getElementById("sim-res-shortfall");
  shortfallEl.textContent = `₹${fc.shortfall.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  shortfallEl.style.color = fc.shortfall > 0 ? "#f43f5e" : "#10b981";

  document.getElementById("sim-message-box").textContent = fc.message;
}

// Save Monthly Anchor
async function saveMonthlyAnchor() {
  const anchorBalance = parseFloat(document.getElementById("sim-anchor-balance").value || 10000);
  try {
    const res = await fetch(`${API_BASE}/api/amb/anchor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ anchor_balance: anchorBalance })
    });
    const data = await res.json();
    if (data.success) {
      alert(`✅ Monthly anchor balance set to ₹${anchorBalance.toLocaleString()} in Supabase!`);
      loadDashboardData();
    } else {
      alert("⚠️ Could not update monthly anchor. Check Supabase credentials in .env.");
    }
  } catch (err) {
    alert("Error updating monthly anchor: " + err.message);
  }
}

// Trigger ntfy push notification
async function triggerNtfyAlert() {
  const targetAmb = parseFloat(document.getElementById("sim-target-amb").value || 10000);
  try {
    const res = await fetch(`${API_BASE}/api/notify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_amb: targetAmb })
    });
    const data = await res.json();
    alert(`📢 Alert notification sent to ntfy.sh!\n\nStatus: ${data.status}\nMessage: ${data.message}`);
  } catch (err) {
    alert("Error pushing ntfy notification: " + err.message);
  }
}

// Fetch & Render Transactions Table
async function loadTransactionsData() {
  try {
    const res = await fetch(`${API_BASE}/api/transactions`);
    const data = await res.json();
    state.transactions = data.transactions || [];
    state.filteredTransactions = state.transactions;
    state.visibleTxnLimit = 10;
    renderTransactionsTable(state.filteredTransactions);
  } catch (err) {
    console.error("Failed fetching transactions:", err);
  }
}

// Filter Transactions
function filterTransactions() {
  const search = document.getElementById("txn-search").value.toLowerCase();
  const cat = document.getElementById("filter-category").value;
  const type = document.getElementById("filter-type").value;

  state.filteredTransactions = state.transactions.filter(t => {
    const searchable = `${t.party || ''} ${t.raw_text || ''} ${t.vpa || ''} ${t.category || ''}`.toLowerCase();
    if (search && !searchable.includes(search)) return false;
    if (cat !== "all" && (t.category || "").toLowerCase() !== cat.toLowerCase()) return false;
    if (type !== "all" && (t.type || "").toLowerCase() !== type.toLowerCase()) return false;
    return true;
  });

  state.visibleTxnLimit = 10; // Reset pagination to initial 10 on filter change
  renderTransactionsTable(state.filteredTransactions);
}

// Render Transactions Table (Paginated)
function renderTransactionsTable(list) {
  const tbody = document.getElementById("txn-table-body");
  tbody.innerHTML = "";

  if (!list || list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #94a3b8; padding: 24px;">No matching transactions found.</td></tr>`;
    updatePaginationControls(0, 0);
    return;
  }

  const visibleList = list.slice(0, state.visibleTxnLimit);

  visibleList.forEach(t => {
    const tr = document.createElement("tr");
    const isDebit = (t.type || "").toLowerCase() === "debit";
    const badgeClass = isDebit ? "badge-debit" : "badge-credit";

    tr.innerHTML = `
      <td>
        <div><strong>${t.date || 'N/A'}</strong></div>
        <div class="sub-text" style="font-size: 11.5px;">${t.time || ''}</div>
      </td>
      <td>
        <div><strong>${t.party || 'Unknown Party'}</strong></div>
        <div class="sub-text" style="font-size: 11.5px;">${t.vpa ? `VPA: ${t.vpa}` : (t.bank || '')}</div>
      </td>
      <td>
        <span class="sub-chip">${t.category || 'Uncategorized'}</span>
      </td>
      <td style="font-family: var(--font-heading); font-weight: 700; color: ${isDebit ? '#f43f5e' : '#10b981'};">
        ${isDebit ? '-' : '+'}₹${(t.amount || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
      </td>
      <td>
        <span class="${badgeClass}">${(t.type || 'N/A').toUpperCase()}</span>
      </td>
      <td>
        <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 12px;" onclick="openTxnModal('${t.id}')">
          View Raw Email
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  updatePaginationControls(visibleList.length, list.length);
}

// Update Pagination Load More Controls
function updatePaginationControls(shownCount, totalCount) {
  const info = document.getElementById("txn-pagination-info");
  const btn = document.getElementById("btn-load-more");

  if (info) {
    info.textContent = `Showing ${shownCount} of ${totalCount} transactions`;
  }

  if (btn) {
    if (shownCount >= totalCount) {
      btn.style.display = "none";
    } else {
      btn.style.display = "inline-flex";
      const remaining = totalCount - shownCount;
      const nextBatch = Math.min(10, remaining);
      btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg> Load ${nextBatch} More Transactions (${remaining} remaining)`;
    }
  }
}

// Open Raw Transaction Modal
window.openTxnModal = function(id) {
  const txn = state.transactions.find(t => t.id === id);
  if (!txn) return;

  state.selectedTxn = txn;
  document.getElementById("modal-id").textContent = txn.id;
  document.getElementById("modal-date").textContent = `${txn.date} (${txn.day_of_week || ''} ${txn.time || ''})`;
  document.getElementById("modal-amount").textContent = `₹${(txn.amount || 0).toLocaleString()} (${txn.type || ''})`;
  document.getElementById("modal-party").textContent = txn.party || "Unknown";
  document.getElementById("modal-vpa").textContent = txn.vpa || "None";
  document.getElementById("modal-source").textContent = txn.source || txn.bank || "Gmail Alert";
  document.getElementById("modal-raw").textContent = txn.raw_text || "No raw text available.";

  document.getElementById("txn-modal").classList.remove("hidden");
};

function closeModal() {
  document.getElementById("txn-modal").classList.add("hidden");
}

// Test Vendor Tagging Studio
async function testVendorTagging() {
  const party = document.getElementById("tag-party-input").value;
  const rawText = document.getElementById("tag-raw-input").value;

  if (!party && !rawText) {
    alert("Please enter a merchant name or raw email text to test categorization.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/categorize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ party, raw_text: rawText })
    });
    const data = await res.json();

    document.getElementById("res-cat-chip").textContent = data.category;
    document.getElementById("res-subcat-chip").textContent = data.sub_category;
    document.getElementById("tag-result-box").classList.remove("hidden");
  } catch (err) {
    alert("Error running categorization test: " + err.message);
  }
}

// Sync Local to Cloud Supabase
async function syncCloudDatabase() {
  const btn = document.getElementById("btn-sync-cloud");
  btn.textContent = "Uploading to Supabase...";
  btn.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/api/sync`, { method: "POST" });
    const data = await res.json();
    alert(`☁️ Sync complete! Uploaded / tagged ${data.upserted_count} transactions to Supabase Cloud PostgreSQL.`);
    loadDashboardData();
  } catch (err) {
    alert("Sync failed: " + err.message);
  } finally {
    btn.textContent = "Sync Local transactions.json to Supabase Cloud";
    btn.disabled = false;
  }
}
