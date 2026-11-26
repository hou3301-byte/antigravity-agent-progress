(() => {
  'use strict';
  window.__agyUsage?.dispose();

  const labels = {
    speed: '最近生成速率', ttft: '最近首 Token', average_speed: '近期平均速率',
    tokens: '会话 Token', cache: '输入缓存命中率', remaining: '最低剩余额度',
    quota: '配额池', metrics: '会话指标'
  };
  const percent = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2});
  const integer = new Intl.NumberFormat('zh-CN');
  const clock = new Intl.DateTimeFormat('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
  const calendar = new Intl.DateTimeFormat('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false});
  const formatPct = value => `${percent.format(value)}%`;
  const element = (tag, value = '', className = '') => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const button = (value, className) => {
    const node = element('button', value, className);
    node.type = 'button';
    return node;
  };

  const style = element('style', /*__AGY_USAGE_CSS__*/ '');
  document.head.append(style);
  const trigger = button('额度', 'agy-usage-trigger');
  trigger.setAttribute('popovertarget', 'agy-usage-panel');
  trigger.title = '额度与用量';
  const panel = element('section', '', 'agy-usage-panel');
  panel.id = 'agy-usage-panel';
  panel.setAttribute('popover', 'auto');
  panel.setAttribute('aria-labelledby', 'agy-usage-heading');
  const header = element('header', '', 'agy-usage-header');
  const heading = element('h2', '用量监控');
  heading.id = 'agy-usage-heading';
  const plan = element('span', '', 'agy-usage-plan');
  const close = button('×', 'agy-usage-close');
  close.setAttribute('aria-label', '关闭用量监控');
  close.setAttribute('popovertarget', panel.id);
  close.setAttribute('popovertargetaction', 'hide');
  header.append(heading, plan, close);

  const sections = element('div', '', 'agy-usage-sections');
  const quotaSection = element('section', '', 'agy-usage-section');
  const quotaHeading = element('div', '', 'agy-usage-row');
  const quotaStatus = element('span', '', 'agy-usage-status');
  quotaHeading.append(element('h3', labels.quota), quotaStatus);
  const pools = element('div', '', 'agy-usage-pools');
  const help = element('details', '', 'agy-usage-help');
  const helpTitle = element('summary', '速率和预占说明');
  help.append(helpTitle, element('p',
    '消耗速率按最近 15 分钟内同一额度周期的观测变化计算，至少需要 3 个样本。生成时按此速率估算最近一次刷新后的预占，可能受同账号其他活动影响。官方剩余百分比保留原值。'));
  quotaSection.append(quotaHeading, pools, help);
  const metricSection = element('section', '', 'agy-usage-section');
  const metricHeading = element('div', '', 'agy-usage-row');
  const metricStatus = element('span', '', 'agy-usage-status');
  metricHeading.append(element('h3', labels.metrics), metricStatus);
  const metrics = element('div', '', 'agy-usage-grid');
  metricSection.append(metricHeading, metrics);
  const settings = element('details', '', 'agy-usage-settings');
  const layoutEditor = element('div');
  settings.append(element('summary', '自定义布局'), layoutEditor);
  panel.append(header, sections, settings);
  document.body.append(panel);

  let snapshot;
  let layout;
  let pending;
  let signature;

  function arrange() {
    for (const key of layout.sections) sections.append(key === 'quota' ? quotaSection : metricSection);
  }

  function renderEditor(focus) {
    layoutEditor.replaceChildren();
    for (const [key, title] of [['sections', '区块顺序'], ['metrics', '指标显示和顺序']]) {
      const list = element('div', '', 'agy-usage-options');
      list.append(element('h4', title));
      layout[key].forEach((id, index, items) => {
        const row = element('div', '', 'agy-usage-option');
        const label = element(key === 'metrics' ? 'label' : 'span');
        if (key === 'metrics') {
          const input = element('input');
          input.type = 'checkbox';
          input.checked = !layout.hidden.includes(id);
          input.addEventListener('change', () => {
            layout.hidden = input.checked ? layout.hidden.filter(hidden => hidden !== id) : [...layout.hidden, id];
            pending = JSON.stringify(layout);
            renderMetrics();
          });
          label.append(input);
        }
        label.append(labels[id]);
        row.append(label);
        for (const [direction, symbol, title] of [[-1, '↑', '上移'], [1, '↓', '下移']]) {
          const move = button(symbol);
          const destination = index + direction;
          const control = `${title}${labels[id]}`;
          move.setAttribute('aria-label', control);
          move.disabled = destination < 0 || destination >= items.length;
          move.addEventListener('click', () => {
            [items[index], items[destination]] = [items[destination], items[index]];
            pending = JSON.stringify(layout);
            arrange();
            renderMetrics();
            renderEditor(destination + direction < 0 || destination + direction >= items.length
              ? `${direction < 0 ? '下移' : '上移'}${labels[id]}` : control);
          });
          row.append(move);
        }
        list.append(row);
      });
      layoutEditor.append(list);
    }
    if (focus) [...layoutEditor.querySelectorAll('button')].find(node => node.getAttribute('aria-label') === focus)?.focus();
  }

  function renderMetrics() {
    if (!layout) return;
    const {session = {}, buckets = [], conversation} = snapshot || {};
    metricStatus.textContent = [session.running ? '执行中' : '', session.model].filter(Boolean).join(' · ');
    const usable = buckets.filter(bucket => !bucket.stale);
    const input = (session.input ?? 0) + (session.cache_read ?? 0);
    const speed = value => value == null ? null : `${percent.format(value)} tok/s`;
    const values = {
      speed: speed(session.speed), ttft: session.ttft == null ? null : `${percent.format(session.ttft)} s`,
      average_speed: speed(session.average_speed),
      tokens: session.output == null ? null : integer.format(input + session.output),
      cache: input > 0 ? formatPct(100 * (session.cache_read ?? 0) / input) : null,
      remaining: usable.length ? formatPct(Math.min(...usable.map(bucket => bucket.remaining))) : null
    };
    const visible = layout.metrics.filter(key => !layout.hidden.includes(key));
    metrics.replaceChildren(...visible.map(key => {
      const cell = element('div', '', 'agy-usage-metric');
      const value = element('strong', values[key] ?? '—');
      cell.append(element('span', labels[key]), value);
      if (key === 'tokens' && session.output != null) {
        value.title = `输入 ${integer.format(input)}（缓存读取 ${integer.format(session.cache_read ?? 0)}），输出 ${integer.format(session.output)}`;
      }
      if (key === 'speed' || key === 'average_speed') value.title = '输出 Token ÷ 生成请求耗时，包含首 Token 等待时间';
      if (key === 'ttft') value.title = '服务端记录的首 Token 耗时';
      return cell;
    }));
    if (!visible.length || !conversation) metrics.append(element('p', visible.length ? '打开对话后可查看会话指标。' : '可在布局设置中启用指标。', 'agy-usage-muted'));
  }

  function renderQuotas() {
    const {buckets = [], fetched, stale} = snapshot || {};
    plan.textContent = snapshot?.plan || '';
    quotaStatus.textContent = stale && fetched ? `刷新失败 · ${clock.format(fetched * 1000)}`
      : fetched ? `更新于 ${clock.format(fetched * 1000)}` : '';
    help.hidden = !buckets.length;
    helpTitle.textContent = !stale && buckets.some(b => !b.stale && b.reset) && !buckets.some(b => b.rate != null)
      ? '速率样本不足 · 查看估算说明' : '速率和预占说明';
    pools.replaceChildren();
    if (!buckets.length) {
      pools.append(element('p', snapshot ? '暂未取得账号配额' : '正在读取额度…', 'agy-usage-muted'));
      return;
    }
    const groups = new Map();
    for (const bucket of buckets) {
      if (!groups.has(bucket.pool)) {
        const group = element('article', '', 'agy-usage-pool');
        group.append(element('h4', bucket.label));
        groups.set(bucket.pool, group);
        pools.append(group);
      }
      const row = element('div', '', 'agy-usage-bucket');
      const caption = element('div', '', 'agy-usage-row');
      const windowLabel = ({'5h': '5 小时', weekly: '周额度'})[bucket.window] || bucket.window || '额度';
      const balance = element('span', '', 'agy-usage-balance');
      balance.append(element('span', `已用 ${formatPct(100 - bucket.remaining)}`),
        element('strong', `剩余 ${formatPct(bucket.remaining)}`));
      caption.append(element('span', windowLabel), balance);
      const bar = element('div', '', 'agy-usage-meter');
      bar.setAttribute('aria-hidden', 'true');
      if (bucket.stale) bar.dataset.stale = '';
      const used = element('span', '', 'agy-usage-used');
      used.style.width = `${100 - bucket.remaining}%`;
      const reserved = element('span', '', 'agy-usage-reserved');
      reserved.style.width = `${bucket.estimate || 0}%`;
      bar.append(used, reserved);
      const meta = element('div', '', 'agy-usage-row agy-usage-meta');
      const reset = element('span', '未提供重置时间');
      if (bucket.reset) {
        reset.dataset.reset = String(bucket.reset);
        reset.title = `${calendar.format(bucket.reset * 1000)} 重置`;
      }
      const rate = bucket.rate == null ? '' : `${percent.format(bucket.rate)} 个百分点/小时`;
      const estimate = bucket.estimate == null ? '' : `预占估算 ${formatPct(bucket.estimate)}`;
      meta.append(reset, element('span', [estimate, rate].filter(Boolean).join(' · ')));
      row.append(caption, bar, meta);
      groups.get(bucket.pool).append(row);
    }
    updateCountdowns();
  }

  function updateCountdowns() {
    for (const node of pools.querySelectorAll('[data-reset]')) {
      const minutes = Math.ceil((Number(node.dataset.reset) - Date.now() / 1000) / 60);
      const hours = Math.floor(minutes / 60);
      const label = minutes <= 0 ? '已到重置时间，等待刷新' : `${hours ? `${hours} 小时 ` : ''}${minutes % 60} 分钟后重置`;
      if (node.textContent !== label) node.textContent = label;
    }
  }

  function position() {
    const anchor = trigger.getBoundingClientRect();
    panel.style.top = `${Math.round(anchor.bottom + 8)}px`;
    panel.style.right = `${Math.max(12, Math.min(Math.round(window.innerWidth - anchor.right), window.innerWidth - 417))}px`;
    panel.style.maxHeight = `calc(100dvh - ${Math.round(anchor.bottom + 20)}px)`;
  }

  panel.addEventListener('beforetoggle', event => { if (event.newState === 'open') position(); });
  window.addEventListener('resize', position);
  const interval = setInterval(updateCountdowns, 15000);

  window.__agyUsage = {
    publish(next, saved) {
      const toolbar = [...document.querySelectorAll('[data-testid="pane-titlebar-toolbar"]')]
        .find(node => node.getClientRects().length);
      if (trigger.parentElement !== toolbar) {
        if (panel.matches(':popover-open')) panel.hidePopover();
        if (toolbar) toolbar.prepend(trigger);
        else trigger.remove();
      }
      const currentRoute = location.pathname.match(/^\/c\/([^/]+)$/)?.[1];
      if (next?.conversation && next.conversation !== currentRoute) next = null;
      if (next && !next.conversation && currentRoute) next = {...next, conversation: currentRoute};
      const stored = JSON.stringify(saved);
      if (pending === stored) pending = null;
      if (!pending && stored !== JSON.stringify(layout)) {
        layout = saved;
        arrange();
        renderEditor();
      }
      const nextSignature = JSON.stringify([next, layout]);
      if (nextSignature !== signature) {
        snapshot = next;
        signature = nextSignature;
        renderQuotas();
        renderMetrics();
        const usable = (next?.buckets || []).filter(bucket => !bucket.stale);
        const label = usable.length ? `额度 ${formatPct(Math.min(...usable.map(bucket => bucket.remaining)))}` : '额度';
        if (trigger.textContent !== label) trigger.textContent = label;
      }
      return pending ? JSON.parse(pending) : null;
    },
    dispose() {
      clearInterval(interval);
      window.removeEventListener('resize', position);
      trigger.remove();
      panel.remove();
      style.remove();
    }
  };
})();
