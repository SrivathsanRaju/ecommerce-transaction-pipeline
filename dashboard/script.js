// ===================== helpers =====================
const fmtInt = n => n.toLocaleString('en-US');
const fmtPctVal = n => n.toFixed(2) + '%';           // value already IS a percent number (e.g. 3.96)
const fmtMoney = n => '₹' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
const fmtRunLabel = iso => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
const fmtRunFull = iso => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const COLORS = {
  accent: '#5ee6c8',
  accent2: '#7aa2ff',
  warn: '#f0b25b',
  bad: '#f0655b',
  good: '#5ee6a0',
  grid: 'rgba(255,255,255,0.05)',
  text: '#838d9c',
};

Chart.defaults.font.family = "ui-monospace, monospace";
Chart.defaults.font.size = 11;
Chart.defaults.color = COLORS.text;

function baseGrid() { return { color: COLORS.grid, drawTicks: false }; }

// ===================== load data =====================
fetch('data.json')
  .then(r => r.json())
  .then(render)
  .catch(err => {
    document.getElementById('overallStatus').textContent = 'data.json not found';
    document.getElementById('overallStatus').classList.add('bad');
    console.error(err);
  });

function render(data) {
  const { pipeline_health, transaction_summary, recent_flagged, generated_at } = data;

  document.getElementById('lastUpdated').textContent = 'snapshot: ' + fmtRunFull(generated_at);
  document.getElementById('footerDate').textContent = generated_at;
  document.getElementById('overallStatus').textContent = 'up to date';

  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('view-' + btn.dataset.view).classList.add('active');
    });
  });

  renderHealth(pipeline_health);
  renderBusiness(transaction_summary, recent_flagged || []);
}

// ===================== HEALTH VIEW =====================
function renderHealth(rows) {
  rows = [...rows].sort((a, b) => a.run_timestamp.localeCompare(b.run_timestamp));
  const latest = rows[rows.length - 1];
  const prev = rows[rows.length - 2];
  const labels = rows.map(r => fmtRunLabel(r.run_timestamp));

  // KPIs
  document.getElementById('kpiRows').textContent = fmtInt(latest.raw_row_count);
  document.getElementById('kpiRowsSub').textContent = fmtRunFull(latest.run_timestamp);

  document.getElementById('kpiDropped').textContent = fmtInt(latest.rows_dropped);
  document.getElementById('kpiDroppedSub').textContent = fmtInt(latest.clean_row_count) + ' rows made it to clean';

  document.getElementById('kpiNull').textContent = fmtPctVal(latest.null_rate_pct);
  document.getElementById('kpiDup').textContent = fmtPctVal(latest.duplicate_rate_pct);

  // cumulative total across tracked history (sums every run currently in data.json —
  // export_data.py keeps a rolling 30-day window, so this resets past that, not a true lifetime count)
  const totalRaw = rows.reduce((sum, r) => sum + r.raw_row_count, 0);
  const totalClean = rows.reduce((sum, r) => sum + r.clean_row_count, 0);
  document.getElementById('kpiTotal').textContent = fmtInt(totalRaw);
  document.getElementById('kpiTotalSub').textContent =
    fmtInt(totalClean) + ' clean · ' + rows.length + ' run' + (rows.length === 1 ? '' : 's') + ' tracked';

  if (prev) {
    const dNull = latest.null_rate_pct - prev.null_rate_pct;
    document.getElementById('kpiNullSub').textContent = (dNull >= 0 ? '+' : '') + dNull.toFixed(2) + 'pp vs prior run';
    const dDup = latest.duplicate_rate_pct - prev.duplicate_rate_pct;
    document.getElementById('kpiDupSub').textContent = (dDup >= 0 ? '+' : '') + dDup.toFixed(2) + 'pp vs prior run';
  }

  // row counts chart
  new Chart(document.getElementById('chartRowCounts'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'raw', data: rows.map(r => r.raw_row_count), borderColor: COLORS.accent2, backgroundColor: 'transparent', borderWidth: 2, pointRadius: 0, tension: 0.3 },
        { label: 'clean', data: rows.map(r => r.clean_row_count), borderColor: COLORS.accent, backgroundColor: 'transparent', borderWidth: 2, pointRadius: 0, tension: 0.3 },
      ]
    },
    options: lineOpts({ legend: true })
  });

  // quality drift chart
  new Chart(document.getElementById('chartQuality'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'null rate', data: rows.map(r => r.null_rate_pct), borderColor: COLORS.bad, backgroundColor: 'rgba(240,101,91,0.08)', fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3 },
        { label: 'duplicate rate', data: rows.map(r => r.duplicate_rate_pct), borderColor: COLORS.warn, backgroundColor: 'rgba(240,178,91,0.08)', fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3 },
      ]
    },
    options: lineOpts({ legend: true, isPct: true })
  });

  // drop breakdown (latest run)
  document.getElementById('dropBreakdownSub').textContent = fmtRunFull(latest.run_timestamp);
  const breakdown = [
    ['null amount', latest.null_amount_count],
    ['null location', latest.null_location_count],
    ['bad payment method', latest.bad_payment_method_count],
    ['duplicate', latest.duplicate_count],
  ];
  new Chart(document.getElementById('chartDropBreakdown'), {
    type: 'bar',
    data: {
      labels: breakdown.map(([k]) => k),
      datasets: [{
        data: breakdown.map(([, v]) => v),
        backgroundColor: [COLORS.bad, COLORS.warn, '#c792ea', COLORS.accent2],
        borderRadius: 4,
        maxBarThickness: 44,
      }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { grid: baseGrid() }, y: { grid: { display: false } } },
      responsive: true,
    }
  });

  // recent runs table
  const tbody = document.querySelector('#runsTable tbody');
  tbody.innerHTML = '';
  [...rows].reverse().slice(0, 20).forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtRunFull(r.run_timestamp)}</td>
      <td>${fmtInt(r.raw_row_count)}</td>
      <td>${fmtInt(r.clean_row_count)}</td>
      <td>${fmtInt(r.rows_dropped)}</td>
      <td>${fmtPctVal(r.null_rate_pct)}</td>
      <td>${fmtPctVal(r.duplicate_rate_pct)}</td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById('recentRunsSub').textContent = Math.min(rows.length, 20) + ' most recent';
}

