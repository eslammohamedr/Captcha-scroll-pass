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
  modelFilter: 'all',
  specsFilter: 'all',
  colorFilter: 'all',
  activationDateFilter: 'all',
  filterOptions: { models: [], colors: [], specs: [], groups: [], activation_dates: [] },
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
  scannedQueue: [],
  deferredInstallPrompt: null
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  // Register Service Worker for PWA
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }

  // Setup PWA Home Screen Install prompt listener
  initPwaInstall();

  loadDashboardStats();
  loadGroups();
  loadFilterOptions();
  loadDevices();
  loadSettings();
  loadReportsHistory();
  startStatusPolling();
});

// --- Tab Switching ---
function switchTab(tabName) {
  state.currentTab = tabName;
  const tabs = ['Dashboard', 'Devices', 'Scanner', 'Reports', 'Settings', 'Terminal'];

  tabs.forEach(t => {
    const view = document.getElementById(`view${t}`);
    const btn = document.getElementById(`tabBtn_${t}`);
    if (view) {
      if (t === tabName) {
        view.classList.remove('hidden');
      } else {
        view.classList.add('hidden');
      }
    }
    if (btn) {
      if (t === tabName) {
        btn.classList.add('text-brand-500');
        btn.classList.remove('text-dark-muted');
      } else {
        btn.classList.remove('text-brand-500');
        btn.classList.add('text-dark-muted');
      }
    }
  });

  if (tabName === 'Dashboard') {
    loadDashboardStats();
  } else if (tabName === 'Devices') {
    loadDevices();
  } else if (tabName === 'Reports') {
    loadReportsHistory();
  } else if (tabName === 'Terminal') {
    loadAgyStatus();
    startAgyEventStream();
  } else if (tabName !== 'Scanner' && state.cameraStream) {
    stopCamera();
  }
}

// --- PWA Add to Home Screen Handling ---
function initPwaInstall() {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    state.deferredInstallPrompt = e;
    console.log('PWA: Native beforeinstallprompt captured!');
    const btnHeader = document.getElementById('btnInstallPwaHeader');
    const banner = document.getElementById('pwaInstallBanner');
    const directBtn = document.getElementById('btnDirectNativeInstall');
    if (btnHeader) btnHeader.classList.remove('hidden');
    if (banner && !sessionStorage.getItem('pwa_banner_dismissed')) {
      banner.classList.remove('hidden');
    }
    if (directBtn) directBtn.classList.remove('hidden');
  });

  window.addEventListener('appinstalled', () => {
    state.deferredInstallPrompt = null;
    const btnHeader = document.getElementById('btnInstallPwaHeader');
    const banner = document.getElementById('pwaInstallBanner');
    const directBtn = document.getElementById('btnDirectNativeInstall');
    if (btnHeader) btnHeader.classList.add('hidden');
    if (banner) banner.classList.add('hidden');
    if (directBtn) directBtn.classList.add('hidden');
    closePwaModal();
    showToast('OPPO Sales Monitor installed to Home Screen! 🎉', 'success');
  });

  // If already running in standalone PWA mode, hide the banner
  if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true) {
    const banner = document.getElementById('pwaInstallBanner');
    const btnHeader = document.getElementById('btnInstallPwaHeader');
    if (banner) banner.classList.add('hidden');
    if (btnHeader) btnHeader.classList.add('hidden');
  }
}

function promptInstallPwa() {
  if (state.deferredInstallPrompt) {
    try {
      state.deferredInstallPrompt.prompt();
      state.deferredInstallPrompt.userChoice.then((choiceResult) => {
        if (choiceResult && choiceResult.outcome === 'accepted') {
          showToast('Installing OPPO Monitor to Home Screen...', 'success');
          const banner = document.getElementById('pwaInstallBanner');
          if (banner) banner.classList.add('hidden');
        }
        state.deferredInstallPrompt = null;
      });
      return;
    } catch (e) {
      console.warn('Native prompt failed:', e);
    }
  }

  // Open interactive modal with direct 1-click shortcut downloads and guides
  openPwaModal();
}

