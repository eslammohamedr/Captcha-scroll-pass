/**
 * OPPO Sales Activation Monitor - Frontend Application Logic
 * Mobile-First Single Page Application (PWA)
 */

let state = {
  currentTab: 'Dashboard',
  stats: {},
  groups: [],
  selectedGroup: 'all',
  statusFilter: 'all',
  newlyActivatedFilter: false,
  searchQuery: '',
  page: 1,
  limit: 25,
  devices: [],
  totalDevices: 0,
  selectedSerials: new Set(),
  workerStatus: null,
  pollTimer: null,
  searchTimeout: null,
  cameraStream: null,
  isScanning: false,
  scannedQueue: []
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  // Register Service Worker for PWA
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }

  loadDashboardStats();
  loadGroups();
  loadDevices();
  loadSettings();
  loadReportsHistory();
  startStatusPolling();
});

// --- Tab Switching ---
function switchTab(tabName) {
  state.currentTab = tabName;
  const tabs = ['Dashboard', 'Devices', 'Scanner', 'Reports', 'Settings'];

  tabs.forEach(t => {
    const view = document.getElementById(`view${t}`);
    const btn = document.getElementById(`tabBtn_${t}`);
    if (t === tabName) {
      view.classList.remove('hidden');
      btn.classList.add('text-brand-500');
      btn.classList.remove('text-dark-muted');
    } else {
      view.classList.add('hidden');
      btn.classList.remove('text-brand-500');
      btn.classList.add('text-dark-muted');
    }
  });

  if (tabName === 'Devices') {
    loadDevices();
  } else if (tabName === 'Reports') {
    loadReportsHistory();
  } else if (tabName !== 'Scanner' && state.cameraStream) {
    stopCamera();
  }
}

// --- Status Polling & Live Floating Banner ---
function startStatusPolling() {
  const poll = async () => {
    try {
      const res = await fetch('/api/run/status');
      if (res.ok) {
        const data = await res.json();
        state.workerStatus = data;
        renderLiveJobBanner(data);
        if (data.next_run) {
          renderNextRun(data.next_run);
        }
      }
    } catch (e) {}

    const interval = (state.workerStatus && state.workerStatus.is_running) ? 1500 : 4000;
    state.pollTimer = setTimeout(poll, interval);
  };
  poll();
  startNextRunCountdownTicker();
}

function renderLiveJobBanner(status) {
  const banner = document.getElementById('liveJobBanner');
  const dot = document.getElementById('headerStatusDot');
  const dotText = document.getElementById('headerStatusText');

  if (!status || !status.is_running) {
    banner.classList.add('hidden');
    dot.className = 'w-2 h-2 rounded-full bg-emerald-500';
    dotText.innerText = 'Ready';
    return;
  }

  // Active Job Running
  banner.classList.remove('hidden');
  dot.className = 'w-2 h-2 rounded-full bg-brand-500 animate-ping';
  dotText.innerText = `Checking (${status.pct}%)`;

  document.getElementById('liveJobTitle').innerText = `Running [${status.run_type.toUpperCase()}] ${status.target_group ? '· ' + status.target_group : ''}`;
  document.getElementById('liveJobProgress').style.width = `${status.pct}%`;
  document.getElementById('liveJobCounts').innerText = `Checking [${status.completed}/${status.total}] (${status.pct}%) · ${status.newly_activated} newly activated`;

  const h = Math.floor(status.elapsed_seconds / 3600);
  const m = Math.floor((status.elapsed_seconds % 3600) / 60);
  const s = status.elapsed_seconds % 60;
  const timeStr = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  document.getElementById('liveJobTimer').innerText = timeStr;

  document.getElementById('liveJobCurrentSerial').innerText = status.current_serial ? `Current Serial: ${status.current_serial}` : 'Initializing workers...';

  // Render recent logs into dashboard activity feed
  if (status.logs && status.logs.length > 0) {
    renderActivityLogs(status.logs);
  }
}

function renderActivityLogs(logs) {
  const container = document.getElementById('activityLogsList');
  if (!container || !logs || logs.length === 0) return;

  container.innerHTML = logs.map(l => {
    let color = 'text-dark-text';
    if (l.level === 'success') color = 'text-emerald-400 font-bold';
    else if (l.level === 'warning') color = 'text-amber-400';
    else if (l.level === 'error') color = 'text-red-400';

    return `<div class="flex items-start space-x-2 py-0.5 border-b border-dark-border/40">
      <span class="text-dark-muted font-mono select-none">${l.time}</span>
      <span class="${color} flex-1 break-all">${escapeHtml(l.message)}</span>
    </div>`;
  }).reverse().join('');
}