// ===================== BUSINESS VIEW =====================
function renderBusiness(txn, flagged) {
  txn = [...txn].sort((a, b) => a.run_timestamp.localeCompare(b.run_timestamp));
  const latest = txn[txn.length - 1];
  const prev = txn[txn.length - 8] || txn[0];
  const labels = txn.map(r => fmtRunLabel(r.run_timestamp));

  document.getElementById('bizCount').textContent = fmtInt(latest.total_transactions);
  document.getElementById('bizVolume').textContent = fmtMoney(latest.total_transaction_value);
  document.getElementById('bizAvg').textContent = '₹' + latest.average_transaction_value.toFixed(2);
  document.getElementById('bizFlagRate').textContent = fmtPctVal(latest.flagged_rate_pct);
  document.getElementById('bizFlagSub').textContent = fmtInt(latest.flagged_transactions) + ' flagged';

  if (prev && prev !== latest) {
    const pct = ((latest.total_transactions - prev.total_transactions) / prev.total_transactions) * 100;
    document.getElementById('bizCountSub').textContent = (pct >= 0 ? '+' : '') + pct.toFixed(1) + '% vs 7 runs ago';
    const pctV = ((latest.total_transaction_value - prev.total_transaction_value) / prev.total_transaction_value) * 100;
    document.getElementById('bizVolumeSub').textContent = (pctV >= 0 ? '+' : '') + pctV.toFixed(1) + '% vs 7 runs ago';
  }

  new Chart(document.getElementById('chartVolume'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'volume', data: txn.map(r => r.total_transaction_value),
        borderColor: COLORS.accent2, backgroundColor: 'rgba(122,162,255,0.08)',
        fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3,
      }]
    },
    options: lineOpts({ money: true })
  });

  new Chart(document.getElementById('chartFlagged'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'flagged rate', data: txn.map(r => r.flagged_rate_pct),
        borderColor: COLORS.warn, backgroundColor: 'rgba(240,178,91,0.1)',
        fill: true, borderWidth: 2, pointRadius: 0, tension: 0.3,
      }]
    },
    options: lineOpts({ isPct: true })
  });

  document.getElementById('flagReasonDate').textContent = fmtRunFull(latest.run_timestamp);
  new Chart(document.getElementById('chartReasons'), {
    type: 'bar',
    data: {
      labels: ['amount outlier', 'high velocity'],
      datasets: [{
        data: [latest.amount_outlier_count, latest.high_velocity_count],
        backgroundColor: [COLORS.accent, COLORS.accent2],
        borderRadius: 4,
        maxBarThickness: 44,
      }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { grid: baseGrid(), ticks: { precision: 0 } }, y: { grid: { display: false } } },
      responsive: true,
    }
  });

  // recent flagged transactions table
  const tbody = document.querySelector('#flaggedTable tbody');
  tbody.innerHTML = '';
  flagged.slice(0, 25).forEach(f => {
    const reasons = [];
    if (f.flag_amount_outlier) reasons.push('amount outlier');
    if (f.flag_high_velocity) reasons.push('high velocity');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${fmtRunFull(f.timestamp)}</td>
      <td>${f.transaction_id}</td>
      <td>₹${Number(f.amount).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
      <td>${f.location}</td>
      <td>${f.merchant_category}</td>
      <td>${f.payment_method}</td>
      <td>${reasons.join(', ') || '—'}</td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById('flaggedTableSub').textContent = flagged.length + ' most recent';
}

// ===================== chart option builders =====================
function lineOpts({ legend = false, isPct = false, money = false } = {}) {
  return {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: legend, labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'circle' } },
      tooltip: {
        backgroundColor: '#12161d',
        borderColor: '#1e232c',
        borderWidth: 1,
        callbacks: isPct ? { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(2)}%` }
          : money ? { label: c => `${c.dataset.label}: ₹${c.parsed.y.toLocaleString()}` }
          : undefined,
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
      y: {
        grid: baseGrid(),
        ticks: isPct ? { callback: v => v.toFixed(1) + '%' }
          : money ? { callback: v => '₹' + (v / 1000).toFixed(0) + 'k' }
          : { callback: v => v >= 1000 ? (v / 1000) + 'k' : v },
      },
    },
  };
}