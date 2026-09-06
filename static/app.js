/* StatsPack Tech Sales Agent Portal — app shell */
'use strict';

const VERSION = 'v1.5';
const $ = id => document.getElementById(id);
let ME = null, SETTINGS = {}, STAGES = [], FX = [];
let AGENT_TYPES = [], INDUSTRIES = [], EVENT_KINDS = [], COMPANY = null, MY_RULES = null;

/* ---------------------------------------------------------------- utils */
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

const usd = n => (n == null || n === '') ? '—'
  : '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const usd0 = n => (n == null || n === '') ? '—'
  : '$' + Math.round(Number(n)).toLocaleString('en-US');
const pct = n => (n == null) ? '—' : (Number(n) * 100).toFixed(1) + '%';
const money = (n, cur) => Number(n || 0).toLocaleString('en-US',
  {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ' + (cur || '');
const dt = s => s ? String(s).slice(0, 10) : '—';

function reportError(e) {
  if (e && e.outage) {
    closeModal();
    view(outageHTML(e.message));
    return;
  }
  toast(e.message, 'err');
}

function outageHTML(msg) {
  return `<div class="card"><div class="card-body" style="text-align:center;padding:44px 24px">
    <div style="font-size:44px;line-height:1;margin-bottom:14px">⛔</div>
    <h2 style="font-size:20px;margin-bottom:10px">The system is temporarily unavailable</h2>
    <p class="muted" style="max-width:520px;margin:0 auto 8px">${esc(msg)}</p>
    <p class="muted" style="max-width:520px;margin:0 auto">Nothing you were doing has been saved.
      Please try again shortly — and tell your StatsPack administrator if it continues.</p>
    <div class="btn-row" style="justify-content:center;margin-top:20px">
      <button class="btn teal" onclick="route()">Try again</button></div>
  </div></div>`;
}

function toast(msg, kind) {
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'err' ? 6000 : 3600);
}

function token() { return localStorage.getItem('sp_token') || ''; }

function signOut() {
  api('/api/logout', 'POST').catch(() => {}).finally(() => {
    localStorage.removeItem('sp_token');
    localStorage.removeItem('sp_user');
    location.replace('/login');
  });
}

async function api(path, method, body) {
  const opts = {method: method || 'GET', headers: {'Authorization': 'Bearer ' + token()}};
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  let data = {};
  try { data = await r.json(); } catch (e) { data = {}; }
  if (r.status === 503) {
    const err = new Error(data.error || 'The database is not responding.');
    err.outage = true;
    throw err;
  }
  if (r.status === 401) { localStorage.removeItem('sp_token'); location.replace('/login'); throw new Error('Signed out'); }
  if (r.status === 428) { openPasswordModal(true); throw new Error(data.error || 'Set a new password'); }
  if (!r.ok) throw new Error(data.error || ('Request failed (' + r.status + ')'));
  return data;
}

/* ---------------------------------------------------------------- modal */
function modal(title, bodyHTML, footHTML) {
  $('modalRoot').innerHTML =
    `<div class="modal-back" id="mBack"><div class="modal" role="dialog" aria-modal="true">
       <div class="modal-head"><h3>${esc(title)}</h3><button class="x" id="mX" aria-label="Close">×</button></div>
       <div class="modal-body">${bodyHTML}</div>
       ${footHTML ? `<div class="modal-foot">${footHTML}</div>` : ''}
     </div></div>`;
  const close = () => { $('modalRoot').innerHTML = ''; };
  $('mX').onclick = close;
  $('mBack').onclick = e => { if (e.target.id === 'mBack') close(); };
  document.addEventListener('keydown', function onEsc(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
  });
  const first = document.querySelector('.modal-body input, .modal-body select, .modal-body textarea');
  if (first) first.focus();
  return close;
}
const closeModal = () => { $('modalRoot').innerHTML = ''; };

function val(id) { const e = $(id); return e ? e.value.trim() : ''; }
function num(id) { const e = $(id); return e ? Number(e.value || 0) : 0; }

/* ---------------------------------------------------------------- chrome */
const NAV_ADMIN = [
  ['dashboard', '▦', 'Dashboard'],
  ['agents', '👤', 'Agents'],
  ['clients', '🏢', 'Clients'],
  ['payments', '💳', 'Payments'],
  ['commissions', '％', 'Commission'],
  ['payouts', '📤', 'Payouts'],
  ['reports', '📑', 'Reports'],
  ['settings', '⚙', 'Settings'],
  ['audit', '🕘', 'Activity log'],
  ['profile', '👤', 'My profile'],
];
const NAV_AGENT = [
  ['dashboard', '▦', 'Dashboard'],
  ['clients', '🏢', 'My clients'],
  ['payments', '💳', 'Payments received'],
  ['commissions', '％', 'My commission'],
  ['payouts', '📤', 'My payouts'],
  ['teamwork', '🤝', 'Teamwork'],
  ['reports', '📑', 'Reports'],
  ['profile', '⚙', 'My profile'],
];

function paintChrome() {
  const av = $('avatar');
  const mine = ME.avatar || ME.avatar_thumb;
  if (mine) av.innerHTML = `<img src="${esc(mine)}" alt="">`;
  else av.textContent = initials(ME.name);
  $('whoName').textContent = ME.name;
  $('whoSub').textContent = (COMPANY && COMPANY.name) || ME.country || 'StatsPack';
  $('whoRole').textContent = ME.email;
  $('verLabel').textContent = 'Agent Portal · ' + VERSION;
  let items = ME.role === 'admin' ? NAV_ADMIN.slice() : NAV_AGENT.slice();
  if (ME.role === 'admin' && ME.is_super) {
    items.splice(1, 0, ['companies', '🏛', 'Companies']);
    items.push(['system', '🩺', 'System health']);
  }
  $('nav').innerHTML =
    `<div class="nav-label">${ME.role === 'admin' ? 'Manage' : 'My work'}</div>` +
    items.map(([k, i, t]) => `<a href="#/${k}" data-k="${k}"><span class="ic">${i}</span>${t}</a>`).join('') +
    `<div class="nav-label">Account</div>
     <a href="#" id="navPw"><span class="ic">🔑</span>Change password</a>
     <a href="#" id="navOut"><span class="ic">⎋</span>Sign out</a>`;
  $('navPw').onclick = e => { e.preventDefault(); openPasswordModal(false); };
  $('navOut').onclick = e => { e.preventDefault(); signOut(); };
}

const PAGE_ICONS = {
  dashboard: '▦', agents: '👤', clients: '🏢', payments: '💳', commissions: '％',
  payouts: '📤', settings: '⚙', audit: '🕘', profile: '⚙', companies: '🏛',
  system: '🩺', reports: '📑', teamwork: '🤝',
};

function setActive(key, title) {
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.k === key));
  $('pageTitle').textContent = title;
  $('pageIcon').textContent = PAGE_ICONS[key] || '▦';
  document.title = title + ' · StatsPack Agent Portal';
}

$('burger').onclick = () => { $('sidebar').classList.add('open'); $('backdrop').classList.add('show'); };
$('backdrop').onclick = () => { $('sidebar').classList.remove('open'); $('backdrop').classList.remove('show'); };

/* ---------------------------------------------------------------- password */
function openPasswordModal(forced) {
  modal(forced ? 'Set a new password' : 'Change password', `
    ${forced ? '<p class="muted" style="margin-bottom:14px">Your account is using a temporary password. Choose your own before you continue.</p>' : ''}
    <div class="field"><label class="lbl" for="pwOld">Current password</label>
      <input class="inp" id="pwOld" type="password" autocomplete="current-password"></div>
    <div class="field"><label class="lbl" for="pwNew">New password</label>
      <input class="inp" id="pwNew" type="password" autocomplete="new-password">
      <div class="hint">At least 8 characters. Avoid anything you use elsewhere.</div></div>
    <div class="field"><label class="lbl" for="pwNew2">Repeat new password</label>
      <input class="inp" id="pwNew2" type="password" autocomplete="new-password"></div>`,
    `${forced ? '' : '<button class="btn ghost" onclick="closeModal()">Cancel</button>'}
     <button class="btn teal" id="pwSave">Save password</button>`);
  $('pwSave').onclick = async () => {
    if (val('pwNew') !== val('pwNew2')) return toast('The two new passwords do not match.', 'err');
    try {
      await api('/api/change-password', 'POST',
        {current_password: val('pwOld'), new_password: val('pwNew')});
      closeModal();
      toast('Password changed.', 'ok');
      if (forced) { setTimeout(() => location.reload(), 600); return; }
      ME.must_change_pw = false;
      route();
    } catch (e) { reportError(e); }
  };
}

/* ================================================================ VIEWS */
const view = html => { $('view').innerHTML = html; };

/* Bars rising like a chart drawing itself — reads as "analysing", not "spinning". */
const loaderHTML = (msg) =>
  `<div class="loader"><div class="loader-chart"><i></i><i></i><i></i><i></i><i></i><i></i></div>
   <div class="loader-text">${esc(msg || 'Crunching the numbers…')}</div></div>`;
const loading = (msg) => view(loaderHTML(msg));
const spinner = '<span class="loader-inline"><i></i><i></i><i></i><i></i></span>';

function busy(btn, label) {
  if (!btn) return () => {};
  const original = btn.innerHTML, wasDisabled = btn.disabled;
  btn.disabled = true;
  btn.innerHTML = spinner + ' ' + esc(label || 'Working…');
  return () => { btn.innerHTML = original; btn.disabled = wasDisabled; };
}