// --- Scheduler Next Run Live Display ---
let countdownTickerStarted = false;

function renderNextRun(nextRun) {
  if (!nextRun) return;
  state.nextRun = nextRun;

  const dashClock = document.getElementById('dashNextRunClock');
  const dashCount = document.getElementById('dashNextRunCountdown');
  const setDisplay = document.getElementById('setNextRunDisplay');

  if (!nextRun.enabled) {
    if (dashClock) dashClock.innerText = 'Paused';
    if (dashCount) dashCount.innerText = 'Automated scheduler is turned off';
    if (setDisplay) setDisplay.innerText = 'Scheduler Paused (Disabled)';
    return;
  }

  const remText = nextRun.remaining_str || 'due now';
  if (dashClock) dashClock.innerText = `at ${nextRun.next_run_clock} (${remText})`;
  if (dashCount) dashCount.innerText = `Auto-checking unactivated serials every ${nextRun.interval_minutes}m`;
  if (setDisplay) setDisplay.innerText = `at ${nextRun.next_run_clock} (${remText})`;
}

function startNextRunCountdownTicker() {
  if (countdownTickerStarted) return;
  countdownTickerStarted = true;

  setInterval(() => {
    if (!state.nextRun || !state.nextRun.enabled || state.nextRun.remaining_seconds === undefined) return;

    if (state.nextRun.remaining_seconds > 0) {
      state.nextRun.remaining_seconds--;
      const rem = state.nextRun.remaining_seconds;
      const m = Math.floor(rem / 60);
      const s = rem % 60;
      const remStr = m > 0 ? `in ${m}m ${String(s).padStart(2, '0')}s` : `in ${s}s`;
      state.nextRun.remaining_str = remStr;

      const dashClock = document.getElementById('dashNextRunClock');
      const setDisplay = document.getElementById('setNextRunDisplay');
      if (dashClock) dashClock.innerText = `at ${state.nextRun.next_run_clock} (${remStr})`;
      if (setDisplay) setDisplay.innerText = `at ${state.nextRun.next_run_clock} (${remStr})`;
    } else {
      state.nextRun.remaining_str = 'due now';
      const dashClock = document.getElementById('dashNextRunClock');
      const setDisplay = document.getElementById('setNextRunDisplay');
      if (dashClock) dashClock.innerText = `at ${state.nextRun.next_run_clock} (due now)`;
      if (setDisplay) setDisplay.innerText = `at ${state.nextRun.next_run_clock} (due now)`;
    }
  }, 1000);
}

// --- Dashboard Metrics ---
async function loadDashboardStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const data = await res.json();
    state.stats = data;

    const activeEl = document.getElementById('metricActive');
    const inactiveEl = document.getElementById('metricInactive');
    const pendingEl = document.getElementById('metricPending');
    if (activeEl) activeEl.innerText = data.active || 0;
    if (inactiveEl) inactiveEl.innerText = data.inactive || 0;
    if (pendingEl) pendingEl.innerText = data.pending || 0;

    document.getElementById('metricActivatedToday').innerText = data.activated_today || 0;
    document.getElementById('metricTotal').innerText = data.total || 0;
    document.getElementById('metricErrors').innerText = data.errors || 0;

    const btnErrCount = document.getElementById('dashBtnErrorsCount');
    if (btnErrCount) btnErrCount.innerText = data.errors || 0;

    const btnPendingCount = document.getElementById('dashBtnPendingCount');
    if (btnPendingCount) btnPendingCount.innerText = data.pending || 0;

    if (data.worker && data.worker.logs) {
      renderActivityLogs(data.worker.logs);
    }
    if (data.next_run) {
      renderNextRun(data.next_run);
    }
    loadDatabaseInfo();
  } catch (e) {}
}

