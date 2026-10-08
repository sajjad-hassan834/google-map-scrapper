// ==========================================================================
// MapLead — Client Application Logic
// ==========================================================================

let currentEngine = 'places'; // 'places' | 'scraper'
let currentAppMode = 'search'; // 'search' | 'crm'
let currentCrmStage = 'ALL';

let qualifiedLeads = [];
let excludedLeads = [];
let crmLeads = [];
let activeDrawerLeadId = null;
let notesDebounceTimer = null;
let pagesToFetch = 3;
let currentCreditStatus = null;
let currentUser = null;
// Auto-route to Railway backend if frontend is hosted on Vercel
const DEFAULT_RAILWAY_BACKEND = 'https://web-production-77cf5.up.railway.app';
let autoDetectedApiBase = '';
if (typeof window !== 'undefined' && window.location && window.location.hostname.includes('vercel.app')) {
  autoDetectedApiBase = DEFAULT_RAILWAY_BACKEND;
}
const API_BASE = window.API_BASE_URL || localStorage.getItem('MAPLEAD_API_BASE') || autoDetectedApiBase;

/**
 * Universal credentialed fetch wrapper.
 * Transmits both signed session cookies and Authorization: Bearer tokens.
 * Works seamlessly across cross-domain deployments (Vercel -> Railway) and single-platform setups.
 */
async function apiFetch(url, options = {}) {
  const token = localStorage.getItem('MAPLEAD_AUTH_TOKEN');
  const headers = {
    ...(options.headers || {})
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const mergedOptions = {
    credentials: 'include',
    ...options,
    headers
  };

  const response = await fetch(url, mergedOptions);
  if (response.status === 401 && !url.includes('/api/auth/status') && !url.includes('/api/auth/login')) {
    localStorage.removeItem('MAPLEAD_AUTH_TOKEN');
    document.getElementById('appShell')?.classList.add('hidden');
    document.getElementById('loginView')?.classList.remove('hidden');
    const errBox = document.getElementById('loginErrorBox');
    const errText = document.getElementById('loginErrorText');
    if (errBox && errText) {
      errText.textContent = 'Session expired. Please enter password to continue.';
      errBox.classList.remove('hidden');
    }
  }
  return response;
}

// DOM Cache
const inputQuery = document.getElementById('inputQuery');
const checkNoWebsite = document.getElementById('checkNoWebsite');
const inputMinReviews = document.getElementById('inputMinReviews');
const selectMinRating = document.getElementById('selectMinRating');
const checkMustHavePhone = document.getElementById('checkMustHavePhone');
const inputDepth = document.getElementById('inputDepth');
const btnSubmitSearch = document.getElementById('btnSubmitSearch');
const btnSubmitText = document.getElementById('btnSubmitText');
const searchSpinner = document.getElementById('searchSpinner');

const resultsSummaryText = document.getElementById('resultsSummaryText');
const btnWhyHidden = document.getElementById('btnWhyHidden');
const btnSaveAll = document.getElementById('btnSaveAll');
const btnExportCsv = document.getElementById('btnExportCsv');
const btnCopyPhones = document.getElementById('btnCopyPhones');

const progressBox = document.getElementById('progressBox');
const progressMessage = document.getElementById('progressMessage');
const progressBarFill = document.getElementById('progressBarFill');
const leadsTableBody = document.getElementById('leadsTableBody');

// ==========================================================================
// Initialization & Authentication Lifecycle
// ==========================================================================
document.addEventListener('DOMContentLoaded', () => {
  checkAuthStatus();

  if (inputMinReviews) {
    inputMinReviews.addEventListener('input', (e) => {
      const val = parseInt(e.target.value) || 0;
      document.querySelectorAll('.pill-btn').forEach(b => {
        b.classList.toggle('active', parseInt(b.textContent) === val);
      });
    });
  }

  // Close user menu on click outside, and close modals when backdrop is clicked
  document.addEventListener('click', (e) => {
    const menuWrapper = document.getElementById('userMenuWrapper');
    if (menuWrapper && !menuWrapper.contains(e.target)) {
      closeUserMenu();
    }
    if (e.target && e.target.classList && (e.target.classList.contains('modal-backdrop') || e.target.classList.contains('modal-overlay'))) {
      e.target.classList.add('hidden');
    }
  });

function closeModalOnBackdrop(event, modalId) {
  if (event && event.target && event.target.id === modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('hidden');
  }
}

  // Escape key closes side drawer, modals, and user menu
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeLeadDrawer();
      closeExcludedModal();
      closeCreditsModal();
      closeSettingsModal();
      closeHelpModal();
      closePolicyModal();
      closeAccountModal();
      closeUserEditModal();
      closeUserMenu();
    }
  });
});

async function checkAuthStatus() {
  try {
    const res = await apiFetch(`${API_BASE}/api/auth/status`);
    if (res.ok) {
      const data = await res.json();
      if (data.authenticated) {
        currentUser = data.user;
        applyBranding(data);
        updateUserMenuUI();
        document.getElementById('loginView')?.classList.add('hidden');
        document.getElementById('appShell')?.classList.remove('hidden');
        applySavedSettings();
        checkScraperHealth();
        loadCrmLeads();
        loadCreditsStatus();
        return;
      }
    }
  } catch (e) {
    console.error('Failed checking auth status:', e);
  }
  // Not authenticated
  currentUser = null;
  document.getElementById('appShell')?.classList.add('hidden');
  document.getElementById('loginView')?.classList.remove('hidden');
  setTimeout(() => document.getElementById('inputPassword')?.focus(), 80);
}

function updateUserMenuUI() {
  if (!currentUser) return;
  const avatarBtn = document.getElementById('userMenuBtn');
  const displayNameEl = document.getElementById('userMenuBrandTitle');
  const roleTagEl = document.getElementById('userMenuRoleTag');
  const menuItemTeam = document.getElementById('menuItemTeam');

  const name = currentUser.display_name || currentUser.username || 'Admin';
  if (displayNameEl) displayNameEl.textContent = name;
  if (avatarBtn) avatarBtn.textContent = (name.charAt(0) || 'A').toUpperCase();

  const isAdmin = currentUser.role === 'admin';
  if (roleTagEl) {
    roleTagEl.textContent = isAdmin ? 'Manager / Admin' : 'Standard Member';
  }
  if (menuItemTeam) {
    menuItemTeam.classList.toggle('hidden', !isAdmin);
  }
}