function promptDirectNativeInstall() {
  if (state.deferredInstallPrompt) {
    try {
      state.deferredInstallPrompt.prompt();
      state.deferredInstallPrompt.userChoice.then((choiceResult) => {
        if (choiceResult && choiceResult.outcome === 'accepted') {
          showToast('Installing OPPO Monitor to Home Screen...', 'success');
          closePwaModal();
        }
        state.deferredInstallPrompt = null;
      });
    } catch (e) {
      showToast('Could not launch browser install prompt', 'warning');
    }
  } else {
    showToast('Native browser prompt unavailable. Use the 1-click launcher or browser menu.', 'info');
  }
}

function openPwaModal() {
  const modal = document.getElementById('modalPwaInstall');
  if (modal) {
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
    const directBtn = document.getElementById('btnDirectNativeInstall');
    if (directBtn) {
      if (state.deferredInstallPrompt) {
        directBtn.classList.remove('hidden');
      } else {
        directBtn.classList.add('hidden');
      }
    }
  }
}

function closePwaModal() {
  const modal = document.getElementById('modalPwaInstall');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
}

function dismissInstallBanner() {
  const banner = document.getElementById('pwaInstallBanner');
  if (banner) banner.classList.add('hidden');
  sessionStorage.setItem('pwa_banner_dismissed', '1');
}

// --- Status Polling & Live Floating Banner ---
let lastRunningState = false;
let statusPollCounter = 0;

function startStatusPolling() {
  const poll = async () => {
    try {
      const res = await fetch('/api/run/status');
      if (res.ok) {
        const data = await res.json();
        const wasRunning = lastRunningState;
        lastRunningState = !!data.is_running;
        state.workerStatus = data;

        renderLiveJobBanner(data);
        if (data.next_run) {
          renderNextRun(data.next_run);
        }

        statusPollCounter++;
        // If a run just completed: refresh dashboard stats, devices, and reports immediately!
        if (wasRunning && !data.is_running) {
          loadDashboardStats();
          loadDevices();
          loadReportsHistory();
          showToast(`Verification run finished! Recovered devices updated.`, 'success');
        } else if (data.is_running && statusPollCounter % 2 === 0) {
          // While running: refresh stats periodically so cards update in real-time
          loadDashboardStats();
        }
      }
    } catch (e) {}

    const interval = (state.workerStatus && state.workerStatus.is_running) ? 1500 : 3500;
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
    html += `<button onclick="openManageGroupsModal()" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-dark-surface border border-dark-border text-dark-muted hover:text-white whitespace-nowrap" title="Manage Groups">📁 Manage</button>`;
    container.innerHTML = html;

    // Populate Group dropdown in Add modal and Scanner
    const modalSelect = document.getElementById('modalAddGroupSelect');
    const scannerSelect = document.getElementById('scannerTargetGroup');
    const options = groups.map(g => `<option value="${escapeHtml(g.name)}">${escapeHtml(g.name)}</option>`).join('');
    if (modalSelect) modalSelect.innerHTML = options;
    if (scannerSelect) scannerSelect.innerHTML = options;

    updateCurrentGroupRunButton();
    renderManageGroupsList();
  } catch (e) {}
}

function setFilterGroup(groupName) {
  state.selectedGroup = groupName;
  state.page = 1;
  loadGroups();
  renderActiveFilterChips();
  loadDevices();
}

function updateCurrentGroupRunButton() {
  const btn = document.getElementById('btnRunCurrentGroup');
  const btnDelete = document.getElementById('btnDeleteCurrentGroup');
  if (state.selectedGroup === 'all') {
    if (btn) btn.innerHTML = `<span>⚡ Run All Devices</span>`;
    if (btnDelete) btnDelete.classList.add('hidden');
  } else {
    if (btn) btn.innerHTML = `<span>⚡ Run Group '${escapeHtml(state.selectedGroup)}'</span>`;
    if (btnDelete) {
      if (state.selectedGroup === 'Default') {
        btnDelete.classList.add('hidden');
      } else {
        btnDelete.classList.remove('hidden');
        btnDelete.innerHTML = `<span>🗑️ Delete '${escapeHtml(state.selectedGroup)}'</span>`;
      }
    }
  }
}