// --- Database Storage Info ---
async function loadDatabaseInfo() {
  try {
    const res = await fetch('/api/database/info');
    if (!res.ok) return;
    const db = await res.json();

    // Dashboard Banner
    const dashEngine = document.getElementById('dashDbEngine');
    const dashSize = document.getElementById('dashDbSizeBadge');
    const dashDetails = document.getElementById('dashDbDetails');
    if (dashEngine) dashEngine.innerText = db.engine;
    if (dashSize) dashSize.innerText = db.file_size;
    if (dashDetails) dashDetails.innerText = `${db.total_devices} devices stored (${db.active_devices} Active, ${db.inactive_devices} Inactive, ${db.pending_devices} Pending)`;

    // Settings Card
    const setSize = document.getElementById('setDbSizeBadge');
    const setEngine = document.getElementById('setDbEngine');
    const setTotal = document.getElementById('setDbTotal');
    const setActive = document.getElementById('setDbActive');
    const setInactive = document.getElementById('setDbInactive');
    if (setSize) setSize.innerText = db.file_size;
    if (setEngine) setEngine.innerText = db.engine;
    if (setTotal) setTotal.innerText = `${db.total_devices} devices`;
    if (setActive) setActive.innerText = db.active_devices;
    if (setInactive) setInactive.innerText = db.inactive_devices;
  } catch (e) {}
}

async function reloadDefaultSheet() {
  if (!confirm('Reload the default 696-serial sheet into the database?')) return;
  showToast('Reloading 696 serials from default sheet...', 'info');
  try {
    const res = await fetch('/api/database/import-default', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      loadDashboardStats();
      loadDatabaseInfo();
      loadDevices();
      loadGroups();
    } else {
      showToast(data.detail || 'Failed to reload sheet', 'error');
    }
  } catch (e) {
    showToast('Failed to reload sheet', 'error');
  }
}

// --- Groups Management ---
async function loadGroups() {
  try {
    const res = await fetch('/api/groups');
    if (!res.ok) return;
    const groups = await res.json();
    state.groups = groups;

    // Render horizontal tabs in Devices view
    const container = document.getElementById('groupsTabsContainer');
    let html = `<button onclick="setFilterGroup('all')" class="px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${state.selectedGroup === 'all' ? 'bg-brand-600 text-white' : 'bg-dark-card text-dark-muted hover:text-white'}">All Groups</button>`;

    groups.forEach(g => {
      const active = state.selectedGroup === g.name;
      html += `<button onclick="setFilterGroup('${escapeHtml(g.name)}')" class="px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${active ? 'bg-brand-600 text-white' : 'bg-dark-card text-dark-muted hover:text-white'}">
        ${escapeHtml(g.name)} <span class="text-[10px] opacity-75">(${g.total_devices})</span>
      </button>`;
    });

    html += `<button onclick="openNewGroupPrompt()" class="px-2.5 py-1.5 rounded-lg text-xs font-bold bg-dark-surface border border-dark-border text-brand-500 hover:border-brand-500 whitespace-nowrap">+ Group</button>`;
    container.innerHTML = html;

    // Populate Group dropdown in Add modal and Scanner
    const modalSelect = document.getElementById('modalAddGroupSelect');
    const scannerSelect = document.getElementById('scannerTargetGroup');
    const options = groups.map(g => `<option value="${escapeHtml(g.name)}">${escapeHtml(g.name)}</option>`).join('');
    if (modalSelect) modalSelect.innerHTML = options;
    if (scannerSelect) scannerSelect.innerHTML = options;

    updateCurrentGroupRunButton();
  } catch (e) {}
}

function setFilterGroup(groupName) {
  state.selectedGroup = groupName;
  state.page = 1;
  loadGroups();
  loadDevices();
}

function updateCurrentGroupRunButton() {
  const btn = document.getElementById('btnRunCurrentGroup');
  if (state.selectedGroup === 'all') {
    btn.innerHTML = `<span>⚡ Run All Devices</span>`;
  } else {
    btn.innerHTML = `<span>⚡ Run Group '${escapeHtml(state.selectedGroup)}'</span>`;
  }
}

async function openNewGroupPrompt() {
  const name = prompt("Enter new group name (e.g. 'Store #2' or 'Batch 2026-10'):");
  if (!name || !name.trim()) return;

  try {
    const res = await fetch('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() })
    });
    if (res.ok) {
      showToast(`Group '${name.trim()}' created!`, 'success');
      state.selectedGroup = name.trim();
      loadGroups();
    } else {
      const err = await res.json();
      showToast(err.detail || 'Could not create group', 'error');
    }
  } catch (e) {
    showToast('Failed to create group', 'error');
  }
}