function initials(name) {
  return (name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
}

function avatarHTML(user, cls) {
  const c = cls || 'avatar-xs';
  const src = user && (user.avatar || user.avatar_thumb);
  return src
    ? `<span class="${c}"><img src="${esc(src)}" alt=""></span>`
    : `<span class="${c}">${esc(initials(user && user.name))}</span>`;
}

const rateTag = r => !r ? '' :
  `<span class="rate-tag ${r.source === 'agent override' ? 'override'
    : (r.source === 'company rate' ? 'company' : 'portal')}">${esc(r.source)}</span>`;

/* Resize in the browser: there is no object storage, so the image is kept as a
   small data URL in the database. 256px square is plenty for an avatar. */
function readAvatar(file) {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) return reject(new Error('Choose an image file.'));
    if (file.size > 8 * 1024 * 1024) return reject(new Error('That file is very large. Pick a smaller one.'));
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read that file.'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('That file is not a readable image.'));
      img.onload = () => {
        const side = Math.min(img.width, img.height);
        const square = (S, q) => {
          const cv = document.createElement('canvas');
          cv.width = S; cv.height = S;
          cv.getContext('2d').drawImage(img,
            (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, S, S);
          return cv.toDataURL('image/jpeg', q);
        };
        // Full size for profile pages, plus a tiny thumbnail so agent lists
        // stay light on mobile data.
        resolve({full: square(256, 0.85), thumb: square(64, 0.7)});
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function avatarEditor(user, onSaved) {
  return `<div class="avatar-edit">
      ${avatarHTML(user, 'avatar')}
      <label class="cam" for="avFile" title="Change photo">✎</label>
      <input type="file" id="avFile" accept="image/*" class="hidden">
    </div>`;
}

function wireAvatar(onDone) {
  const input = $('avFile');
  if (!input) return;
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      const img = await readAvatar(file);
      const r = await api('/api/me/avatar', 'PUT',
                          {avatar: img.full, avatar_thumb: img.thumb});
      ME.avatar = r.avatar; ME.avatar_thumb = r.avatar_thumb;
      paintChrome();
      toast('Profile picture updated.', 'ok');
      if (onDone) onDone();
    } catch (e) { reportError(e); }
    input.value = '';
  };
}

function stat(colour, icon, label, value, leftFoot, rightFoot) {
  return `<div class="stat ${colour}">
    <div class="chip">${icon}</div>
    <div class="k">${esc(label)}</div>
    <div class="v">${value}</div>
    <div class="rule"></div>
    <div class="sub"><span>${leftFoot || ''}</span><span>${rightFoot || ''}</span></div>
  </div>`;
}

function bar(frac) {
  if (frac == null) return '<span class="muted">no quota set</span>';
  const p = Math.max(0, Math.min(1, frac));
  const cls = frac >= 1 ? 'over' : (frac < 0.5 ? 'low' : '');
  return `<div style="display:flex;align-items:center;gap:8px">
    <div class="bar" style="flex:1"><i class="${cls}" style="width:${(p * 100).toFixed(0)}%"></i></div>
    <span class="num" style="font-size:12.5px">${pct(frac)}</span></div>`;
}

const EVENT_ICON = {note:'📝', stage:'⇢', call:'📞', meeting:'🤝', demo:'🖥',
                    proposal:'📄', email:'✉', 'site visit':'📍'};

function flowchart(progress, timeline) {
  const stageDates = {};
  (timeline || []).filter(e => e.kind === 'stage' && e.to_stage)
    .forEach(e => { if (!stageDates[e.to_stage]) stageDates[e.to_stage] = e.created_at; });

  const lost = progress.index === -1;
  const steps = progress.pipeline.map((name, i) => {
    let cls = '';
    if (!lost) {
      if (i < progress.index) cls = 'done';
      else if (i === progress.index) cls = progress.closed ? 'done current' : 'current';
    } else if (stageDates[name]) cls = 'done';
    const mark = cls.indexOf('done') >= 0 ? '✓' : (i + 1);
    return `<div class="flow-step ${cls}">
      <div class="dot">${mark}</div>
      <div class="nm">${esc(name)}</div>
      <div class="when">${stageDates[name] ? dt(stageDates[name]) : ''}</div>
    </div>`;
  });

  if (lost) {
    steps.push(`<div class="flow-step lost"><div class="dot">✕</div>
      <div class="nm">${esc(progress.outcome)}</div>
      <div class="when">${stageDates[progress.outcome] ? dt(stageDates[progress.outcome]) : ''}</div></div>`);
  }

  const banner = lost
    ? `<div class="flow-banner lost">This client is marked ${esc(progress.outcome)}. Any commission already earned stands; no new commission accrues.</div>`
    : (progress.closed
        ? '<div class="flow-banner won">Won — commission is driven by payments received, not by this stage.</div>'
        : '');
  return `<div class="flow">${steps.join('')}</div>${banner}`;
}

function timelineHTML(timeline, canDelete) {
  if (!timeline.length) return emptyState('Nothing logged yet', 'Add a note after your next call or meeting.');
  return `<div class="tl">${timeline.slice().reverse().map(e => {
    const isStage = e.kind === 'stage';
    const lost = isStage && (e.to_stage === 'Lost' || e.to_stage === 'Churned');
    return `<div class="tl-item ${isStage ? 'stage' : ''} ${lost ? 'stage-lost' : ''}">
      <span class="pip"></span>
      <div class="tl-head">
        <span>${EVENT_ICON[e.kind] || '•'}</span>
        <strong style="color:var(--ink)">${isStage
          ? (e.from_stage ? esc(e.from_stage) + ' → ' + esc(e.to_stage) : 'Added as ' + esc(e.to_stage))
          : esc(e.kind.charAt(0).toUpperCase() + e.kind.slice(1))}</strong>
        <span>·</span><span>${esc(e.author_name || '—')}</span>
        <span>·</span><span>${esc(e.created_at)}</span>
        ${canDelete && !isStage ? `<button class="btn ghost sm" data-delev="${e.id}">Delete</button>` : ''}
      </div>
      ${e.body ? `<div class="tl-body">${esc(e.body)}</div>` : ''}
      ${e.next_step ? `<div class="tl-next"><strong>Next:</strong> ${esc(e.next_step)}${
        e.due_date ? ' — by ' + dt(e.due_date) : ''}</div>` : ''}
    </div>`;
  }).join('')}</div>`;
}

function kindBadge(k) {
  const map = {'First payment': 'first', 'Monthly (year 1)': 'year1',
               'After month 12': 'expired', 'Voided': 'voided'};
  return `<span class="badge ${map[k] || ''}">${esc(k)}</span>`;
}

function stageBadge(s) {
  const cls = s === 'Won' ? 'won' : (s === 'Lost' || s === 'Churned' ? 'lost' : 'info');
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function emptyState(title, msg) {
  return `<div class="empty"><strong>${esc(title)}</strong>${esc(msg)}</div>`;
}

/* ---------------------------------------------------------- ADMIN: dashboard */
async function viewAdminDashboard() {
  setActive('dashboard', 'Dashboard');
  loading('Crunching the numbers…');
  const d = await api('/api/admin/overview');
  const t = d.totals;
  const maxTrend = Math.max(1, ...d.trend.map(x => x.amount));

  view(`
    <div class="stats">
      ${stat('teal','💰','Collected', usd0(t.collected_usd),
             `<b>${t.clients_won}</b> paying`, `<b>${t.clients}</b> clients`)}
      ${stat('amber','％','Agent commission', usd0(t.agent_commission_usd),
             `<b>${t.active_agents}</b> active agents`, '')}
      ${stat('slate','🏦','StatsPack share', usd0(t.statspack_share_usd),
             'after commission', '')}
      ${stat('coral','📤','Owed to agents', usd0(t.owed_usd),
             `<b>${usd0(t.paid_out_usd)}</b> paid`, `<b>${usd0(t.in_flight_usd)}</b> pending`)}
    </div>

    <div class="card">
      <div class="card-head"><h2>Collections by month</h2><span class="spacer"></span>
        <span class="muted">last ${d.trend.length} month${d.trend.length === 1 ? '' : 's'}, USD</span></div>
      <div class="card-body spark-wrap">
        ${d.trend.length ? `<div class="spark">${d.trend.map(x =>
          `<div style="height:${Math.max(3, x.amount / maxTrend * 100)}%" title="${x.month}: ${usd(x.amount)}">
             <span>${x.month.slice(2)}</span></div>`).join('')}</div>`
          : '<p class="muted">No payments recorded yet.</p>'}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Agent leaderboard</h2><span class="spacer"></span>
        <a href="#/agents" class="btn ghost sm">Manage agents</a></div>
      ${d.leaderboard.length ? `<div class="table-scroll"><table>
        <thead><tr><th>Agent</th><th>Country</th><th class="num">Clients won</th>
          <th class="num">Collected</th><th class="num">Commission</th><th class="num">Outstanding</th>
          <th style="min-width:150px">Quota attainment</th></tr></thead>
        <tbody>${d.leaderboard.map(a => `<tr>
          <td><a href="#/agent/${a.id}"><strong>${esc(a.name)}</strong></a>
            ${a.status !== 'Active' ? ' <span class="badge Suspended">Suspended</span>' : ''}</td>
          <td>${esc(a.country) || '—'}</td>
          <td class="num">${a.clients_won}</td>
          <td class="num">${usd0(a.collected_usd)}</td>
          <td class="num">${usd0(a.earned_usd)}</td>
          <td class="num">${usd0(a.outstanding_usd)}</td>
          <td>${bar(a.attainment)}</td></tr>`).join('')}</tbody></table></div>`
        : emptyState('No agents yet', 'Add your first agent to start tracking commission.')}
    </div>

    <div class="card">
      <div class="card-head"><h2>Recent payments</h2><span class="spacer"></span>
        <a href="#/payments" class="btn ghost sm">All payments</a></div>
      ${d.recent_payments.length ? `<div class="table-scroll"><table>
        <thead><tr><th>Date</th><th>Client</th><th>Type</th><th class="num">Amount</th>
          <th class="num">In USD</th><th class="num">Agent gets</th></tr></thead>
        <tbody>${d.recent_payments.map(p => `<tr>
          <td>${dt(p.paid_date)}</td><td>${esc(p.client_name)}</td>
          <td>${kindBadge(p.commission_kind)}</td>
          <td class="num">${money(p.amount, p.currency)}</td>
          <td class="num">${usd(p.amount_usd)}</td>
          <td class="num"><strong>${usd(p.agent_commission_usd)}</strong></td></tr>`).join('')}</tbody></table></div>`
        : emptyState('No payments yet', 'Record a client payment to trigger the first commission.')}
    </div>`);
}

/* ---------------------------------------------------------- ADMIN: agents */
let AGENT_SEARCH = '';
let AGENT_FILTER = {agent_type: '', status: ''};

async function viewAgents() {
  setActive('agents', 'Agents');
  loading('Loading agents…');
  const qs = new URLSearchParams(Object.assign({q: AGENT_SEARCH}, AGENT_FILTER));
  const d = await api('/api/admin/agents?' + qs.toString());
  view(`
    <div class="card">
      <div class="card-head"><h2>Sales agents</h2><span class="spacer"></span>
        <input class="inp" id="agentSearch" placeholder="Search name, email, country, type…"
               style="width:auto;min-width:230px" value="${esc(AGENT_SEARCH)}">
        <button class="btn teal" id="addAgent">Add agent</button></div>
      <div class="filterbar" style="padding-top:10px;padding-bottom:10px">
        <div class="fgroup"><label for="agFilterType">Type</label>
          <select class="inp" id="agFilterType"><option value="">All types</option>
            ${AGENT_TYPES.map(t => `<option ${AGENT_FILTER.agent_type === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}
          </select></div>
        <div class="fgroup"><label for="agFilterStatus">Status</label>
          <select class="inp" id="agFilterStatus"><option value="">All</option>
            ${['Active','Suspended'].map(t => `<option ${AGENT_FILTER.status === t ? 'selected' : ''}>${t}</option>`).join('')}
          </select></div>
        <button class="btn ghost sm" id="agClear">Clear</button>
        <span class="filter-count">${d.matched} agent(s)${
          (AGENT_SEARCH || AGENT_FILTER.agent_type || AGENT_FILTER.status) ? ' matching' : ''}</span>
      </div>
      ${d.agents.length ? `<div class="table-scroll"><table>
        <thead><tr><th>Name</th><th>Type</th>${ME.is_super ? '<th>Company</th>' : ''}<th>Commission</th><th class="num">Clients</th>
          <th class="num">Collected</th><th class="num">Earned</th><th class="num">Outstanding</th>
          <th>Status</th><th></th></tr></thead>
        <tbody>${d.agents.map(a => `<tr>
          <td><a href="#/agent/${a.id}" class="who-row">${avatarHTML(a)}<strong>${esc(a.name)}</strong></a>
            <div class="muted" style="font-size:12px">${esc(a.email)}</div></td>
          <td>${a.agent_type ? esc(a.agent_type)
            : '<span class="badge warn">not set</span>'}</td>
          ${ME.is_super ? `<td><span class="co-badge">${esc(a.company_name || '—')}</span></td>` : ''}
          <td><span style="font-size:13px">${pct(a.rules.first_rate)} / ${pct(a.rules.recurring_rate)}</span>
            <div>${rateTag(a.rules)}</div></td>
          <td class="num">${a.clients_won}/${a.clients_total}</td>
          <td class="num">${usd0(a.collected_usd)}</td>
          <td class="num">${usd0(a.earned_usd)}</td>
          <td class="num"><strong>${usd0(a.outstanding_usd)}</strong></td>
          <td><span class="badge ${a.status}">${a.status}</span></td>
          <td><a class="btn ghost sm" href="#/agent/${a.id}">Open</a></td></tr>`).join('')}</tbody></table></div>`
        : emptyState(
            (AGENT_SEARCH || AGENT_FILTER.agent_type || AGENT_FILTER.status)
              ? 'No agents match' : 'No agents yet',
            (AGENT_SEARCH || AGENT_FILTER.agent_type || AGENT_FILTER.status)
              ? 'Try a different search or clear the filters.'
              : 'Add an agent and share their sign-in details.')}
    </div>`);
  $('addAgent').onclick = agentModal;
  const box = $('agentSearch');
  if (box) {
    let timer;
    box.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        AGENT_SEARCH = box.value.trim();
        viewAgents().then(() => {
          const b = $('agentSearch');
          if (b) { b.focus(); b.setSelectionRange(b.value.length, b.value.length); }
        });
      }, 250);
    };
    box.onkeydown = e => { if (e.key === 'Escape') { box.value = ''; box.oninput(); } };
  }
  if ($('agFilterType')) $('agFilterType').onchange = () => {
    AGENT_FILTER.agent_type = val('agFilterType'); viewAgents();
  };
  if ($('agFilterStatus')) $('agFilterStatus').onchange = () => {
    AGENT_FILTER.status = val('agFilterStatus'); viewAgents();
  };
  if ($('agClear')) $('agClear').onclick = () => {
    AGENT_SEARCH = ''; AGENT_FILTER = {agent_type: '', status: ''}; viewAgents();
  };
}

async function agentModal() {
  let companies = [];
  if (ME.is_super) {
    try { companies = (await api('/api/admin/companies')).companies; } catch (e) { companies = []; }
  }
  const coPicker = companies.length ? `
      <div><label class="lbl" for="aCompany">Company</label>
        <select class="inp" id="aCompany">${companies.map(c =>
          `<option value="${c.id}" ${c.is_host ? 'selected' : ''}>${esc(c.name)}${c.is_host ? ' (host)' : ''}</option>`).join('')}
        </select></div>` : '';

  modal('Add agent', `
    <div class="grid2">
      <div><label class="lbl" for="aName">Full name</label><input class="inp" id="aName" placeholder="Thabo Mokoena"></div>
      <div><label class="lbl" for="aEmail">Email (their username)</label><input class="inp" id="aEmail" type="email" placeholder="thabo@statspack.co.ls"></div>
      <div><label class="lbl" for="aType">Agent type</label>
        <select class="inp" id="aType">${AGENT_TYPES.map(t => `<option>${esc(t)}</option>`).join('')}</select></div>
      ${coPicker}
      <div><label class="lbl" for="aPhone">Phone</label><input class="inp" id="aPhone" placeholder="+266 …"></div>
      <div><label class="lbl" for="aCountry">Country</label><input class="inp" id="aCountry" placeholder="Lesotho"></div>
      <div><label class="lbl" for="aQuota">Annual quota (USD)</label><input class="inp" id="aQuota" type="number" min="0" step="100" value="0">
        <div class="hint">Used for the attainment bar. Leave 0 if you don't set quotas.</div></div>
      <div><label class="lbl" for="aStart">Start date</label><input class="inp" id="aStart" type="date"></div>
    </div>

    <h4 style="margin:18px 0 4px;font-size:14px">Commission</h4>
    <p class="hint" style="margin-bottom:10px">Leave blank to use the standard rate.
      Fill these in only when this agent is on different terms.</p>
    <div class="grid2">
      <div><label class="lbl" for="aFirst">First payment (%)</label>
        <input class="inp" id="aFirst" type="number" min="0" max="100" step="0.5" placeholder="inherit"></div>
      <div><label class="lbl" for="aRecur">Monthly (%)</label>
        <input class="inp" id="aRecur" type="number" min="0" max="100" step="0.5" placeholder="inherit"></div>
      <div><label class="lbl" for="aWindow">Window (months)</label>
        <input class="inp" id="aWindow" type="number" min="1" max="120" step="1" placeholder="inherit"></div>
    </div>

    <div style="margin-top:14px"><label class="lbl" for="aNotes">Notes</label>
      <textarea class="inp" id="aNotes" placeholder="Territory, contract reference, anything worth remembering."></textarea></div>
    <p class="hint" style="margin-top:12px">A one-time password is generated for you to pass on. They must change it at first sign-in.</p>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="aSave">Create agent</button>`);
  $('aSave').onclick = async () => {
    const pctOrNull = id => val(id) === '' ? null : num(id) / 100;
    try {
      const r = await api('/api/admin/agents', 'POST', {
        name: val('aName'), email: val('aEmail'), phone: val('aPhone'),
        country: val('aCountry'), quota_usd: num('aQuota'), start_date: val('aStart'),
        notes: val('aNotes'), agent_type: val('aType'),
        company_id: $('aCompany') ? num('aCompany') : undefined,
        first_rate: pctOrNull('aFirst'), recurring_rate: pctOrNull('aRecur'),
        window_months: val('aWindow') === '' ? null : num('aWindow')
      });
      modal('Agent created', `
        <p>Give these details to the agent. The password is shown once and only once.</p>
        <div class="copybox">${esc(val('aEmail'))}<br>${esc(r.temp_password)}</div>
        <p class="hint">They will be asked to choose their own password the first time they sign in.</p>`,
        `<button class="btn teal" onclick="closeModal();route()">Done</button>`);
    } catch (e) { reportError(e); }
  };
}

/* ---------------------------------------------------- ADMIN: agent progress */
async function viewAgentDetail(id) {
  setActive('agents', 'Agent progress');
  loading('Building the scorecard…');
  const d = await api('/api/admin/agents/' + id + '/progress');
  const m = d.metrics, a = d.agent;
  setActive('agents', a.name);

  view(`
    <div class="card">
      <div class="card-head">
        ${avatarHTML(a, 'avatar-lg')}
        <h2>${esc(a.name)} <span class="badge ${a.status}">${a.status}</span>
          ${a.agent_type ? `<div class="muted" style="font-size:13px;font-weight:400">${esc(a.agent_type)}</div>` : ''}</h2>
        <span class="spacer"></span>
        <button class="btn ghost sm" id="edit">Edit details</button>
        <button class="btn ghost sm" id="editRates">Commission</button>
        <button class="btn ghost sm" id="resetPw">Reset password</button>
        <button class="btn ${a.status === 'Active' ? 'danger' : 'teal'} sm" id="toggle">
          ${a.status === 'Active' ? 'Suspend' : 'Reactivate'}</button>
      </div>
      <div class="card-body">
        <div class="grid2">
          <div><span class="lbl">Email</span>${esc(a.email)}</div>
          <div><span class="lbl">Phone</span>${esc(a.phone) || '—'}</div>
          <div><span class="lbl">Country</span>${esc(a.country) || '—'}</div>
          <div><span class="lbl">Started</span>${dt(a.start_date)}</div>
          <div><span class="lbl">Commission</span>
            ${pct(d.rules.first_rate)} first / ${pct(d.rules.recurring_rate)} monthly
            · ${d.rules.window_months} months ${rateTag(d.rules)}</div>
        </div>
        ${d.notes ? `<div style="margin-top:14px"><span class="lbl">Notes</span>${esc(d.notes)}</div>` : ''}
      </div>
    </div>

    <div class="stats">
      ${stat('teal','💰','Collected', usd0(m.collected_usd),
             `<b>${m.clients_won}</b> won`, `of <b>${m.clients_total}</b>`)}
      ${stat('amber','％','Commission earned', usd0(m.earned_usd),
             `<b>${usd0(m.first_payment_usd)}</b> first`, `<b>${usd0(m.recurring_usd)}</b> monthly`)}
      ${stat('coral','📤','Outstanding', usd0(m.outstanding_usd),
             `<b>${usd0(m.paid_out_usd)}</b> paid`, '')}
      ${stat('green','📈','Open pipeline', usd0(m.pipeline_usd), 'monthly value', 'not yet won')}
    </div>

    <div class="card">
      <div class="card-head"><h2>Performance</h2></div>
      <div class="card-body">
        <div class="grid2">
          <div><span class="lbl">Quota attainment${a.quota_usd ? ' (of ' + usd0(a.quota_usd) + ')' : ''}</span>${bar(m.attainment)}</div>
          <div><span class="lbl">Win rate</span>${m.win_rate == null ? '<span class="muted">no decided deals yet</span>' : pct(m.win_rate)}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Progress notes</h2><span class="spacer"></span>
        <button class="btn teal sm" id="addNote">Add note</button></div>
      ${d.reviews.length ? `<div class="card-body">${d.reviews.map(r => `
        <div style="border-left:3px solid var(--teal);padding:2px 0 2px 13px;margin-bottom:15px">
          <div class="muted" style="font-size:12px">${dt(r.created_at)} · ${esc(r.author_name || 'admin')}
            ${r.period ? '· ' + esc(r.period) : ''} · rated ${r.rating}/5</div>
          <div style="margin-top:3px;white-space:pre-wrap">${esc(r.body)}</div>
          <button class="btn ghost sm" style="margin-top:7px" data-del="${r.id}">Delete</button>
        </div>`).join('')}</div>`
        : emptyState('No notes yet', 'Record a coaching conversation or a monthly review.')}
    </div>

    <div class="card">
      <div class="card-head"><h2>Their clients</h2></div>
      ${d.clients.length ? `<div class="table-scroll"><table>
        <thead><tr><th>Client</th><th>Country</th><th>Stage</th><th class="num">Monthly value</th>
          <th class="num">Payments</th><th class="num">Collected</th></tr></thead>
        <tbody>${d.clients.map(c => `<tr>
          <td><a href="#/client/${c.id}"><strong>${esc(c.name)}</strong></a></td>
          <td>${esc(c.country) || '—'}</td><td>${stageBadge(c.stage)}</td>
          <td class="num">${money(c.monthly_value, c.currency)}</td>
          <td class="num">${c.payments_count}</td>
          <td class="num">${usd0(c.collected_usd)}</td></tr>`).join('')}</tbody></table></div>`
        : emptyState('No clients yet', 'This agent has not logged any clients.')}
    </div>

    ${paymentsTable(d.payments, 'Payments on their clients', false)}

    <div class="card">
      <div class="card-head"><h2>Payouts</h2><span class="spacer"></span>
        <button class="btn teal sm" id="addPayout">Record payout</button></div>
      ${payoutRows(d.payouts, false)}
    </div>

    ${ME.is_super && !a.is_super && a.id !== ME.id ? `<div class="danger">
      <h3>Permanently delete this agent</h3>
      <p>Erases ${esc(a.name)} and every client, payment, payout and note attached to them.
        This cannot be undone and it is not a suspension — if you only want to stop them
        signing in, use Suspend above instead.</p>
      <div class="btn-row">
        <button class="btn ghost" id="exportAgent">Download their records first</button>
        <button class="btn danger" id="purgeAgent">Delete permanently</button>
      </div>
    </div>` : ''}`);

  $('edit').onclick = () => editAgentModal(a, d.notes);
  $('editRates').onclick = () => ratesModal(a, d.rules);
  $('resetPw').onclick = async () => {
    if (!confirm('Reset this agent\u2019s password? Their current password stops working immediately.')) return;
    try {
      const r = await api('/api/admin/agents/' + id + '/reset-password', 'POST', {});
      modal('New temporary password', `
        <p>Pass this to ${esc(a.name)}. It is shown once.</p>
        <div class="copybox">${esc(a.email)}<br>${esc(r.temp_password)}</div>`,
        `<button class="btn teal" onclick="closeModal()">Done</button>`);
    } catch (e) { reportError(e); }
  };
  $('toggle').onclick = async () => {
    const next = a.status === 'Active' ? 'Suspended' : 'Active';
    try {
      await api('/api/admin/agents/' + id, 'PATCH', {status: next});
      toast('Agent ' + (next === 'Active' ? 'reactivated' : 'suspended') + '.', 'ok');
      route();
    } catch (e) { reportError(e); }
  };
  $('addNote').onclick = () => noteModal(id);
  $('addPayout').onclick = () => payoutModal(id, a.name, m.outstanding_usd);
  wirePayoutButtons();
  if ($('exportAgent')) $('exportAgent').onclick = () => downloadAgent(id, a.name);
  if ($('purgeAgent')) $('purgeAgent').onclick = () => purgeAgentModal(id, a.name);
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this note?')) return;
    await api('/api/admin/reviews/' + b.dataset.del, 'DELETE', {});
    toast('Note deleted.', 'ok'); route();
  });
}

async function downloadAgent(id, name) {
  try {
    const data = await api('/api/admin/agents/' + id + '/export');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
    a.download = 'statspack-agent-' + name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()
                 + '-' + new Date().toISOString().slice(0, 10) + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
    toast('Records downloaded.', 'ok');
    return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}

async function purgeAgentModal(id, name) {
  let f;
  try {
    f = (await api('/api/admin/agents/' + id + '/footprint')).footprint;
  } catch (e) { return toast(e.message, 'err'); }

  modal('Permanently delete ' + name, `
    <div class="danger" style="margin-bottom:0">
      <h3>This erases records you may be required to keep</h3>
      <p>Commission history is a financial record. Once deleted it is gone from the database
         entirely — there is no undo, and a backup taken afterwards will not contain it.</p>
    </div>

    <p style="margin:16px 0 8px"><strong>About to be destroyed:</strong></p>
    <div class="danger-list"><ul>
      <li><strong>${f.clients}</strong> client record(s)${
        f.client_names.length ? ' — ' + f.client_names.map(esc).join(', ')
          + (f.clients > f.client_names.length ? ', …' : '') : ''}</li>
      <li><strong>${f.payments}</strong> payment(s), totalling <strong>${usd(f.collected_usd)}</strong> collected</li>
      <li><strong>${f.payouts_paid}</strong> payout(s) already marked paid, worth <strong>${usd(f.payouts_paid_usd)}</strong></li>
      <li><strong>${f.reviews}</strong> progress note(s), plus the agent's login</li>
    </ul></div>

    <p class="muted" style="margin-bottom:14px">A line will remain in the activity log recording
      that you did this, and what was removed. Nothing else survives.</p>

    <label class="lbl" for="pgName">Type <strong>${esc(name)}</strong> to confirm</label>
    <input class="inp confirm-input" id="pgName" placeholder="${esc(name)}" autocomplete="off">`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn ghost" id="pgExport">Download records</button>
     <button class="btn danger" id="pgGo" disabled>Delete permanently</button>`);

  const nameInput = $('pgName'), goBtn = $('pgGo');
  nameInput.oninput = () => {
    goBtn.disabled = nameInput.value.trim().toLowerCase() !== name.trim().toLowerCase();
  };
  $('pgExport').onclick = () => downloadAgent(id, name);
  goBtn.onclick = async () => {
    const done = busy(goBtn, 'Deleting…');
    try {
      const r = await api('/api/admin/agents/' + id + '/purge', 'DELETE',
                          {confirm_name: nameInput.value.trim()});
      closeModal();
      toast(`${name} deleted — ${r.deleted.clients} client(s), `
            + `${r.deleted.payments} payment(s) removed.`, 'ok');
      location.hash = '#/agents';
      route();
    } catch (e) { reportError(e); done(); }
  };
}

async function overpaidModal(agentId, agentName) {
  modal('Nothing outstanding for ' + agentName, loaderHTML('Working out why…'), '');
  let d;
  try { d = await api('/api/admin/agents/' + agentId + '/reconcile'); }
  catch (e) { closeModal(); return reportError(e); }
  const p = d.position;

  modal('Nothing outstanding for ' + agentName, `
    <div class="danger" style="border-color:#E8D5A8;background:#FFFBF0;margin-bottom:0">
      <h3 style="color:#8A6413">Already paid ${usd(d.overpaid_usd)} more than earned</h3>
      <p style="color:#7A5A12">This is not an error, and you do not normally need to do
        anything. The ${usd(d.overpaid_usd)} carries forward and comes off their next
        commission automatically.</p>
    </div>

    <h4 style="margin:18px 0 8px;font-size:14px">How the balance stands</h4>
    <dl class="kv">
      <dt>Commission earned</dt><dd>${usd(p.earned_usd)}</dd>
      <dt>Already paid out</dt><dd>${usd(p.paid_out_usd)}</dd>
      <dt>Payouts in progress</dt><dd>${usd(p.in_flight_usd)}</dd>
      <dt>Net position</dt><dd><strong style="color:#A8412A">${usd(p.outstanding_usd)}</strong></dd>
      <dt>Current rate</dt><dd>${pct(d.rules.first_rate)} first / ${pct(d.rules.recurring_rate)}
        monthly ${rateTag(d.rules)}</dd>
    </dl>

    ${d.reasons.length ? `<h4 style="margin:18px 0 8px;font-size:14px">Why this happened</h4>
      <ul style="margin-left:18px;font-size:13.5px;color:var(--ink-soft)">
        ${d.reasons.map(r => `<li style="margin-bottom:5px">${esc(r)}</li>`).join('')}
      </ul>` : ''}

    <p class="hint" style="margin-top:16px">If you decide to pay them anyway and write off the
      difference, use Pay anyway — it records a payout above what has been earned, and the
      activity log keeps a note of it.</p>`,
    `<button class="btn ghost" onclick="closeModal()">Close</button>
     <button class="btn danger" id="ovForce">Pay anyway</button>`);

  $('ovForce').onclick = () => {
    closeModal();
    forcePayoutModal(agentId, agentName, d.overpaid_usd);
  };
}

function forcePayoutModal(agentId, agentName, overpaid) {
  modal('Pay ' + agentName + ' anyway', `
    <p class="muted" style="margin-bottom:14px">They are already ${usd(overpaid)} ahead. Anything
      you record here is on top of that, and will not be recovered automatically.</p>
    <div class="grid2">
      <div><label class="lbl" for="fpAmount">Amount (USD)</label>
        <input class="inp" id="fpAmount" type="number" min="0.01" step="0.01"></div>
      <div><label class="lbl" for="fpPeriod">Period</label>
        <input class="inp" id="fpPeriod" placeholder="August 2026"></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="fpNote">Reason (recorded in the log)</label>
      <textarea class="inp" id="fpNote" placeholder="Agreed to honour the original rate for this period."></textarea></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn danger" id="fpSave">Record payout</button>`);
  $('fpSave').onclick = async () => {
    const done = busy($('fpSave'), 'Saving…');
    try {
      await api('/api/payouts', 'POST', {
        agent_id: agentId, amount_usd: num('fpAmount'), period_label: val('fpPeriod'),
        note: val('fpNote'), force: true});
      closeModal(); toast('Payout recorded.', 'ok'); route();
    } catch (e) { reportError(e); done(); }
  };
}

function ratesModal(a, rules) {
  const asPct = v => v === null || v === undefined ? '' : (v * 100).toFixed(1);
  modal('Commission for ' + a.name, `
    <p class="muted" style="margin-bottom:14px">Currently
      <strong>${pct(rules.first_rate)}</strong> on the first payment and
      <strong>${pct(rules.recurring_rate)}</strong> monthly for
      <strong>${rules.window_months}</strong> months — ${esc(rules.source)}.</p>
    <p class="hint" style="margin-bottom:14px">Clear a field to fall back to the standard rate.
      Changing these recalculates this agent's existing commission, so change them only when
      their agreement changes.</p>
    <div class="grid2">
      <div><label class="lbl" for="rFirst">First payment (%)</label>
        <input class="inp" id="rFirst" type="number" min="0" max="100" step="0.5"
          placeholder="inherit" value="${asPct(a.first_rate)}"></div>
      <div><label class="lbl" for="rRecur">Monthly (%)</label>
        <input class="inp" id="rRecur" type="number" min="0" max="100" step="0.5"
          placeholder="inherit" value="${asPct(a.recurring_rate)}"></div>
      <div><label class="lbl" for="rWindow">Window (months)</label>
        <input class="inp" id="rWindow" type="number" min="1" max="120" step="1"
          placeholder="inherit" value="${a.window_months === null || a.window_months === undefined ? '' : a.window_months}"></div>
      <div><label class="lbl" for="rType">Agent type</label>
        <select class="inp" id="rType">${AGENT_TYPES.map(t =>
          `<option ${a.agent_type === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>
    </div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="rSave">Save</button>`);
  $('rSave').onclick = async () => {
    const done = busy($('rSave'), 'Saving…');
    try {
      await api('/api/admin/agents/' + a.id, 'PATCH', {
        first_rate: val('rFirst') === '' ? '' : num('rFirst') / 100,
        recurring_rate: val('rRecur') === '' ? '' : num('rRecur') / 100,
        window_months: val('rWindow') === '' ? '' : num('rWindow'),
        agent_type: val('rType')});
      closeModal(); toast('Commission updated.', 'ok'); route();
    } catch (e) { reportError(e); done(); }
  };
}

function editAgentModal(a, notes) {
  modal('Edit ' + a.name, `
    <div class="grid2">
      <div><label class="lbl" for="eName">Full name</label><input class="inp" id="eName" value="${esc(a.name)}"></div>
      <div><label class="lbl" for="eEmail">Email</label><input class="inp" id="eEmail" value="${esc(a.email)}"></div>
      <div><label class="lbl" for="ePhone">Phone</label><input class="inp" id="ePhone" value="${esc(a.phone)}"></div>
      <div><label class="lbl" for="eCountry">Country</label><input class="inp" id="eCountry" value="${esc(a.country)}"></div>
      <div><label class="lbl" for="eQuota">Annual quota (USD)</label><input class="inp" id="eQuota" type="number" min="0" step="100" value="${a.quota_usd}"></div>
      <div><label class="lbl" for="eStart">Start date</label><input class="inp" id="eStart" type="date" value="${esc(a.start_date)}"></div>
      <div><label class="lbl" for="eType">Agent type</label>
        <select class="inp" id="eType">
          <option value="">— not set —</option>
          ${AGENT_TYPES.map(t => `<option ${a.agent_type === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}
        </select>
        ${!a.agent_type ? '<div class="hint">This agent predates agent types. Set one now.</div>' : ''}</div>
    </div>

    <h4 style="margin:18px 0 4px;font-size:14px">Commission</h4>
    <p class="hint" style="margin-bottom:10px">Blank means they follow the standard rate.</p>
    <div class="grid2">
      <div><label class="lbl" for="eFirst">First payment (%)</label>
        <input class="inp" id="eFirst" type="number" min="0" max="100" step="0.5" placeholder="inherit"
          value="${a.first_rate === null || a.first_rate === undefined ? '' : (a.first_rate * 100).toFixed(1)}"></div>
      <div><label class="lbl" for="eRecur">Monthly (%)</label>
        <input class="inp" id="eRecur" type="number" min="0" max="100" step="0.5" placeholder="inherit"
          value="${a.recurring_rate === null || a.recurring_rate === undefined ? '' : (a.recurring_rate * 100).toFixed(1)}"></div>
      <div><label class="lbl" for="eWindow">Window (months)</label>
        <input class="inp" id="eWindow" type="number" min="1" max="120" step="1" placeholder="inherit"
          value="${a.window_months === null || a.window_months === undefined ? '' : a.window_months}"></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="eNotes">Notes</label>
      <textarea class="inp" id="eNotes">${esc(notes || '')}</textarea></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="eSave">Save changes</button>`);
  $('eSave').onclick = async () => {
    try {
      await api('/api/admin/agents/' + a.id, 'PATCH', {
        name: val('eName'), email: val('eEmail'), phone: val('ePhone'), country: val('eCountry'),
        quota_usd: num('eQuota'), start_date: val('eStart'), notes: val('eNotes'),
        agent_type: val('eType'),
        first_rate: val('eFirst') === '' ? '' : num('eFirst') / 100,
        recurring_rate: val('eRecur') === '' ? '' : num('eRecur') / 100,
        window_months: val('eWindow') === '' ? '' : num('eWindow')
      });
      closeModal(); toast('Agent updated.', 'ok'); route();
    } catch (e) { reportError(e); }
  };
}

function noteModal(agentId) {
  modal('Add progress note', `
    <div class="grid2">
      <div><label class="lbl" for="nPeriod">Period</label><input class="inp" id="nPeriod" placeholder="August 2026"></div>
      <div><label class="lbl" for="nRating">Rating</label>
        <select class="inp" id="nRating">
          <option value="5">5 — excellent</option><option value="4">4 — strong</option>
          <option value="3" selected>3 — on track</option><option value="2">2 — needs support</option>
          <option value="1">1 — serious concern</option></select></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="nBody">What happened</label>
      <textarea class="inp" id="nBody" placeholder="Be specific: what you observed, what you agreed, what happens next."></textarea></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="nSave">Save note</button>`);
  $('nSave').onclick = async () => {
    try {
      await api('/api/admin/reviews', 'POST', {
        agent_id: agentId, period: val('nPeriod'), rating: num('nRating'), body: val('nBody')});
      closeModal(); toast('Note saved.', 'ok'); route();
    } catch (e) { reportError(e); }
  };
}

async function viewCompanies() {
  setActive('companies', 'Companies');
  loading('Loading companies…');
  const d = await api('/api/admin/companies');

  view(`<div class="card">
    <div class="card-head"><h2>Companies on the platform</h2><span class="spacer"></span>
      <button class="btn teal" id="addCo">Register company</button></div>
    <div class="card-body" style="padding-bottom:6px">
      <p class="muted">Each company manages its own agents and clients and cannot see anyone
        else's. Their administrator signs in at the same address you do.</p>
    </div>
    <div class="table-scroll"><table>
      <thead><tr><th>Company</th><th>Country</th><th class="num">Admins</th><th class="num">Agents</th>
        <th class="num">Clients</th><th class="num">Collected</th><th class="num">Commission</th>
        <th>Commission rules</th><th>Status</th><th></th></tr></thead>
      <tbody>${d.companies.map(c => `<tr>
        <td><strong>${esc(c.name)}</strong>
          ${c.is_host ? '<span class="co-badge host">host</span>' : ''}
          ${c.contact_email ? `<div class="muted" style="font-size:12px">${esc(c.contact_email)}</div>` : ''}</td>
        <td>${esc(c.country) || '—'}</td>
        <td class="num">${c.admins}</td><td class="num">${c.agents}</td><td class="num">${c.clients}</td>
        <td class="num">${usd0(c.collected_usd)}</td>
        <td class="num">${usd0(c.commission_usd)}</td>
        <td style="font-size:13px">${pct(c.first_rate)} / ${pct(c.recurring_rate)} · ${c.window_months}m</td>
        <td><span class="badge ${c.status}">${c.status}</span></td>
        <td><button class="btn ghost sm" data-co="${c.id}">Edit</button></td>
      </tr>`).join('')}</tbody></table></div>
  </div>`);

  $('addCo').onclick = () => companyModal(null);
  document.querySelectorAll('[data-co]').forEach(b => b.onclick =
    () => companyModal(d.companies.find(c => c.id === Number(b.dataset.co))));
}

function companyModal(existing) {
  // Guard against a click Event arriving here: an existing company has an id,
  // anything else is a new registration.
  const editing = !!(existing && typeof existing === 'object' && existing.id);
  const c = editing ? existing : {};
  modal(editing ? 'Edit ' + c.name : 'Register a company', `
    <div class="grid2">
      <div><label class="lbl" for="coName">Company name</label>
        <input class="inp" id="coName" value="${esc(c.name || '')}" placeholder="Kalahari Systems"></div>
      <div><label class="lbl" for="coCountry">Country</label>
        <input class="inp" id="coCountry" value="${esc(c.country || '')}" placeholder="Botswana"></div>
      <div><label class="lbl" for="coEmail">Contact email</label>
        <input class="inp" id="coEmail" type="email" value="${esc(c.contact_email || '')}"></div>
      <div><label class="lbl" for="coPhone">Contact phone</label>
        <input class="inp" id="coPhone" value="${esc(c.contact_phone || '')}"></div>
    </div>

    <h4 style="margin:18px 0 4px;font-size:14px">Commission rules for their agents</h4>
    <p class="hint" style="margin-bottom:10px">Their default. Individual agents can still be
      put on different terms.</p>
    <div class="grid2">
      <div><label class="lbl" for="coFirst">First payment (%)</label>
        <input class="inp" id="coFirst" type="number" min="0" max="100" step="0.5"
          value="${((c.first_rate !== undefined ? c.first_rate : 0.6) * 100).toFixed(1)}"></div>
      <div><label class="lbl" for="coRecur">Monthly (%)</label>
        <input class="inp" id="coRecur" type="number" min="0" max="100" step="0.5"
          value="${((c.recurring_rate !== undefined ? c.recurring_rate : 0.1) * 100).toFixed(1)}"></div>
      <div><label class="lbl" for="coWindow">Window (months)</label>
        <input class="inp" id="coWindow" type="number" min="1" max="120" step="1"
          value="${c.window_months || 12}"></div>
      ${editing && !c.is_host ? `<div><label class="lbl" for="coStatus">Status</label>
        <select class="inp" id="coStatus">
          <option ${c.status === 'Active' ? 'selected' : ''}>Active</option>
          <option ${c.status === 'Suspended' ? 'selected' : ''}>Suspended</option>
        </select><div class="hint">Suspending signs out everyone in the company.</div></div>` : ''}
    </div>

    ${editing ? '' : `
      <h4 style="margin:18px 0 4px;font-size:14px">Their administrator</h4>
      <p class="hint" style="margin-bottom:10px">This person creates and manages their agents.
        You will get a one-time password to pass on.</p>
      <div class="grid2">
        <div><label class="lbl" for="coAdminName">Name</label>
          <input class="inp" id="coAdminName" placeholder="Kagiso Seretse"></div>
        <div><label class="lbl" for="coAdminEmail">Email</label>
          <input class="inp" id="coAdminEmail" type="email" placeholder="kagiso@kalahari.co.bw"></div>
      </div>`}

    <div style="margin-top:14px"><label class="lbl" for="coNotes">Notes</label>
      <textarea class="inp" id="coNotes">${esc(c.notes || '')}</textarea></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="coSave">${editing ? 'Save changes' : 'Register company'}</button>`);

  $('coSave').onclick = async () => {
    const done = busy($('coSave'), 'Saving…');
    const payload = {
      name: val('coName'), country: val('coCountry'), contact_email: val('coEmail'),
      contact_phone: val('coPhone'), notes: val('coNotes'),
      first_rate: num('coFirst') / 100, recurring_rate: num('coRecur') / 100,
      window_months: num('coWindow'),
    };
    if ($('coStatus')) payload.status = val('coStatus');
    try {
      if (editing) {
        if (!c.id) throw new Error('Missing company reference — reload and try again.');
        await api('/api/admin/companies/' + c.id, 'PATCH', payload);
        closeModal(); toast('Company updated.', 'ok'); route();
      } else {
        payload.admin_name = val('coAdminName');
        payload.admin_email = val('coAdminEmail');
        const r = await api('/api/admin/companies', 'POST', payload);
        modal('Company registered', `
          <p>Give these details to their administrator. The password is shown once.</p>
          <div class="copybox">${esc(r.admin_email)}<br>${esc(r.temp_password)}</div>
          <p class="hint">They will choose their own password at first sign-in, then create
            their agents themselves.</p>`,
          `<button class="btn teal" onclick="closeModal();route()">Done</button>`);
      }
    } catch (e) { reportError(e); done(); }
  };
}

function meter(stats) {
  if (!stats || stats.used_mb === null || stats.used_mb === undefined)
    return '<p class="muted">Size unavailable.</p>';
  const pct = stats.pct || 0;
  const cls = pct >= 90 ? 'crit' : (pct >= 75 ? 'warn' : '');
  return `<div class="meter"><i class="${cls}" style="width:${Math.min(100, pct)}%"></i></div>
    <div class="dir-meta"><b>${stats.used_mb.toFixed(1)} MB</b> used ·
      <b>${(stats.free_mb || 0).toFixed(1)} MB</b> free of ${stats.quota_mb} MB
      (${pct.toFixed(1)}%)</div>`;
}

async function viewSystem() {
  setActive('system', 'System health');
  loading('Checking the databases…');
  const d = await api('/api/admin/system');
  const m = d.mirror, sb = d.standby;

  const standbyCard = !sb.configured
    ? `<div class="db-card"><h3><span class="dot-off"></span>Standby database</h3>
         <div class="host">Not configured</div>
         <p class="muted">Set <strong>MIRROR_DATABASE_URL</strong> in Render to a Supabase
           connection string and redeploy. The primary keeps every read and write; the standby
           receives a full copy on a timer.</p></div>`
    : `<div class="db-card">
         <h3><span class="${sb.reachable ? 'dot-ok' : 'dot-bad'}"></span>Standby${
           m.target ? ' — ' + esc(m.target) : ''}</h3>
         <div class="host">${esc(sb.host || '')}</div>
         ${sb.reachable ? meter(sb)
           : `<p style="color:#A8412A;font-weight:600">Cannot be reached</p>
              <p class="muted" style="font-size:12.5px">${esc(sb.error || '')}</p>`}
         <div class="dir-meta" style="margin-top:10px">
           Last copy: <b>${m.last_success ? esc(m.last_success) : 'never'}</b>${
             m.age_minutes !== undefined ? ` (${m.age_minutes < 60
               ? Math.round(m.age_minutes) + ' min ago'
               : (m.age_minutes / 60).toFixed(1) + ' hours ago'})` : ''}<br>
           ${m.rows ? `<b>${m.rows.toLocaleString()}</b> rows in ${m.duration_s}s · ` : ''}
           every ${m.interval_minutes} min · ${m.failures} failure(s)
         </div>
         ${m.last_error ? `<p class="muted" style="color:#A8412A;margin-top:8px;font-size:12.5px">
           Last error: ${esc(m.last_error)}</p>` : ''}
       </div>`;

  view(`
    ${d.incidents.length ? `<div class="card"><div class="card-head">
      <h2>Open incidents (${d.incidents.length})</h2></div><div class="card-body">
      ${d.incidents.map(i => `<div class="incident ${esc(i.severity)}">
        <h4>${esc(i.title)}</h4>
        <p>${esc(i.detail)}</p>
        <p style="margin-top:6px">First seen ${esc(i.first_seen)} · last ${esc(i.last_seen)}
          · ${i.occurrences} occurrence(s)</p>
        <button class="btn ghost sm" style="margin-top:8px" data-res="${i.id}">Mark resolved</button>
      </div>`).join('')}</div></div>` : `<div class="card"><div class="card-body">
      <strong style="color:#1C6E64">✔ No open incidents.</strong>
      <span class="muted"> Both databases are behaving.</span></div></div>`}

    <div class="db-grid">
      <div class="db-card">
        <h3><span class="dot-ok"></span>Primary — live database</h3>
        <div class="host">${esc(d.primary.engine)} · every read and write goes here</div>
        ${meter(d.primary)}
        <div class="dir-meta" style="margin-top:10px">
          ${(d.primary.tables || []).slice(0, 5).map(t =>
            `${esc(t.name)}: <b>${(t.rows ?? 0).toLocaleString()}</b> rows`).join(' · ')}
        </div>
      </div>
      ${standbyCard}
    </div>

    ${d.drift ? `<div class="card">
      <div class="card-head"><h2>Row counts, primary vs standby</h2><span class="spacer"></span>
        <button class="btn teal sm" id="runMirror">Copy to standby now</button></div>
      <div class="table-scroll"><table>
        <thead><tr><th>Table</th><th class="num">Primary</th><th class="num">Standby</th>
          <th>In step</th></tr></thead>
        <tbody>${Object.keys(d.drift).map(k => {
          const r = d.drift[k], same = r.primary === r.standby;
          return `<tr><td>${esc(k)}</td><td class="num">${r.primary}</td>
            <td class="num">${r.standby === null ? '—' : r.standby}</td>
            <td>${same ? '<span class="badge won">matched</span>'
              : '<span class="badge warn">behind — run a copy</span>'}</td></tr>`;
        }).join('')}</tbody></table></div>
      <div class="card-body"><p class="muted">A standby that is behind is normal between
        scheduled copies. It only matters if it stays behind after a manual run.</p></div>
    </div>` : (sb.configured ? '' : '')}

    ${sb.configured ? `<div class="card">
      <div class="card-head"><h2>If the primary died right now</h2></div>
      <div class="card-body">
        ${m.last_success ? `
          <p style="font-size:15px">You would lose everything recorded since
            <strong>${esc(m.last_success)} UTC</strong>${m.age_minutes !== undefined
              ? ` — about <strong>${m.age_minutes < 60
                  ? Math.round(m.age_minutes) + ' minutes'
                  : (m.age_minutes / 60).toFixed(1) + ' hours'}</strong> of work` : ''}.</p>
          <p class="muted" style="margin-top:8px">Everything before that is on the standby and
            ready to run. To shrink this gap, lower <strong>MIRROR_INTERVAL_MINUTES</strong> in
            Render, or press <em>Copy to standby now</em> after recording payments.</p>`
        : `<p style="color:#A8412A;font-weight:600">The standby has never been copied to —
            you would lose everything. Press <em>Copy to standby now</em>.</p>`}
        <div class="btn-row" style="margin-top:14px">
          <button class="btn teal" id="runMirror3">Copy to standby now</button></div>
      </div>
    </div>` : ''}

    ${d.keepalive ? `<div class="card">
      <div class="card-head"><h2>Keeping the database awake</h2><span class="spacer"></span>
        <span class="badge ${d.keepalive.awake_now ? 'won' : ''}">${
          d.keepalive.awake_now ? 'inside the window now' : 'sleeping outside hours'}</span></div>
      <div class="card-body">
        ${d.keepalive.enabled ? `
          <p class="muted">Pinged every ${d.keepalive.every_minutes} minutes between
            <strong>${esc(d.keepalive.window)}</strong> on days ${esc(d.keepalive.days)}
            (UTC+${d.keepalive.tz_offset}), so agents never wait for a cold database during
            working hours.</p>
          <div class="meter" style="margin-top:14px"><i class="${
            d.keepalive.quota_pct >= 85 ? 'crit' : (d.keepalive.quota_pct >= 70 ? 'warn' : '')
          }" style="width:${Math.min(100, d.keepalive.quota_pct)}%"></i></div>
          <div class="dir-meta">About <b>${d.keepalive.cu_hours_per_month}</b> of
            <b>${d.keepalive.quota_cu_hours}</b> free compute-hours a month
            (${d.keepalive.quota_pct}%) · ${d.keepalive.pings} ping(s) so far</div>
          ${!d.keepalive.within_quota ? `<div class="incident" style="margin-top:14px">
            <h4>This window is too wide for the free plan</h4>
            <p>Running out of compute-hours suspends the database until the next billing
              period — worse than letting it sleep. Narrow the hours with
              KEEPALIVE_START_HOUR and KEEPALIVE_END_HOUR.</p></div>` : ''}
          ${d.keepalive.last_error ? `<p class="muted" style="color:#A8412A;margin-top:8px">
            Last ping failed: ${esc(d.keepalive.last_error)}</p>` : ''}`
        : '<p class="muted">Keep-alive is off. The database sleeps after 5 minutes idle and the first request of the day waits a moment while it wakes.</p>'}
      </div>
    </div>` : ''}

    <div class="card">
      <div class="card-head"><h2>How failover works</h2></div>
      <div class="card-body">
        <p class="muted">The standby is a <strong>warm backup, not automatic failover</strong>.
          If the primary fails, the portal stops rather than quietly writing to a second copy —
          two databases both accepting commission writes would eventually disagree about who is
          owed what, with no safe way to reconcile.</p>
        <p class="muted" style="margin-top:10px">To recover: point <strong>DATABASE_URL</strong>
          at the standby in Render and redeploy. You lose only what was written since the last
          copy shown above. That is a deliberate decision for you to take, knowingly.</p>
        ${sb.configured ? '<div class="btn-row" style="margin-top:14px"><button class="btn ghost" id="runMirror2">Copy to standby now</button></div>' : ''}
      </div>
    </div>

    ${d.history.length ? `<div class="card">
      <div class="card-head"><h2>Incident history</h2></div>
      <div class="table-scroll"><table>
        <thead><tr><th>First seen</th><th>Kind</th><th>Title</th><th class="num">Times</th>
          <th>Status</th></tr></thead>
        <tbody>${d.history.map(i => `<tr>
          <td class="muted">${esc(i.first_seen)}</td><td><span class="badge">${esc(i.kind)}</span></td>
          <td>${esc(i.title)}</td><td class="num">${i.occurrences}</td>
          <td>${i.resolved ? '<span class="badge won">resolved</span>'
            : '<span class="badge lost">open</span>'}</td></tr>`).join('')}</tbody></table></div>
    </div>` : ''}`);

  const runMirror = async (btn) => {
    const done = busy(btn, 'Copying…');
    try {
      const r = await api('/api/admin/system/mirror', 'POST', {});
      toast(`Copied ${r.rows.toLocaleString()} rows in ${r.duration_s}s.`, 'ok');
      viewSystem();
    } catch (e) { reportError(e); done(); }
  };
  if ($('runMirror')) $('runMirror').onclick = () => runMirror($('runMirror'));
  if ($('runMirror2')) $('runMirror2').onclick = () => runMirror($('runMirror2'));
  if ($('runMirror3')) $('runMirror3').onclick = () => runMirror($('runMirror3'));
  document.querySelectorAll('[data-res]').forEach(b => b.onclick = async () => {
    try {
      await api('/api/admin/incidents/' + b.dataset.res + '/resolve', 'POST', {});
      toast('Incident marked resolved.', 'ok'); viewSystem();
    } catch (e) { reportError(e); }
  });
}

const REPORT_BLURB = {
  commission: 'What each agent has earned, been paid, and is still owed.',
  payments: 'Every payment received, with how the commission was split.',
  pipeline: 'All clients, their stage, value and collections to date.',
  payouts: 'Payouts created, approved and paid.',
  agents: 'Win rates, quota attainment and partnerships per agent.',
  activity: 'The dated trail of calls, meetings and stage changes.',
};

let REPORT_PICK = 'commission';

async function viewReports() {
  setActive('reports', 'Reports');
  loading('Preparing reports…');
  const d = await api('/api/reports');

  view(`<div class="card">
    <div class="card-head"><h2>Download a report</h2></div>
    <div class="card-body">
      <p class="muted" style="margin-bottom:16px">Reports download as CSV, which opens in Excel,
        Google Sheets or LibreOffice.${ME.role === 'admin' ? ''
          : ' They cover your own clients only.'}</p>
      <div class="rep-grid">${d.types.map(t => `
        <div class="rep-card ${REPORT_PICK === t.key ? 'picked' : ''}" data-rep="${t.key}">
          <strong>${esc(t.label)}</strong>
          <span>${esc(REPORT_BLURB[t.key] || '')}</span>
        </div>`).join('')}</div>

      <div class="grid2" style="margin-top:20px;max-width:520px">
        <div><label class="lbl" for="rFrom">From (optional)</label>
          <input class="inp" id="rFrom" type="date"></div>
        <div><label class="lbl" for="rTo">To (optional)</label>
          <input class="inp" id="rTo" type="date"></div>
      </div>
      <p class="hint" style="margin-top:8px">Leave the dates blank for everything to date.
        Dates filter on when the payment or activity happened.</p>

      <div class="btn-row" style="margin-top:18px">
        <button class="btn teal" id="dlRep">Download CSV</button>
        <button class="btn ghost" id="prevRep">Preview first rows</button>
      </div>
      <div id="repPreview" style="margin-top:18px"></div>
    </div>
  </div>`);

  document.querySelectorAll('[data-rep]').forEach(c => c.onclick = () => {
    REPORT_PICK = c.dataset.rep;
    document.querySelectorAll('[data-rep]').forEach(x =>
      x.classList.toggle('picked', x.dataset.rep === REPORT_PICK));
  });

  const build = async (btn) => {
    const done = busy(btn, 'Building…');
    try {
      const r = await api('/api/reports/build', 'POST',
        {kind: REPORT_PICK, from: val('rFrom'), to: val('rTo')});
      done();
      return r;
    } catch (e) { reportError(e); done(); return null; }
  };

  $('dlRep').onclick = async () => {
    const r = await build($('dlRep'));
    if (!r) return;
    if (!r.rows) return toast('That report has no rows for the dates chosen.', 'err');
    // A BOM keeps Excel happy with accented names.
    const blob = new Blob(['\ufeff' + r.csv], {type: 'text/csv;charset=utf-8;'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = r.filename; a.click();
    URL.revokeObjectURL(a.href);
    toast(`${r.label} downloaded — ${r.rows} row(s).`, 'ok');
  };

  $('prevRep').onclick = async () => {
    const r = await build($('prevRep'));
    if (!r) return;
    const lines = r.csv.split('\n').slice(0, 11);
    const cells = l => (l.match(/("([^"]|"")*"|[^,]*)/g) || [])
      .filter((x, i, arr) => x !== '' || i < arr.length - 1)
      .map(x => x.replace(/^"|"$/g, '').replace(/""/g, '"'));
    const head = cells(lines[0]);
    $('repPreview').innerHTML = `
      <p class="muted" style="margin-bottom:8px">${esc(r.label)} — ${r.rows} row(s),
        showing the first ${Math.max(0, lines.length - 1)}.</p>
      <div class="table-scroll"><table>
        <thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${lines.slice(1).filter(Boolean).map(l =>
          `<tr>${cells(l).map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody>
      </table></div>`;
  };
}

/* ------------------------------- TEAMWORK (agent co-working) ------------- */
let DIR_FILTER = {country: '', industry: ''};

async function viewTeamwork() {
  setActive('teamwork', 'Teamwork');
  loading('Finding colleagues…');
  const [dir, collabs, mine] = await Promise.all([
    api('/api/agents/directory?country=' + encodeURIComponent(DIR_FILTER.country)
        + '&industry=' + encodeURIComponent(DIR_FILTER.industry)),
    api('/api/collaborations'),
    api('/api/clients'),
  ]);

  const countries = [...new Set(dir.agents.map(a => a.country).filter(Boolean))].sort();
  const active = collabs.collaborations.filter(c => c.status === 'Accepted');
  const pending = collabs.collaborations.filter(c => c.status === 'Requested');

  view(`
    ${collabs.incoming.length ? `<div class="card">
      <div class="card-head"><h2>Someone needs your help (${collabs.incoming.length})</h2></div>
      <div class="card-body">${collabs.incoming.map(c => `
        <div class="incident warning" style="border-left-color:var(--teal);background:#F2FBFA">
          <h4 style="color:#1C6E64">${esc(c.owner_name)} asked you to co-work ${esc(c.client_name)}</h4>
          <p>${esc(c.reason) || 'No note given.'}</p>
          <p style="margin-top:6px">You would receive <strong>${pct(c.split_pct)}</strong>
            of their commission on this client, for as long as the partnership runs.</p>
          <div class="btn-row" style="margin-top:10px">
            <button class="btn teal sm" data-acc="${c.id}">Accept</button>
            <button class="btn ghost sm" data-dec="${c.id}">Decline</button>
          </div>
        </div>`).join('')}</div>
    </div>` : ''}

    <div class="card">
      <div class="card-head"><h2>Ask a colleague to help on a client</h2></div>
      <div class="card-body">
        <p class="muted" style="margin-bottom:14px">Chasing a client in another country, or in an
          industry you have not sold into? Bring in a colleague who has. You agree a share of
          <em>your</em> commission — StatsPack's share does not change.</p>
        <div class="filters" style="margin-bottom:16px">
          <select class="inp" id="fCountry">
            <option value="">All countries</option>
            ${countries.map(c => `<option ${DIR_FILTER.country === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}
          </select>
          <select class="inp" id="fIndustry">
            <option value="">Any industry closed</option>
            ${(dir.industries || []).map(i => `<option ${DIR_FILTER.industry === i ? 'selected' : ''}>${esc(i)}</option>`).join('')}
          </select>
          <button class="btn ghost sm" id="fClear">Clear</button>
        </div>
        ${dir.agents.length ? `<div class="dir-grid">${dir.agents.map(a => `
          <div class="dir-card" data-agent="${a.id}" data-name="${esc(a.name)}">
            <div class="dir-head">${avatarHTML(a, 'avatar-xs')}
              <div><strong>${esc(a.name)}</strong>
                <div class="muted" style="font-size:12px">${esc(a.agent_type) || 'Agent'}${
                  a.country ? ' · ' + esc(a.country) : ''}</div></div></div>
            <div class="dir-meta">
              <b>${a.clients_won}</b> client(s) won${a.win_rate !== null
                ? ` · <b>${pct(a.win_rate)}</b> win rate` : ''}<br>
              <b>${a.collaborations}</b> partnership(s) · ${a.months_active} month(s) active
            </div>
            ${a.countries.length ? `<div class="chips">${a.countries.slice(0, 4)
              .map(c => `<span class="chip-sm">${esc(c)}</span>`).join('')}</div>` : ''}
            ${a.industries.length ? `<div class="chips">${a.industries.slice(0, 3)
              .map(i => `<span class="chip-sm">${esc(i)}</span>`).join('')}</div>` : ''}
          </div>`).join('')}</div>`
          : emptyState('Nobody matches', 'Try clearing the filters.')}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>My partnerships</h2></div>
      ${(active.length || pending.length) ? `<div class="table-scroll"><table>
        <thead><tr><th>Client</th><th>Lead agent</th><th>Partner</th><th class="num">Partner share</th>
          <th>Status</th><th></th></tr></thead>
        <tbody>${active.concat(pending).map(c => `<tr>
          <td><a href="#/client/${c.client_id}"><strong>${esc(c.client_name)}</strong></a></td>
          <td>${esc(c.owner_name)}</td><td>${esc(c.partner_name)}</td>
          <td class="num">${pct(c.split_pct)}</td>
          <td><span class="badge ${c.status === 'Accepted' ? 'won' : 'warn'}">${esc(c.status)}</span></td>
          <td>${c.status === 'Accepted'
            ? `<button class="btn ghost sm" data-end="${c.id}">End</button>` : ''}</td>
        </tr>`).join('')}</tbody></table></div>`
        : emptyState('No partnerships yet', 'Pick a colleague above to get started.')}
    </div>`);

  $('fCountry').onchange = () => { DIR_FILTER.country = val('fCountry'); viewTeamwork(); };
  $('fIndustry').onchange = () => { DIR_FILTER.industry = val('fIndustry'); viewTeamwork(); };
  $('fClear').onclick = () => { DIR_FILTER = {country: '', industry: ''}; viewTeamwork(); };

  document.querySelectorAll('[data-agent]').forEach(c => c.onclick = () =>
    askHelpModal(Number(c.dataset.agent), c.dataset.name, mine.clients));

  const respond = async (id, status, note) => {
    try {
      await api('/api/collaborations/' + id, 'PATCH', {status, response_note: note || ''});
      toast('Partnership ' + status.toLowerCase() + '.', 'ok');
      viewTeamwork();
    } catch (e) { reportError(e); }
  };
  document.querySelectorAll('[data-acc]').forEach(b => b.onclick = () => respond(b.dataset.acc, 'Accepted'));
  document.querySelectorAll('[data-dec]').forEach(b => b.onclick = () => {
    const why = prompt('Anything to tell them? (optional)') || '';
    respond(b.dataset.dec, 'Declined', why);
  });
  document.querySelectorAll('[data-end]').forEach(b => b.onclick = () => {
    if (!confirm('End this partnership? Future commission returns fully to the lead agent. '
                 + 'Commission already earned is not affected.')) return;
    respond(b.dataset.end, 'Ended');
  });
}

function askHelpModal(partnerId, partnerName, clients) {
  const mine = (clients || []).filter(c => !c.shared_with_me
    && !['Lost', 'Churned'].includes(c.stage));
  if (!mine.length) {
    return toast('You have no open clients to share yet.', 'err');
  }
  modal('Ask ' + partnerName + ' to help', `
    <div><label class="lbl" for="hClient">Which client?</label>
      <select class="inp" id="hClient">${mine.map(c =>
        `<option value="${c.id}">${esc(c.name)}${c.country ? ' — ' + esc(c.country) : ''}
         (${esc(c.stage)})</option>`).join('')}</select></div>
    <div style="margin-top:14px"><label class="lbl" for="hSplit">Their share of your commission (%)</label>
      <input class="inp" id="hSplit" type="number" min="1" max="90" step="5" value="30">
      <div class="hint">Taken from your commission only. StatsPack's share is unaffected, and
        commission already earned on this client does not change.</div></div>
    <div style="margin-top:14px"><label class="lbl" for="hWhy">Why you need them</label>
      <textarea class="inp" id="hWhy" placeholder="Client is in Botswana and wants an in-person meeting."></textarea></div>
    <p class="hint" style="margin-top:12px">Once they accept they can see this client and log
      activity on it, but cannot change the deal or its stage. Either of you can end it later.</p>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="hSend">Send request</button>`);
  $('hSend').onclick = async () => {
    const done = busy($('hSend'), 'Sending…');
    try {
      await api('/api/collaborations', 'POST', {
        client_id: num('hClient'), partner_id: partnerId,
        split_pct: num('hSplit') / 100, reason: val('hWhy')});
      closeModal(); toast('Request sent to ' + partnerName + '.', 'ok'); viewTeamwork();
    } catch (e) { reportError(e); done(); }
  };
}

/* ---------------------------------------------------------- CLIENTS */
async function viewClients() {
  const admin = ME.role === 'admin';
  setActive('clients', admin ? 'Clients' : 'My clients');
  loading('Loading clients…');
  const d = await api('/api/clients');
  let agents = [];
  if (admin) agents = (await api('/api/admin/agents')).agents;

  view(`
    <div class="card">
      <div class="card-head"><h2>${admin ? 'All clients' : 'My clients'}</h2>
        <span class="spacer"></span>
        <input class="inp" id="search" placeholder="Search clients…" style="width:auto;min-width:180px">
        <button class="btn teal" id="addClient">Add client</button></div>
      ${d.clients.length ? `<div class="table-scroll"><table id="ctab">
        <thead><tr><th>Client</th>${admin ? '<th>Agent</th>' : ''}<th>Industry</th><th>Country</th><th>Stage</th>
          <th class="num">Monthly value</th><th class="num">Payments</th><th class="num">Collected</th>
          <th>Year-1 window ends</th></tr></thead>
        <tbody>${d.clients.map(c => `<tr data-s="${esc((c.name + ' ' + c.country + ' ' + c.agent_name + ' ' + c.product + ' ' + (c.industry || '')).toLowerCase())}">
          <td><a href="#/client/${c.id}"><strong>${esc(c.name)}</strong></a>
            ${c.shared_with_me ? '<span class="shared-flag">helping</span>' : ''}
            ${c.product ? `<div class="muted">${esc(c.product)}</div>` : ''}</td>
          ${admin ? `<td>${esc(c.agent_name)}</td>` : ''}
          <td class="muted">${esc(c.industry) || '—'}</td>
          <td>${esc(c.country) || '—'}</td><td>${stageBadge(c.stage)}</td>
          <td class="num">${money(c.monthly_value, c.currency)}</td>
          <td class="num">${c.payments_count}</td>
          <td class="num">${usd0(c.collected_usd)}</td>
          <td>${c.window_end ? dt(c.window_end) : '<span class="muted">not started</span>'}</td>
        </tr>`).join('')}</tbody></table></div>`
        : emptyState('No clients yet', admin
            ? 'Add a client, or ask your agents to log their own.'
            : 'Add your first client to start building your pipeline.')}
    </div>`);

  $('addClient').onclick = () => clientModal(null, agents);
  const s = $('search');
  if (s) s.oninput = () => {
    const q = s.value.toLowerCase();
    document.querySelectorAll('#ctab tbody tr').forEach(tr =>
      tr.style.display = tr.dataset.s.includes(q) ? '' : 'none');
  };
}

function clientModal(existing, agents) {
  const c = existing || {};
  const curOpts = FX.map(f => `<option value="${f.code}" ${c.currency === f.code ? 'selected' : ''}>${f.code} — ${esc(f.label)}</option>`).join('');
  const indOpts = ['<option value="">— not set —</option>'].concat(
    INDUSTRIES.map(i => `<option ${c.industry === i ? 'selected' : ''}>${esc(i)}</option>`)).join('');
  const stageOpts = STAGES.map(s => `<option ${c.stage === s ? 'selected' : ''}>${s}</option>`).join('');
  const agentPicker = (ME.role === 'admin' && agents && agents.length)
    ? `<div><label class="lbl" for="cAgent">Agent</label><select class="inp" id="cAgent">
         ${agents.map(a => `<option value="${a.id}" ${c.agent_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
       </select></div>` : '';

  modal(existing ? 'Edit client' : 'Add client', `
    <div class="grid2">
      <div><label class="lbl" for="cName">Client name</label><input class="inp" id="cName" value="${esc(c.name || '')}" placeholder="Copperbelt Mining Services"></div>
      ${agentPicker}
      <div><label class="lbl" for="cPerson">Contact person</label><input class="inp" id="cPerson" value="${esc(c.contact_person || '')}"></div>
      <div><label class="lbl" for="cEmail">Contact email</label><input class="inp" id="cEmail" type="email" value="${esc(c.contact_email || '')}"></div>
      <div><label class="lbl" for="cPhone">Contact phone</label><input class="inp" id="cPhone" value="${esc(c.contact_phone || '')}"></div>
      <div><label class="lbl" for="cCountry">Country</label><input class="inp" id="cCountry" value="${esc(c.country || '')}"></div>
      <div><label class="lbl" for="cIndustry">Industry</label><select class="inp" id="cIndustry">${indOpts}</select></div>
      <div><label class="lbl" for="cProduct">Product</label><input class="inp" id="cProduct" value="${esc(c.product || '')}" placeholder="SmartRegister"></div>
      <div><label class="lbl" for="cCur">Currency</label><select class="inp" id="cCur">${curOpts}</select></div>
      <div><label class="lbl" for="cValue">Monthly value</label><input class="inp" id="cValue" type="number" min="0" step="0.01" value="${c.monthly_value || 0}">
        <div class="hint">What they pay each month, in their currency.</div></div>
      <div><label class="lbl" for="cStage">Stage</label><select class="inp" id="cStage">${stageOpts}</select></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="cNotes">Notes</label>
      <textarea class="inp" id="cNotes">${esc(c.notes || '')}</textarea></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="cSave">${existing ? 'Save changes' : 'Add client'}</button>`);

  $('cSave').onclick = async () => {
    const payload = {
      name: val('cName'), contact_person: val('cPerson'), contact_email: val('cEmail'),
      contact_phone: val('cPhone'), country: val('cCountry'), product: val('cProduct'),
      currency: val('cCur'), monthly_value: num('cValue'), stage: val('cStage'),
      industry: val('cIndustry'), notes: val('cNotes')
    };
    if (existing && payload.stage !== existing.stage) {
      payload.stage_note = 'Stage changed from the client form.';
    }
    if ($('cAgent')) payload.agent_id = num('cAgent');
    try {
      if (existing) await api('/api/clients/' + existing.id, 'PATCH', payload);
      else await api('/api/clients', 'POST', payload);
      closeModal(); toast(existing ? 'Client updated.' : 'Client added.', 'ok'); route();
    } catch (e) { reportError(e); }
  };
}

async function viewClientDetail(id) {
  setActive('clients', 'Client');
  loading('Loading client…');
  const d = await api('/api/clients/' + id);
  const c = d.client;
  setActive('clients', c.name);
  const admin = ME.role === 'admin';
  const collected = d.payments.filter(p => !p.voided).reduce((s, p) => s + Number(p.amount_usd), 0);
  const comm = d.payments.reduce((s, p) => s + Number(p.agent_commission_usd), 0);

  view(`
    <div class="card">
      <div class="card-head"><h2>${esc(c.name)} ${stageBadge(c.stage)}
        ${c.shared_with_me ? '<span class="shared-flag">you are helping on this</span>' : ''}</h2>
        <span class="spacer"></span>
        ${c.shared_with_me ? '' : '<button class="btn ghost sm" id="editC">Edit</button>'}
        ${(!c.shared_with_me && ME.role === 'agent') ? '<button class="btn ghost sm" id="getHelp">Ask for help</button>' : ''}
        ${(admin && !c.shared_with_me) ? '<button class="btn danger sm" id="delClient">Delete client</button>' : ''}
        ${admin ? '<button class="btn teal sm" id="addPay">Record payment</button>' : ''}</div>
      <div class="card-body"><div class="grid2">
        <div><span class="lbl">Agent</span>${esc(c.agent_name)}</div>
        <div><span class="lbl">Contact</span>${esc(c.contact_person) || '—'}${c.contact_email ? '<br>' + esc(c.contact_email) : ''}${c.contact_phone ? '<br>' + esc(c.contact_phone) : ''}</div>
        <div><span class="lbl">Country</span>${esc(c.country) || '—'}</div>
        <div><span class="lbl">Product</span>${esc(c.product) || '—'}</div>
        <div><span class="lbl">Monthly value</span>${money(c.monthly_value, c.currency)} <span class="muted">(${usd(c.monthly_value_usd)})</span></div>
        <div><span class="lbl">Won on</span>${dt(c.won_date)}</div>
      </div>
      ${c.notes ? `<div style="margin-top:14px"><span class="lbl">Notes</span><span style="white-space:pre-wrap">${esc(c.notes)}</span></div>` : ''}
      </div>
    </div>

    <div class="stats">
      ${stat('teal','💰','Collected', usd0(collected),
             `<b>${d.payments.filter(p => !p.voided).length}</b> payment(s)`, '')}
      ${stat('amber','％','Agent commission', usd0(comm), '', '')}
      ${stat('slate','📅','Year-1 window ends',
             `<span style="font-size:21px">${d.payments.length && d.payments[0].window_end ? dt(d.payments[0].window_end) : '—'}</span>`,
             'from first payment', '')}
    </div>

    <div class="card">
      <div class="card-head"><h2>Pipeline progress</h2><span class="spacer"></span>
        <span class="muted">${esc(c.industry) || 'industry not set'}</span></div>
      <div class="card-body">${flowchart(d.progress, d.timeline)}</div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Activity trail</h2><span class="spacer"></span>
        <button class="btn teal sm" id="addEvent">Log activity</button></div>
      <div class="card-body">${timelineHTML(d.timeline, admin)}</div>
    </div>

    ${paymentsTable(d.payments, 'Payment history', admin)}`);

  if ($('editC')) $('editC').onclick = async () => {
    let agents = [];
    if (admin) agents = (await api('/api/admin/agents')).agents;
    clientModal(c, agents);
  };
  if ($('getHelp')) $('getHelp').onclick = () => { location.hash = '#/teamwork'; };
  if ($('delClient')) $('delClient').onclick = () => deleteClientModal(c);
  if ($('addPay')) $('addPay').onclick = () => paymentModal(c);
  $('addEvent').onclick = () => eventModal(c);
  document.querySelectorAll('[data-delev]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this entry?')) return;
    try {
      await api(`/api/clients/${c.id}/timeline/${b.dataset.delev}`, 'DELETE', {});
      toast('Entry deleted.', 'ok'); route();
    } catch (e) { reportError(e); }
  });
  wirePaymentButtons();
}

function eventModal(client) {
  modal('Log activity — ' + client.name, `
    <div class="grid2">
      <div><label class="lbl" for="evKind">What happened</label>
        <select class="inp" id="evKind">${EVENT_KINDS.filter(k => k !== 'stage')
          .map(k => `<option value="${k}">${EVENT_ICON[k] || ''} ${k.charAt(0).toUpperCase() + k.slice(1)}</option>`).join('')}
        </select></div>
      <div><label class="lbl" for="evDue">Follow-up by</label>
        <input class="inp" id="evDue" type="date"></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="evBody">Notes</label>
      <textarea class="inp" id="evBody" placeholder="Who you spoke to, what they said, what was agreed."></textarea></div>
    <div style="margin-top:14px"><label class="lbl" for="evNext">Next step</label>
      <input class="inp" id="evNext" placeholder="Send the security pack"></div>
    <p class="hint" style="margin-top:12px">Entries are dated and cannot be edited afterwards,
      so this stays a real record of what happened rather than a summary written later.</p>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="evSave">Save entry</button>`);
  $('evSave').onclick = async () => {
    const done = busy($('evSave'), 'Saving…');
    try {
      await api(`/api/clients/${client.id}/timeline`, 'POST', {
        kind: val('evKind'), body: val('evBody'),
        next_step: val('evNext'), due_date: val('evDue')});
      closeModal(); toast('Activity logged.', 'ok'); route();
    } catch (e) { reportError(e); done(); }
  };
}

async function downloadClient(client) {
  try {
    const data = await api(`/api/clients/${client.id}/export`);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
    a.download = 'statspack-client-' + client.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()
                 + '-' + new Date().toISOString().slice(0, 10) + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
    toast('Client records downloaded.', 'ok');
  } catch (e) { reportError(e); }
}

async function deleteClientModal(client) {
  let info;
  try {
    info = await api(`/api/clients/${client.id}/footprint`);
  } catch (e) { return reportError(e); }
  const f = info.footprint;

  // A client that has never paid carries no commission history, so an ordinary
  // admin can remove it. One that has paid is financial record.
  if (info.can_delete_simply) {
    modal('Delete ' + client.name, `
      <p>This client has no payments recorded, so nothing financial is lost.</p>
      <div class="danger-list"><ul>
        <li><strong>${f.timeline_entries}</strong> activity entries will be removed</li>
        <li><strong>${f.collaborations}</strong> partnership record(s) will be removed</li>
      </ul></div>
      <p class="muted">If you might work this client again later, setting the stage to
        <strong>Lost</strong> keeps the history instead.</p>`,
      `<button class="btn ghost" onclick="closeModal()">Cancel</button>
       <button class="btn danger" id="dcGo">Delete client</button>`);
    $('dcGo').onclick = async () => {
      const done = busy($('dcGo'), 'Deleting…');
      try {
        await api('/api/clients/' + client.id, 'DELETE', {});
        closeModal(); toast('Client deleted.', 'ok');
        location.hash = '#/clients'; route();
      } catch (e) { reportError(e); done(); }
    };
    return;
  }

  if (!ME.is_super) {
    return modal('Cannot delete ' + client.name, `
      <div class="danger" style="margin-bottom:0">
        <h3>This client has payments recorded</h3>
        <p>Deleting it would erase <strong>${usd(f.collected_usd)}</strong> of collections and
          <strong>${usd(f.commission_usd)}</strong> of agent commission. Only a super user can
          do that.</p>
      </div>
      <p class="muted" style="margin-top:14px">Set the stage to <strong>Churned</strong>
        instead. The client stops appearing as active and every record survives.</p>`,
      `<button class="btn teal" onclick="closeModal()">Understood</button>`);
  }

  modal('Permanently delete ' + client.name, `
    <div class="danger" style="margin-bottom:0">
      <h3>This erases financial records</h3>
      <p>Commission history may be legally required for years after a client leaves.
        There is no undo, and a backup taken afterwards will not contain it.</p>
    </div>
    <p style="margin:16px 0 8px"><strong>About to be destroyed:</strong></p>
    <div class="danger-list"><ul>
      <li><strong>${f.payments}</strong> payment(s), totalling <strong>${usd(f.collected_usd)}</strong> collected</li>
      <li><strong>${usd(f.commission_usd)}</strong> of agent commission</li>
      <li><strong>${f.timeline_entries}</strong> activity entries</li>
      <li><strong>${f.collaborations}</strong> partnership record(s)</li>
    </ul></div>
    <p class="muted" style="margin-bottom:14px">Setting the stage to <strong>Churned</strong>
      achieves the same day-to-day result and keeps the record.</p>
    <label class="lbl" for="dcName">Type <strong>${esc(client.name)}</strong> to confirm</label>
    <input class="inp confirm-input" id="dcName" placeholder="${esc(client.name)}" autocomplete="off">`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn ghost" id="dcExport">Download records</button>
     <button class="btn danger" id="dcPurge" disabled>Delete permanently</button>`);

  const nameInput = $('dcName'), go = $('dcPurge');
  nameInput.oninput = () => {
    go.disabled = nameInput.value.trim().toLowerCase() !== client.name.trim().toLowerCase();
  };
  $('dcExport').onclick = () => downloadClient(client);
  go.onclick = async () => {
    const done = busy(go, 'Deleting…');
    try {
      const r = await api('/api/clients/' + client.id + '/purge', 'DELETE',
                          {confirm_name: nameInput.value.trim()});
      closeModal();
      toast(`${client.name} deleted — ${r.deleted.payments} payment(s) removed.`, 'ok');
      location.hash = '#/clients'; route();
    } catch (e) { reportError(e); done(); }
  };
}

/* ---------------------------------------------------------- PAYMENTS */
function paymentsTable(payments, title, admin) {
  return `<div class="card">
    <div class="card-head"><h2>${esc(title)}</h2><span class="spacer"></span>
      <span class="muted">${payments.length} record${payments.length === 1 ? '' : 's'}</span></div>
    ${payments.length ? `<div class="table-scroll"><table>
      <thead><tr><th>Date</th><th>Client</th><th>Commission type</th><th class="num">Amount</th>
        <th class="num">In USD</th><th class="num">Rate</th><th class="num">Agent</th>
        <th class="num">StatsPack</th><th>Ref</th>${admin ? '<th></th>' : ''}</tr></thead>
      <tbody>${payments.map(p => `<tr${p.voided ? ' style="opacity:.55"' : ''}>
        <td>${dt(p.paid_date)}</td>
        <td>${esc(p.client_name || '')}</td>
        <td>${kindBadge(p.commission_kind)}</td>
        <td class="num">${money(p.amount, p.currency)}</td>
        <td class="num">${usd(p.amount_usd)}</td>
        <td class="num">${p.voided ? '—' : pct(p.commission_rate)}</td>
        <td class="num"><strong>${usd(p.agent_commission_usd)}</strong></td>
        <td class="num">${usd(p.statspack_share_usd)}</td>
        <td class="muted">${esc(p.reference || '—')}</td>
        ${admin ? `<td><button class="btn ghost sm" data-void="${p.id}">${p.voided ? 'Restore' : 'Void'}</button></td>` : ''}
      </tr>`).join('')}</tbody></table></div>`
      : emptyState('No payments recorded', 'Commission is calculated the moment a payment is recorded.')}
  </div>`;
}

function wirePaymentButtons() {
  document.querySelectorAll('[data-void]').forEach(b => b.onclick = async () => {
    const restoring = b.textContent === 'Restore';
    if (!confirm(restoring
      ? 'Restore this payment? Commission will be recalculated.'
      : 'Void this payment? It stays on the record but earns no commission.')) return;
    try {
      await api('/api/payments/' + b.dataset.void + '/void', 'POST', {});
      toast(restoring ? 'Payment restored.' : 'Payment voided.', 'ok');
      route();
    } catch (e) { reportError(e); }
  });
}

async function viewPayments() {
  const admin = ME.role === 'admin';
  setActive('payments', admin ? 'Payments' : 'Payments received');
  loading('Loading payments…');
  const d = await api('/api/payments');
  let clients = [];
  if (admin) clients = (await api('/api/clients')).clients;

  view(`
    ${admin ? `<div class="card"><div class="card-head">
      <h2>Record a client payment</h2><span class="spacer"></span>
      <button class="btn teal" id="newPay">Record payment</button></div>
      <div class="card-body"><p class="muted">Recording a payment is what triggers commission.
      The first payment for a client pays the agent ${pct(Number(SETTINGS.commission_first_rate))};
      every monthly payment inside the following ${SETTINGS.commission_window_months} months pays
      ${pct(Number(SETTINGS.commission_recurring_rate))}; after that the agent's commission ceases.</p></div></div>` : ''}
    ${paymentsTable(d.payments, admin ? 'All payments' : 'Payments on my clients', admin)}`);

  if ($('newPay')) $('newPay').onclick = () => paymentModal(null, clients);
  wirePaymentButtons();
}

function paymentModal(client, clients) {
  const curOpts = FX.map(f => `<option value="${f.code}" ${(client && client.currency === f.code) ? 'selected' : ''}>${f.code}</option>`).join('');
  const picker = client
    ? `<div><span class="lbl">Client</span><strong>${esc(client.name)}</strong></div>`
    : `<div><label class="lbl" for="pClient">Client</label><select class="inp" id="pClient">
         ${(clients || []).map(c => `<option value="${c.id}" data-cur="${c.currency}" data-val="${c.monthly_value}">${esc(c.name)} — ${esc(c.agent_name)}</option>`).join('')}
       </select></div>`;

  modal('Record payment', `
    <div class="grid2">
      ${picker}
      <div><label class="lbl" for="pDate">Date received</label>
        <input class="inp" id="pDate" type="date" value="${new Date().toISOString().slice(0, 10)}"></div>
      <div><label class="lbl" for="pAmount">Amount received</label>
        <input class="inp" id="pAmount" type="number" min="0.01" step="0.01" value="${client ? client.monthly_value : ''}"></div>
      <div><label class="lbl" for="pCur">Currency</label><select class="inp" id="pCur">${curOpts}</select></div>
      <div><label class="lbl" for="pRef">Reference</label><input class="inp" id="pRef" placeholder="Bank ref / invoice no."></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="pNote">Note</label>
      <textarea class="inp" id="pNote" placeholder="Optional."></textarea></div>
    <p class="hint" style="margin-top:12px">The exchange rate is locked in at today's rate from Settings,
      so later rate changes will not move historic commission.</p>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="pSave">Record payment</button>`);

  if ($('pClient')) $('pClient').onchange = e => {
    const o = e.target.selectedOptions[0];
    $('pCur').value = o.dataset.cur;
    if (!$('pAmount').value) $('pAmount').value = o.dataset.val;
  };
  if ($('pClient') && $('pClient').selectedOptions[0]) {
    $('pCur').value = $('pClient').selectedOptions[0].dataset.cur;
  }

  $('pSave').onclick = async () => {
    try {
      const r = await api('/api/payments', 'POST', {
        client_id: client ? client.id : num('pClient'),
        amount: num('pAmount'), currency: val('pCur'), paid_date: val('pDate'),
        reference: val('pRef'), note: val('pNote')
      });
      closeModal();
      const p = r.payment;
      toast(`Payment recorded — ${p.commission_kind}, agent earns ${usd(p.agent_commission_usd)}.`, 'ok');
      route();
    } catch (e) { reportError(e); }
  };
}

/* ---------------------------------------------------------- COMMISSIONS */
async function viewCommissions() {
  const admin = ME.role === 'admin';
  setActive('commissions', admin ? 'Commission' : 'My commission');
  loading('Calculating commission…');
  const d = await api('/api/commissions');
  const r = d.rules;

  const rules = `<div class="card">
    <div class="card-head"><h2>How commission is calculated</h2></div>
    <div class="table-scroll"><table class="rule-table">
      <thead><tr><th>Payment stage</th><th class="num">Agent</th><th class="num">StatsPack</th>
        <th>Duration</th><th>Trigger</th></tr></thead>
      <tbody>
        <tr><td><strong>First client payment</strong></td>
          <td class="num">${pct(r.first_rate)}</td><td class="num">${pct(1 - r.first_rate)}</td>
          <td>Once only</td><td>On receipt of the client's first payment</td></tr>
        <tr><td><strong>Monthly payments — year 1</strong></td>
          <td class="num">${pct(r.recurring_rate)}</td><td class="num">${pct(1 - r.recurring_rate)}</td>
          <td>${r.window_months} months from the first payment</td><td>On receipt of each monthly payment</td></tr>
        <tr><td><strong>After month ${r.window_months}</strong></td>
          <td class="num">0.0%</td><td class="num">100.0%</td>
          <td>Indefinite</td><td class="muted">Commission ceases</td></tr>
      </tbody></table></div></div>`;

  if (!admin) {
    const c = d.commissions[0];
    view(rules + `
      <div class="stats">
        ${stat('teal','％','Earned to date', usd0(c.earned_usd),
               `<b>${usd0(c.first_payment_usd)}</b> first`, `<b>${usd0(c.recurring_usd)}</b> monthly`)}
        ${stat('green','✔','Paid to me', usd0(c.paid_out_usd), '', '')}
        ${stat('amber','⏳','Being processed', usd0(c.in_flight_usd), '', '')}
        ${stat('coral','📤','Still owed', usd0(c.outstanding_usd), '', '')}
      </div>`);
    return;
  }

  view(rules + `
    <div class="card">
      <div class="card-head"><h2>Commission by agent</h2></div>
      ${d.commissions.length ? `<div class="table-scroll"><table>
        <thead><tr><th>Agent</th><th class="num">Collected</th><th class="num">First-payment</th>
          <th class="num">Monthly</th><th class="num">Total earned</th><th class="num">Paid</th>
          <th class="num">Processing</th><th class="num">Outstanding</th><th></th></tr></thead>
        <tbody>${d.commissions.map(c => `<tr>
          <td><a href="#/agent/${c.agent_id}"><strong>${esc(c.agent_name)}</strong></a></td>
          <td class="num">${usd0(c.collected_usd)}</td>
          <td class="num">${usd0(c.first_payment_usd)}</td>
          <td class="num">${usd0(c.recurring_usd)}</td>
          <td class="num"><strong>${usd0(c.earned_usd)}</strong></td>
          <td class="num">${usd0(c.paid_out_usd)}</td>
          <td class="num">${usd0(c.in_flight_usd)}</td>
          <td class="num"><strong ${c.outstanding_usd < -0.01
            ? 'style="color:#A8412A"' : ''}>${usd0(c.outstanding_usd)}</strong>
            ${c.outstanding_usd < -0.01 ? '<div class="muted" style="font-size:11px">overpaid</div>' : ''}</td>
          <td><button class="btn ${c.outstanding_usd < -0.01 ? 'ghost' : 'teal'} sm"
                data-pay="${c.agent_id}" data-name="${esc(c.agent_name)}"
                data-out="${c.outstanding_usd}">${c.outstanding_usd < -0.01 ? 'Why?' : 'Pay'}</button></td>
        </tr>`).join('')}</tbody></table></div>`
        : emptyState('No agents yet', 'Commission appears once you have agents with paying clients.')}
    </div>`);

  document.querySelectorAll('[data-pay]').forEach(b => b.onclick =
    () => payoutModal(Number(b.dataset.pay), b.dataset.name, Number(b.dataset.out)));
}

/* ---------------------------------------------------------- PAYOUTS */
function payoutRows(payouts, showAgent) {
  if (!payouts.length) return emptyState('No payouts yet', 'Record a payout when you have paid an agent.');
  const admin = ME.role === 'admin';
  return `<div class="table-scroll"><table>
    <thead><tr><th>Created</th>${showAgent ? '<th>Agent</th>' : ''}<th>Period</th>
      <th class="num">Amount</th><th>Status</th><th>Paid on</th><th>Reference</th>${admin ? '<th></th>' : ''}</tr></thead>
    <tbody>${payouts.map(p => `<tr>
      <td>${dt(p.created_at)}</td>
      ${showAgent ? `<td>${esc(p.agent_name || '')}</td>` : ''}
      <td>${esc(p.period_label) || '—'}</td>
      <td class="num"><strong>${usd(p.amount_usd)}</strong></td>
      <td><span class="badge ${p.status}">${p.status}</span></td>
      <td>${dt(p.paid_date)}</td>
      <td class="muted">${esc(p.reference) || '—'}</td>
      ${admin ? `<td><div class="btn-row">
        ${p.status !== 'Paid' ? `<button class="btn teal sm" data-mark="${p.id}">Mark paid</button>` : ''}
        ${p.status === 'Pending' ? `<button class="btn ghost sm" data-cancel="${p.id}">Cancel</button>` : ''}
      </div></td>` : ''}
    </tr>`).join('')}</tbody></table></div>`;
}

function wirePayoutButtons() {
  document.querySelectorAll('[data-mark]').forEach(b => b.onclick = async () => {
    try {
      await api('/api/payouts/' + b.dataset.mark, 'PATCH', {status: 'Paid'});
      toast('Payout marked paid.', 'ok'); route();
    } catch (e) { reportError(e); }
  });
  document.querySelectorAll('[data-cancel]').forEach(b => b.onclick = async () => {
    if (!confirm('Cancel this payout?')) return;
    try {
      await api('/api/payouts/' + b.dataset.cancel, 'PATCH', {status: 'Cancelled'});
      toast('Payout cancelled.', 'ok'); route();
    } catch (e) { reportError(e); }
  });
}

async function payoutModal(agentId, agentName, outstanding) {
  if (outstanding < -0.01) return overpaidModal(agentId, agentName);
  modal('Record payout to ' + agentName, `
    <p class="muted" style="margin-bottom:14px">Outstanding commission: <strong>${usd(outstanding)}</strong></p>
    <div class="grid2">
      <div><label class="lbl" for="poAmount">Amount (USD)</label>
        <input class="inp" id="poAmount" type="number" min="0.01" step="0.01" value="${outstanding > 0 ? outstanding.toFixed(2) : ''}"></div>
      <div><label class="lbl" for="poPeriod">Period</label><input class="inp" id="poPeriod" placeholder="August 2026"></div>
      <div><label class="lbl" for="poRef">Reference</label><input class="inp" id="poRef" placeholder="EFT ref"></div>
    </div>
    <div style="margin-top:14px"><label class="lbl" for="poNote">Note</label>
      <textarea class="inp" id="poNote"></textarea></div>
    <p class="hint" style="margin-top:12px">This creates a Pending payout. Mark it Paid once the money has actually left.</p>`,
    `<button class="btn ghost" onclick="closeModal()">Cancel</button>
     <button class="btn teal" id="poSave">Create payout</button>`);
  $('poSave').onclick = async () => {
    try {
      await api('/api/payouts', 'POST', {
        agent_id: agentId, amount_usd: num('poAmount'), period_label: val('poPeriod'),
        reference: val('poRef'), note: val('poNote')});
      closeModal(); toast('Payout created.', 'ok'); route();
    } catch (e) { reportError(e); }
  };
}

async function viewPayouts() {
  const admin = ME.role === 'admin';
  setActive('payouts', admin ? 'Payouts' : 'My payouts');
  loading('Loading payouts…');
  const d = await api('/api/payouts');
  view(`<div class="card">
    <div class="card-head"><h2>${admin ? 'All payouts' : 'Payouts to me'}</h2><span class="spacer"></span>
      ${admin ? '<a class="btn ghost sm" href="#/commissions">Pay an agent</a>' : ''}</div>
    ${payoutRows(d.payouts, admin)}</div>`);
  wirePayoutButtons();
}

/* ---------------------------------------------------------- AGENT dashboard */
async function viewAgentDashboard() {
  setActive('dashboard', 'Dashboard');
  loading('Crunching the numbers…');
  const d = await api('/api/agent/overview');
  const m = d.metrics, r = d.rules;

  view(`
    <div class="stats">
      ${stat('teal','％','Commission earned', usd0(m.earned_usd),
             `<b>${usd0(m.first_payment_usd)}</b> first`, `<b>${usd0(m.recurring_usd)}</b> monthly`)}
      ${stat('coral','📤','Still owed to me', usd0(m.outstanding_usd),
             `<b>${usd0(m.paid_out_usd)}</b> paid`, '')}
      ${stat('green','🏢','Clients won', String(m.clients_won),
             `of <b>${m.clients_total}</b> logged`, '')}
      ${stat('amber','📈','Open pipeline', usd0(m.pipeline_usd), 'monthly value', 'not yet won')}
    </div>

    ${m.quota_usd ? `<div class="card"><div class="card-head"><h2>Progress to quota</h2></div>
      <div class="card-body"><span class="lbl">${usd0(m.collected_usd)} collected of ${usd0(m.quota_usd)}</span>
      ${bar(m.attainment)}</div></div>` : ''}

    <div class="card">
      <div class="card-head"><h2>My clients</h2><span class="spacer"></span>
        <a class="btn teal sm" href="#/clients">Manage clients</a></div>
      ${d.clients.length ? `<div class="table-scroll"><table>
        <thead><tr><th>Client</th><th>Stage</th><th class="num">Monthly value</th><th>Country</th></tr></thead>
        <tbody>${d.clients.slice(0, 8).map(c => `<tr>
          <td><a href="#/client/${c.id}"><strong>${esc(c.name)}</strong></a></td>
          <td>${stageBadge(c.stage)}</td>
          <td class="num">${money(c.monthly_value, c.currency)}</td>
          <td>${esc(c.country) || '—'}</td></tr>`).join('')}</tbody></table></div>`
        : emptyState('No clients yet', 'Add your first client from the My clients page.')}
    </div>

    ${paymentsTable(d.payments.slice(0, 10), 'Recent payments from my clients', false)}

    <div class="card">
      <div class="card-head"><h2>Notes from StatsPack</h2></div>
      ${d.reviews.length ? `<div class="card-body">${d.reviews.map(rv => `
        <div style="border-left:3px solid var(--teal);padding:2px 0 2px 13px;margin-bottom:15px">
          <div class="muted" style="font-size:12px">${dt(rv.created_at)}
            ${rv.period ? '· ' + esc(rv.period) : ''} · rated ${rv.rating}/5</div>
          <div style="margin-top:3px;white-space:pre-wrap">${esc(rv.body)}</div>
        </div>`).join('')}</div>`
        : emptyState('No notes yet', 'Feedback from your StatsPack manager will appear here.')}
    </div>`);
}

/* ---------------------------------------------------------- SETTINGS */
async function viewSettings() {
  setActive('settings', 'Settings');
  loading('Loading settings…');
  const [d, fx] = await Promise.all([
    api('/api/admin/settings'), api('/api/admin/fx/status')]);
  const s = d.settings;

  const keyBanner = fx.key_present
    ? `<p class="muted" style="margin-bottom:14px">Rates refresh automatically from
         <strong>ExchangeRate-API</strong> every ${fx.interval_hours} hours.
         Last run: <strong>${fx.last_sync ? esc(fx.last_sync) : 'never'}</strong> —
         ${esc(fx.last_status)}.</p>`
    : `<div class="danger" style="border-color:#E8D5A8;background:#FFFBF0">
         <h3 style="color:#8A6413">Automatic rates are switched off</h3>
         <p style="color:#7A5A12">No API key is set, so the rates below stay exactly as you type them.
           Get a free key at <a href="https://www.exchangerate-api.com/" target="_blank"
           rel="noopener">exchangerate-api.com</a>, then add it in Render as the environment
           variable <strong>EXCHANGERATE_API_KEY</strong> and redeploy.</p></div>`;

  view(`
    <div class="card">
      <div class="card-head"><h2>Commission rules</h2></div>
      <div class="card-body">
        <p class="muted" style="margin-bottom:16px">These drive every commission figure in the portal.
          Changing them recalculates historic payments too, so change them only when the agent
          agreement itself changes.</p>
        <div class="grid2">
          <div><label class="lbl" for="sFirst">First payment — agent share (%)</label>
            <input class="inp" id="sFirst" type="number" min="0" max="100" step="0.5" value="${(Number(s.commission_first_rate) * 100).toFixed(1)}"></div>
          <div><label class="lbl" for="sRecur">Monthly payments — agent share (%)</label>
            <input class="inp" id="sRecur" type="number" min="0" max="100" step="0.5" value="${(Number(s.commission_recurring_rate) * 100).toFixed(1)}"></div>
          <div><label class="lbl" for="sWindow">Commission window (months)</label>
            <input class="inp" id="sWindow" type="number" min="1" max="120" step="1" value="${s.commission_window_months}">
            <div class="hint">Counted from each client's first payment.</div></div>
        </div>
        <div class="btn-row" style="margin-top:16px"><button class="btn teal" id="saveRules">Save rules</button></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Exchange rates</h2><span class="spacer"></span>
        <span class="muted">units per 1 USD</span>
        <button class="btn teal sm" id="syncFx" ${fx.key_present ? '' : 'disabled'}>Sync now</button></div>
      <div class="card-body">
        ${keyBanner}
        <div class="grid2" style="margin-bottom:18px">
          <div><label class="lbl" for="fxAuto">Automatic refresh</label>
            <select class="inp" id="fxAuto" ${fx.key_present ? '' : 'disabled'}>
              <option value="1" ${fx.auto ? 'selected' : ''}>On</option>
              <option value="0" ${fx.auto ? '' : 'selected'}>Off — manual only</option>
            </select></div>
          <div><label class="lbl" for="fxHours">Refresh every (hours)</label>
            <input class="inp" id="fxHours" type="number" min="1" max="168" value="${fx.interval_hours}"
              ${fx.key_present ? '' : 'disabled'}></div>
        </div>
        <p class="muted" style="margin-bottom:12px">Each payment locks in the rate that applied the
          moment it was recorded, so syncing only ever affects future payments. Historic commission
          cannot shift underneath you.</p>
        <div id="fxList">
          ${d.fx.map(f => `<div class="fx-row">
            <span class="fx-code">${f.code}</span>
            <span class="fx-label">${esc(f.label)}</span>
            <span class="src ${f.source === 'ExchangeRate-API' ? 'live' : ''}">${
              f.code === 'USD' ? 'base' : (f.source === 'ExchangeRate-API' ? 'live' : 'manual')}</span>
            <input class="inp" id="fx_${f.code}" type="number" min="0.0001" step="0.0001"
              value="${f.rate}" ${f.code === 'USD' ? 'disabled' : ''}>
          </div>`).join('')}
        </div>
        <div class="btn-row" style="margin-top:16px">
          <button class="btn teal" id="saveFx">Save rates &amp; settings</button></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Backup</h2></div>
      <div class="card-body">
        <p class="muted" style="margin-bottom:14px">Downloads every agent, client, payment and payout as a
          JSON file. Passwords are never included. Keep a copy somewhere off this server.</p>
        <button class="btn ghost" id="dlBackup">Download backup</button>
      </div>
    </div>`);

  $('saveRules').onclick = async () => {
    const done = busy($('saveRules'), 'Saving…');
    try {
      await api('/api/admin/settings', 'PUT', {settings: {
        commission_first_rate: num('sFirst') / 100,
        commission_recurring_rate: num('sRecur') / 100,
        commission_window_months: num('sWindow')}});
      toast('Commission rules saved.', 'ok');
      SETTINGS = (await api('/api/me')).settings;
    } catch (e) { reportError(e); }
    done();
  };

  $('saveFx').onclick = async () => {
    const done = busy($('saveFx'), 'Saving…');
    const rates = d.fx.map(f => ({code: f.code, rate: Number($('fx_' + f.code).value), label: f.label}));
    try {
      await api('/api/admin/fx', 'PUT', {rates});
      if (fx.key_present) {
        await api('/api/admin/settings', 'PUT', {settings: {
          fx_auto: val('fxAuto'), fx_interval_hours: num('fxHours')}});
      }
      toast('Exchange rates saved.', 'ok');
      FX = (await api('/api/fx')).fx;
      viewSettings();
    } catch (e) { reportError(e); done(); }
  };

  if ($('syncFx')) $('syncFx').onclick = async () => {
    const done = busy($('syncFx'), 'Fetching…');
    try {
      const r = await api('/api/admin/fx/sync', 'POST', {});
      const rows = r.updated.length
        ? `<div class="table-scroll"><table>
             <thead><tr><th>Currency</th><th class="num">Was</th><th class="num">Now</th>
               <th class="num">Change</th></tr></thead>
             <tbody>${r.updated.map(u => `<tr><td><strong>${u.code}</strong></td>
               <td class="num">${u.old.toFixed(4)}</td><td class="num">${u.new.toFixed(4)}</td>
               <td class="num"><span class="delta ${u.change_pct > 0 ? 'up' : 'down'}">${
                 u.change_pct > 0 ? '+' : ''}${u.change_pct}%</span></td></tr>`).join('')}
             </tbody></table></div>`
        : '<p class="muted">Every rate already matched the provider — nothing changed.</p>';
      const unmatched = r.unmatched.length
        ? `<div class="danger" style="border-color:#E8D5A8;background:#FFFBF0;margin-top:14px">
             <h3 style="color:#8A6413">Left untouched: ${r.unmatched.map(esc).join(', ')}</h3>
             <p style="color:#7A5A12">The provider does not publish these codes, so your manual
               rates stand. ZWG is the usual one — Zimbabwe's newer code. Set it by hand, or add
               ZWL instead if that matches your invoices.</p></div>`
        : '';
      modal('Rates synced', rows + unmatched,
        '<button class="btn teal" onclick="closeModal();viewSettings()">Done</button>');
      FX = (await api('/api/fx')).fx;
    } catch (e) { reportError(e); }
    done();
  };

  $('dlBackup').onclick = async () => {
    const done = busy($('dlBackup'), 'Preparing…');
    try {
      const data = await api('/api/admin/backup');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
      a.download = 'statspack-portal-backup-' + new Date().toISOString().slice(0, 10) + '.json';
      a.click();
      URL.revokeObjectURL(a.href);
      toast('Backup downloaded.', 'ok');
    } catch (e) { reportError(e); }
    done();
  };
}

let AUDIT_FILTERS = {actor: '', action: '', from: '', to: '', q: '', limit: '300'};

async function viewAudit() {
  setActive('audit', 'Activity log');
  loading('Reading the log…');
  const qs = new URLSearchParams(
    Object.entries(AUDIT_FILTERS).filter(([, v]) => v !== '' && v != null)).toString();
  const d = await api('/api/admin/audit' + (qs ? '?' + qs : ''));

  const opts = (list, sel) => ['<option value="">All</option>']
    .concat(list.map(v => `<option value="${esc(v)}" ${sel === v ? 'selected' : ''}>${esc(v)}</option>`))
    .join('');

  view(`<div class="card">
    <div class="card-head"><h2>Activity log</h2><span class="spacer"></span>
      <button class="btn ghost sm" id="csv">Export CSV</button></div>

    <div class="filterbar">
      <div class="fgroup"><label for="fActor">Who</label>
        <select class="inp" id="fActor">${opts(d.actors, AUDIT_FILTERS.actor)}</select></div>
      <div class="fgroup"><label for="fAction">Action</label>
        <select class="inp" id="fAction">${opts(d.actions, AUDIT_FILTERS.action)}</select></div>
      <div class="fgroup"><label for="fFrom">From</label>
        <input class="inp" id="fFrom" type="date" value="${esc(AUDIT_FILTERS.from)}"></div>
      <div class="fgroup"><label for="fTo">To</label>
        <input class="inp" id="fTo" type="date" value="${esc(AUDIT_FILTERS.to)}"></div>
      <div class="fgroup"><label for="fQ">Search detail</label>
        <input class="inp" id="fQ" placeholder="client, amount, email…" value="${esc(AUDIT_FILTERS.q)}"></div>
      <div class="fgroup"><label for="fLimit">Show</label>
        <select class="inp" id="fLimit">
          ${[100, 300, 500, 1000].map(n =>
            `<option value="${n}" ${String(AUDIT_FILTERS.limit) === String(n) ? 'selected' : ''}>${n} rows</option>`).join('')}
        </select></div>
      <button class="btn teal" id="apply">Apply</button>
      <button class="btn ghost" id="clear">Clear</button>
      <span class="filter-count">${d.matched.toLocaleString()} event(s) match${
        d.matched > d.audit.length ? ` · showing latest ${d.audit.length}` : ''}</span>
    </div>

    ${d.audit.length ? `<div class="table-scroll"><table>
      <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th><th>IP</th></tr></thead>
      <tbody>${d.audit.map(a => `<tr ${d.can_drill ? `class="clickable" data-log="${a.id}"` : ''}>
        <td class="muted" style="white-space:nowrap">${esc(a.created_at)}</td>
        <td>${esc(a.actor)}</td>
        <td><span class="badge ${a.action.indexOf('PURGED') >= 0 ? 'lost'
              : (a.action.indexOf('failed') >= 0 ? 'warn' : '')}">${esc(a.action)}</span></td>
        <td>${esc(a.detail)}${a.meta ? ' <span class="muted" style="font-size:11px">· details</span>' : ''}</td>
        <td class="muted">${esc(a.ip)}</td></tr>`).join('')}</tbody></table></div>`
      : emptyState('Nothing matches', 'Widen the dates or clear the filters.')}
  </div>`);

  const read = () => ({
    actor: val('fActor'), action: val('fAction'), from: val('fFrom'),
    to: val('fTo'), q: val('fQ'), limit: val('fLimit'),
  });
  $('apply').onclick = () => { AUDIT_FILTERS = read(); viewAudit(); };
  $('clear').onclick = () => {
    AUDIT_FILTERS = {actor: '', action: '', from: '', to: '', q: '', limit: '300'};
    viewAudit();
  };
  $('fQ').onkeydown = e => { if (e.key === 'Enter') { AUDIT_FILTERS = read(); viewAudit(); } };
  ['fActor', 'fAction', 'fFrom', 'fTo', 'fLimit'].forEach(id => {
    $(id).onchange = () => { AUDIT_FILTERS = read(); viewAudit(); };
  });
  document.querySelectorAll('tr[data-log]').forEach(tr => tr.onclick =
    () => logDetailModal(tr.dataset.log));
  $('csv').onclick = () => {
    const rows = [['When', 'Who', 'Action', 'Detail', 'IP']].concat(
      d.audit.map(a => [a.created_at, a.actor, a.action, a.detail, a.ip]));
    const csv = rows.map(r => r.map(c =>
      '"' + String(c == null ? '' : c).replace(/"/g, '""') + '"').join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}));
    a.download = 'statspack-activity-log-' + new Date().toISOString().slice(0, 10) + '.csv';
    a.click();
    URL.revokeObjectURL(a.href);
    toast('Activity log exported.', 'ok');
  };
}

async function logDetailModal(id) {
  modal('Log entry', loaderHTML('Opening the record…'), '');
  let d;
  try {
    d = await api('/api/admin/audit/' + id);
  } catch (e) { closeModal(); return toast(e.message, 'err'); }

  const e = d.entry;
  const metaKeys = Object.keys(d.meta || {});
  const pretty = v => v === null || v === undefined || v === ''
    ? '<span class="muted">—</span>'
    : (typeof v === 'object' ? `<pre class="meta">${esc(JSON.stringify(v, null, 2))}</pre>` : esc(String(v)));

  modal(`Log entry #${e.id}`, `
    <dl class="kv">
      <dt>When</dt><dd>${esc(e.created_at)} <span class="muted">(UTC)</span></dd>
      <dt>Action</dt><dd><span class="badge">${esc(e.action)}</span></dd>
      <dt>Who</dt><dd>${esc(e.actor) || '—'}${d.actor ? `
        <div class="muted" style="font-size:12.5px">${esc(d.actor.name)} · ${esc(d.actor.role)}${
          d.actor.is_super ? ' · super user' : ''} · ${esc(d.actor.status)}</div>` : `
        <div class="muted" style="font-size:12.5px">account no longer exists</div>`}</dd>
      <dt>Company</dt><dd>${d.company ? esc(d.company.name) : '<span class="muted">—</span>'}</dd>
      <dt>Subject</dt><dd>${esc(e.target) || '<span class="muted">—</span>'}</dd>
      <dt>Summary</dt><dd>${esc(e.detail) || '<span class="muted">—</span>'}</dd>
      <dt>IP address</dt><dd>${esc(e.ip) || '<span class="muted">—</span>'}</dd>
    </dl>

    ${metaKeys.length ? `
      <h4 style="margin:20px 0 8px;font-size:14px">Recorded detail</h4>
      <dl class="kv">${metaKeys.map(k =>
        `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${pretty(d.meta[k])}</dd>`).join('')}</dl>`
      : '<p class="muted" style="margin-top:18px">No structured detail was captured for this action.</p>'}

    ${d.nearby && d.nearby.length ? `
      <h4 style="margin:20px 0 8px;font-size:14px">Around the same time, by the same person</h4>
      <div class="table-scroll"><table>
        <thead><tr><th>When</th><th>Action</th><th>Detail</th></tr></thead>
        <tbody>${d.nearby.map(n => `<tr class="clickable" data-jump="${n.id}">
          <td class="muted" style="white-space:nowrap">${esc(n.created_at)}</td>
          <td><span class="badge">${esc(n.action)}</span></td>
          <td>${esc(n.detail)}</td></tr>`).join('')}</tbody></table></div>` : ''}`,
    `<button class="btn ghost" onclick="closeModal()">Close</button>
     <button class="btn teal" id="logCopy">Copy as JSON</button>`);

  document.querySelectorAll('tr[data-jump]').forEach(tr =>
    tr.onclick = () => logDetailModal(tr.dataset.jump));
  $('logCopy').onclick = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(d, null, 2));
      toast('Copied to clipboard.', 'ok');
    } catch (err) { toast('Could not copy — your browser blocked it.', 'err'); }
  };
}

async function viewProfile() {
  setActive('profile', 'My profile');
  view(`<div class="card">
    <div class="card-head"><h2>My details</h2></div>
    <div class="card-body">
    ${avatarEditor(ME)}
    <p class="muted" style="text-align:center;margin-bottom:18px;font-size:12.5px">
      Tap the pencil to change your photo. It is resized to 256px before upload.</p>
    ${MY_RULES ? `<div class="card" style="box-shadow:none;border:1px solid var(--line);margin-bottom:16px">
      <div class="card-body"><span class="lbl">My commission</span>
        ${pct(MY_RULES.first_rate)} of the first payment, then ${pct(MY_RULES.recurring_rate)}
        monthly for ${MY_RULES.window_months} months ${rateTag(MY_RULES)}</div></div>` : ''}
    <div class="grid2">
      <div><span class="lbl">Name</span>${esc(ME.name)}</div>
      <div><span class="lbl">Email</span>${esc(ME.email)}</div>
      <div><span class="lbl">Country</span>${esc(ME.country) || '—'}</div>
      <div><span class="lbl">Phone</span>${esc(ME.phone) || '—'}</div>
      <div><span class="lbl">Started</span>${dt(ME.start_date)}</div>
      <div><span class="lbl">Annual quota</span>${ME.quota_usd ? usd0(ME.quota_usd) : '—'}</div>
      <div><span class="lbl">Agent type</span>${esc(ME.agent_type) || '—'}</div>
      <div><span class="lbl">Company</span>${esc((COMPANY && COMPANY.name) || '—')}</div>
    </div>
    <p class="hint" style="margin-top:16px">Ask your StatsPack administrator to correct any of these.</p>
    <div class="btn-row" style="margin-top:14px">
      <button class="btn teal" onclick="openPasswordModal(false)">Change password</button>
      ${ME.avatar ? '<button class="btn ghost" id="rmAvatar">Remove photo</button>' : ''}</div>
    </div></div>`);
  wireAvatar(() => viewProfile());
  if ($('rmAvatar')) $('rmAvatar').onclick = async () => {
    try {
      await api('/api/me/avatar', 'PUT', {avatar: ''});
      ME.avatar = ''; ME.avatar_thumb = ''; paintChrome(); toast('Photo removed.', 'ok'); viewProfile();
    } catch (e) { reportError(e); }
  };
}

/* ---------------------------------------------------------------- router */
const ROUTES = {
  dashboard: () => ME.role === 'admin' ? viewAdminDashboard() : viewAgentDashboard(),
  agents: viewAgents,
  clients: viewClients,
  payments: viewPayments,
  commissions: viewCommissions,
  payouts: viewPayouts,
  settings: viewSettings,
  audit: viewAudit,
  companies: viewCompanies,
  system: viewSystem,
  reports: viewReports,
  teamwork: viewTeamwork,
  profile: viewProfile,
};

async function route() {
  const hash = (location.hash || '#/dashboard').slice(2);
  const [key, arg] = hash.split('/');
  $('sidebar').classList.remove('open');
  $('backdrop').classList.remove('show');
  window.scrollTo(0, 0);
  try {
    if (key === 'agent' && arg) return await viewAgentDetail(arg);
    if (key === 'client' && arg) return await viewClientDetail(arg);
    const fn = ROUTES[key];
    if (!fn) { location.hash = '#/dashboard'; return; }
    if ((key === 'agents' || key === 'settings' || key === 'audit') && ME.role !== 'admin') {
      location.hash = '#/dashboard'; return;
    }
    if ((key === 'companies' || key === 'system') && !ME.is_super) {
      location.hash = '#/dashboard'; return;
    }
    if (key === 'teamwork' && ME.role !== 'agent') { location.hash = '#/dashboard'; return; }
    await fn();
  } catch (e) {
    if (e.message === 'Signed out') return;
    if (e.outage) return view(outageHTML(e.message));
    view(`<div class="empty"><strong>Could not load this page</strong>${esc(e.message)}
      <div class="btn-row" style="justify-content:center;margin-top:14px">
        <button class="btn ghost" onclick="route()">Try again</button></div></div>`);
  }
}
window.addEventListener('hashchange', route);

async function checkAlerts() {
  try {
    const d = await api('/api/admin/alerts');
    const bar = document.getElementById('alertBar');
    if (!d.count) { if (bar) bar.remove(); return; }
    const html = `<div class="alert-bar" id="alertBar">⚠
      <span>${d.count} open incident${d.count > 1 ? 's' : ''}:
        ${esc(d.incidents[0].title)}${d.count > 1 ? ' and others' : ''}</span>
      <a href="#/system">Open system health</a></div>`;
    if (bar) bar.outerHTML = html;
    else document.querySelector('.topbar').insertAdjacentHTML('afterend', html);
  } catch (e) { /* never let the banner break the page */ }
}
setInterval(() => { if (ME && ME.is_super) checkAlerts(); }, 120000);

/* ---------------------------------------------------------------- boot */
(async function boot() {
  if (!token()) { location.replace('/login'); return; }
  try {
    const d = await api('/api/me');
    ME = d.user; SETTINGS = d.settings; STAGES = d.stages;
    AGENT_TYPES = d.agent_types || []; INDUSTRIES = d.industries || [];
    EVENT_KINDS = d.event_kinds || []; COMPANY = d.company; MY_RULES = d.my_rules;
    paintChrome();
    if (ME.must_change_pw) { openPasswordModal(true); return; }
    FX = (await api('/api/fx')).fx;
    const h = await fetch('/api/health').then(r => r.json()).catch(() => null);
    if (h) $('engineChip').textContent = h.version + ' · ' + (h.engine.startsWith('postgres') ? 'Postgres' : 'local');
    route();
    if (ME.is_super) checkAlerts();
  } catch (e) {
    localStorage.removeItem('sp_token');
    location.replace('/login');
  }
})();
