const COLORS = ['#29b5e8','#ff6384','#ffce56','#4caf50','#9c27b0','#ff9800','#00bcd4','#e91e63'];
let ordersChart, segmentChart, regionChart;
const latencies = [];
const queryTimes = [];
let currentWarehouse = 'interactive';
let currentScale = '10';
let currentSegment = '';
let currentLookback = '90';
let fetchGeneration = 0;
let segmentsGeneration = 0;
let locustPollTimer = null;
let locustAvailable = false;
let locustRunning = false;

function resetStatsDisplay() {
    document.getElementById('stat-count').textContent = '0';
    document.getElementById('stat-avg').textContent = '\u2014';
    document.getElementById('stat-min').textContent = '\u2014';
    document.getElementById('stat-max').textContent = '\u2014';
    document.getElementById('stat-qavg').textContent = '\u2014';
    document.getElementById('stat-qmin').textContent = '\u2014';
    document.getElementById('stat-qmax').textContent = '\u2014';
}

function panelsForFetch() {
    const panels = ['kpis', 'orders-time', 'region', 'latest'];
    if (!currentSegment) panels.splice(2, 0, 'segment');
    return panels;
}

function setPanelsLoading(loading, panels) {
    if (!panels) panels = panelsForFetch();
    for (const id of panels) {
        document.querySelector('[data-panel="' + id + '"]')?.classList.toggle('is-loading', loading);
    }
}

function initCharts() {
    const commonOpts = {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
            x: { grid: { color: '#2a2d37' }, ticks: { color: '#888', font: { size: 10 } } },
            y: { grid: { color: '#2a2d37' }, ticks: { color: '#888', font: { size: 10 } } }
        }
    };

    ordersChart = new Chart(document.getElementById('ordersChart'), {
        type: 'line',
        data: { labels: [], datasets: [
            { label: 'Orders', data: [], borderColor: '#29b5e8', backgroundColor: 'rgba(41,181,232,0.1)', fill: true, tension: 0.3, pointRadius: 0 },
            { label: 'Revenue', data: [], borderColor: '#4caf50', backgroundColor: 'rgba(76,175,80,0.05)', fill: true, tension: 0.3, pointRadius: 0, yAxisID: 'y1' }
        ]},
        options: {
            ...commonOpts,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { display: true, labels: { color: '#888', font: { size: 10 } } } },
            scales: {
                ...commonOpts.scales,
                x: { ...commonOpts.scales.x, type: 'time', time: { unit: 'day', displayFormats: { day: 'MMM d' } } },
                y: { ...commonOpts.scales.y, position: 'left', title: { display: true, text: 'Orders', color: '#888' } },
                y1: { ...commonOpts.scales.y, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'Revenue', color: '#888' } }
            }
        }
    });

    segmentChart = new Chart(document.getElementById('segmentChart'), {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: COLORS, borderWidth: 0 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { position: 'right', labels: { color: '#ccc', font: { size: 10 }, padding: 8 } } },
        },
    });

    regionChart = new Chart(document.getElementById('regionChart'), {
        type: 'bar',
        data: { labels: [], datasets: [{ label: 'Orders', data: [], backgroundColor: COLORS.slice(0, 5), borderRadius: 4 }] },
        options: { ...commonOpts, maintainAspectRatio: false, animation: false, indexAxis: 'y' }
    });
}

function apiParams() {
    const p = new URLSearchParams();
    p.set('warehouse', currentWarehouse);
    p.set('scale', currentScale);
    p.set('lookback', currentLookback);
    if (currentSegment) p.set('segment', currentSegment);
    return p;
}

function clearSegmentOptions() {
    const sel = document.getElementById('segment-filter');
    sel.querySelectorAll('option:not(:first-child)').forEach(function(o) { o.remove(); });
    sel.value = '';
}

async function loadSegments() {
    const gen = ++segmentsGeneration;
    const warehouse = currentWarehouse;
    const scale = currentScale;
    const sel = document.getElementById('segment-filter');
    sel.disabled = true;
    try {
        const p = new URLSearchParams();
        p.set('warehouse', warehouse);
        p.set('scale', scale);
        const res = await fetch('/api/segments?' + p, { cache: 'no-store' });
        const names = await res.json();
        if (gen !== segmentsGeneration) return;
        if (warehouse !== currentWarehouse || scale !== currentScale) return;
        clearSegmentOptions();
        for (const name of names) {
            const o = document.createElement('option');
            o.value = name;
            o.textContent = name;
            sel.appendChild(o);
        }
        if (currentSegment && names.includes(currentSegment)) {
            sel.value = currentSegment;
        } else {
            sel.value = '';
            currentSegment = '';
        }
    } finally {
        if (gen === segmentsGeneration) sel.disabled = false;
    }
}

