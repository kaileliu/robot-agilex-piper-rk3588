(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const pct = value => finite(value) ? `${value.toFixed(1)}%` : '—';
  const temp = value => finite(value) ? `${value.toFixed(1)} °C` : '—';
  const freq = value => finite(value) ? `${Math.round(value)} MHz` : '频率未提供';
  const colors = {cpu: '#8ee6b7', npu: '#74b9ff', gpu: '#c2a2ff', ram: '#f4c67c'};
  const labels = {cpu: 'CPU', npu: 'NPU', gpu: 'GPU', ram: '内存'};
  let history = [], range = 600, hoverTime = null, lastSample = null;
  let plot = {left: 45, top: 12, width: 1, height: 1, start: 0, end: 1};
  const canvas = el('resourceChart');
  const ctx = canvas.getContext('2d');
  function bytes(value, suffix = '') {
    if (!finite(value)) return '—';
    let index = 0;
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
    return `${value.toFixed(index ? 1 : 0)} ${units[index]}${suffix}`;
  }
  const clock = value => new Date(value * 1000).toLocaleTimeString('zh-CN', {hour12: false});
  function meter(id, value) {
    el(id).style.width = `${finite(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
  }
  function detail(parent, name, value) {
    const row = document.createElement('div'); row.className = 'detail-line';
    const key = document.createElement('span'), val = document.createElement('span');
    key.textContent = name; val.textContent = value; row.append(key, val); parent.append(row);
  }
  function render(sample) {
    const {cpu, npu, gpu, memory: ram, storage: disk, network: net} = sample;
    el('cpuUsage').textContent = pct(cpu.percent); meter('cpuBar', cpu.percent);
    el('cpuInfo').textContent = `${cpu.count} 核 · ${temp(cpu.temperature_c)}`;
    el('npuUsage').textContent = pct(npu.percent); meter('npuBar', npu.percent);
    el('npuCores').textContent = npu.cores.map(c => `C${c.id} ${Math.round(c.percent)}%`).join(' · ') || '系统未提供核心负载';
    el('npuInfo').textContent = `${freq(npu.frequency_mhz)} · ${temp(npu.temperature_c)}`;
    el('gpuUsage').textContent = pct(gpu.percent); meter('gpuBar', gpu.percent);
    el('gpuInfo').textContent = `${freq(gpu.frequency_mhz)} · ${temp(gpu.temperature_c)}`;
    el('ramUsage').textContent = pct(ram.percent); meter('ramBar', ram.percent);
    el('ramInfo').textContent = `${bytes(ram.used_bytes)} / ${bytes(ram.total_bytes)}`;
    el('swapInfo').textContent = ram.swap_total_bytes ? `交换区 ${bytes(ram.swap_used_bytes)} / ${bytes(ram.swap_total_bytes)}` : '未启用交换区';
    const fs = disk.filesystems[0];
    el('diskUsage').textContent = pct(fs?.percent); meter('diskBar', fs?.percent);
    el('diskInfo').textContent = fs ? `${bytes(fs.used_bytes)} / ${bytes(fs.total_bytes)}` : '磁盘信息未提供';
    el('diskIO').textContent = `读 ${bytes(disk.read_bytes_s, '/s')} · 写 ${bytes(disk.write_bytes_s, '/s')}`;
    el('netRx').textContent = `↓ ${bytes(net.rx_bytes_s, '/s')}`;
    el('netTx').textContent = `↑ ${bytes(net.tx_bytes_s, '/s')}`;
    el('netInfo').textContent = net.interfaces.map(n => n.interface).join(' · ') || '没有活动网卡';
    const uptime = sample.uptime_s;
    el('resourceTime').textContent = `更新于 ${clock(sample.timestamp)} · 已运行 ${Math.floor(uptime / 3600)} 时 ${Math.floor(uptime % 3600 / 60)} 分`;
    const cores = el('cpuCores'); cores.replaceChildren();
    for (const core of cpu.cores) {
      const row = document.createElement('div'); row.className = 'core-row';
      const name = document.createElement('span'); name.textContent = `CPU ${core.id}`;
      const bar = document.createElement('div'); bar.className = 'meter';
      const fill = document.createElement('span'); fill.style.width = `${core.percent ?? 0}%`; bar.append(fill);
      const value = document.createElement('span'); value.textContent = pct(core.percent);
      row.append(name, bar, value); cores.append(row);
    }
    el('cpuLoad').textContent = `系统负载（1 / 5 / 15 分钟）：${cpu.load_average.map(v => v.toFixed(2)).join(' / ')}`;
    const thermal = el('thermalDetails'); thermal.replaceChildren();
    detail(thermal, 'CPU / NPU / GPU', `${temp(cpu.temperature_c)} / ${temp(npu.temperature_c)} / ${temp(gpu.temperature_c)}`);
    detail(thermal, 'DDR 频率', freq(ram.ddr_frequency_mhz));
    const disks = el('storageDetails'); disks.replaceChildren();
    for (const fs of disk.filesystems) detail(disks, `${fs.mount} · ${fs.filesystem}`, `可用 ${bytes(fs.free_bytes)} · ${pct(fs.percent)}`);
    for (const device of disk.devices) detail(disks, `${device.device} I/O 忙碌`, pct(device.busy_percent));
    const processes = el('processRows'); processes.replaceChildren();
    for (const process of sample.processes) {
      const row = document.createElement('tr');
      for (const value of [process.name, process.pid, pct(process.cpu_percent), bytes(process.rss_bytes)]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      processes.append(row);
    }
    el('recordingInfo').textContent = sample.recording.error || `每秒自动记录，曲线保留最近 1 小时；本机日志最多 ${bytes(sample.recording.max_log_bytes)}，达到上限后轮转。进程 CPU 按整机 ${cpu.count} 核计算。`;
    el('resourceConnection').textContent = sample.recording.enabled ? '● 每秒记录中' : '记录写入异常';
    el('resourceSummary').classList.remove('stale');
  }
  function addHistory(rows) {
    const entries = new Map(history.map(row => [row.timestamp, row]));
    for (const row of rows) entries.set(row.timestamp, row);
    history = [...entries.values()].sort((a, b) => a.timestamp - b.timestamp).slice(-3600);
    draw();
  }
  function draw() {
    if (!ctx) return;
    const rect = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!rect.width) return;
    if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
      canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(rect.height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, rect.width, rect.height);
    const end = lastSample?.timestamp ?? history.at(-1)?.timestamp ?? Date.now() / 1000;
    plot = {left: 45, top: 12, width: rect.width - 60, height: rect.height - 43, start: end - range, end};
    const x = time => plot.left + (time - plot.start) / range * plot.width;
    const y = value => plot.top + (100 - value) / 100 * plot.height;
    ctx.font = '11px system-ui'; ctx.lineWidth = 1;
    for (const value of [0, 25, 50, 75, 100]) {
      ctx.strokeStyle = '#2a3643'; ctx.beginPath(); ctx.moveTo(plot.left, y(value)); ctx.lineTo(rect.width - 15, y(value)); ctx.stroke();
      ctx.fillStyle = '#90a5b8'; ctx.textAlign = 'right'; ctx.fillText(`${value}%`, plot.left - 8, y(value) + 4);
    }
    for (let index = 0; index <= 4; index++) {
      const time = plot.start + index / 4 * range;
      ctx.textAlign = index === 0 ? 'left' : index === 4 ? 'right' : 'center';
      ctx.fillText(clock(time), x(time), rect.height - 5);
    }
    const points = history.filter(row => row.timestamp >= plot.start && row.timestamp <= plot.end);
    for (const input of document.querySelectorAll('[data-series]')) {
      if (!input.checked) continue;
      const field = input.dataset.series;
      ctx.strokeStyle = colors[field]; ctx.lineWidth = 1.8; ctx.beginPath();
      let previous = null;
      for (const row of points) {
        if (!finite(row[field])) { previous = null; continue; }
        const px = x(row.timestamp), py = y(Math.max(0, Math.min(100, row[field])));
        if (previous === null || row.timestamp - previous > 5) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        previous = row.timestamp;
      }
      ctx.stroke();
    }
    if (points.length < 2) {
      ctx.fillStyle = '#90a5b8'; ctx.textAlign = 'center'; ctx.fillText('正在积累历史记录…', rect.width / 2, rect.height / 2);
    }
    const tooltip = el('chartTooltip');
    if (hoverTime !== null && points.length) {
      const selected = points.reduce((best, row) => Math.abs(row.timestamp - hoverTime) < Math.abs(best.timestamp - hoverTime) ? row : best);
      ctx.strokeStyle = '#92a6b8'; ctx.setLineDash([3, 3]); ctx.beginPath();
      ctx.moveTo(x(selected.timestamp), plot.top); ctx.lineTo(x(selected.timestamp), plot.top + plot.height); ctx.stroke(); ctx.setLineDash([]);
      tooltip.hidden = false;
      tooltip.textContent = `${clock(selected.timestamp)}\n` + Object.keys(colors).map(key => `${labels[key]} ${pct(selected[key])}`).join(' · ');
    } else tooltip.hidden = true;
  }
  async function loadHistory() {
    try {
      const response = await fetch(`/resources/history?seconds=${range}`, {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw Error('History unavailable');
      addHistory((await response.json()).history || []);
    } catch { /* Current samples continue accumulating if historical data is unavailable. */ }
  }
  el('historyRange').onchange = event => {
    range = Number(event.target.value); el('exportResources').href = `/resources/export.csv?seconds=${range}`;
    hoverTime = null; loadHistory(); draw();
  };
  document.querySelectorAll('[data-series]').forEach(input => input.onchange = draw);
  canvas.onpointermove = event => {
    const left = event.clientX - canvas.getBoundingClientRect().left;
    hoverTime = plot.start + Math.max(0, Math.min(1, (left - plot.left) / plot.width)) * range;
    draw();
  };
  canvas.onpointerleave = () => { hoverTime = null; draw(); };
  new ResizeObserver(draw).observe(canvas);
  async function poll() {
    try {
      const response = await fetch('/resources', {cache: 'no-store', signal: AbortSignal.timeout(4000)});
      if (!response.ok) throw Error('Monitor unavailable');
      const result = await response.json();
      if (!result.ok || !result.sample) throw Error('Monitor stale');
      const s = result.sample; lastSample = s; render(s);
      addHistory([{timestamp: s.timestamp, cpu: s.cpu.percent, npu: s.npu.percent, gpu: s.gpu.percent, ram: s.memory.percent}]);
    } catch {
      el('resourceConnection').textContent = '监控连接中断，重试中';
      el('resourceSummary').classList.add('stale');
    }
    setTimeout(poll, 1000);
  }
  loadHistory(); poll();
})();