function applyBranding(data) {
  if (!data) return;
  const brandTitle = document.getElementById('userMenuBrandTitle');
  if (brandTitle && data.brand_name && !currentUser) brandTitle.textContent = data.brand_name;
  const footerBrand = document.getElementById('footerBrand');
  if (footerBrand && data.brand_name) footerBrand.textContent = data.brand_name;
  const footerVer = document.getElementById('footerVersion');
  if (footerVer && data.app_version) footerVer.textContent = `v${data.app_version}`;
  const tagline = document.getElementById('loginTagline');
  if (tagline && data.brand_tagline) tagline.textContent = data.brand_tagline;
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  const pwdInput = document.getElementById('inputPassword');
  const errBox = document.getElementById('loginErrorBox');
  const errText = document.getElementById('loginErrorText');
  const submitBtn = document.getElementById('btnLoginSubmit');
  const spinner = document.getElementById('loginSpinner');
  const btnText = document.getElementById('btnLoginText');

  const password = pwdInput ? pwdInput.value : '';
  if (!password) return;

  if (errBox) errBox.classList.add('hidden');
  if (submitBtn) submitBtn.disabled = true;
  if (spinner) spinner.classList.remove('hidden');
  if (btnText) btnText.textContent = 'Signing in...';

  try {
    const res = await apiFetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });

    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || 'Incorrect password. Please try again.';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      if (pwdInput) {
        pwdInput.focus();
        pwdInput.select();
      }
      return;
    }

    // Authenticated
    if (data.token) {
      localStorage.setItem('MAPLEAD_AUTH_TOKEN', data.token);
    }
    if (data.user) {
      currentUser = data.user;
      updateUserMenuUI();
    }
    if (pwdInput) pwdInput.value = '';
    document.getElementById('loginView')?.classList.add('hidden');
    document.getElementById('appShell')?.classList.remove('hidden');
    showToast('Signed in');
    applySavedSettings();
    checkScraperHealth();
    loadCrmLeads();
    loadCreditsStatus();
  } catch (e) {
    if (errBox && errText) {
      errText.textContent = 'Network or connection error. Please try again.';
      errBox.classList.remove('hidden');
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
    if (spinner) spinner.classList.add('hidden');
    if (btnText) btnText.textContent = 'Sign in';
  }
}

async function handleLogout() {
  try {
    await apiFetch(`${API_BASE}/api/auth/logout`, { method: 'POST' });
  } catch (e) {
    console.error('Logout error:', e);
  }
  localStorage.removeItem('MAPLEAD_AUTH_TOKEN');
  closeUserMenu();
  document.getElementById('appShell')?.classList.add('hidden');
  document.getElementById('loginView')?.classList.remove('hidden');
  const pwdInput = document.getElementById('inputPassword');
  if (pwdInput) {
    pwdInput.value = '';
    setTimeout(() => pwdInput.focus(), 80);
  }
  showToast('Signed out');
}

