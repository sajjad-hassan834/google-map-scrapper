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
// Base API URL: Defaults to relative /api or custom Railway backend URL
const API_BASE = window.API_BASE_URL || localStorage.getItem('MAPLEAD_API_BASE') || '';

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
// Initialization
// ==========================================================================
document.addEventListener('DOMContentLoaded', () => {
  checkScraperHealth();
  loadCrmLeads();
  loadCreditsStatus();

  if (inputMinReviews) {
    inputMinReviews.addEventListener('input', (e) => {
      const val = parseInt(e.target.value) || 0;
      document.querySelectorAll('.pill-btn').forEach(b => {
        b.classList.toggle('active', parseInt(b.textContent) === val);
      });
    });
  }

  // Escape key closes side drawer and modals
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeLeadDrawer();
      closeExcludedModal();
      closeCreditsModal();
    }
  });
});

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
    const res = await fetch(`${API_BASE}/api/credits/status`);
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
  const modal = document.getElementById('creditsModal');
  if (!modal) return;
  modal.classList.remove('hidden');

  try {
    const res = await fetch(`${API_BASE}/api/credits/status`);
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
    const res = await fetch(`${API_BASE}/api/credits/settings`, {
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
    const res = await fetch(`${API_BASE}/api/scraper/health`);
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

    const resp = await fetch(`${API_BASE}/api/places/search`, {
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
    const geoResp = await fetch(`${API_BASE}/api/geocode?place=${encodeURIComponent(query)}`);
    if (!geoResp.ok) throw new Error('Could not resolve location coordinates.');
    const geo = await geoResp.json();

    showProgress('Starting local scraper job...', 30);

    const jobResp = await fetch(`${API_BASE}/api/scraper/start`, {
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

      const stResp = await fetch(`${API_BASE}/api/scraper/status/${jobId}`);
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

    const resResp = await fetch(`${API_BASE}/api/scraper/results/${jobId}`, {
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
    const res = await fetch(`${API_BASE}/api/crm/leads`);
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
    const res = await fetch(`${API_BASE}/api/crm/leads`, {
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
    const res = await fetch(`${API_BASE}/api/crm/import`, {
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
    const res = await fetch(`${API_BASE}/api/crm/leads/${encodeURIComponent(activeDrawerLeadId)}/stage`, {
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
      const res = await fetch(`${API_BASE}/api/crm/leads/${encodeURIComponent(activeDrawerLeadId)}/notes`, {
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
    const res = await fetch(`${API_BASE}/api/crm/leads/${encodeURIComponent(activeDrawerLeadId)}`, {
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
    const resp = await fetch(`${API_BASE}/api/export/csv`, {
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
    const resp = await fetch(`${API_BASE}/api/export/csv`, {
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