// --- Devices Listing & Filtering ---
function setFilterStatus(status) {
  state.statusFilter = status;
  state.newlyActivatedFilter = false;
  state.page = 1;
  updateFilterButtons();
  loadDevices();
}

function setFilterNewlyActivated() {
  state.newlyActivatedFilter = !state.newlyActivatedFilter;
  state.statusFilter = 'all';
  state.page = 1;
  updateFilterButtons();
  loadDevices();
}

function updateFilterButtons() {
  ['all', 'Active', 'Inactive', 'Pending', 'Not Run', 'Error'].forEach(s => {
    const btnId = s === 'Not Run' ? 'filterBtn_NotRun' : `filterBtn_${s}`;
    const btn = document.getElementById(btnId);
    if (btn) {
      if (state.statusFilter === s && !state.newlyActivatedFilter) {
        if (s === 'Pending') {
          btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-purple-600 text-white text-[11px]';
        } else if (s === 'Not Run') {
          btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-blue-600 text-white text-[11px]';
        } else {
          btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-brand-600 text-white text-[11px]';
        }
      } else {
        if (s === 'Pending') {
          btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-dark-card text-purple-400 hover:text-purple-300 text-[11px]';
        } else if (s === 'Not Run') {
          btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-dark-card text-blue-400 hover:text-blue-300 text-[11px]';
        } else {
          btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-dark-card text-dark-muted hover:text-white text-[11px]';
        }
      }
    }
  });

  const newlyBtn = document.getElementById('filterBtn_Newly');
  if (newlyBtn) {
    newlyBtn.className = state.newlyActivatedFilter
      ? 'px-2.5 py-1 rounded-md font-bold bg-amber-500 text-black text-[11px]'
      : 'px-2.5 py-1 rounded-md font-semibold bg-dark-card text-amber-400 hover:text-amber-300 text-[11px]';
  }

  const btnRetryErrorsView = document.getElementById('btnRetryErrorsView');
  if (btnRetryErrorsView) {
    if (state.statusFilter === 'Error') {
      btnRetryErrorsView.classList.remove('hidden');
    } else {
      btnRetryErrorsView.classList.add('hidden');
    }
  }
}

function debounceDeviceSearch() {
  clearTimeout(state.searchTimeout);
  state.searchTimeout = setTimeout(() => {
    state.searchQuery = document.getElementById('deviceSearchInput').value.trim();
    state.page = 1;
    loadDevices();
  }, 300);
}