// ==========================================================================
// User Navigation Menu
// ==========================================================================
function toggleUserMenu() {
  const dropdown = document.getElementById('userMenuDropdown');
  const btn = document.getElementById('userMenuBtn');
  if (!dropdown) return;
  const isHidden = dropdown.classList.contains('hidden');
  dropdown.classList.toggle('hidden', !isHidden);
  if (btn) btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

function closeUserMenu() {
  const dropdown = document.getElementById('userMenuDropdown');
  const btn = document.getElementById('userMenuBtn');
  if (dropdown && !dropdown.classList.contains('hidden')) {
    dropdown.classList.add('hidden');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }
}

// ==========================================================================
// Settings Modal & Preferences
// ==========================================================================
function openSettingsModal() {
  closeUserMenu();
  const modal = document.getElementById('settingsModal');
  if (!modal) return;

  if (currentCreditStatus) {
    const limitInput = document.getElementById('settingsMonthlyLimit');
    const bufferInput = document.getElementById('settingsSafetyBuffer');
    if (limitInput) limitInput.value = currentCreditStatus.monthly_limit || 1000;
    if (bufferInput) bufferInput.value = currentCreditStatus.safety_buffer || 50;
  }

  const saved = getStoredSettings();
  const pagesSel = document.getElementById('settingsDefaultPages');
  const minRev = document.getElementById('settingsMinReviews');
  const minRat = document.getElementById('settingsMinRating');
  const noWeb = document.getElementById('settingsNoWebsite');
  const phone = document.getElementById('settingsMustHavePhone');

  if (pagesSel) pagesSel.value = saved.defaultPages || '3';
  if (minRev) minRev.value = saved.minReviews !== undefined ? saved.minReviews : '1';
  if (minRat) minRat.value = saved.minRating !== undefined ? saved.minRating : '0.0';
  if (noWeb) noWeb.checked = saved.noWebsiteOnly !== undefined ? saved.noWebsiteOnly : true;
  if (phone) phone.checked = saved.mustHavePhone !== undefined ? saved.mustHavePhone : true;

  modal.classList.remove('hidden');
}

function closeSettingsModal() {
  const modal = document.getElementById('settingsModal');
  if (modal) modal.classList.add('hidden');
}

function getStoredSettings() {
  try {
    const raw = localStorage.getItem('MAPLEAD_SETTINGS');
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

async function saveUserSettings() {
  const limitInput = document.getElementById('settingsMonthlyLimit');
  const bufferInput = document.getElementById('settingsSafetyBuffer');
  const pagesSel = document.getElementById('settingsDefaultPages');
  const minRev = document.getElementById('settingsMinReviews');
  const minRat = document.getElementById('settingsMinRating');
  const noWeb = document.getElementById('settingsNoWebsite');
  const phone = document.getElementById('settingsMustHavePhone');

  const monthly_limit = parseInt(limitInput?.value) || 1000;
  const safety_buffer = parseInt(bufferInput?.value) || 50;

  try {
    const res = await apiFetch(`${API_BASE}/api/credits/settings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ monthly_limit, safety_buffer })
    });
    if (res.ok) {
      const data = await res.json();
      currentCreditStatus = data;
      updateCreditsMeterUI(data);
    }
  } catch (e) {
    console.error('Failed to sync credit settings:', e);
  }

  const settingsObj = {
    defaultPages: pagesSel ? pagesSel.value : '3',
    minReviews: minRev ? parseInt(minRev.value) || 0 : 1,
    minRating: minRat ? minRat.value : '0.0',
    noWebsiteOnly: noWeb ? noWeb.checked : true,
    mustHavePhone: phone ? phone.checked : true
  };
  localStorage.setItem('MAPLEAD_SETTINGS', JSON.stringify(settingsObj));

  applySavedSettings();
  closeSettingsModal();
  showToast('Settings saved');
}

function applySavedSettings() {
  const saved = getStoredSettings();
  if (saved.defaultPages) {
    setPagesToFetch(parseInt(saved.defaultPages) || 3);
  }
  if (saved.minReviews !== undefined && inputMinReviews) {
    inputMinReviews.value = saved.minReviews;
    document.querySelectorAll('.pill-btn').forEach(b => {
      b.classList.toggle('active', parseInt(b.textContent) === saved.minReviews);
    });
  }
  if (saved.minRating !== undefined && selectMinRating) {
    selectMinRating.value = saved.minRating;
  }
  if (saved.noWebsiteOnly !== undefined && checkNoWebsite) {
    checkNoWebsite.checked = saved.noWebsiteOnly;
  }
  if (saved.mustHavePhone !== undefined && checkMustHavePhone) {
    checkMustHavePhone.checked = saved.mustHavePhone;
  }
}

// ==========================================================================
// Help and Policy Modals
// ==========================================================================
function openHelpModal() {
  closeUserMenu();
  const modal = document.getElementById('helpModal');
  if (modal) modal.classList.remove('hidden');
}

function closeHelpModal() {
  const modal = document.getElementById('helpModal');
  if (modal) modal.classList.add('hidden');
}

function openPolicyModal(type) {
  const modal = document.getElementById('policyModal');
  const title = document.getElementById('policyModalTitle');
  const body = document.getElementById('policyModalBody');
  if (!modal || !title || !body) return;

  if (type === 'privacy') {
    title.textContent = 'Privacy policy';
    body.innerHTML = `
      <h4>Information we process</h4>
      <p>MapLead queries publicly available business listings directly from Google Places API or user-specified search parameters. All search queries and results are stored locally in your deployment database for caching and lead tracking.</p>
      <h4>Data ownership & cookies</h4>
      <p>Your session cookies and stored CRM leads belong entirely to your own installation. MapLead does not transmit your leads, API keys, or application passwords to any external analytics or third-party servers.</p>
      <h4>Third-party APIs</h4>
      <p>When executing searches, requests are dispatched to Google Places API subject to Google's standard developer terms and privacy policies.</p>
    `;
  } else {
    title.textContent = 'Terms of service';
    body.innerHTML = `
      <h4>Acceptable use</h4>
      <p>MapLead is designed for freelancers, web designers, and marketing agencies conducting targeted local B2B outreach. You agree to use search results responsibly and comply with applicable local telemarketing, commercial communication, and anti-spam regulations (such as CAN-SPAM).</p>
      <h4>API quotas & billing</h4>
      <p>You are solely responsible for managing your Google Cloud billing accounts and monitoring Google Places API usage. While MapLead includes safety buffers and credit meters, usage metrics in the Google Cloud Console govern official billing.</p>
      <h4>Disclaimer</h4>
      <p>This software is provided "as is", without warranty of any kind. You are responsible for ensuring your outreach practices comply with local regulations and ethical standards.</p>
    `;
  }

  modal.classList.remove('hidden');
}

function closePolicyModal() {
  const modal = document.getElementById('policyModal');
  if (modal) modal.classList.add('hidden');
}

// ==========================================================================
// Account & Team Management (Manager / Admin Level)
// ==========================================================================
let teamUsersList = [];

function openAccountModal(tab = 'profile') {
  closeUserMenu();
  const modal = document.getElementById('accountModal');
  if (!modal) return;

  // Populate profile fields with currentUser
  if (currentUser) {
    const usernameEl = document.getElementById('profUsername');
    const badgeEl = document.getElementById('profRoleBadge');
    const nameEl = document.getElementById('profDisplayName');
    const emailEl = document.getElementById('profEmail');

    if (usernameEl) usernameEl.value = currentUser.username || '';
    if (badgeEl) {
      const isAdmin = currentUser.role === 'admin';
      badgeEl.textContent = isAdmin ? 'Manager / Admin' : 'Standard Member';
      badgeEl.className = `role-badge ${isAdmin ? 'admin' : 'member'}`;
    }
    if (nameEl) nameEl.value = currentUser.display_name || '';
    if (emailEl) emailEl.value = currentUser.email || '';
  }

  // Clear password fields
  const curPwd = document.getElementById('pwdCurrent');
  const newPwd = document.getElementById('pwdNew');
  if (curPwd) curPwd.value = '';
  if (newPwd) newPwd.value = '';

  // Show/Hide team tab button based on permissions
  const tabTeamBtn = document.getElementById('accountTabBtnTeam');
  if (tabTeamBtn) {
    tabTeamBtn.classList.toggle('hidden', currentUser?.role !== 'admin');
  }

  switchAccountTab(tab === 'team' && currentUser?.role === 'admin' ? 'team' : 'profile');
  modal.classList.remove('hidden');
}

function closeAccountModal() {
  const modal = document.getElementById('accountModal');
  if (modal) modal.classList.add('hidden');
}

function switchAccountTab(tab) {
  const btnProfile = document.getElementById('accountTabBtnProfile');
  const btnTeam = document.getElementById('accountTabBtnTeam');
  const contentProfile = document.getElementById('accountContentProfile');
  const contentTeam = document.getElementById('accountContentTeam');

  if (tab === 'team') {
    btnProfile?.classList.remove('active');
    btnTeam?.classList.add('active');
    contentProfile?.classList.add('hidden');
    contentTeam?.classList.remove('hidden');
    loadAllUsers();
  } else {
    btnProfile?.classList.add('active');
    btnTeam?.classList.remove('active');
    contentProfile?.classList.remove('hidden');
    contentTeam?.classList.add('hidden');
  }
}

async function handleProfileFormSubmit(event) {
  event.preventDefault();
  const btn = document.getElementById('btnSaveProfile');
  const displayName = document.getElementById('profDisplayName')?.value.trim();
  const email = document.getElementById('profEmail')?.value.trim();

  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch(`${API_BASE}/api/account/me`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: displayName, email })
    });

    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to update profile');
      return;
    }

    if (data.user) {
      currentUser = data.user;
      updateUserMenuUI();
    }
    showToast('Profile updated successfully');
  } catch (e) {
    showToast('Network error updating profile');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handlePasswordChangeSubmit(event) {
  event.preventDefault();
  const btn = document.getElementById('btnChangePassword');
  const currentPassword = document.getElementById('pwdCurrent')?.value;
  const newPassword = document.getElementById('pwdNew')?.value;

  if (!newPassword || newPassword.length < 6) {
    showToast('New password must be at least 6 characters');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch(`${API_BASE}/api/account/me`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword
      })
    });

    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to change password');
      return;
    }

    const curPwd = document.getElementById('pwdCurrent');
    const newPwd = document.getElementById('pwdNew');
    if (curPwd) curPwd.value = '';
    if (newPwd) newPwd.value = '';
    showToast('Password changed successfully');
  } catch (e) {
    showToast('Network error updating password');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// User Accounts CRUD for Admins
async function loadAllUsers() {
  const tbody = document.getElementById('usersTableBody');
  if (!tbody) return;

  tbody.innerHTML = `
    <tr>
      <td colspan="5" style="text-align: center; color: var(--color-text-muted); padding: 24px;">
        Loading team accounts...
      </td>
    </tr>
  `;

  try {
    const res = await apiFetch(`${API_BASE}/api/users`);
    if (!res.ok) {
      tbody.innerHTML = `
        <tr>
          <td colspan="5" style="text-align: center; color: var(--color-destructive); padding: 24px;">
            Failed to load users list. Admin permissions required.
          </td>
        </tr>
      `;
      return;
    }

    const data = await res.json();
    teamUsersList = data.users || [];
    renderUsersTable(teamUsersList);
  } catch (e) {
    tbody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align: center; color: var(--color-destructive); padding: 24px;">
          Error connecting to users service.
        </td>
      </tr>
    `;
  }
}

function renderUsersTable(users) {
  const tbody = document.getElementById('usersTableBody');
  if (!tbody) return;

  if (!users || users.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align: center; color: var(--color-text-muted); padding: 24px;">
          No team members registered yet.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = users.map(u => {
    const isSelf = currentUser && (currentUser.id === u.id || currentUser.username === u.username);
    const isAdmin = u.role === 'admin';
    const isActive = u.status === 'active';

    const roleBadge = isAdmin ?
      '<span class="role-badge admin">Manager / Admin</span>' :
      '<span class="role-badge member">Member</span>';

    const statusBadge = isActive ?
      '<span class="status-badge active">Active</span>' :
      '<span class="status-badge disabled">Disabled</span>';

    const userSelfTag = isSelf ? ' <span class="quiet-hint" style="font-size:11px;">(You)</span>' : '';

    return `
      <tr>
        <td>
          <strong style="color: var(--color-text-main);">${escapeHtml(u.username)}</strong>${userSelfTag}
        </td>
        <td>
          <div>${escapeHtml(u.display_name || '—')}</div>
          <div class="quiet-hint" style="font-size: 11px;">${escapeHtml(u.email || 'No email')}</div>
        </td>
        <td>${roleBadge}</td>
        <td>${statusBadge}</td>
        <td style="text-align: right;">
          <div style="display: inline-flex; gap: 6px; align-items: center; justify-content: flex-end;">
            <button type="button" class="btn-action-sm" onclick="openEditUserModal('${u.id}')" title="Edit account details">
              Edit
            </button>
            <button type="button" class="btn-action-sm" onclick="promptResetUserPassword('${u.id}', '${escapeHtml(u.username)}')" title="Reset password">
              Key
            </button>
            ${!isSelf ? `
              <button type="button" class="btn-action-sm" onclick="toggleUserStatus('${u.id}', '${u.status}')" title="${isActive ? 'Disable account' : 'Activate account'}">
                ${isActive ? 'Disable' : 'Enable'}
              </button>
              <button type="button" class="btn-action-sm danger" onclick="deleteUserAccount('${u.id}', '${escapeHtml(u.username)}')" title="Delete user">
                Delete
              </button>
            ` : ''}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function openCreateUserModal() {
  document.getElementById('userEditModalTitle').textContent = 'Add new team member';
  document.getElementById('editUserId').value = '';
  const usernameInput = document.getElementById('editUsername');
  if (usernameInput) {
    usernameInput.value = '';
    usernameInput.disabled = false;
  }
  document.getElementById('editRole').value = 'member';
  document.getElementById('editDisplayName').value = '';
  document.getElementById('editEmail').value = '';
  const pwdInput = document.getElementById('editPassword');
  if (pwdInput) {
    pwdInput.value = '';
    pwdInput.required = true;
  }
  document.getElementById('editPasswordLabel').textContent = 'Initial password';
  document.getElementById('editPasswordHint').textContent = 'Required for new accounts (minimum 6 characters).';
  document.getElementById('userEditModal').classList.remove('hidden');
}

function openEditUserModal(userId) {
  const user = teamUsersList.find(u => u.id === userId);
  if (!user) return;

  document.getElementById('userEditModalTitle').textContent = `Edit account: ${user.username}`;
  document.getElementById('editUserId').value = user.id;
  const usernameInput = document.getElementById('editUsername');
  if (usernameInput) {
    usernameInput.value = user.username;
    usernameInput.disabled = true; // Username is immutable
  }
  document.getElementById('editRole').value = user.role || 'member';
  document.getElementById('editDisplayName').value = user.display_name || '';
  document.getElementById('editEmail').value = user.email || '';
  const pwdInput = document.getElementById('editPassword');
  if (pwdInput) {
    pwdInput.value = '';
    pwdInput.required = false;
  }
  document.getElementById('editPasswordLabel').textContent = 'New password (optional)';
  document.getElementById('editPasswordHint').textContent = 'Leave blank to keep existing password.';
  document.getElementById('userEditModal').classList.remove('hidden');
}

function closeUserEditModal() {
  const modal = document.getElementById('userEditModal');
  if (modal) modal.classList.add('hidden');
}

async function handleUserFormSubmit(event) {
  event.preventDefault();
  const btn = document.getElementById('btnSaveUser');
  const userId = document.getElementById('editUserId').value;
  const username = document.getElementById('editUsername').value.trim();
  const role = document.getElementById('editRole').value;
  const displayName = document.getElementById('editDisplayName').value.trim();
  const email = document.getElementById('editEmail').value.trim();
  const password = document.getElementById('editPassword').value;

  if (btn) btn.disabled = true;

  try {
    if (userId) {
      // Update existing user
      const payload = {
        role,
        display_name: displayName,
        email
      };
      if (password) {
        if (password.length < 6) {
          showToast('Password must be at least 6 characters');
          if (btn) btn.disabled = false;
          return;
        }
        payload.password = password;
      }

      const res = await apiFetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || 'Failed to update user');
        return;
      }
      showToast('User account updated');
    } else {
      // Create new user
      if (!username || !password) {
        showToast('Username and password are required');
        if (btn) btn.disabled = false;
        return;
      }
      if (password.length < 6) {
        showToast('Password must be at least 6 characters');
        if (btn) btn.disabled = false;
        return;
      }

      const res = await apiFetch(`${API_BASE}/api/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username,
          password,
          role,
          display_name: displayName,
          email
        })
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || 'Failed to create user');
        return;
      }
      showToast(`User ${username} created`);
    }

    closeUserEditModal();
    loadAllUsers();
  } catch (e) {
    showToast('Network error saving user');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function toggleUserStatus(userId, currentStatus) {
  const newStatus = currentStatus === 'active' ? 'disabled' : 'active';
  const label = newStatus === 'active' ? 'enable' : 'disable';

  if (!confirm(`Are you sure you want to ${label} this account?`)) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    if (res.ok) {
      showToast(`Account ${newStatus}`);
      loadAllUsers();
    } else {
      const data = await res.json();
      showToast(data.detail || 'Failed to change account status');
    }
  } catch (e) {
    showToast('Failed to update status');
  }
}

async function promptResetUserPassword(userId, username) {
  const newPass = prompt(`Enter new password for ${username} (min 6 characters):`);
  if (!newPass) return;
  if (newPass.length < 6) {
    showToast('Password must be at least 6 characters');
    return;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: newPass })
    });
    if (res.ok) {
      showToast(`Password updated for ${username}`);
    } else {
      const data = await res.json();
      showToast(data.detail || 'Failed to reset password');
    }
  } catch (e) {
    showToast('Failed to reset password');
  }
}

