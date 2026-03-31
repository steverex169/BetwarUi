// main.js - AJAX logic for dashboard
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {} };
  if (body !== null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`HTTP ${res.status}: ${t}`);
  }
  return res.json().catch(()=>null);
}

function el(q) { return document.querySelector(q); }
function els(q) { return Array.from(document.querySelectorAll(q)); }

// Update status badge
async function updateStatus() {
  try {
    const st = await api('/api/scraper/status', 'GET');
    const running = st.running;
    const dot = el('#statusDot'), text = el('#statusText');
    if (running) {
      dot.className = 'h-3 w-3 rounded-full bg-green-500';
      text.textContent = 'Running';
    } else {
      dot.className = 'h-3 w-3 rounded-full bg-red-400';
      text.textContent = 'Stopped';
    }
  } catch(e) {
    console.error(e);
    el('#statusDot').className = 'h-3 w-3 rounded-full bg-gray-300';
    el('#statusText').textContent = 'Unknown';
  }
}

// Populate players table
async function loadPlayers() {
  const tbody = el('#playersTbody');
  tbody.innerHTML = '<tr><td colspan="5" class="p-4 text-slate-500">Loading...</td></tr>';
  try {
    const players = await api('/api/players', 'GET');
    if (!players || players.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="p-4 text-slate-500">No players configured</td></tr>';
      return;
    }
    tbody.innerHTML = '';
    players.forEach(p => {
      const tr = document.createElement('tr');
      tr.dataset.name = p.name;
      tr.innerHTML = `
        <td class="p-2">${p.name}</td>
        <td class="p-2">${(p.sports||[]).join(', ')}</td>
        <td class="p-2">${p.line_adjust||0}</td>
        <td class="p-2">${p.price||0}</td>
        <td class="p-2">
          <button class="editBtn px-2 py-1 bg-yellow-400 rounded">Edit</button>
          <button class="deleteBtn px-2 py-1 bg-red-500 text-white rounded">Delete</button>
        </td>`;
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="p-4 text-red-500">Error loading players: ${e.message}</td></tr>`;
  }
  wireRowButtons();
}

function wireRowButtons() {
  els('.deleteBtn').forEach(btn => {
    btn.onclick = async (ev) => {
      const row = ev.target.closest('tr');
      const name = row.dataset.name;
      if (!confirm(`Delete player "${name}"?`)) return;
      await api(`/api/players/${encodeURIComponent(name)}/delete`, 'POST');
      await loadPlayers();
    };
  });

  els('.editBtn').forEach(btn => {
    btn.onclick = (ev) => {
      const row = ev.target.closest('tr');
      const name = row.dataset.name;
      // set modal values
      document.getElementById('modalTitle').textContent = `Edit ${name}`;
      const cells = row.children;
      document.getElementById('playerName').value = name;
      document.getElementById('playerName').disabled = true;
      document.getElementById('playerSports').value = row.children[1].textContent;
      document.getElementById('playerLine').value = row.children[2].textContent;
      document.getElementById('playerPrice').value = row.children[3].textContent;
      showPlayerModal(true);
    };
  });
}

function showPlayerModal(edit = false) {
  const modal = document.getElementById('playerModal');
  modal.style.display = 'flex';
  modal.classList.remove('hidden');
  if (!edit) {
    document.getElementById('playerName').disabled = false;
    document.getElementById('modalTitle').textContent = 'Add Player';
    document.getElementById('playerForm').reset();
  }
}

function hidePlayerModal() {
  const modal = document.getElementById('playerModal');
  modal.style.display = 'none';
  modal.classList.add('hidden');
}

// Manual bet modal helpers
function showManualBetModal() {
  const modal = document.getElementById('manualBetModal');
  modal.style.display = 'flex';
  modal.classList.remove('hidden');
  document.getElementById('manualBetForm').reset();
  el('#manualBetResult').textContent = '';
}
function hideManualBetModal() {
  const modal = document.getElementById('manualBetModal');
  modal.style.display = 'none';
  modal.classList.add('hidden');
}

document.addEventListener('DOMContentLoaded', async () => {
  // initial load
  await loadPlayers();
  await updateStatus();

  // periodic status update
  setInterval(updateStatus, 5000);

  // start/stop buttons
  document.getElementById('startBtn').onclick = async () => {
    await api('/api/scraper/start', 'POST');
    await updateStatus();
  };
  document.getElementById('stopBtn').onclick = async () => {
    await api('/api/scraper/stop', 'POST');
    await updateStatus();
  };

  // settings save
  document.getElementById('saveSettings').onclick = async () => {
    const headless = !!document.getElementById('headless').checked;
    const betPlace = !!document.getElementById('betPlace').checked;
    const scrapeInterval = parseInt(document.getElementById('scrapeInterval').value || 20);
    const chromeProxy = document.getElementById('chromeProxy').value || '';
    await api('/api/settings', 'POST', {
      headless, bet_place_enabled: betPlace, scrape_interval: scrapeInterval, chrome_proxy: chromeProxy
    });
    alert('Settings saved');
  };

  // open add modal
  document.getElementById('openAddPlayer').onclick = () => showPlayerModal(false);

  // open manual bet modal
  document.getElementById('openManualBet').onclick = () => showManualBetModal();

  // cancel modals
  document.getElementById('cancelPlayer').onclick = () => hidePlayerModal();
  document.getElementById('cancelManualBet').onclick = () => hideManualBetModal();

  // save player
  document.getElementById('playerForm').onsubmit = async (ev) => {
    ev.preventDefault();
    const name = document.getElementById('playerName').value.trim();
    const sports = document.getElementById('playerSports').value.split(',').map(s=>s.trim()).filter(Boolean);
    const line_adjust = parseInt(document.getElementById('playerLine').value || 0);
    const price = parseFloat(document.getElementById('playerPrice').value || 0);

    if (!name) { alert('Name required'); return; }

    // if disabled=false => new; if disabled=true => update
    if (document.getElementById('playerName').disabled) {
      await api(`/api/players/${encodeURIComponent(name)}/update`, 'POST', {sports, line_adjust, price});
    } else {
      await api('/api/players', 'POST', {name, sports, line_adjust, price});
    }
    hidePlayerModal();
    await loadPlayers();
  };

  // manual bet submit
  document.getElementById('manualBetForm').onsubmit = async (ev) => {
    ev.preventDefault();

    const payload = {
      player: document.getElementById('betPlayer').value.trim(),
      sport: document.getElementById('betSport').value.trim(),
      rot: document.getElementById('betRot').value.trim(),
      selection: document.getElementById('betSelection').value.trim(),
      line: parseFloat(document.getElementById('betLine').value || 0),
      ou: (document.getElementById('betOU').value || '').trim(),
      odds: document.getElementById('betOdds').value.trim(),
      stake: parseFloat(document.getElementById('betStake').value || 0),
      note: document.getElementById('betNote').value.trim()
    };

    // minimal local validation
    if (!payload.player || !payload.selection || !payload.stake) {
      el('#manualBetResult').textContent = 'Player, selection and stake are required.';
      return;
    }

    el('#manualBetResult').textContent = 'Running dry-run... check server logs — waiting for response...';

    try {
      const res = await api('/submit_bet', 'POST', payload);
      el('#manualBetResult').textContent = `Result: ${res.status} — ${res.message || JSON.stringify(res)}`;
    } catch (err) {
      el('#manualBetResult').textContent = `Error: ${err.message}`;
    }
  };
});