async function loadDevices() {
  const container = document.getElementById('devicesCardsList');
  container.innerHTML = '<div class="py-8 text-center text-xs text-dark-muted">Loading devices...</div>';

  const params = new URLSearchParams({
    search: state.searchQuery,
    group: state.selectedGroup,
    status: state.statusFilter,
    newly_activated: state.newlyActivatedFilter ? 'true' : 'false',
    page: state.page,
    limit: state.limit
  });

  try {
    const res = await fetch(`/api/devices?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    state.devices = data.devices;
    state.totalDevices = data.total;

    document.getElementById('devicesListCountText').innerText = `Showing ${data.devices.length} of ${data.total} devices`;
    document.getElementById('pageInfoSpan').innerText = `Page ${data.page} of ${data.pages}`;
    document.getElementById('btnPrevPage').disabled = data.page <= 1;
    document.getElementById('btnNextPage').disabled = data.page >= data.pages;

    if (data.devices.length === 0) {
      container.innerHTML = '<div class="py-8 text-center text-xs text-dark-muted">No devices match your filter. Click "+ Add Serials" above.</div>';
      return;
    }

    renderDevicesCards(data.devices);
  } catch (e) {
    container.innerHTML = '<div class="py-8 text-center text-xs text-red-400">Failed to load devices.</div>';
  }
}

function renderDevicesCards(devices) {
  const container = document.getElementById('devicesCardsList');
  container.innerHTML = devices.map(d => {
    const isSelected = state.selectedSerials.has(d.serial);
    const activeBadge = getActiveBadgeHtml(d.is_active, d.status);
    const modelStr = d.model_name ? `${escapeHtml(d.model_name)} ${escapeHtml(d.specs || '')} ${escapeHtml(d.color || '')}` : 'OPPO Device (Pending Details)';

    return `
      <div class="bg-dark-surface border border-dark-border rounded-xl p-3 flex items-start space-x-3 transition hover:border-dark-muted">
        <!-- Checkbox -->
        <input type="checkbox" onchange="toggleSelectSerial('${d.serial}')" ${isSelected ? 'checked' : ''} class="mt-1 rounded bg-dark-main border-dark-border text-brand-600 focus:ring-0">

        <!-- Device Info -->
        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between mb-1">
            <span class="font-mono text-xs font-bold text-white tracking-wide flex items-center space-x-1.5 cursor-pointer" onclick="copyToClipboard('${d.serial}')">
              <span>${d.serial}</span>
              <svg class="w-3.5 h-3.5 text-dark-muted hover:text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
            </span>
            ${activeBadge}
          </div>

          <div class="text-xs text-dark-text truncate font-medium mb-1.5">${modelStr}</div>

          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-dark-muted">
            <span>🏷️ ${escapeHtml(d.group_name || 'Default')}</span>
            ${d.is_active === 'Yes' && d.activation_date ? `<span class="text-emerald-400 font-semibold">📅 Act: ${escapeHtml(d.activation_date)}</span>` : ''}
            ${d.is_active === 'Pending' ? `<span class="text-purple-400 font-medium">🔄 Pending System Sync</span>` : ''}
            ${d.is_active === 'No' ? `<span class="text-amber-400/90 font-medium">⏳ Not Activated</span>` : ''}
            ${d.is_active === 'Not Run' ? `<span class="text-blue-400 font-medium">🆕 Not Run</span>` : ''}
            ${d.last_checked_at ? `<span>🕒 ${escapeHtml(d.last_checked_at)}</span>` : ''}
          </div>
        </div>

        <!-- Quick Check Button -->
        <button onclick="checkSingleSerial('${d.serial}')" class="bg-dark-card hover:bg-dark-border text-dark-text text-[11px] font-semibold px-2.5 py-1.5 rounded-lg active:scale-95 transition">
          Check
        </button>
      </div>
    `;
  }).join('');
}

function getActiveBadgeHtml(isActive, status) {
  if (isActive === 'Yes') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">✔ Active</span>`;
  } else if (isActive === 'No') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30">⏳ Inactive</span>`;
  } else if (status === 'Error' || status === 'Captcha Failed' || status === 'No Response' || isActive === 'Error') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-red-500/20 text-red-400 border border-red-500/30">⚠️ ${status || 'Error'}</span>`;
  } else if (isActive === 'Pending' || status === 'Pending') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-500/20 text-purple-400 border border-purple-500/30">🔄 Pending Sync</span>`;
  } else if (status === 'Not Run' || isActive === 'Not Run') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-500/20 text-blue-400 border border-blue-500/30">🆕 Not Run</span>`;
  }
  return `<span class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-dark-card text-dark-muted">Unchecked</span>`;
}

function toggleSelectSerial(serial) {
  if (state.selectedSerials.has(serial)) {
    state.selectedSerials.delete(serial);
  } else {
    state.selectedSerials.add(serial);
  }
  updateSelectionBar();
}

function updateSelectionBar() {
  const bar = document.getElementById('selectionActionBar');
  const countSpan = document.getElementById('selectedCountSpan');
  if (state.selectedSerials.size > 0) {
    bar.classList.remove('hidden');
    countSpan.innerText = state.selectedSerials.size;
  } else {
    bar.classList.add('hidden');
  }
}

function prevPage() {
  if (state.page > 1) {
    state.page--;
    loadDevices();
  }
}

function nextPage() {
  state.page++;
  loadDevices();
}

// --- Execution Triggers ---
async function triggerRun(mode, extra = {}) {
  const payload = { mode, ...extra };
  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || 'Run started!', 'success');
      loadDashboardStats();
    } else {
      showToast(data.detail || 'Could not start run', 'error');
    }
  } catch (e) {
    showToast('Failed to start run', 'error');
  }
}

async function triggerRunErrorsOnly() {
  await triggerRun('errors', { errors_only: true });
}

function checkSingleSerial(serial) {
  triggerRun('selected', { serials: [serial] });
}

function runSelectedDevices() {
  if (state.selectedSerials.size === 0) return;
  const serials = Array.from(state.selectedSerials);
  triggerRun('selected', { serials });
  state.selectedSerials.clear();
  updateSelectionBar();
}