async function deleteUserAccount(userId, username) {
  if (!confirm(`Permanently delete account "${username}"? This cannot be undone.`)) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}`, {
      method: 'DELETE'
    });
    if (res.ok) {
      showToast(`Account "${username}" deleted`);
      loadAllUsers();
    } else {
      const data = await res.json();
      showToast(data.detail || 'Failed to delete user');
    }
  } catch (e) {
    showToast('Failed to delete user');
  }
}

// ==========================================================================
// App Mode Switcher (Find leads vs My leads)
// ==========================================================================
function setAppMode(mode) {
  currentAppMode = mode;
  const viewSearch = document.getElementById('viewFindLeads');
  const viewCrm = document.getElementById('viewMyLeads');
  const tabSearch = document.getElementById('tabNavSearch');
  const tabCrm = document.getElementById('tabNavCrm');

  if (mode === 'search') {
    viewSearch.classList.remove('hidden');
    viewCrm.classList.add('hidden');
    tabSearch.classList.add('active');
    tabCrm.classList.remove('active');
  } else {
    viewSearch.classList.add('hidden');
    viewCrm.classList.remove('hidden');
    tabSearch.classList.remove('active');
    tabCrm.classList.add('active');
    closeLeadDrawer();
    loadCrmLeads();
  }
}

// ==========================================================================
// Engine Switcher & Filter Controls
// ==========================================================================
function switchEngine(engine) {
  currentEngine = engine;
  const depthCol = document.getElementById('scraperDepthCol');
  const pagesWrapper = document.getElementById('pagesSelectorWrapper');
  const estimateText = document.getElementById('searchCreditsEstimate');

  if (depthCol) {
    depthCol.classList.toggle('hidden', engine !== 'scraper');
  }

  if (pagesWrapper) {
    pagesWrapper.classList.toggle('hidden', engine === 'scraper');
  }

  if (estimateText) {
    if (engine === 'scraper') {
      estimateText.textContent = 'Deep search uses no Google credits (unlimited, free).';
    } else {
      estimateText.textContent = `This search will use up to ${pagesToFetch} credits (max ${pagesToFetch * 20} businesses).`;
    }
  }
}

function setPagesToFetch(n) {
  pagesToFetch = Math.max(1, Math.min(3, n));
  [1, 2, 3].forEach(num => {
    const btn = document.getElementById(`btnPages${num}`);
    if (btn) btn.classList.toggle('active', num === pagesToFetch);
  });

  const estimateText = document.getElementById('searchCreditsEstimate');
  if (estimateText && currentEngine === 'places') {
    estimateText.textContent = `This search will use up to ${pagesToFetch} credits (max ${pagesToFetch * 20} businesses).`;
  }
}

function switchToDeepSearch() {
  const radio = document.querySelector('input[name="searchEngine"][value="scraper"]');
  if (radio) radio.checked = true;
  switchEngine('scraper');

  const alertBox = document.getElementById('creditLimitAlert');
  if (alertBox) alertBox.classList.add('hidden');

  showToast('Switched to Deep search (0 credits)');
}

// Credits Meter & Settings
async function loadCreditsStatus() {
  try {
    const res = await apiFetch(`${API_BASE}/api/credits/status`);
    if (!res.ok) return;
    const data = await res.json();
    currentCreditStatus = data;
    updateCreditsMeterUI(data);
  } catch (e) {
    console.error('Failed to load credits status:', e);
  }
}

function updateCreditsMeterUI(status) {
  if (!status) return;
  const meterBtn = document.getElementById('creditsMeterBtn');
  const mainText = document.getElementById('meterMainText');
  const barFill = document.getElementById('meterBarFill');
  const subText = document.getElementById('meterSubText');

  if (mainText) {
    mainText.textContent = `Fast search: ${status.credits_left.toLocaleString()} of ${status.monthly_limit.toLocaleString()} left`;
  }

  const pct = Math.max(0, Math.min(100, status.percentage_left || 0));
  if (barFill) {
    barFill.style.width = `${pct}%`;
  }

  if (subText) {
    subText.textContent = `Resets in ${status.days_remaining} days · about ${status.daily_rate} per day`;
  }

  if (meterBtn) {
    meterBtn.classList.remove('meter-warn', 'meter-danger');
    if (pct < 10) {
      meterBtn.classList.add('meter-danger');
    } else if (pct < 25) {
      meterBtn.classList.add('meter-warn');
    }
  }
}

async function openCreditsModal() {
  closeUserMenu();
  const modal = document.getElementById('creditsModal');
  if (!modal) return;
  modal.classList.remove('hidden');

  try {
    const res = await apiFetch(`${API_BASE}/api/credits/status`);
    if (res.ok) {
      const data = await res.json();
      currentCreditStatus = data;
      updateCreditsMeterUI(data);

      document.getElementById('modalUsedCredits').textContent = data.used.toLocaleString();
      document.getElementById('modalRemainingCredits').textContent = data.credits_left.toLocaleString();
      document.getElementById('modalDailyRate').textContent = `about ${data.daily_rate}`;
      document.getElementById('modalResetDays').textContent = `${data.days_remaining} days`;

      document.getElementById('inputMonthlyLimit').value = data.monthly_limit;
      document.getElementById('inputSafetyBuffer').value = data.safety_buffer;

      const tbody = document.getElementById('creditsLogsTableBody');
      const recent = data.recent_searches || [];
      if (recent.length === 0) {
        tbody.innerHTML = `
          <tr class="empty-row">
            <td colspan="4" style="text-align: center; color: var(--color-text-muted); padding: 24px;">
              No searches recorded yet this month.
            </td>
          </tr>
        `;
      } else {
        tbody.innerHTML = recent.map(log => {
          const statusBadge = log.cached ?
            '<span class="free-badge">Saved (0 credits)</span>' :
            '<span class="quiet-hint">Fresh API call</span>';
          return `
            <tr>
              <td><span class="quiet-hint">${escapeHtml(log.search_date)}</span></td>
              <td><strong>${escapeHtml(log.query)}</strong></td>
              <td>${log.credits_used} credit${log.credits_used === 1 ? '' : 's'}</td>
              <td style="text-align: right;">${statusBadge}</td>
            </tr>
          `;
        }).join('');
      }
    }
  } catch (e) {
    console.error('Error fetching credit modal data:', e);
  }
}

function closeCreditsModal() {
  const modal = document.getElementById('creditsModal');
  if (modal) modal.classList.add('hidden');
}

async function saveCreditSettings() {
  const limit = parseInt(document.getElementById('inputMonthlyLimit').value) || 1000;
  const buffer = parseInt(document.getElementById('inputSafetyBuffer').value) || 50;

  try {
    const res = await apiFetch(`${API_BASE}/api/credits/settings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ monthly_limit: limit, safety_buffer: buffer })
    });

    if (res.ok) {
      const updated = await res.json();
      currentCreditStatus = updated;
      updateCreditsMeterUI(updated);
      showToast('Credit settings saved');
      closeCreditsModal();
    } else {
      showToast('Failed to save settings');
    }
  } catch (e) {
    showToast('Network error saving settings');
  }
}