async function refreshDashboard(reloadSegments, resetSegment) {
    if (resetSegment) {
        currentSegment = '';
        clearSegmentOptions();
        updateChartsLayout();
    }
    latencies.length = 0;
    queryTimes.length = 0;
    resetStatsDisplay();
    const gen = ++fetchGeneration;
    setPanelsLoading(true);
    if (reloadSegments) await loadSegments();
    if (gen !== fetchGeneration) return;
    fetchData(gen);
}

function setWarehouse(wh) {
    if (locustRunning) return;
    const warehouseChanged = wh !== currentWarehouse;
    currentWarehouse = wh;
    document.getElementById('lbl-interactive').className = wh === 'interactive' ? 'active' : '';
    document.getElementById('lbl-standard').className = wh === 'standard' ? 'active' : '';
    void refreshDashboard(true, warehouseChanged);
}

function setScale(scale) {
    if (scale === currentScale) return;
    currentScale = scale;
    void refreshDashboard(true, true);
}

async function timedFetch(path) {
    const u = new URL(path, window.location.origin);
    const params = apiParams();
    for (const [k, v] of params) u.searchParams.set(k, v);
    const start = performance.now();
    const res = await fetch(u.pathname + u.search);
    const data = await res.json();
    latencies.push(performance.now() - start);
    const qt = res.headers.get('X-Query-Time-Ms');
    if (qt) queryTimes.push(Number(qt));
    return data;
}

function updateChartsLayout() {
    const grid = document.getElementById('charts-grid');
    grid.classList.toggle('charts-grid--segment-filtered', Boolean(currentSegment));
    requestAnimationFrame(function() {
        [ordersChart, segmentChart, regionChart].forEach(function(c) { if (c) c.resize(); });
    });
}

function updateStats() {
    if (latencies.length === 0) {
        resetStatsDisplay();
        return;
    }
    const avg = latencies.reduce(function(a, b) { return a + b; }, 0) / latencies.length;
    document.getElementById('stat-count').textContent = latencies.length;
    document.getElementById('stat-avg').textContent = Math.round(avg);
    document.getElementById('stat-min').textContent = Math.round(Math.min.apply(null, latencies));
    document.getElementById('stat-max').textContent = Math.round(Math.max.apply(null, latencies));
    if (queryTimes.length > 0) {
        const qavg = queryTimes.reduce(function(a, b) { return a + b; }, 0) / queryTimes.length;
        document.getElementById('stat-qavg').textContent = Math.round(qavg);
        document.getElementById('stat-qmin').textContent = Math.round(Math.min.apply(null, queryTimes));
        document.getElementById('stat-qmax').textContent = Math.round(Math.max.apply(null, queryTimes));
    }
}

function statusClass(status) {
    const s = (status || '').toUpperCase();
    if (s === 'F') return 'status-F';
    if (s === 'O') return 'status-O';
    return 'status-P';
}

function formatRevenueMillions(value) {
    const millions = Number(value || 0) / 1e6;
    return '$' + millions.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + 'M';
}