function runCurrentGroup() {
  if (state.selectedGroup === 'all') {
    triggerRun('all');
  } else {
    triggerRun('group', { group_name: state.selectedGroup });
  }
}

async function stopActiveJob() {
  if (!confirm('Stop current verification run?')) return;
  try {
    const res = await fetch('/api/run/stop', { method: 'POST' });
    const data = await res.json();
    showToast(data.message || 'Stop requested', 'info');
  } catch (e) {
    showToast('Failed to stop run', 'error');
  }
}

async function deleteSelectedDevices() {
  if (state.selectedSerials.size === 0) return;
  if (!confirm(`Delete ${state.selectedSerials.size} selected device(s)?`)) return;

  try {
    const res = await fetch('/api/devices/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serials: Array.from(state.selectedSerials) })
    });
    if (res.ok) {
      showToast(`Deleted devices!`, 'success');
      state.selectedSerials.clear();
      updateSelectionBar();
      loadDevices();
      loadDashboardStats();
      loadGroups();
    }
  } catch (e) {
    showToast('Failed to delete devices', 'error');
  }
}

// --- Add Devices Modal ---
let addMode = 'bulk';

function openAddModal() {
  document.getElementById('modalAddDevices').classList.remove('hidden');
}

function closeAddModal() {
  document.getElementById('modalAddDevices').classList.add('hidden');
}

function switchAddMode(mode) {
  addMode = mode;
  const tabBulk = document.getElementById('modalTabBulk');
  const tabFile = document.getElementById('modalTabFile');
  const secBulk = document.getElementById('addModeBulkSection');
  const secFile = document.getElementById('addModeFileSection');

  if (mode === 'bulk') {
    tabBulk.className = 'py-2 rounded-lg font-bold bg-brand-600 text-white';
    tabFile.className = 'py-2 rounded-lg font-semibold bg-dark-card text-dark-muted';
    secBulk.classList.remove('hidden');
    secFile.classList.add('hidden');
  } else {
    tabFile.className = 'py-2 rounded-lg font-bold bg-brand-600 text-white';
    tabBulk.className = 'py-2 rounded-lg font-semibold bg-dark-card text-dark-muted';
    secFile.classList.remove('hidden');
    secBulk.classList.add('hidden');
  }
}

async function submitAddDevices() {
  const group = document.getElementById('modalAddGroupSelect').value;
  const btn = document.getElementById('btnSubmitAddDevices');

  btn.disabled = true;
  btn.innerText = 'Saving...';

  try {
    if (addMode === 'bulk') {
      const text = document.getElementById('modalBulkSerialsText').value.trim();
      if (!text) {
        showToast('Please paste at least one serial number', 'error');
        btn.disabled = false;
        btn.innerText = 'Save Devices';
        return;
      }
      const res = await fetch('/api/devices/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ serials_text: text, group_name: group })
      });
      const data = await res.json();
      if (res.ok) {
        showToast(data.message, 'success');
        document.getElementById('modalBulkSerialsText').value = '';
        closeAddModal();
        loadDevices();
        loadDashboardStats();
        loadGroups();
      } else {
        showToast(data.detail || 'Failed to add devices', 'error');
      }
    } else {
      // File upload mode
      const fileInput = document.getElementById('modalFileInput');
      if (!fileInput.files || fileInput.files.length === 0) {
        showToast('Please select a file to upload', 'error');
        btn.disabled = false;
        btn.innerText = 'Save Devices';
        return;
      }
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);

      const res = await fetch(`/api/devices/upload?group_name=${encodeURIComponent(group)}`, {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (res.ok) {
        showToast(data.message, 'success');
        fileInput.value = '';
        closeAddModal();
        loadDevices();
        loadDashboardStats();
        loadGroups();
      } else {
        showToast(data.detail || 'Failed to upload file', 'error');
      }
    }
  } catch (e) {
    showToast('Failed to save devices', 'error');
  } finally {
    btn.disabled = false;
    btn.innerText = 'Save Devices';
  }
}