function toggleFiltersPanel() {
  const panel = document.getElementById('filtersPanel');
  const btn = document.getElementById('btnToggleFilters');
  const txt = document.getElementById('filterToggleText');

  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !isHidden);
  txt.textContent = isHidden ? 'Hide filters' : 'Show filters';
}

function setMinReviews(count) {
  inputMinReviews.value = count;
  document.querySelectorAll('.pill-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.textContent) === count);
  });
}

// Scraper Health Check
async function checkScraperHealth() {
  const statusEl = document.getElementById('systemStatus');
  try {
    const res = await apiFetch(`${API_BASE}/api/scraper/health`);
    const data = await res.json();
    if (data.status === 'up') {
      statusEl.querySelector('.status-dot').style.backgroundColor = 'var(--color-success)';
      statusEl.querySelector('.status-label').textContent = 'Google API connected · Scraper online';
    } else {
      statusEl.querySelector('.status-dot').style.backgroundColor = 'var(--color-text-dim)';
      statusEl.querySelector('.status-label').textContent = 'Google API connected · Scraper offline';
    }
  } catch (e) {
    statusEl.querySelector('.status-dot').style.backgroundColor = 'var(--color-text-dim)';
    statusEl.querySelector('.status-label').textContent = 'Google API connected';
  }
}