async function openNewGroupPrompt() {
  const name = prompt("Enter new group name (e.g. 'testgroup' or 'Store #2'):");
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

// Group Modal & Deletion Management
function openManageGroupsModal() {
  const modal = document.getElementById('modalManageGroups');
  if (modal) {
    modal.classList.remove('hidden');
    renderManageGroupsList();
    const input = document.getElementById('inputNewGroupName');
    if (input) input.value = '';
  }
}

function closeManageGroupsModal() {
  const modal = document.getElementById('modalManageGroups');
  if (modal) modal.classList.add('hidden');
}

function renderManageGroupsList() {
  const list = document.getElementById('manageGroupsList');
  if (!list) return;
  if (!state.groups || state.groups.length === 0) {
    list.innerHTML = `<p class="text-xs text-dark-muted py-2 text-center">No groups found.</p>`;
    return;
  }
  let html = '';
  state.groups.forEach(g => {
    const isDefault = g.name === 'Default';
    html += `
      <div class="flex items-center justify-between p-2.5 rounded-lg bg-dark-card border border-dark-border/50 text-xs">
        <div class="flex items-center space-x-2">
          <span class="font-bold text-white">${escapeHtml(g.name)}</span>
          <span class="text-[10px] bg-dark-surface px-2 py-0.5 rounded text-dark-muted font-mono">${g.total_devices} devices</span>
          ${isDefault ? '<span class="text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded font-semibold">Protected</span>' : ''}
        </div>
        <div>
          ${isDefault ? '' : `
            <button onclick="deleteGroupByName('${escapeHtml(g.name)}')" class="bg-red-500/20 hover:bg-red-500/30 text-red-400 px-2.5 py-1 rounded text-xs font-semibold active:scale-95 transition flex items-center space-x-1">
              <span>🗑️ Delete</span>
            </button>
          `}
        </div>
      </div>
    `;
  });
  list.innerHTML = html;
}

async function createGroupFromModal() {
  const input = document.getElementById('inputNewGroupName');
  const name = input ? input.value.trim() : '';
  if (!name) {
    showToast('Please enter a group name', 'warning');
    return;
  }
  try {
    const res = await fetch('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Group '${name}' created!`, 'success');
      if (input) input.value = '';
      await loadGroups();
      renderManageGroupsList();
    } else {
      showToast(data.detail || 'Could not create group', 'error');
    }
  } catch (e) {
    showToast('Failed to create group', 'error');
  }
}

async function deleteCurrentGroup() {
  if (state.selectedGroup === 'all' || state.selectedGroup === 'Default') return;
  await deleteGroupByName(state.selectedGroup);
}

async function deleteGroupByName(groupName) {
  if (groupName === 'Default') {
    showToast("Cannot delete the protected 'Default' group", 'warning');
    return;
  }
  if (!confirm(`Are you sure you want to delete group '${groupName}'?\nAny devices in this group will be safely moved back to 'Default'.`)) {
    return;
  }
  try {
    const res = await fetch(`/api/groups/${encodeURIComponent(groupName)}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      if (state.selectedGroup === groupName) {
        state.selectedGroup = 'all';
      }
      await loadGroups();
      await loadDevices();
      renderManageGroupsList();
    } else {
      showToast(data.detail || 'Failed to delete group', 'error');
    }
  } catch (e) {
    showToast('Failed to delete group', 'error');
  }
}

// --- Devices Listing & Filtering ---
async function loadFilterOptions() {
  try {
    const res = await fetch('/api/devices/filter-options');
    if (!res.ok) return;
    const data = await res.json();
    state.filterOptions = data;

    // Populate Model dropdown
    const modelSelect = document.getElementById('deviceFilterModel');
    if (modelSelect) {
      let modelHtml = '<option value="all">All Models</option>';
      if (data.models && data.models.length > 0) {
        data.models.forEach(m => {
          modelHtml += `<option value="${escapeHtml(m.name)}" ${state.modelFilter === m.name ? 'selected' : ''}>${escapeHtml(m.name)} (${m.count})</option>`;
        });
      }
      modelSelect.innerHTML = modelHtml;
      const countModel = document.getElementById('labelCountModel');
      if (countModel) countModel.innerText = `(${data.models.length})`;
    }

    // Populate Specs dropdown
    const specsSelect = document.getElementById('deviceFilterSpecs');
    if (specsSelect) {
      let specsHtml = '<option value="all">All Specs</option>';
      if (data.specs && data.specs.length > 0) {
        data.specs.forEach(s => {
          specsHtml += `<option value="${escapeHtml(s.name)}" ${state.specsFilter === s.name ? 'selected' : ''}>${escapeHtml(s.name)} (${s.count})</option>`;
        });
      }
      specsSelect.innerHTML = specsHtml;
      const countSpecs = document.getElementById('labelCountSpecs');
      if (countSpecs) countSpecs.innerText = `(${data.specs.length})`;
    }

    // Populate Color dropdown
    const colorSelect = document.getElementById('deviceFilterColor');
    if (colorSelect) {
      let colorHtml = '<option value="all">All Colors</option>';
      if (data.colors && data.colors.length > 0) {
        data.colors.forEach(c => {
          colorHtml += `<option value="${escapeHtml(c.name)}" ${state.colorFilter === c.name ? 'selected' : ''}>${escapeHtml(c.name)} (${c.count})</option>`;
        });
      }
      colorSelect.innerHTML = colorHtml;
      const countColor = document.getElementById('labelCountColor');
      if (countColor) countColor.innerText = `(${data.colors.length})`;
    }

    // Populate Activation Date dropdown
    const actDateSelect = document.getElementById('deviceFilterActivationDate');
    if (actDateSelect) {
      let actDateHtml = '<option value="all">All Dates</option>';
      if (data.activation_dates && data.activation_dates.length > 0) {
        data.activation_dates.forEach(d => {
          const cleanDate = d.name.replace(/\s*\(UTC[+-]?\d*\)/i, '').trim();
          actDateHtml += `<option value="${escapeHtml(d.name)}" ${state.activationDateFilter === d.name ? 'selected' : ''}>${escapeHtml(cleanDate)} (${d.count})</option>`;
        });
      }
      actDateSelect.innerHTML = actDateHtml;
      const countActDate = document.getElementById('labelCountActivationDate');
      if (countActDate) countActDate.innerText = `(${data.activation_dates.length})`;
    }
  } catch (e) {}
}

function onCategoryFilterChange() {
  const modelSel = document.getElementById('deviceFilterModel');
  const specsSel = document.getElementById('deviceFilterSpecs');
  const colorSel = document.getElementById('deviceFilterColor');
  const actDateSel = document.getElementById('deviceFilterActivationDate');

  if (modelSel) state.modelFilter = modelSel.value;
  if (specsSel) state.specsFilter = specsSel.value;
  if (colorSel) state.colorFilter = colorSel.value;
  if (actDateSel) state.activationDateFilter = actDateSel.value;

  state.page = 1;
  renderActiveFilterChips();
  loadDevices();
}

function onDeviceSearchInput(val) {
  const clearBtn = document.getElementById('btnClearSearch');
  if (clearBtn) {
    if (val && val.trim().length > 0) {
      clearBtn.classList.remove('hidden');
    } else {
      clearBtn.classList.add('hidden');
    }
  }
  clearTimeout(state.searchTimeout);
  state.searchTimeout = setTimeout(() => {
    state.searchQuery = val.trim();
    state.page = 1;
    renderActiveFilterChips();
    loadDevices();
  }, 250);
}

function clearDeviceSearch() {
  const input = document.getElementById('deviceSearchInput');
  if (input) input.value = '';
  const clearBtn = document.getElementById('btnClearSearch');
  if (clearBtn) clearBtn.classList.add('hidden');
  state.searchQuery = '';
  state.page = 1;
  renderActiveFilterChips();
  loadDevices();
}

function renderActiveFilterChips() {
  const container = document.getElementById('activeFiltersContainer');
  if (!container) return;

  const chips = [];

  if (state.searchQuery) {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-dark-card border border-dark-border text-white text-[11px]">
        <span>🔍 "${escapeHtml(state.searchQuery)}"</span>
        <button onclick="clearDeviceSearch()" class="text-dark-muted hover:text-red-400 font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.modelFilter && state.modelFilter !== 'all') {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-brand-600/20 border border-brand-500/40 text-brand-400 text-[11px]">
        <span>📱 ${escapeHtml(state.modelFilter)}</span>
        <button onclick="removeFilter('model')" class="text-brand-300 hover:text-white font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.specsFilter && state.specsFilter !== 'all') {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-blue-500/20 border border-blue-500/40 text-blue-400 text-[11px]">
        <span>💾 ${escapeHtml(state.specsFilter)}</span>
        <button onclick="removeFilter('specs')" class="text-blue-300 hover:text-white font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.colorFilter && state.colorFilter !== 'all') {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-purple-500/20 border border-purple-500/40 text-purple-400 text-[11px]">
        <span>🎨 ${escapeHtml(state.colorFilter)}</span>
        <button onclick="removeFilter('color')" class="text-purple-300 hover:text-white font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.activationDateFilter && state.activationDateFilter !== 'all') {
    const cleanDate = state.activationDateFilter.replace(/\s*\(UTC[+-]?\d*\)/i, '').trim();
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 text-[11px]">
        <span>📅 ${escapeHtml(cleanDate)}</span>
        <button onclick="removeFilter('activation_date')" class="text-emerald-300 hover:text-white font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.selectedGroup && state.selectedGroup !== 'all') {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-dark-card border border-dark-border text-white text-[11px]">
        <span>📁 ${escapeHtml(state.selectedGroup)}</span>
        <button onclick="removeFilter('group')" class="text-dark-muted hover:text-red-400 font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.statusFilter && state.statusFilter !== 'all') {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-dark-card border border-dark-border text-white text-[11px]">
        <span>🏷️ ${escapeHtml(state.statusFilter)}</span>
        <button onclick="removeFilter('status')" class="text-dark-muted hover:text-red-400 font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (state.newlyActivatedFilter) {
    chips.push(`
      <span class="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-amber-500/20 border border-amber-500/40 text-amber-400 text-[11px]">
        <span>🔥 Detected Today</span>
        <button onclick="removeFilter('newly')" class="text-amber-300 hover:text-white font-bold ml-1">&times;</button>
      </span>
    `);
  }

  if (chips.length > 0) {
    container.classList.remove('hidden');
    container.innerHTML = `
      <span class="text-[10px] text-dark-muted font-semibold uppercase mr-1">Active:</span>
      ${chips.join('')}
      <button onclick="resetAllFilters()" class="text-[11px] font-bold text-red-400 hover:text-red-300 hover:underline px-1 py-0.5 ml-1">
        Reset All ✕
      </button>
    `;
  } else {
    container.classList.add('hidden');
    container.innerHTML = '';
  }
}

function removeFilter(type) {
  if (type === 'model') {
    state.modelFilter = 'all';
    const sel = document.getElementById('deviceFilterModel');
    if (sel) sel.value = 'all';
  } else if (type === 'specs') {
    state.specsFilter = 'all';
    const sel = document.getElementById('deviceFilterSpecs');
    if (sel) sel.value = 'all';
  } else if (type === 'color') {
    state.colorFilter = 'all';
    const sel = document.getElementById('deviceFilterColor');
    if (sel) sel.value = 'all';
  } else if (type === 'activation_date') {
    state.activationDateFilter = 'all';
    const sel = document.getElementById('deviceFilterActivationDate');
    if (sel) sel.value = 'all';
  } else if (type === 'group') {
    state.selectedGroup = 'all';
    loadGroups();
  } else if (type === 'status') {
    state.statusFilter = 'all';
    updateFilterButtons();
  } else if (type === 'newly') {
    state.newlyActivatedFilter = false;
    updateFilterButtons();
  }
  state.page = 1;
  renderActiveFilterChips();
  loadDevices();
}

function resetAllFilters() {
  state.searchQuery = '';
  state.modelFilter = 'all';
  state.specsFilter = 'all';
  state.colorFilter = 'all';
  state.activationDateFilter = 'all';
  state.statusFilter = 'all';
  state.selectedGroup = 'all';
  state.newlyActivatedFilter = false;
  state.page = 1;

  const input = document.getElementById('deviceSearchInput');
  if (input) input.value = '';
  const clearBtn = document.getElementById('btnClearSearch');
  if (clearBtn) clearBtn.classList.add('hidden');

  const modelSel = document.getElementById('deviceFilterModel');
  if (modelSel) modelSel.value = 'all';
  const specsSel = document.getElementById('deviceFilterSpecs');
  if (specsSel) specsSel.value = 'all';
  const colorSel = document.getElementById('deviceFilterColor');
  if (colorSel) colorSel.value = 'all';
  const actDateSel = document.getElementById('deviceFilterActivationDate');
  if (actDateSel) actDateSel.value = 'all';

  updateFilterButtons();
  loadGroups();
  renderActiveFilterChips();
  loadDevices();
}

function setFilterStatus(status) {
  state.statusFilter = status;
  state.newlyActivatedFilter = false;
  state.page = 1;
  updateFilterButtons();
  renderActiveFilterChips();
  loadDevices();
}

function setFilterNewlyActivated() {
  state.newlyActivatedFilter = !state.newlyActivatedFilter;
  state.statusFilter = 'all';
  state.page = 1;
  updateFilterButtons();
  renderActiveFilterChips();
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
    renderActiveFilterChips();
    loadDevices();
  }, 250);
}

async function loadDevices() {
  const container = document.getElementById('devicesCardsList');
  container.innerHTML = '<div class="py-8 text-center text-xs text-dark-muted">Loading devices...</div>';

  const params = new URLSearchParams({
    search: state.searchQuery,
    group: state.selectedGroup,
    status: state.statusFilter,
    model: state.modelFilter === 'all' ? '' : state.modelFilter,
    specs: state.specsFilter === 'all' ? '' : state.specsFilter,
    color: state.colorFilter === 'all' ? '' : state.colorFilter,
    activation_date: state.activationDateFilter === 'all' ? '' : state.activationDateFilter,
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
let uploadedFilePreviewData = null;

function openAddModal() {
  document.getElementById('modalAddDevices').classList.remove('hidden');
}

function closeAddModal() {
  document.getElementById('modalAddDevices').classList.add('hidden');
  const card = document.getElementById('excelColumnSelectorCard');
  if (card) card.classList.add('hidden');
  uploadedFilePreviewData = null;
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

async function onUploadFileSelected(input) {
  const card = document.getElementById('excelColumnSelectorCard');
  const select = document.getElementById('modalExcelColumnSelect');
  const samplesBox = document.getElementById('excelColumnSamplesPreview');
  const pill = document.getElementById('excelFileStatsPill');

  if (!input.files || input.files.length === 0) {
    if (card) card.classList.add('hidden');
    uploadedFilePreviewData = null;
    return;
  }

  const file = input.files[0];
  const filename = file.name.toLowerCase();

  // If plain .txt file, column selection is not needed
  if (filename.endsWith('.txt')) {
    if (card) card.classList.add('hidden');
    uploadedFilePreviewData = null;
    return;
  }

  // Request column inspection preview from backend
  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/devices/upload-preview', {
      method: 'POST',
      body: formData
    });
    if (res.ok) {
      const data = await res.json();
      uploadedFilePreviewData = data;
      if (card) card.classList.remove('hidden');

      if (pill) {
        pill.innerText = `${data.total_rows || 0} rows · ${data.columns.length} cols`;
      }

      let html = `<option value="">-- Auto-Detect (${escapeHtml(data.columns[data.detected_index]?.header || 'Recommended')}) --</option>`;
      data.columns.forEach(col => {
        const isDetected = (col.index === data.detected_index);
        html += `<option value="${col.letter}" ${isDetected ? 'selected' : ''}>
          Col ${col.letter}: "${escapeHtml(col.header)}" ${isDetected ? '★ Auto Detected' : (col.is_candidate ? '✓ Candidate' : '')}
        </option>`;
      });
      if (select) {
        select.innerHTML = html;
      }
      updateColumnSamplePreview();
    } else {
      if (card) card.classList.add('hidden');
      uploadedFilePreviewData = null;
    }
  } catch (e) {
    if (card) card.classList.add('hidden');
    uploadedFilePreviewData = null;
  }
}

function onSelectedColumnChange() {
  updateColumnSamplePreview();
}

function updateColumnSamplePreview() {
  const select = document.getElementById('modalExcelColumnSelect');
  const samplesBox = document.getElementById('excelColumnSamplesPreview');
  if (!samplesBox || !uploadedFilePreviewData || !uploadedFilePreviewData.columns) return;

  const chosenVal = select ? select.value : '';
  let targetCol = null;

  if (chosenVal) {
    targetCol = uploadedFilePreviewData.columns.find(c => c.letter === chosenVal || String(c.index) === chosenVal || c.header === chosenVal);
  }
  if (!targetCol && uploadedFilePreviewData.detected_index !== undefined) {
    targetCol = uploadedFilePreviewData.columns[uploadedFilePreviewData.detected_index];
  }
  if (!targetCol && uploadedFilePreviewData.columns.length > 0) {
    targetCol = uploadedFilePreviewData.columns[0];
  }

  if (targetCol && targetCol.samples && targetCol.samples.length > 0) {
    const samplesStr = targetCol.samples.map(s => escapeHtml(s)).join(', ');
    samplesBox.innerHTML = `<div><span class="text-white font-semibold">Column ${targetCol.letter} ("${escapeHtml(targetCol.header)}"):</span></div><div class="text-[10px] text-brand-300 mt-0.5 truncate">${samplesStr}</div>`;
  } else {
    samplesBox.innerHTML = `<span class="text-dark-muted">No sample values available for this column.</span>`;
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

      const chosenColumn = document.getElementById('modalExcelColumnSelect') ? document.getElementById('modalExcelColumnSelect').value : '';
      const params = new URLSearchParams({ group_name: group });
      if (chosenColumn) {
        params.set('serial_column', chosenColumn);
      }

      const res = await fetch(`/api/devices/upload?${params.toString()}`, {
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

// --- AGY Remote YOLO Terminal ---
let agyEventSource = null;
let agyTerminalLineCount = 0;
let agyIsRunning = false;

function setAgyPrompt(promptText) {
  const input = document.getElementById('agyPromptInput');
  if (input) {
    input.value = promptText;
    input.focus();
  }
}

async function loadAgyStatus() {
  try {
    const res = await fetch('/api/terminal/agy/status');
    if (!res.ok) return;
    const data = await res.json();
    updateAgyUiStatus(data.is_running, data.last_prompt);

    // If terminal is empty or uninitialized, load existing buffered logs
    const consoleEl = document.getElementById('agyTerminalConsole');
    if (consoleEl && consoleEl.dataset.initialized !== 'true') {
      const logsRes = await fetch('/api/terminal/agy/logs');
      if (logsRes.ok) {
        const logsData = await logsRes.json();
        if (logsData.lines && logsData.lines.length > 0) {
          consoleEl.innerHTML = '';
          agyTerminalLineCount = 0;
          logsData.lines.forEach(line => appendTerminalLine(line));
        }
      }
      consoleEl.dataset.initialized = 'true';
    }
  } catch (e) {}
}

function updateAgyUiStatus(isRunning, prompt = '') {
  agyIsRunning = isRunning;
  const dot = document.getElementById('agyStatusDot');
  const text = document.getElementById('agyStatusText');
  const btnRun = document.getElementById('btnAgyRun');
  const btnStop = document.getElementById('btnAgyStop');

  if (isRunning) {
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-brand-500 animate-ping';
    if (text) text.innerText = 'Running YOLO...';
    if (btnRun) btnRun.classList.add('hidden');
    if (btnStop) btnStop.classList.remove('hidden');
  } else {
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-emerald-500';
    if (text) text.innerText = 'Ready';
    if (btnRun) btnRun.classList.remove('hidden');
    if (btnStop) btnStop.classList.add('hidden');
  }
}

function startAgyEventStream() {
  if (agyEventSource) return;

  agyEventSource = new EventSource('/api/terminal/agy/stream');

  agyEventSource.addEventListener('line', (e) => {
    try {
      const data = JSON.parse(e.data);
      appendTerminalLine(data.text);
    } catch (err) {
      appendTerminalLine(e.data);
    }
  });

  agyEventSource.addEventListener('status', (e) => {
    try {
      const data = JSON.parse(e.data);
      updateAgyUiStatus(data.is_running, data.last_prompt);
      if (data.status === 'completed') {
        appendTerminalLine(`\n[Process completed with exit code ${data.exit_code}]\n`);
        showToast('AGY execution finished!', 'success');
      } else if (data.status === 'stopped') {
        appendTerminalLine(`\n[Process terminated by user]\n`);
        showToast('AGY execution stopped', 'warning');
      }
    } catch (err) {}
  });

  agyEventSource.onerror = () => {
    // EventSource will auto-reconnect
  };
}

function appendTerminalLine(text) {
  const consoleEl = document.getElementById('agyTerminalConsole');
  if (!consoleEl) return;

  // Clear placeholder text on first real output
  if (consoleEl.innerText.includes('Ready to execute')) {
    consoleEl.innerHTML = '';
  }

  const lineEl = document.createElement('div');
  lineEl.className = 'break-all font-mono whitespace-pre-wrap';

  // Format line styling based on content
  if (text.startsWith('>') || text.startsWith('$')) {
    lineEl.className += ' text-brand-400 font-bold';
  } else if (text.toLowerCase().includes('error') || text.toLowerCase().includes('failed:')) {
    lineEl.className += ' text-red-400';
  } else if (text.toLowerCase().includes('success') || text.toLowerCase().includes('completed')) {
    lineEl.className += ' text-emerald-300';
  } else if (text.toLowerCase().includes('warning:')) {
    lineEl.className += ' text-amber-400';
  }

  lineEl.textContent = text;
  consoleEl.appendChild(lineEl);

  agyTerminalLineCount++;
  const countText = document.getElementById('terminalLineCountText');
  if (countText) countText.innerText = `${agyTerminalLineCount} lines`;

  // Auto scroll to bottom
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

async function runAgyCommand() {
  const input = document.getElementById('agyPromptInput');
  const prompt = input ? input.value.trim() : '';
  if (!prompt) {
    showToast('Please enter a prompt or instruction for AGY', 'warning');
    if (input) input.focus();
    return;
  }

  const sendTelegram = document.getElementById('agySendTelegramCheckbox') ? document.getElementById('agySendTelegramCheckbox').checked : true;
  const modeSelect = document.getElementById('agyModeSelect');
  const mode = modeSelect ? modeSelect.value : 'auto';

  appendTerminalLine(`\n> agy --dangerously-skip-permissions --mode ${mode} -p "${prompt}"`);
  updateAgyUiStatus(true, prompt);
  startAgyEventStream();

  try {
    const res = await fetch('/api/terminal/agy/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: prompt,
        mode: mode,
        send_telegram: sendTelegram
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('AGY YOLO execution started!', 'success');
    } else {
      showToast(data.detail || 'Failed to start AGY', 'error');
      updateAgyUiStatus(false);
    }
  } catch (e) {
    showToast('Failed to start AGY execution', 'error');
    updateAgyUiStatus(false);
  }
}

async function stopAgyProcess() {
  try {
    const res = await fetch('/api/terminal/agy/stop', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || 'Process stopped', 'info');
      updateAgyUiStatus(false);
    } else {
      showToast(data.detail || 'Could not stop process', 'error');
    }
  } catch (e) {
    showToast('Failed to stop process', 'error');
  }
}

function clearAgyTerminal() {
  const consoleEl = document.getElementById('agyTerminalConsole');
  if (consoleEl) {
    consoleEl.innerHTML = '';
    agyTerminalLineCount = 0;
    const countText = document.getElementById('terminalLineCountText');
    if (countText) countText.innerText = '0 lines';
  }
}

function copyTerminalText() {
  const consoleEl = document.getElementById('agyTerminalConsole');
  if (!consoleEl) return;
  const text = consoleEl.innerText;
  if (!text) {
    showToast('Terminal is empty', 'info');
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    showToast('Terminal output copied to clipboard!', 'success');
  }).catch(() => {
    showToast('Failed to copy output', 'error');
  });
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