// --- Camera Barcode Scanner ---
async function toggleCamera() {
  const btn = document.getElementById('btnToggleCamera');
  const video = document.getElementById('scannerVideo');

  if (state.isScanning) {
    stopCamera();
    btn.innerText = 'Start Camera';
    btn.className = 'bg-brand-600 hover:bg-brand-500 text-white text-xs font-bold py-2.5 px-4 rounded-xl shadow active:scale-95';
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false
    });
    state.cameraStream = stream;
    video.srcObject = stream;
    state.isScanning = true;
    btn.innerText = 'Stop Camera';
    btn.className = 'bg-red-600 hover:bg-red-500 text-white text-xs font-bold py-2.5 px-4 rounded-xl shadow active:scale-95';

    startBarcodeDetectionLoop(video);
  } catch (err) {
    showToast('Could not access camera: ' + err.message, 'error');
  }
}

function stopCamera() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach(t => t.stop());
    state.cameraStream = null;
  }
  state.isScanning = false;
}

async function startBarcodeDetectionLoop(video) {
  // Use native BarcodeDetector if available (supported on Chrome Android & Safari iOS 17+)
  if ('BarcodeDetector' in window) {
    const detector = new BarcodeDetector({ formats: ['code_128', 'code_39', 'ean_13', 'qr_code', 'data_matrix'] });
    const detect = async () => {
      if (!state.isScanning) return;
      try {
        const barcodes = await detector.detect(video);
        if (barcodes.length > 0) {
          handleScannedBarcode(barcodes[0].rawValue);
        }
      } catch (e) {}
      if (state.isScanning) {
        requestAnimationFrame(detect);
      }
    };
    detect();
  } else {
    showToast("BarcodeDetector API not supported in this browser. Try Chrome on Android or Safari on iOS.", "warning");
  }
}

async function handleScannedBarcode(serial) {
  serial = serial.trim().toUpperCase();
  if (serial.length < 5) return;

  // Don't duplicate in queue
  if (state.scannedQueue.includes(serial)) return;

  state.scannedQueue.unshift(serial);
  showToast(`Scanned: ${serial}!`, 'success');

  // Auto-save to target group
  const group = document.getElementById('scannerTargetGroup').value || 'Default';
  try {
    await fetch('/api/devices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serial, group_name: group })
    });
    loadDashboardStats();
    loadGroups();
  } catch (e) {}

  renderScannedQueue();
}

function renderScannedQueue() {
  const container = document.getElementById('scannedQueueList');
  if (state.scannedQueue.length === 0) {
    container.innerHTML = '<div class="text-dark-muted py-2 text-center text-xs">No serials scanned yet.</div>';
    return;
  }

  container.innerHTML = state.scannedQueue.map(sn => `
    <div class="bg-dark-main border border-dark-border rounded-lg p-2 flex items-center justify-between">
      <span class="font-mono font-bold text-white">${sn}</span>
      <button onclick="checkSingleSerial('${sn}')" class="bg-brand-600 text-white font-semibold px-2.5 py-1 rounded text-[11px]">Check</button>
    </div>
  `).join('');
}

function clearScannedQueue() {
  state.scannedQueue = [];
  renderScannedQueue();
}