async function fetchData(expectedGen) {
    const gen = expectedGen != null ? expectedGen : ++fetchGeneration;
    const panels = panelsForFetch();
    setPanelsLoading(true, panels);
    try {
        const [timeData, kpis, segments, regions, latest, tableStats] = await Promise.all([
            timedFetch('/api/orders-over-time'),
            timedFetch('/api/kpis'),
            currentSegment ? Promise.resolve(null) : timedFetch('/api/by-segment'),
            timedFetch('/api/by-region'),
            timedFetch('/api/latest-orders'),
            timedFetch('/api/table-stats'),
        ]);

        if (gen !== fetchGeneration) return;

        const suffix = currentSegment ? ' \u2014 ' + currentSegment : '';
        document.getElementById('h-orders-time').textContent = 'Orders & Revenue Over Time \u2014 last ' + currentLookback + ' days (SF' + currentScale + ')' + suffix;
        document.getElementById('h-by-region').textContent = 'Orders by Region (SF' + currentScale + ')' + suffix;
        document.getElementById('h-latest').textContent = 'Latest Orders (SF' + currentScale + ')' + suffix;

        document.getElementById('kpi-orders').textContent = (kpis.TOTAL_ORDERS || 0).toLocaleString();
        document.getElementById('kpi-revenue').textContent = formatRevenueMillions(kpis.TOTAL_REVENUE);
        document.getElementById('kpi-customers').textContent = (kpis.TOTAL_CUSTOMERS || 0).toLocaleString();
        document.getElementById('kpi-lineitems').textContent = (kpis.TOTAL_LINE_ITEMS || 0).toLocaleString();
        document.getElementById('kpi-aov').textContent = '$' + Number(kpis.AVG_ORDER_VALUE || 0).toFixed(2);

        ordersChart.data.labels = timeData.map(function(r) { return r.ORDER_DAY; });
        ordersChart.data.datasets[0].data = timeData.map(function(r) { return r.TOTAL_ORDERS; });
        ordersChart.data.datasets[1].data = timeData.map(function(r) { return r.TOTAL_REVENUE; });
        ordersChart.update('none');

        if (segments) {
            segmentChart.data.labels = segments.map(function(r) { return r.MARKET_SEGMENT; });
            segmentChart.data.datasets[0].data = segments.map(function(r) { return r.REVENUE; });
            segmentChart.update('none');
        }

        regionChart.data.labels = regions.map(function(r) { return r.REGION; });
        regionChart.data.datasets[0].data = regions.map(function(r) { return r.ORDER_COUNT; });
        regionChart.update('none');

        const tbody = document.querySelector('#latestTable tbody');
        tbody.innerHTML = latest.map(function(r) {
            return '<tr>'
                + '<td>' + (r.ORDER_DATE ? new Date(r.ORDER_DATE).toLocaleDateString() : '\u2014') + '</td>'
                + '<td class="' + statusClass(r.STATUS) + '">' + (r.STATUS || '\u2014') + '</td>'
                + '<td>$' + Number(r.TOTAL_AMOUNT).toFixed(2) + '</td>'
                + '<td>' + (r.MARKET_SEGMENT || '\u2014') + '</td>'
                + '<td>' + (r.REGION || '\u2014') + '</td>'
                + '</tr>';
        }).join('');
    } catch (err) {
        console.error('Fetch error:', err);
    } finally {
        if (gen === fetchGeneration) setPanelsLoading(false, panels);
    }
    updateStats();
    updateChartsLayout();
}

async function waitForReady(maxWaitMs) {
    if (!maxWaitMs) maxWaitMs = 120000;
    const banner = document.getElementById('ready-banner');
    const msg = document.getElementById('ready-msg');
    const start = Date.now();
    console.log('[dashboard] Waiting for API readiness (/api/ready)...');
    msg.textContent = 'Waiting for API service to be ready\u2026';
    while (Date.now() - start < maxWaitMs) {
        var elapsed = Math.round((Date.now() - start) / 1000);
        try {
            const res = await fetch('/api/ready');
            if (res.ok) {
                console.log('[dashboard] API ready after ' + elapsed + 's');
                banner.classList.add('hidden');
                return true;
            }
            console.log('[dashboard] /api/ready returned ' + res.status + ', retrying...');
            if (res.status === 503) {
                msg.textContent = 'Warming up Snowflake connection pool\u2026 (' + elapsed + 's)';
            } else {
                msg.textContent = 'Waiting for API service to be ready\u2026 (' + elapsed + 's)';
            }
        } catch (e) {
            console.log('[dashboard] /api/ready not reachable yet: ' + e.message);
            msg.textContent = 'Waiting for API service to be ready\u2026 (' + elapsed + 's)';
        }
        await new Promise(function(r) { setTimeout(r, 1000); });
    }
    console.warn('[dashboard] API readiness timed out after ' + (maxWaitMs / 1000) + 's');
    msg.textContent = 'API readiness timed out. The dashboard may be slow initially.';
    setTimeout(function() { banner.classList.add('hidden'); }, 5000);
    return false;
}