// ==========================================================================
// Search Operations
// ==========================================================================
async function handleSearchSubmit(e) {
  e.preventDefault();
  const query = inputQuery.value.trim();
  if (!query) return;

  setSearchingState(true);
  qualifiedLeads = [];
  excludedLeads = [];
  renderResultsTable();

  if (currentEngine === 'places') {
    await runPlacesSearch(query);
  } else {
    await runScraperSearch(query);
  }

  setSearchingState(false);
}

async function runPlacesSearch(query) {
  showProgress('Searching Google Places...', 35);
  const creditAlert = document.getElementById('creditLimitAlert');
  if (creditAlert) creditAlert.classList.add('hidden');

  try {
    const payload = {
      api_key: '', // Handled securely on backend via .env
      query: query,
      no_website_only: checkNoWebsite.checked,
      min_reviews: parseInt(inputMinReviews.value) || 0,
      min_rating: parseFloat(selectMinRating.value) || 0,
      must_have_phone: checkMustHavePhone.checked,
      max_pages: pagesToFetch
    };

    const resp = await apiFetch(`${API_BASE}/api/places/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!resp.ok) {
      const err = await resp.json();
      if (resp.status === 429 || (err.detail && err.detail.includes('free limit'))) {
        if (creditAlert) {
          creditAlert.classList.remove('hidden');
          creditAlert.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
      throw new Error(err.detail || 'Failed to search Google Places.');
    }

    const data = await resp.json();
    showProgress('Complete', 100);

    qualifiedLeads = data.results || [];
    excludedLeads = data.excluded_results || [];

    // Cache indicator pill
    const cachedBadge = document.getElementById('cachedResultBadge');
    if (cachedBadge) {
      cachedBadge.classList.toggle('hidden', !data.from_cache);
    }

    if (data.credit_status) {
      updateCreditsMeterUI(data.credit_status);
    }

    updateResultsSummary(data.total_scanned, qualifiedLeads.length, excludedLeads.length, data.from_cache);
    renderResultsTable();
  } catch (err) {
    showToast(`Search: ${err.message}`);
    resultsSummaryText.textContent = err.message.includes('free limit') ? 'Monthly free limit reached.' : 'Search failed. Please try again.';
  } finally {
    setTimeout(hideProgress, 800);
  }
}

async function runScraperSearch(query) {
  showProgress('Geocoding location...', 15);

  try {
    const geoResp = await apiFetch(`${API_BASE}/api/geocode?place=${encodeURIComponent(query)}`);
    if (!geoResp.ok) throw new Error('Could not resolve location coordinates.');
    const geo = await geoResp.json();

    showProgress('Starting local scraper job...', 30);

    const jobResp = await apiFetch(`${API_BASE}/api/scraper/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        keywords: [query],
        lat: geo.lat,
        lon: geo.lon,
        depth: parseInt(inputDepth.value) || 10,
        email: false,
        max_time: 300
      })
    });

    if (!jobResp.ok) {
      const err = await jobResp.json();
      throw new Error(err.detail || 'Failed to start scraper job');
    }

    const job = await jobResp.json();
    const jobId = job.job_id;

    let attempts = 0;
    while (attempts < 60) {
      attempts++;
      showProgress(`Scraping Google Maps... (${attempts * 6}s)`, Math.min(30 + attempts * 1.5, 90));
      await new Promise(r => setTimeout(r, 6000));

      const stResp = await apiFetch(`${API_BASE}/api/scraper/status/${jobId}`);
      if (!stResp.ok) continue;
      const stData = await stResp.json();

      if (stData.status === 'ok') break;
      if (stData.status === 'failed') throw new Error('Local scraper reported failure.');
    }

    showProgress('Filtering results...', 95);

    const filterPayload = {
      no_website_only: checkNoWebsite.checked,
      min_reviews: parseInt(inputMinReviews.value) || 0,
      min_rating: parseFloat(selectMinRating.value) || 0,
      must_have_phone: checkMustHavePhone.checked
    };

    const resResp = await apiFetch(`${API_BASE}/api/scraper/results/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(filterPayload)
    });

    if (!resResp.ok) {
      const err = await resResp.json();
      throw new Error(err.detail || 'Failed to download scraper results');
    }

    const resData = await resResp.json();
    showProgress('Complete', 100);

    qualifiedLeads = resData.results || [];
    excludedLeads = resData.excluded_results || [];

    updateResultsSummary(resData.total_scanned, qualifiedLeads.length, excludedLeads.length);
    renderResultsTable();
  } catch (err) {
    showToast(`Scraper error: ${err.message}`);
    resultsSummaryText.textContent = 'Scrape stopped. Try checking Docker container.';
  } finally {
    setTimeout(hideProgress, 800);
  }
}

function updateResultsSummary(totalScanned, qualifiedCount, excludedCount, fromCache = false) {
  if (totalScanned === 0) {
    resultsSummaryText.textContent = '0 businesses found.';
    btnWhyHidden.classList.add('hidden');
  } else {
    const cacheNotice = fromCache ? ' (from 30-day cache, 0 credits)' : '';
    resultsSummaryText.textContent = `${qualifiedCount} of ${totalScanned} businesses match${cacheNotice}.`;
    btnWhyHidden.classList.toggle('hidden', excludedCount === 0);
  }

  const hasLeads = qualifiedCount > 0;
  btnSaveAll.disabled = !hasLeads;
  btnExportCsv.disabled = !hasLeads;
  btnCopyPhones.disabled = !hasLeads;
}

// Render Results Table
function renderResultsTable() {
  if (qualifiedLeads.length === 0) {
    leadsTableBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="5">
          <div class="empty-state">
            <p class="empty-title">No matching businesses found</p>
            <p class="empty-subtitle">Try adjusting your filters (e.g. lowering minimum reviews) or specifying a nearby city.</p>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  leadsTableBody.innerHTML = qualifiedLeads.map((lead, idx) => {
    const isSaved = crmLeads.some(l => l.id === lead.id || (l.name === lead.name && l.phone === lead.phone));
    const starStr = lead.rating ? `${lead.rating.toFixed(1)}★` : '—';
    const cleanPhone = (lead.phone || '').replace(/[^0-9+]/g, '');
    const isTopLead = (lead.reviews >= 50 && lead.rating >= 4.3);

    return `
      <tr data-lead-id="${escapeHtml(lead.id)}">
        <td>
          <span class="business-name">${escapeHtml(lead.name)}</span>
          <span class="business-sub">
            ${escapeHtml(lead.category || 'Local business')}
            ${isTopLead ? '<span class="tag-top-lead">Top lead</span>' : ''}
          </span>
        </td>
        <td>
          <div class="rating-info">
            <span class="rating-stars">${starStr}</span>
            <span class="reviews-count">(${lead.reviews} reviews)</span>
          </div>
        </td>
        <td>
          ${lead.phone ? `
            <a href="tel:${cleanPhone}" class="phone-link" title="Click to call">${escapeHtml(lead.phone)}</a>
          ` : '<span class="quiet-hint">No phone</span>'}
        </td>
        <td>
          <span class="address-text" title="${escapeHtml(lead.address)}">${escapeHtml(lead.address || '—')}</span>
          ${lead.google_maps_url ? `
            <a href="${lead.google_maps_url}" target="_blank" rel="noopener" class="maps-text-link">Google Maps ↗</a>
          ` : ''}
        </td>
        <td style="text-align: right;">
          ${isSaved ? `
            <span class="quiet-hint" style="color: var(--color-success); font-weight: 500;">✓ Saved</span>
          ` : `
            <button type="button" class="btn btn-secondary btn-sm" onclick="saveSingleLead(${idx})">
              Save
            </button>
          `}
        </td>
      </tr>
    `;
  }).join('');
}

// ==========================================================================
// Excluded Listings Modal
// ==========================================================================
function openExcludedModal() {
  const modal = document.getElementById('excludedModal');
  const tbody = document.getElementById('excludedModalTableBody');
  const subtitle = document.getElementById('modalExcludedSubtitle');

  subtitle.textContent = `${excludedLeads.length} listings were hidden based on your current filters.`;

  if (excludedLeads.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No listings were excluded.</td></tr>';
  } else {
    tbody.innerHTML = excludedLeads.map(item => `
      <tr>
        <td><strong>${escapeHtml(item.name)}</strong></td>
        <td><span class="reason-tag">${escapeHtml(item.exclusion_reason || 'Filtered out')}</span></td>
        <td>${item.reviews || 0}</td>
        <td>${item.rating ? item.rating.toFixed(1) + '★' : '—'}</td>
      </tr>
    `).join('');
  }

  modal.classList.remove('hidden');
}

function closeExcludedModal() {
  document.getElementById('excludedModal').classList.add('hidden');
}

function closeModalOnBackdrop(e, modalId) {
  if (e.target.id === modalId) {
    document.getElementById(modalId).classList.add('hidden');
  }
}

// ==========================================================================
// CRM Lead Operations
// ==========================================================================
async function loadCrmLeads() {
  try {
    const res = await apiFetch(`${API_BASE}/api/crm/leads`);
    if (!res.ok) return;
    const data = await res.json();
    crmLeads = data.leads || [];

    updateCrmStats(data.stats);
    renderCrmTable();
  } catch (e) {
    console.error('Failed to load CRM leads:', e);
  }
}

function updateCrmStats(stats) {
  if (!stats) return;
  const total = stats.total_leads || 0;
  const stages = stats.stages || {};
  const contacted = (stages['Contacted'] || 0) + (stages['Audit Sent'] || 0) + (stages['In Discussion'] || 0);
  const won = stages['Closed Won'] || 0;

  document.getElementById('navCrmCount').textContent = total;
  document.getElementById('crmQuietStats').textContent = `${total} leads · ${contacted} contacted · ${won} won`;

  // Stage tab counts
  document.getElementById('countStageAll').textContent = total;
  document.getElementById('countStageNew').textContent = stages['New Lead'] || 0;
  document.getElementById('countStageContacted').textContent = stages['Contacted'] || 0;
  document.getElementById('countStageDiscussion').textContent = (stages['In Discussion'] || 0) + (stages['Audit Sent'] || 0);
  document.getElementById('countStageWon').textContent = won;
  document.getElementById('countStageLost').textContent = stages['Not Interested'] || 0;
}

function filterCrmStage(stage) {
  currentCrmStage = stage;
  document.querySelectorAll('.stage-tab').forEach(tab => {
    tab.classList.toggle('active', tab.getAttribute('data-stage') === stage);
  });
  renderCrmTable();
}

function renderCrmTable() {
  const tbody = document.getElementById('crmTableBody');
  const searchTxt = (document.getElementById('crmSearchInput')?.value || '').toLowerCase().trim();

  let filtered = crmLeads;
  if (currentCrmStage !== 'ALL') {
    if (currentCrmStage === 'In Discussion') {
      filtered = filtered.filter(l => l.stage === 'In Discussion' || l.stage === 'Audit Sent');
    } else {
      filtered = filtered.filter(l => l.stage === currentCrmStage);
    }
  }

  if (searchTxt) {
    filtered = filtered.filter(l =>
      (l.name || '').toLowerCase().includes(searchTxt) ||
      (l.phone || '').toLowerCase().includes(searchTxt) ||
      (l.address || '').toLowerCase().includes(searchTxt)
    );
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr class="empty-row">
        <td colspan="5">
          <div class="empty-state">
            <p class="empty-title">No leads in this stage</p>
            <p class="empty-subtitle">Save leads from your search or change the active stage filter above.</p>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = filtered.map(lead => {
    const starStr = lead.rating ? `${lead.rating.toFixed(1)}★` : '—';
    const cleanPhone = (lead.phone || '').replace(/[^0-9+]/g, '');
    let stageClass = '';
    if (lead.stage === 'Closed Won') stageClass = 'won';
    if (lead.stage === 'Not Interested') stageClass = 'lost';

    return `
      <tr onclick="openLeadDrawer('${escapeHtml(lead.id)}')">
        <td>
          <span class="business-name">${escapeHtml(lead.name)}</span>
          <span class="business-sub">${escapeHtml(lead.address || '—')}</span>
        </td>
        <td>
          <div class="rating-info">
            <span class="rating-stars">${starStr}</span>
            <span class="reviews-count">(${lead.reviews} reviews)</span>
          </div>
        </td>
        <td>
          ${lead.phone ? `
            <a href="tel:${cleanPhone}" class="phone-link" onclick="event.stopPropagation()">${escapeHtml(lead.phone)}</a>
          ` : '<span class="quiet-hint">No phone</span>'}
        </td>
        <td>
          <span class="stage-badge ${stageClass}">${escapeHtml(lead.stage || 'New Lead')}</span>
        </td>
        <td style="text-align: right;">
          <button type="button" class="btn btn-subtle btn-xs" onclick="event.stopPropagation(); openLeadDrawer('${escapeHtml(lead.id)}')">
            Details ↗
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

// Save Single Lead to CRM
async function saveSingleLead(idx) {
  const lead = qualifiedLeads[idx];
  if (!lead) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/crm/leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(lead)
    });
    if (res.ok) {
      showToast(`Saved ${lead.name} to My leads`);
      await loadCrmLeads();
      renderResultsTable();
    }
  } catch (e) {
    showToast('Failed to save lead');
  }
}