// --- Reports & Two Sheets History ---
async function loadReportsHistory() {
  const container = document.getElementById('reportsHistoryList');
  try {
    const res = await fetch('/api/reports/history?limit=15');
    if (!res.ok) return;
    const runs = await res.json();

    if (runs.length === 0) {
      container.innerHTML = '<div class="text-dark-muted py-4 text-center text-xs">No runs recorded yet. Start a check to generate reports!</div>';
      return;
    }

    container.innerHTML = runs.map(r => {
      const fullSheetName = r.full_sheet_name || '';
      const newSheetName = r.newly_activated_sheet_name || '';

      return `
        <div class="bg-dark-main border border-dark-border rounded-xl p-3.5 space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-bold text-xs text-white uppercase tracking-wider">Run #${r.id} · ${escapeHtml(r.run_type)}</span>
            <span class="text-[11px] text-dark-muted font-mono">${escapeHtml(r.finished_at || r.started_at)}</span>
          </div>

          <div class="flex flex-wrap items-center gap-3 text-xs text-dark-muted">
            <span>Checked: <b class="text-white">${r.total_checked}</b></span>
            <span>Newly Activated: <b class="text-emerald-400">${r.newly_activated_count}</b> 🔥</span>
            <span>Status: <b class="${r.status === 'completed' ? 'text-emerald-400' : 'text-amber-400'}">${escapeHtml(r.status)}</b></span>
          </div>

          <!-- Download Action Buttons for the TWO sheets -->
          <div class="flex flex-wrap gap-2 pt-1 border-t border-dark-border/50">
            ${fullSheetName ? `
              <a href="/api/reports/download/${encodeURIComponent(fullSheetName)}" download class="flex-1 min-w-[130px] bg-dark-card hover:bg-dark-border text-white text-xs font-semibold py-2 px-3 rounded-lg flex items-center justify-center space-x-1 border border-dark-border transition active:scale-95">
                <span>📄 Full Sheet (.xlsx)</span>
              </a>
            ` : ''}

            ${newSheetName ? `
              <a href="/api/reports/download/${encodeURIComponent(newSheetName)}" download class="flex-1 min-w-[130px] bg-emerald-950/40 hover:bg-emerald-900/40 text-emerald-400 text-xs font-bold py-2 px-3 rounded-lg flex items-center justify-center space-x-1 border border-emerald-500/30 transition active:scale-95">
                <span>🔥 Activated Sheet (.xlsx)</span>
              </a>
            ` : ''}
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    container.innerHTML = '<div class="text-dark-muted py-4 text-center text-xs text-red-400">Failed to load run history.</div>';
  }
}

// --- Settings & Scheduler ---
async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    if (!res.ok) return;
    const settings = await res.json();

    document.getElementById('settingScheduleEnabled').checked = settings.schedule_enabled === '1';
    document.getElementById('settingScheduleInterval').value = settings.schedule_interval_minutes || '60';

    document.getElementById('settingTelegramEnabled').checked = settings.telegram_notifications_enabled === '1';
    document.getElementById('settingTelegramToken').value = settings.telegram_bot_token || '';
    document.getElementById('settingTelegramChatId').value = settings.telegram_chat_id || '';
    document.getElementById('settingTelegramSendSheets').checked = settings.telegram_send_sheets === '1';

    document.getElementById('settingThreadsCount').value = settings.threads_count || '2';
    document.getElementById('settingDelaySeconds').value = settings.delay_seconds || '2.0';
  } catch (e) {}
}

async function saveAllSettings() {
  const settings = {
    schedule_enabled: document.getElementById('settingScheduleEnabled').checked ? '1' : '0',
    schedule_interval_minutes: document.getElementById('settingScheduleInterval').value,
    telegram_notifications_enabled: document.getElementById('settingTelegramEnabled').checked ? '1' : '0',
    telegram_bot_token: document.getElementById('settingTelegramToken').value.trim(),
    telegram_chat_id: document.getElementById('settingTelegramChatId').value.trim(),
    telegram_send_sheets: document.getElementById('settingTelegramSendSheets').checked ? '1' : '0',
    threads_count: document.getElementById('settingThreadsCount').value,
    delay_seconds: document.getElementById('settingDelaySeconds').value
  };

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings })
    });
    if (res.ok) {
      showToast('Settings saved successfully!', 'success');
    } else {
      showToast('Failed to save settings', 'error');
    }
  } catch (e) {
    showToast('Failed to save settings', 'error');
  }
}

async function testTelegramConnection() {
  const token = document.getElementById('settingTelegramToken').value.trim();
  const chat_id = document.getElementById('settingTelegramChatId').value.trim();

  if (!token || !chat_id) {
    showToast('Please enter both Bot Token and Chat ID first', 'error');
    return;
  }

  showToast('Testing Telegram connection...', 'info');

  try {
    const res = await fetch('/api/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, chat_id })
    });
    const data = await res.json();
    if (data.success) {
      showToast(data.message, 'success');
    } else {
      showToast(data.message, 'error');
    }
  } catch (e) {
    showToast('Failed to test Telegram', 'error');
  }
}

// --- Utilities ---
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');

  let bg = 'bg-dark-card text-white border-dark-border';
  if (type === 'success') bg = 'bg-emerald-600 text-white border-emerald-500';
  else if (type === 'error') bg = 'bg-red-600 text-white border-red-500';
  else if (type === 'warning') bg = 'bg-amber-600 text-white border-amber-500';

  toast.className = `${bg} border text-xs font-semibold py-2.5 px-4 rounded-xl shadow-xl flex items-center space-x-2 transition-all duration-300 transform translate-y-2 opacity-0 pointer-events-auto max-w-sm`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;

  container.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  });

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast(`Copied ${text}!`, 'success');
  }).catch(() => {});
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