async function initDashboard() {
    console.log('[dashboard] Initializing...');
    await waitForReady();
    try {
        console.log('[dashboard] Fetching /api/config...');
        const res = await fetch('/api/config');
        const cfg = await res.json();
        console.log('[dashboard] Config received:', JSON.stringify(cfg));
        if (cfg.defaultScale && document.getElementById('scale-select').querySelector('option[value="' + cfg.defaultScale + '"]')) {
            currentScale = cfg.defaultScale;
            document.getElementById('scale-select').value = cfg.defaultScale;
        }
        if (cfg.lookbackDays && document.getElementById('lookback-select').querySelector('option[value="' + cfg.lookbackDays + '"]')) {
            currentLookback = String(cfg.lookbackDays);
            document.getElementById('lookback-select').value = currentLookback;
        }
        if (cfg.locustAvailable) {
            console.log('[dashboard] Locust integration enabled, showing panel');
            locustAvailable = true;
            document.getElementById('locust-panel').classList.add('visible');
            startLocustPolling();
        } else {
            console.log('[dashboard] Locust integration not available (locustAvailable=' + cfg.locustAvailable + ')');
        }
    } catch (err) {
        console.warn('[dashboard] Could not load config:', err);
    }
    console.log('[dashboard] Loading segments and dashboard data...');
    await loadSegments();
    await fetchData();
    console.log('[dashboard] Init complete.');
}

// ----- Locust integration -----
function updateLocustUI(data) {
    const state = data.state || 'stopped';
    const el = document.getElementById('locust-state');
    el.textContent = state;
    el.className = 'locust-state locust-state--' + state.replace(/[^a-z]/g, '');
    document.getElementById('locust-user-count').textContent = data.userCount || 0;
    document.getElementById('locust-rps').textContent = data.totalRps || 0;
    const running = state === 'running' || state === 'spawning';
    locustRunning = running;
    document.getElementById('locust-start').disabled = running;
    document.getElementById('locust-stop').disabled = !running;
    document.getElementById('lbl-interactive').classList.toggle('disabled', running);
    document.getElementById('lbl-standard').classList.toggle('disabled', running);

}

async function pollLocustStatus() {
    try {
        const res = await fetch('/api/locust/status');
        const data = await res.json();
        if (!data.available) {
            console.log('[locust] Poll: not available');
            return;
        }
        console.log('[locust] Poll: state=' + data.state + ' users=' + data.userCount + ' rps=' + data.totalRps);
        updateLocustUI(data);
    } catch (e) {
        console.warn('[locust] Poll error:', e.message);
    }
}

function startLocustPolling() {
    if (locustPollTimer) return;
    pollLocustStatus();
    locustPollTimer = setInterval(pollLocustStatus, 2000);
}

function stopLocustPolling() {
    if (locustPollTimer) { clearInterval(locustPollTimer); locustPollTimer = null; }
}

async function locustStart() {
    const userCount = parseInt(document.getElementById('locust-users').value, 10) || 10;
    const spawnRate = parseInt(document.getElementById('locust-spawn').value, 10) || 5;
    console.log('[locust] Starting: users=' + userCount + ' spawnRate=' + spawnRate + ' wh=' + currentWarehouse + ' scale=' + currentScale + ' lookback=' + currentLookback);
    document.getElementById('locust-start').disabled = true;
    try {
        const res = await fetch('/api/locust/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                userCount: userCount,
                spawnRate: spawnRate,
                warehouse: currentWarehouse,
                scale: currentScale,
                lookback: currentLookback,
            }),
        });
        const data = await res.json();
        console.log('[locust] Start response:', JSON.stringify(data));
        startLocustPolling();
    } catch (err) {
        console.error('[locust] Start error:', err);
        document.getElementById('locust-start').disabled = false;
    }
}

async function locustStop() {
    console.log('[locust] Stopping...');
    document.getElementById('locust-stop').disabled = true;
    try {
        const res = await fetch('/api/locust/stop', { method: 'POST' });
        const data = await res.json();
        console.log('[locust] Stop response:', JSON.stringify(data));
        setTimeout(pollLocustStatus, 500);
    } catch (err) {
        console.error('[locust] Stop error:', err);
        document.getElementById('locust-stop').disabled = false;
    }
}

// ----- Bootstrap -----
initCharts();
document.getElementById('lbl-interactive').addEventListener('click', function() { setWarehouse('interactive'); });
document.getElementById('lbl-standard').addEventListener('click', function() { setWarehouse('standard'); });
document.getElementById('locust-start').addEventListener('click', locustStart);
document.getElementById('locust-stop').addEventListener('click', locustStop);
document.getElementById('scale-select').addEventListener('change', function(e) { setScale(e.target.value); });
document.getElementById('lookback-select').addEventListener('change', function(e) {
    currentLookback = e.target.value;
    void refreshDashboard(true, false);
});
document.getElementById('segment-filter').addEventListener('change', function(e) {
    currentSegment = e.target.value;
    latencies.length = 0;
    queryTimes.length = 0;
    resetStatsDisplay();
    updateChartsLayout();
    const gen = ++fetchGeneration;
    fetchData(gen);
});
initDashboard();