// Bulk Save All Qualified Leads to CRM
async function importAllToCrm() {
  if (qualifiedLeads.length === 0) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/crm/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(qualifiedLeads)
    });

    if (res.ok) {
      const data = await res.json();
      showToast(`Saved ${data.imported_count} leads to My leads`);
      await loadCrmLeads();
      renderResultsTable();
    }
  } catch (e) {
    showToast('Failed to save all leads');
  }
}

// ==========================================================================
// Slide-Over Drawer (Lead Details, Notes & Script)
// ==========================================================================
function openLeadDrawer(leadId) {
  activeDrawerLeadId = leadId;
  const lead = crmLeads.find(l => l.id === leadId);
  if (!lead) return;

  document.getElementById('drawerLeadName').textContent = lead.name;
  document.getElementById('drawerLeadCategory').textContent = lead.category || 'Local business';
  document.getElementById('drawerReviews').textContent = `${lead.reviews || 0} reviews (${lead.rating ? lead.rating.toFixed(1) + '★' : 'No rating'})`;
  document.getElementById('drawerPhone').textContent = lead.phone || '—';
  document.getElementById('drawerAddress').textContent = lead.address || '—';

  const mapsLink = document.getElementById('drawerMapsLink');
  if (lead.google_maps_url) {
    mapsLink.href = lead.google_maps_url;
    mapsLink.style.display = 'inline-block';
  } else {
    mapsLink.style.display = 'none';
  }

  document.getElementById('drawerStageSelect').value = lead.stage || 'New Lead';
  document.getElementById('drawerNotesInput').value = lead.notes || '';
  document.getElementById('drawerFollowUpInput').value = lead.follow_up_date || '';

  // Generate outreach message
  const area = lead.address ? (lead.address.split(',')[1] || 'your area').trim() : 'your area';
  const reviewsCount = lead.reviews || 'numerous';
  const ratingStr = lead.rating ? `${lead.rating.toFixed(1)}★` : '5-star';

  const message = `Hi ${lead.name} team,

I noticed your Google profile has a strong reputation (${reviewsCount} reviews, ${ratingStr} rating) in ${area}, but there's no website linked to your listing.

You're likely losing potential clients every week who look you up and can't find your service details online.

I design fast, clean websites for local businesses. Would you be open to a 60-second video demo showing how a dedicated website could bring you more inquiries?

Best regards,
[Your Name]`;

  document.getElementById('drawerOutreachMessage').textContent = message;

  // Show drawer and backdrop
  document.getElementById('drawerBackdrop').classList.remove('hidden');
  document.getElementById('leadDrawer').classList.remove('hidden');
}

