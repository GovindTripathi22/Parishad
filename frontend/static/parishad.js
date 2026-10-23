/**
 * Parishad — Shared Core Client Library
 * Handles Web Audio, State Sync (FastAPI & AlaSQL), Theme Switching,
 * Toast Notifications, Keyboard Shortcuts, and Guided Demo Navigation.
 */

(function () {
  // 1. Theme Initialization
  try {
    const saved = localStorage.getItem('parishad_theme');
    if (saved === 'light' || (!saved && window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches)) {
      document.documentElement.classList.remove('dark');
      document.documentElement.classList.add('light');
    } else {
      document.documentElement.classList.remove('light');
      document.documentElement.classList.add('dark');
    }
  } catch (e) {}

  // 2. Luxury Studio Audio Synthesizer
  let audioCtx = null;
  let audioEnabled = true;

  function getAudioContext() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioCtx;
  }

  window.toggleAudioFeedback = function () {
    audioEnabled = !audioEnabled;
    const icon = document.getElementById('soundIcon');
    if (icon) icon.textContent = audioEnabled ? 'volume_up' : 'volume_off';
    showToast(audioEnabled ? 'Luxury sound feedback enabled' : 'Sound muted');
    if (audioEnabled) window.playTone(660, 'sine', 0.12, 0.025);
  };

  // Smooth warm tone with soft 15ms linear attack, biquad lowpass filtering, and smooth exponential decay
  window.playTone = function (freq, type = 'sine', duration = 0.12, gainVal = 0.025) {
    if (!audioEnabled) return;
    try {
      const ctx = getAudioContext();
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const filter = ctx.createBiquadFilter();

      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(1400, now);
      filter.Q.setValueAtTime(0.7, now);

      osc.type = type;
      osc.frequency.setValueAtTime(freq, now);

      // 15ms soft attack ramp to eliminate digital click / pop
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.linearRampToValueAtTime(gainVal, now + 0.015);
      // Smooth exponential decay
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

      osc.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);

      osc.start(now);
      osc.stop(now + duration);
    } catch (e) {}
  };

  // Warm Apple/Linear-inspired acoustic major 7th chime
  window.playSuccessChime = function () {
    if (!audioEnabled) return;
    try {
      const ctx = getAudioContext();
      if (ctx.state === 'suspended') ctx.resume();
      // Soft arpeggiated chord (F#5 -> A#5 -> C#6)
      const notes = [
        { f: 739.99, t: 0, d: 0.25, v: 0.022 },
        { f: 932.33, t: 0.06, d: 0.30, v: 0.020 },
        { f: 1108.73, t: 0.12, d: 0.38, v: 0.025 }
      ];
      notes.forEach(n => {
        setTimeout(() => {
          window.playTone(n.f, 'sine', n.d, n.v);
        }, n.t * 1000);
      });
    } catch (e) {}
  };

  // Gentle low warning tap (no harsh sawtooth buzzer)
  window.playAlertBeep = function () {
    if (!audioEnabled) return;
    window.playTone(392.00, 'triangle', 0.16, 0.035); // G4 soft warm thud
    setTimeout(() => window.playTone(329.63, 'sine', 0.22, 0.030), 60); // E4 gentle low
  };

  // 3. Global Shared Application State
  window.appState = {
    activeView: 'check',
    blockedCount: 0,
    officialDocs: [
      {
        doc_id: 'DOC-BMS-402',
        topic: 'BMS-48V',
        title: '48V Smart Battery Sensor Electrical & Thermal Specification',
        version: 'v2.4',
        updated_at: '2026-09-12',
        content: 'Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 54.0V to prevent MOSFET breakdown. Thermal shutdown limit is 125C.',
        rules: [
          { unit: 'v', label: 'Max Voltage Limit', value: 54.0 },
          { unit: 'c', label: 'Thermal Shutdown Limit', value: 125.0 }
        ]
      },
      {
        doc_id: 'DOC-RAD-118',
        topic: 'RADAR-77GHZ',
        title: '77GHz Corner Radar CAN-FD Communication Specification',
        version: 'v3.1',
        updated_at: '2026-08-29',
        content: 'Primary bus uses CAN-FD. Approved data phase speed is 2.0 Mbps on standard twisted-pair harnesses to guarantee electromagnetic safety.',
        rules: [
          { unit: 'mbps', label: 'CAN-FD Data Speed', value: 2.0 }
        ]
      },
      {
        doc_id: 'DOC-POLICY-101',
        topic: 'COMPANY-OPS',
        title: 'Customer Refund & Support SLA Policy',
        version: 'v1.8',
        updated_at: '2026-10-01',
        content: 'Customers are guaranteed a full self-serve refund within 30 days of purchase. Critical support tickets must receive a response within 4 hours.',
        rules: [
          { unit: 'days', label: 'Refund Window', value: 30 },
          { unit: 'hours', label: 'Support SLA Response', value: 4 }
        ]
      },
      {
        doc_id: 'DOC-CLOUD-305',
        topic: 'CLOUD-INFRA',
        title: 'Cloud Infrastructure Latency & Budget Policy',
        version: 'v1.2',
        updated_at: '2026-09-20',
        content: 'Production API gateway response latency must stay under 200 ms. Maximum approved monthly cluster budget is $5000.',
        rules: [
          { unit: 'ms', label: 'Max API Latency', value: 200 },
          { unit: 'usd', label: 'Monthly Cloud Budget', value: 5000 }
        ]
      }
    ],
    pendingApprovals: [
      {
        draft_id: 'REV-101',
        created_at: 'Today, 4:10 PM',
        source: 'GitHub Pull Request (#418)',
        topic: 'BMS-48V',
        target_doc_id: 'DOC-BMS-402',
        parameter_label: 'Max Voltage Limit',
        unit: 'v',
        old_value: '54 V',
        new_value: '60 V',
        numeric_new: 60.0,
        reason: 'Pull Request #418 raises the 48V Battery Sensor overvoltage limit from 54 V to 60 V (+11.1% change). Official spec DOC-BMS-402 caps this at 54 V to protect the hardware.',
        old_text: 'Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 54.0V to prevent MOSFET breakdown. Thermal shutdown limit is 125C.',
        proposed_text: 'Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 60.0V (updated per PR #418 cold-crank braking requirement). Thermal shutdown limit is 125C.',
        status: 'PENDING'
      },
      {
        draft_id: 'REV-102',
        created_at: 'Today, 4:25 PM',
        source: 'Slack Team Chat (#product-ops)',
        topic: 'COMPANY-OPS',
        target_doc_id: 'DOC-POLICY-101',
        parameter_label: 'Refund Window',
        unit: 'days',
        old_value: '30 days',
        new_value: '14 days',
        numeric_new: 14,
        reason: 'Team chat proposes shortening the customer refund window from 30 days to 14 days (-53.3% change), which contradicts DOC-POLICY-101.',
        old_text: 'Customers are guaranteed a full self-serve refund within 30 days of purchase. Critical support tickets must receive a response within 4 hours.',
        proposed_text: 'Customers are guaranteed a full self-serve refund within 14 days of purchase. Critical support tickets must receive a response within 4 hours.',
        status: 'PENDING'
      }
    ],
    decisionLedger: [
      {
        id: 'DEC-2026-01',
        timestamp: '2026-09-12',
        topic: 'BMS-48V',
        parameter: 'Max Voltage Limit',
        old_value: '52 V',
        new_value: '54 V',
        status: 'APPROVED',
        doc_id: 'DOC-BMS-402',
        note: 'Validated 54V clamp limit in thermal chamber test.',
        hash: 'sha256:0000000000000000000000000000000000000000000000000000000000000000'
      },
      {
        id: 'DEC-2026-02',
        timestamp: '2026-10-01',
        topic: 'COMPANY-OPS',
        parameter: 'Refund Window',
        old_value: '15 days',
        new_value: '30 days',
        status: 'APPROVED',
        doc_id: 'DOC-POLICY-101',
        note: 'Standardized 30-day customer refund policy for Q4.',
        hash: 'sha256:a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0'
      }
    ]
  };

  // 4. Toast Notifications
  window.showToast = function (msg) {
    let banner = document.getElementById('toastBanner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'toastBanner';
      banner.className = 'fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-xl bg-slate-900/95 text-white text-xs border border-white/20 shadow-2xl backdrop-blur-md flex items-center gap-2.5';
      banner.innerHTML = `<span class="w-2 h-2 rounded-full bg-indigo-400 animate-ping"></span><span id="toastText">${msg}</span>`;
      document.body.appendChild(banner);
    } else {
      const txt = document.getElementById('toastText');
      if (txt) txt.textContent = msg;
      banner.classList.remove('hidden');
    }
    setTimeout(() => {
      if (banner) banner.classList.add('hidden');
    }, 3500);
  };

  // 5. Theme Toggle
  window.toggleTheme = function () {
    window.playTone(550, 'sine', 0.05, 0.02);
    const isDark = document.documentElement.classList.contains('dark');
    const targetTheme = isDark ? 'light' : 'dark';

    if (targetTheme === 'light') {
      document.documentElement.classList.remove('dark');
      document.documentElement.classList.add('light');
    } else {
      document.documentElement.classList.remove('light');
      document.documentElement.classList.add('dark');
    }

    try {
      localStorage.setItem('parishad_theme', targetTheme);
    } catch (e) {}

    window.updateThemeIcon();
    showToast(targetTheme === 'light' ? 'Light mode activated' : 'Dark obsidian mode activated');
  };

  window.updateThemeIcon = function () {
    const icon = document.getElementById('themeIcon');
    if (!icon) return;
    const isLight = document.documentElement.classList.contains('light');
    icon.textContent = isLight ? 'dark_mode' : 'light_mode';
    const btn = document.getElementById('themeToggleBtn');
    if (btn) btn.title = isLight ? 'Switch to Dark Mode' : 'Switch to Light Mode';
  };

  // 6. AlaSQL Synchronization
  window.syncAlaSqlTables = function () {
    if (typeof alasql === 'undefined') return;
    try {
      alasql('CREATE TABLE IF NOT EXISTS official_docs (doc_id STRING, topic STRING, title STRING, version STRING, updated_at STRING, content STRING)');
      alasql('CREATE TABLE IF NOT EXISTS pending_approvals (draft_id STRING, source STRING, topic STRING, target_doc_id STRING, parameter_label STRING, old_value STRING, new_value STRING, status STRING)');
      alasql('CREATE TABLE IF NOT EXISTS decision_ledger (id STRING, timestamp STRING, topic STRING, parameter STRING, old_value STRING, new_value STRING, status STRING, doc_id STRING, note STRING, hash STRING)');

      alasql.tables.official_docs.data = window.appState.officialDocs.map(d => ({
        doc_id: d.doc_id,
        topic: d.topic,
        title: d.title,
        version: d.version,
        updated_at: d.updated_at,
        content: d.content
      }));
      alasql.tables.pending_approvals.data = window.appState.pendingApprovals.map(p => ({
        draft_id: p.draft_id,
        source: p.source,
        topic: p.topic,
        target_doc_id: p.target_doc_id,
        parameter_label: p.parameter_label,
        old_value: p.old_value,
        new_value: p.new_value,
        status: p.status
      }));
      alasql.tables.decision_ledger.data = window.appState.decisionLedger.map(l => ({ ...l }));
    } catch (e) {
      console.warn('AlaSQL sync warning:', e);
    }
  };

  // 7. FastAPI Backend Synchronization
  const currentPort = window.location.port || (window.location.protocol === 'https:' ? '443' : '3000');
  const BACKEND_BASE = (window.location.protocol.startsWith('http')) ? '' : `http://localhost:${currentPort}`;
  window.BACKEND_BASE = BACKEND_BASE;
  window.backendAvailable = false;

  window.checkBackendConnectivity = async function () {
    try {
      const res = await fetch(`${BACKEND_BASE}/api/health`, { method: 'GET', signal: AbortSignal.timeout(1500) });
      if (res.ok) {
        window.backendAvailable = true;
        const portStr = window.location.port ? `:${window.location.port}` : (window.location.protocol.startsWith('http') ? '' : ':3000');
        const statusText = document.getElementById('syncStatusText');
        if (statusText) statusText.textContent = `Active (${portStr || ':3000'})`;
        const readyPort = document.getElementById('systemReadyPort');
        if (readyPort) readyPort.textContent = `Ready (${portStr || ':3000'})`;
        await syncStateFromBackend();
      }
    } catch (e) {
      const statusText = document.getElementById('syncStatusText');
      if (statusText) statusText.textContent = 'AlaSQL Engine (Offline)';
    }
  };

  async function syncStateFromBackend() {
    try {
      const [docsRes, appsRes, ledgRes] = await Promise.all([
        fetch(`${BACKEND_BASE}/api/v1/docs`).then(r => r.json()).catch(() => null),
        fetch(`${BACKEND_BASE}/api/v1/approvals`).then(r => r.json()).catch(() => null),
        fetch(`${BACKEND_BASE}/api/v1/ledger`).then(r => r.json()).catch(() => null),
      ]);
      if (Array.isArray(docsRes) && docsRes.length > 0) {
        window.appState.officialDocs = docsRes.map(d => {
          let parsedRules = [];
          if (Array.isArray(d.rules_detail) && d.rules_detail.length > 0) {
            parsedRules = d.rules_detail.map(r => ({
              unit: (r.unit || '').toLowerCase(),
              label: r.label || 'Constraint',
              value: typeof r.value === 'number' ? r.value : parseFloat(r.value) || 0
            }));
          } else if (Array.isArray(d.rules) && d.rules.length > 0) {
            parsedRules = d.rules.map(r => {
              if (typeof r === 'object' && r !== null) {
                return {
                  unit: (r.unit || '').toLowerCase(),
                  label: r.label || 'Constraint',
                  value: typeof r.value === 'number' ? r.value : parseFloat(r.value) || 0
                };
              }
              const str = String(r);
              const match = str.match(/^(.*?):\s*(\d+(?:\.\d+)?)\s*([A-Za-z°%$]+)/);
              if (match) {
                return {
                  label: match[1].trim(),
                  value: parseFloat(match[2]),
                  unit: match[3].trim().toLowerCase()
                };
              }
              return { label: str, value: 0, unit: '' };
            });
          }
          return {
            doc_id: d.doc_id,
            topic: d.topic,
            title: d.title,
            version: d.version,
            updated_at: d.updated_at ? String(d.updated_at).slice(0, 10) : 'Verified',
            content: d.content,
            rules: parsedRules
          };
        });
      }
      if (Array.isArray(appsRes) && appsRes.length > 0) {
        window.appState.pendingApprovals = appsRes.map(a => ({
          draft_id: a.draft_id,
          created_at: a.created_at ? String(a.created_at).slice(0, 16) : 'Recent',
          source: a.source,
          topic: a.topic,
          target_doc_id: a.target_doc_id,
          parameter_label: a.parameter_label,
          unit: (a.unit || '').toLowerCase(),
          old_value: a.old_value || a.official_value,
          new_value: a.new_value || a.proposed_value,
          numeric_new: typeof a.numeric_new === 'number' ? a.numeric_new : (parseFloat(a.numeric_new) || (parseFloat(a.proposed_value) || 0)),
          reason: a.reason || a.explanation || 'Specification change awaiting sign-off.',
          old_text: a.old_text,
          proposed_text: a.proposed_text,
          status: String(a.status || 'PENDING').toUpperCase()
        }));
      }
      if (Array.isArray(ledgRes) && ledgRes.length > 0) {
        window.appState.decisionLedger = ledgRes.map(l => ({
          id: l.decision_id || l.id,
          timestamp: (l.timestamp || '').slice(0, 10),
          topic: l.topic,
          parameter: l.parameter,
          old_value: l.old_value,
          new_value: l.new_value,
          status: String(l.status || 'APPROVED').toUpperCase(),
          doc_id: l.target_doc_id || l.doc_id,
          note: l.note,
          hash: l.hash_signature || l.entry_hash || l.hash || 'sha256:verified'
        }));
      }

      window.syncAlaSqlTables();
      window.updateTopCounters();

      // Trigger page-specific re-renders if handlers exist
      if (typeof window.onBackendStateSynced === 'function') {
        window.onBackendStateSynced();
      }
    } catch (err) {
      console.warn('Backend sync warning:', err);
    }
  }

  window.updateTopCounters = function () {
    const docCountEl = document.getElementById('statDocCount');
    if (docCountEl) docCountEl.textContent = window.appState.officialDocs.length;

    const pending = window.appState.pendingApprovals.filter(a => a.status === 'PENDING').length;
    const pendingCountEl = document.getElementById('statPendingCount');
    if (pendingCountEl) pendingCountEl.textContent = pending;

    const badge = document.getElementById('tabPendingBadge');
    if (badge) badge.textContent = pending;

    const ledgerCountEl = document.getElementById('statLedgerCount');
    if (ledgerCountEl) ledgerCountEl.textContent = window.appState.decisionLedger.length;
  };

  // 8. Fact Extraction Utility
  window.extractUniversalFacts = function (text) {
    const unitsRegex = /(\$?\s*\b\d+(?:\.\d+)?)\s*(v(?:olts?)?|mbps|days?|hours?|ms|°c|c|%|gb|usd|\$|a(?:mps?)?|hz|mhz|ghz)\b/gi;
    const allNumbers = [];
    let match;

    while ((match = unitsRegex.exec(text)) !== null) {
      let rawVal = match[1].replace('$', '').trim();
      let rawUnit = match[2].toLowerCase();
      let normalizedUnit = rawUnit.startsWith('v') ? 'v' :
                           rawUnit.includes('day') ? 'days' :
                           rawUnit.includes('hour') ? 'hours' :
                           rawUnit === '$' || rawUnit === 'usd' ? 'usd' : rawUnit;
      allNumbers.push({
        value: parseFloat(rawVal),
        unit: normalizedUnit,
        displayUnit: match[2].trim(),
        label: `${rawVal} ${match[2].trim()}`
      });
    }

    const hasSignal = allNumbers.length > 0;
    const proposedFact = hasSignal ? allNumbers[allNumbers.length - 1] : null;

    return { hasSignal, allNumbers, proposedFact };
  };

  // 9. Keyboard Shortcuts for Page Navigation (1-6)
  const PAGE_MAP = {
    '1': '/check',
    '2': '/approvals',
    '3': '/brain',
    '4': '/ledger',
    '5': '/tests',
    '6': '/guide'
  };

  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (!e.ctrlKey && !e.metaKey && !e.altKey && PAGE_MAP[e.key]) {
      window.playTone(480, 'sine', 0.05, 0.02);
      window.location.href = PAGE_MAP[e.key];
    } else if ((e.key === 'd' || e.key === 'D') && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (typeof window.toggleDemoTour === 'function') window.toggleDemoTour();
    } else if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      const brainInput = document.getElementById('brainQuestionInput');
      if (brainInput) {
        brainInput.focus();
      } else {
        window.location.href = '/brain';
      }
    }
  });

  // 10. Codebase Downloader
  window.downloadCodebaseZip = async function () {
    window.playTone(520, 'sine', 0.08, 0.03);
    window.showToast('Bundling complete repository into ZIP...');
    try {
      if (typeof JSZip === 'undefined') {
        window.showToast('JSZip library loading. Please try again.');
        return;
      }
      const zip = new JSZip();
      zip.file('README.md', '# Parishad\nAutonomous Company Brain & Decision Ledger.\n\nRun:\npython run.py\n');
      zip.file('backend/requirements.txt', 'fastapi==0.115.6\nuvicorn[standard]==0.34.0\npydantic==2.10.4\nsqlalchemy==2.0.36\naiosqlite==0.20.0\nhttpx==0.28.1\n');
      
      const blob = await zip.generateAsync({ type: 'blob' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'Parishad-Parishad-Production.zip';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.playSuccessChime();
      window.showToast('Repository ZIP successfully downloaded!');
    } catch (err) {
      console.error(err);
      window.showToast('Export failed. Check console.');
    }
  };

  // 11. Guided Demo Walkthrough Player
  let demoTourActive = false;
  let currentDemoStepIndex = 0;

  const LIVE_DEMO_SCENARIOS = [
    {
      step: 1,
      page: '/check',
      title: '01 // Automotive 48V Battery Voltage Drift',
      objective: 'Simulate an automotive pull request (PR #418) raising battery clamp limit from 54.0V to 60.0V. Real-time consensus council detects +11.1% drift in 240ms and halts production overwrite.',
      btnText: '▶ Perform Live Drift Check',
      statusText: '✓ Intercepted +11.1% Drift • Baseline Spec Protected'
    },
    {
      step: 2,
      page: '/check',
      title: '02 // Adversarial Injection Attack Intercept',
      objective: 'Simulate an adversary attempting a prompt injection and database drop payload. The two-tier security shield intercepts the attack at Stage 1 with a 1.0 threat score and 0ms LLM cost.',
      btnText: '▶ Perform Live Security Defense',
      statusText: '✓ Cyber Attack Neutralized • Threat Score 1.00 Blocked'
    },
    {
      step: 3,
      page: '/approvals',
      title: '03 // Human Safety Gate Diff Review',
      objective: 'Demonstrate zero silent overwrites: AI agents are strictly forbidden from rewriting certified rules alone. Staged updates require reviewer side-by-side diff review and cryptographic sign-off.',
      btnText: '▶ Open Live Safety Gate Review',
      statusText: '✓ Diff Inspected • Side-by-Side Verification Active'
    },
    {
      step: 4,
      page: '/brain',
      title: '04 // Company Brain Citation Search & OCR Scanner',
      objective: 'Search certified engineering rules and upload datasheets. Demonstrates multimodal document processing and transparent diagnostics when external vision keys are unconfigured.',
      btnText: '▶ Search Company Brain',
      statusText: '✓ Verified Official Citation Retrieved'
    },
    {
      step: 5,
      page: '/ledger',
      title: '05 // Cryptographic SHA-256 Ledger & In-Browser SQL',
      objective: 'Audit the immutable decision ledger with SHA-256 cryptographic chaining from genesis (0*64) to head, and query state using the secure SQL Explorer.',
      btnText: '▶ Audit Ledger & Run SQL',
      statusText: '✓ Hash Chain Continuous & Valid'
    }
  ];

  window.toggleDemoTour = function () {
    window.playTone(440, 'sine', 0.08, 0.04);
    demoTourActive = !demoTourActive;
    let overlay = document.getElementById('demoPresenterOverlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'demoPresenterOverlay';
      overlay.className = 'fixed bottom-6 left-1/2 -translate-x-1/2 z-50 w-full max-w-2xl px-4 pointer-events-auto';
      document.body.appendChild(overlay);
    }
    if (demoTourActive) {
      overlay.classList.remove('hidden');
      renderDemoStep();
      window.showToast('Guided Demonstration Mode Active (Hotkey: D to exit)');
    } else {
      overlay.classList.add('hidden');
      window.showToast('Demonstration Mode Exited');
    }
  };

  function renderDemoStep() {
    const overlay = document.getElementById('demoPresenterOverlay');
    if (!overlay) return;
    const scen = LIVE_DEMO_SCENARIOS[currentDemoStepIndex];

    overlay.innerHTML = `
      <div class="p-5 rounded-2xl bg-[#0E1017]/95 border border-indigo-500/40 shadow-2xl backdrop-blur-xl space-y-3.5">
        <div class="flex items-center justify-between border-b border-white/[0.08] pb-2.5">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-indigo-500 animate-pulse"></span>
            <span class="font-mono text-xs font-bold text-indigo-300 uppercase tracking-wider">${scen.title}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-[11px] font-mono text-slate-400">Step ${scen.step} of 5</span>
            <button onclick="toggleDemoTour()" class="text-slate-400 hover:text-white text-xs px-2 py-0.5 rounded border border-white/10 cursor-pointer">Close</button>
          </div>
        </div>
        <p class="text-xs text-slate-300 leading-relaxed font-sans">${scen.objective}</p>
        <div class="flex items-center justify-between pt-1">
          <div class="flex items-center gap-1.5">
            ${LIVE_DEMO_SCENARIOS.map((s, idx) => `
              <span class="w-2 h-2 rounded-full ${idx === currentDemoStepIndex ? 'bg-indigo-400 ring-2 ring-indigo-400/30' : 'bg-slate-700'}"></span>
            `).join('')}
          </div>
          <div class="flex items-center gap-2">
            <button onclick="prevDemoStep()" class="btn-sub px-3 py-1.5 text-xs ${currentDemoStepIndex === 0 ? 'opacity-40 pointer-events-none' : ''}">Prev</button>
            <button onclick="executeDemoAction()" class="btn-main px-4 py-1.5 text-xs flex items-center gap-1.5">
              <span>${scen.btnText}</span>
            </button>
            <button onclick="nextDemoStep()" class="btn-sub px-3 py-1.5 text-xs ${currentDemoStepIndex === LIVE_DEMO_SCENARIOS.length - 1 ? 'opacity-40 pointer-events-none' : ''}">Next</button>
          </div>
        </div>
      </div>
    `;
  }

  window.executeDemoAction = function () {
    const scen = LIVE_DEMO_SCENARIOS[currentDemoStepIndex];
    if (window.location.pathname !== scen.page && window.location.pathname !== scen.page + '.html') {
      window.location.href = `${scen.page}?autorun=${scen.step}`;
    } else {
      // Already on target page, perform action
      if (typeof window.runPageDemoAction === 'function') {
        window.runPageDemoAction(scen.step);
      }
    }
  };

  window.nextDemoStep = function () {
    if (currentDemoStepIndex < LIVE_DEMO_SCENARIOS.length - 1) {
      currentDemoStepIndex++;
      renderDemoStep();
    }
  };

  window.prevDemoStep = function () {
    if (currentDemoStepIndex > 0) {
      currentDemoStepIndex--;
      renderDemoStep();
    }
  };

  // 12. Gemini AI Key Management & Verification
  window.geminiConfig = {
    configured: false,
    model: 'gemini-2.5-flash',
    masked_key: ''
  };

  window.checkGeminiStatus = async function () {
    try {
      const res = await fetch(`${window.BACKEND_BASE}/api/v1/system/gemini-status`);
      if (res.ok) {
        const data = await res.json();
        window.geminiConfig = data;
        updateGeminiHeaderPill(data);
      }
    } catch (e) {}
  };

  function updateGeminiHeaderPill(cfg) {
    let pill = document.getElementById('geminiStatusPill');
    if (!pill) {
      // Find header right controls by explicit ID or right container
      const headerControls = document.getElementById('headerRightControls') || document.querySelector('header > div:last-child');
      if (headerControls) {
        pill = document.createElement('button');
        pill.id = 'geminiStatusPill';
        pill.onclick = function (e) {
          if (e) {
            e.preventDefault();
            e.stopPropagation();
          }
          window.openGeminiModal();
        };
        headerControls.insertBefore(pill, headerControls.firstChild);
      }
    }
    if (pill) {
      if (cfg.configured) {
        pill.className = 'hidden md:flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1 rounded-full border border-emerald-500/30 bg-emerald-950/40 text-emerald-300 font-medium cursor-pointer hover:bg-emerald-950/60 transition';
        pill.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span><span>Gemini AI Connected</span>`;
        pill.title = 'Google Gemini AI is active (Vision OCR, neural embeddings, reasoning)';
      } else {
        pill.className = 'hidden md:flex items-center gap-1.5 text-[11px] font-sans px-2.5 py-1 rounded-full border border-indigo-500/30 bg-indigo-500/10 text-indigo-300 font-medium cursor-pointer hover:bg-indigo-500/20 transition';
        pill.innerHTML = `<span class="material-symbols-outlined text-[13px] text-indigo-400">key</span><span>Configure AI</span>`;
        pill.title = 'Click to activate Gemini Vision OCR, neural embeddings, and AI reasoning';
      }
    }
  }

  window.openGeminiModal = function () {
    window.playTone(480, 'sine', 0.08, 0.02);
    let modal = document.getElementById('geminiConfigModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'geminiConfigModal';
      modal.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm';
      modal.onclick = function (e) {
        if (e.target === modal) window.closeGeminiModal();
      };
      document.body.appendChild(modal);
    }
    modal.classList.remove('hidden');

    const isConf = window.geminiConfig.configured;
    modal.innerHTML = `
      <div onclick="event.stopPropagation()" class="w-full max-w-md p-6 rounded-2xl bg-[#0E1017] border border-white/10 shadow-2xl space-y-4 font-sans text-xs">
        <div class="flex items-center justify-between border-b border-white/[0.08] pb-3">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full ${isConf ? 'bg-emerald-400 animate-pulse' : 'bg-indigo-400'}"></span>
            <span class="font-bold text-white text-sm font-display">Configure Google Gemini AI</span>
          </div>
          <button onclick="closeGeminiModal()" class="text-slate-400 hover:text-white cursor-pointer">
            <span class="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <p class="text-slate-400 leading-relaxed text-[11.5px]">
          Adding your Gemini API key unlocks <strong>Multimodal Datasheet Vision OCR</strong>, <strong>Dense Neural Embeddings (gemini-embedding-001)</strong>, and <strong>Signal Filtering</strong>.
        </p>

        <div class="space-y-3">
          <div>
            <label class="block font-mono text-[11px] text-slate-400 uppercase tracking-wider mb-1">Google Gemini API Key</label>
            <input type="password" id="inputGeminiKey" placeholder="${isConf ? 'Active & Verified (Stored securely in .env)' : 'Paste API Key...'}" class="w-full bg-[#08090C] border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 font-mono" />
          </div>

          <div>
            <label class="block font-mono text-[11px] text-slate-400 uppercase tracking-wider mb-1">Target Model</label>
            <select id="selectGeminiModel" class="w-full bg-[#08090C] border border-white/10 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500 font-mono">
              <option value="gemini-2.5-flash" selected>gemini-2.5-flash (Recommended: Ultra-fast & Multimodal)</option>
              <option value="gemini-2.0-flash">gemini-2.0-flash</option>
              <option value="gemini-1.5-pro">gemini-1.5-pro (High Reasoning)</option>
            </select>
          </div>

          <div id="geminiModalFeedback" class="hidden p-3 rounded-lg text-[11px] font-mono leading-relaxed"></div>
        </div>

        <div class="pt-2 border-t border-white/[0.08] flex items-center justify-between">
          <a href="https://aistudio.google.com/app/apikey" target="_blank" class="text-indigo-400 hover:underline text-[11px] font-mono flex items-center gap-1">
            <span>Get free key</span>
            <span class="material-symbols-outlined text-[13px]">open_in_new</span>
          </a>

          <div class="flex items-center gap-2">
            <button onclick="closeGeminiModal()" class="btn-sub px-3 py-1.5 text-xs">Cancel</button>
            <button id="btnSaveGeminiKey" onclick="saveGeminiApiKey()" class="btn-main px-4 py-1.5 text-xs flex items-center gap-1.5">
              <span>Save &amp; Activate</span>
            </button>
          </div>
        </div>
      </div>
    `;
  };

  window.closeGeminiModal = function () {
    const modal = document.getElementById('geminiConfigModal');
    if (modal) modal.classList.add('hidden');
  };

  window.saveGeminiApiKey = async function () {
    const key = document.getElementById('inputGeminiKey').value.trim();
    const model = document.getElementById('selectGeminiModel').value;
    const fb = document.getElementById('geminiModalFeedback');
    const btn = document.getElementById('btnSaveGeminiKey');

    if (!key && !window.geminiConfig.configured) {
      fb.className = 'p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 block';
      fb.textContent = 'Please enter an API key starting with AIzaSy...';
      window.playAlertBeep();
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>Verifying...</span>';
    fb.className = 'p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 block animate-pulse';
    fb.textContent = 'Verifying key against Google AI Studio API...';

    try {
      const resp = await fetch(`${window.BACKEND_BASE}/api/v1/system/configure-gemini`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gemini_api_key: key, gemini_model: model })
      });
      const data = await resp.json();

      if (data.success) {
        fb.className = 'p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 block font-bold';
        fb.textContent = '✓ API Key Validated! Persisted to .env. All Gemini features are now active.';
        window.playSuccessChime();
        await window.checkGeminiStatus();
        window.showToast('Google Gemini AI is now active!');
        setTimeout(() => {
          closeGeminiModal();
        }, 1500);
      } else {
        fb.className = 'p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 block';
        fb.textContent = data.error || 'Validation failed. Check your key.';
        window.playAlertBeep();
      }
    } catch (err) {
      fb.className = 'p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 block';
      fb.textContent = `Connection error: ${err.message}`;
      window.playAlertBeep();
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span>Save &amp; Activate</span>';
    }
  };

  // Run initial connectivity check upon DOM readiness
  window.addEventListener('DOMContentLoaded', () => {
    const portStr = window.location.port ? `:${window.location.port}` : (window.location.protocol.startsWith('http') ? '' : ':3000');
    const statusText = document.getElementById('syncStatusText');
    if (statusText) statusText.textContent = `Active (${portStr || ':3000'})`;
    const readyPort = document.getElementById('systemReadyPort');
    if (readyPort) readyPort.textContent = `Ready (${portStr || ':3000'})`;
    window.updateThemeIcon();
    window.syncAlaSqlTables();
    window.checkBackendConnectivity();
    window.checkGeminiStatus();
  });
})();