function closeLeadDrawer() {
  document.getElementById('drawerBackdrop').classList.add('hidden');
  document.getElementById('leadDrawer').classList.add('hidden');
  activeDrawerLeadId = null;
}

// Handle Drawer Stage Change
async function handleDrawerStageChange(newStage) {
  if (!activeDrawerLeadId) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/crm/leads/${encodeURIComponent(activeDrawerLeadId)}/stage`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage: newStage })
    });
    if (res.ok) {
      showToast(`Updated stage to ${newStage}`);
      loadCrmLeads();
    }
  } catch (e) {
    showToast('Failed to update stage');
  }
}

// Debounced Notes & Follow-Up Save
function handleDrawerNotesInput() {
  if (!activeDrawerLeadId) return;

  const indicator = document.getElementById('notesSavedIndicator');
  indicator.textContent = 'Saving...';
  indicator.style.color = 'var(--color-text-dim)';

  clearTimeout(notesDebounceTimer);
  notesDebounceTimer = setTimeout(async () => {
    const notes = document.getElementById('drawerNotesInput').value;
    const followUp = document.getElementById('drawerFollowUpInput').value;

    try {
      const res = await apiFetch(`${API_BASE}/api/crm/leads/${encodeURIComponent(activeDrawerLeadId)}/notes`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: notes, follow_up_date: followUp })
      });
      if (res.ok) {
        indicator.textContent = 'Saved';
        indicator.style.color = 'var(--color-success)';
        // Update local array
        const item = crmLeads.find(l => l.id === activeDrawerLeadId);
        if (item) {
          item.notes = notes;
          item.follow_up_date = followUp;
        }
      }
    } catch (e) {
      indicator.textContent = 'Error';
      indicator.style.color = 'var(--color-destructive)';
    }
  }, 600);
}

// Delete Lead from Drawer
async function deleteDrawerLead() {
  if (!activeDrawerLeadId) return;
  if (!confirm('Remove this lead from your CRM?')) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/crm/leads/${encodeURIComponent(activeDrawerLeadId)}`, {
      method: 'DELETE'
    });
    if (res.ok) {
      showToast('Lead deleted');
      closeLeadDrawer();
      loadCrmLeads();
    }
  } catch (e) {
    showToast('Failed to delete lead');
  }
}

// Outreach Message Copy & WhatsApp
function copyOutreachMessage() {
  const msg = document.getElementById('drawerOutreachMessage').textContent;
  navigator.clipboard.writeText(msg);
  showToast('Outreach message copied');
}

function launchDrawerWhatsApp() {
  if (!activeDrawerLeadId) return;
  const lead = crmLeads.find(l => l.id === activeDrawerLeadId);
  if (!lead || !lead.phone) {
    showToast('No phone number available');
    return;
  }

  const cleanPhone = lead.phone.replace(/[^0-9]/g, '');
  const text = encodeURIComponent(document.getElementById('drawerOutreachMessage').textContent);
  window.open(`https://wa.me/${cleanPhone}?text=${text}`, '_blank');
}

// ==========================================================================
// Export & Copy Helpers
// ==========================================================================
function copyAllPhones() {
  const phones = qualifiedLeads.map(l => l.phone).filter(Boolean);
  if (phones.length === 0) {
    showToast('No phone numbers found');
    return;
  }
  navigator.clipboard.writeText(phones.join('\n'));
  showToast(`Copied ${phones.length} phone numbers`);
}

async function exportCsv() {
  if (qualifiedLeads.length === 0) return;
  try {
    const resp = await apiFetch(`${API_BASE}/api/export/csv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(qualifiedLeads)
    });
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `leads_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast('CSV downloaded');
  } catch (e) {
    showToast('Export failed');
  }
}

async function exportCrmCsv() {
  if (crmLeads.length === 0) {
    showToast('No leads in CRM to export');
    return;
  }
  try {
    const resp = await apiFetch(`${API_BASE}/api/export/csv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(crmLeads)
    });
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `crm_leads_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast('CRM CSV downloaded');
  } catch (e) {
    showToast('Export failed');
  }
}

// ==========================================================================
// Toast & UI State Helpers
// ==========================================================================
function showToast(message) {
  const toast = document.getElementById('toastNotification');
  const msgEl = document.getElementById('toastMessage');

  msgEl.textContent = message;
  toast.classList.remove('hidden');

  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.add('hidden');
  }, 2400);
}

function setSearchingState(isSearching) {
  btnSubmitSearch.disabled = isSearching;
  searchSpinner.classList.toggle('hidden', !isSearching);
  btnSubmitText.textContent = isSearching ? 'Searching...' : 'Find leads';
}

function showProgress(msg, percent) {
  progressBox.classList.remove('hidden');
  progressMessage.textContent = msg;
  progressBarFill.style.width = `${percent}%`;
}

function hideProgress() {
  progressBox.classList.add('hidden');
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
