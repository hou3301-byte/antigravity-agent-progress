(() => {
  window.__agyProgress?.dispose();

  const groups = 'button[data-testid="worked-for-collapsible"], button[data-testid="progress-updates-collapsible"]';
  const toolRows = 'button[data-testid="tool-group-collapsible"], [role="button"][aria-expanded]';
  const legacyRunning = '[data-testid="ephemeral-tool-status"]';
  const runningLabel = /^(?:正在运行|运行中|执行中|running|executing)(?:$|\s)/i;
  const finishedLabel = /^(?:已运行|已执行|ran|executed|failed|error|失败|运行失败|已失败)(?:$|\s)/i;
  const canceledLabel = /^(?:已取消|已停止|cancell?ed|interrupted)(?:$|\s)/i;
  const taskLabels = {pending: '待办', running: '进行中', done: '完成', blocked: '阻塞', paused: '已暂停', canceled: '已停止'};
  const taskIcons = {pending: '○', running: '●', done: '✓', blocked: '!', paused: 'Ⅱ', canceled: '–'};
  const manuallyToggled = new WeakSet();
  const attempted = new WeakSet();
  const nativeStarts = new WeakMap();
  let snapshot = null;
  let boundTurn = null;
  let frame = 0;
  let signature = '';
  let listOpen = false;
  let settled = false;
  let currentTools = [];
  let idleSince = 0;

  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (value) node.textContent = value;
    if (className) node.className = className;
    return node;
  }

  const style = document.createElement('style');
  style.textContent = /*__AGY_PROGRESS_CSS__*/ '';
  document.head.append(style);

  const panel = element('section', '', 'agy-progress-panel');
  panel.setAttribute('aria-label', '本轮执行状态');
  const header = element('div', '', 'agy-progress-header');
  const taskToggle = element('button', '', 'agy-progress-toggle');
  taskToggle.type = 'button';
  taskToggle.title = '展开或收起任务清单（Ctrl+T）';
  taskToggle.setAttribute('aria-controls', 'agy-progress-tasks');
  const taskIcon = element('span', '', 'agy-progress-chevron');
  taskIcon.setAttribute('aria-hidden', 'true');
  const summary = element('span', '', 'agy-progress-summary');
  const title = element('strong', '任务', 'agy-progress-title');
  const current = element('span', '', 'agy-progress-current');
  summary.append(title, current);
  const meter = element('span', '', 'agy-progress-meter');
  meter.setAttribute('aria-hidden', 'true');
  const count = element('span', '', 'agy-progress-count');
  taskToggle.append(taskIcon, summary, meter, count);
  const toolToggle = element('button', '工具详情', 'agy-tool-toggle');
  toolToggle.type = 'button';
  toolToggle.title = '展开或收起本轮工具详情（Ctrl+O）';
  header.append(taskToggle, toolToggle);
  const list = element('ul', '', 'agy-progress-tasks');
  list.id = 'agy-progress-tasks';
  list.setAttribute('aria-label', '任务清单');
  const fallback = element('div', '', 'agy-progress-fallback');
  const fallbackLabel = element('span');
  fallback.append(fallbackLabel);
  panel.append(header, list, fallback);

  const timer = element('span', '', 'agy-live-timer');
  const dot = element('span', '', 'agy-live-dot');
  dot.setAttribute('aria-hidden', 'true');
  const clock = element('time');
  timer.append(dot, clock);

  const visible = node => node?.getClientRects().length > 0;

  function setList(open) {
    listOpen = open;
    list.hidden = !open;
    taskToggle.setAttribute('aria-expanded', String(open));
  }

  function toggleTools() {
    const expand = currentTools.every(row => row.getAttribute('aria-expanded') === 'false');
    for (const row of currentTools) {
      if (row.isConnected && row.getAttribute('aria-expanded') !== String(expand)) row.click();
    }
    requestSync();
  }

  taskToggle.addEventListener('click', () => setList(!listOpen));
  toolToggle.addEventListener('click', toggleTools);

  function normalizeText(text) {
    return (text || '').replace(/^\s*(?:\[[ /xX]\]\s*)?/, '').replace(/\s+/g, ' ').trim();
  }

  function newestTurn() {
    const viewports = [...document.querySelectorAll('[data-testid="autoscroll-viewport"]')]
      .filter(viewport => visible(viewport) && viewport.querySelector('[data-testid="user-message"]'));
    if (viewports.length !== 1) return null;
    const users = viewports[0].querySelectorAll('[data-testid="user-message"]');
    return users[users.length - 1]?.parentElement || null;
  }

  function syncSourceLists(response, sources) {
    const hidden = new Set();
    if (response && sources.length) {
      const matched = new Set();
      for (const ul of response.querySelectorAll('ul.contains-task-list, [data-testid="planner-response-text"] ul')) {
        const lis = [...ul.children].filter(node => node.tagName === 'LI');
        if (!ul.classList.contains('contains-task-list') && !lis.some(li => /^\s*\[\/\]/.test(li.textContent))) continue;
        const index = sources.findIndex((labels, i) => !matched.has(i) && labels.length === lis.length &&
          lis.every((li, j) => normalizeText(li.textContent) === normalizeText(labels[j])));
        if (index >= 0) {
          matched.add(index);
          hidden.add(ul);
        }
      }
    }
    for (const old of document.querySelectorAll('.agy-todo-source-hidden')) {
      if (!hidden.has(old)) old.classList.remove('agy-todo-source-hidden');
    }
    for (const ul of hidden) {
      if (!ul.classList.contains('agy-todo-source-hidden')) ul.classList.add('agy-todo-source-hidden');
    }
  }

  function toolStatus(row) {
    if (row.matches(legacyRunning)) return row.textContent.trim();
    const label = row.firstElementChild?.querySelector('span[title]');
    // 2.34 puts the status and the summary in sibling spans of a native role=button.
    if (label?.previousElementSibling) return label.previousElementSibling.textContent.trim();
    if (row.dataset.testid === 'tool-group-collapsible') return row.firstElementChild?.textContent.trim() || '';
    return '';
  }

  function renderTasks(items, phase) {
    const finished = items.filter(item => item.state === 'done').length;
    const focus = items.find(item => item.state === 'running' || item.state === 'blocked');
    const terminal = phase === 'done' || phase === 'canceled' || phase === 'paused';
    if (terminal && !settled) setList(false);
    settled = terminal;
    taskToggle.hidden = !items.length;
    current.textContent = ({
      done: '已完成', canceled: '已停止', paused: '已暂停', background: '等待后台任务', blocked: '遇到阻塞'
    })[phase] || focus?.label || '';
    current.title = current.textContent;
    count.textContent = `${finished}/${items.length}`;
    count.setAttribute('aria-label', `已完成 ${finished} 项，共 ${items.length} 项`);

    meter.replaceChildren(...items.map(item => {
      const segment = element('span');
      segment.dataset.state = item.state;
      return segment;
    }));
    list.replaceChildren(...items.map(item => {
      const state = item.state === 'running' && (phase === 'canceled' || phase === 'paused') ? phase : item.state;
      const row = element('li', '', 'agy-progress-task');
      row.dataset.state = state;
      const mark = element('span', taskIcons[state], 'agy-task-icon');
      mark.setAttribute('aria-hidden', 'true');
      const text = element('span', item.label, 'agy-task-label');
      const status = element('span', taskLabels[state], 'agy-task-state');
      row.append(mark, text, status);
      return row;
    }));
  }

  function applyTimer() {
    if (!timer.isConnected || !clock.dataset.since) return;
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(clock.dataset.since)));
    const value = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    if (clock.textContent !== value) clock.textContent = value;
  }

  function mountTimer(host, since, mode) {
    if (!host) {
      timer.remove();
      return;
    }
    timer.dataset.mode = mode;
    clock.dataset.since = String(since);
    if (timer.parentElement !== host) {
      if (mode === 'foreground' && host !== fallback && host.firstElementChild?.matches('svg')) host.prepend(timer);
      else host.append(timer);
    }
    applyTimer();
  }

  function sync() {
    frame = 0;
    const turn = newestTurn();
    const response = turn?.querySelector('[data-testid="agent-response"]');
    const sameConversation = snapshot && location.pathname === `/c/${snapshot.conversation}`;
    if (sameConversation && response && !boundTurn) boundTurn = turn;
    const data = sameConversation && boundTurn === turn ? snapshot : null;
    const items = data?.items || [];
    const generating = [...document.querySelectorAll('[data-testid="input-cancel-button"]')].some(visible);
    if (generating) idleSince = 0;
    else if (!idleSince) idleSince = Date.now();

    for (const button of response?.querySelectorAll(groups) ?? []) {
      if (visible(button) && button.getAttribute('aria-expanded') === 'false' &&
          !manuallyToggled.has(button) && !attempted.has(button)) {
        attempted.add(button);
        button.click();
      }
    }

    const rows = [...(response?.querySelectorAll(toolRows) ?? [])].map(row => [row, toolStatus(row)]);
    currentTools = rows.filter(([row, status]) => visible(row) &&
      (row.dataset.testid === 'tool-group-collapsible' || runningLabel.test(status) ||
        finishedLabel.test(status) || canceledLabel.test(status))).map(([row]) => row);
    const opened = currentTools.filter(row => row.getAttribute('aria-expanded') === 'true').length;
    toolToggle.hidden = !currentTools.length;
    toolToggle.setAttribute('aria-pressed', opened && opened < currentTools.length ? 'mixed' : String(Boolean(opened)));

    const canceled = rows.some(([, status]) => canceledLabel.test(status));
    const background = !canceled && data?.background_count && Number.isFinite(data.background_start);
    const complete = data?.done || (items.length && items.every(item => item.state === 'done'));
    const phase = complete ? 'done' : canceled ? 'canceled' :
      items.some(item => item.state === 'blocked') ? 'blocked' :
      generating ? 'running' : background ? 'background' : Date.now() - idleSince < 1000 ? 'running' : 'paused';

    const native = generating ? [
      ...rows.filter(([row, status]) => runningLabel.test(status) && visible(row)).map(([row]) => row),
      ...[...(response?.querySelectorAll(legacyRunning) ?? [])].filter(visible)
    ].pop() : null;
    const hookStart = data?.running_start;
    const fallbackSince = background ? data.background_start :
      (!native && generating && Number.isFinite(hookStart) ? hookStart : null);
    const useFallback = Number.isFinite(fallbackSince) && !native;
    fallback.hidden = !useFallback;
    if (useFallback) {
      const label = background ? `后台运行 · ${data.background_count} 项` : '工具调用中';
      if (fallbackLabel.textContent !== label) fallbackLabel.textContent = label;
    }

    if (response && (items.length || currentTools.length > 1 || useFallback)) {
      const viewport = response.closest('[data-testid="autoscroll-viewport"]');
      const following = viewport && viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 64;
      const next = JSON.stringify({items, phase});
      const changed = signature !== next;
      if (changed) {
        renderTasks(items, phase);
        panel.dataset.phase = phase;
        signature = next;
      }
      const moved = response.lastElementChild !== panel;
      if (moved) response.append(panel);
      if (following && (changed || moved)) viewport.scrollTop = viewport.scrollHeight;
    } else {
      panel.remove();
    }

    syncSourceLists(response, data?.sources || []);
    if (native) {
      const label = [...native.childNodes].filter(node => node !== timer).map(node => node.textContent).join('');
      let observed = nativeStarts.get(native);
      if (!observed || observed.label !== label) {
        observed = {label, since: Date.now() / 1000};
        nativeStarts.set(native, observed);
      }
      const since = Number.isFinite(hookStart) ? hookStart : observed.since;
      // Preserve the host chevron and keep the timer outside the truncated label.
      const slot = native.matches(legacyRunning) ? native : native.firstElementChild?.nextElementSibling || native;
      mountTimer(slot, since, 'foreground');
    } else {
      mountTimer(useFallback ? fallback : null, fallbackSince, background ? 'background' : 'foreground');
    }
  }

  function requestSync() {
    if (!frame) frame = requestAnimationFrame(sync);
  }

  function rememberClick(event) {
    const button = event.target.closest?.(groups);
    if (button && event.isTrusted) manuallyToggled.add(button);
  }

  function shortcuts(event) {
    if (!panel.isConnected || !event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || event.repeat ||
        event.target.closest?.('input, textarea, [contenteditable="true"]')) return;
    const key = event.key.toLowerCase();
    if (key === 't' && !taskToggle.hidden) setList(!listOpen);
    else if (key === 'o' && !toolToggle.hidden) toggleTools();
    else return;
    event.preventDefault();
    event.stopPropagation();
  }

  setList(false);
  const observer = new MutationObserver(requestSync);
  observer.observe(document.body, {
    childList: true, subtree: true, characterData: true, attributes: true,
    attributeFilter: ['aria-expanded', 'aria-label', 'title']
  });
  const interval = setInterval(applyTimer, 250);
  document.addEventListener('click', rememberClick, true);
  document.addEventListener('keydown', shortcuts, true);

  window.__agyProgress = {
    publish(next) {
      if (snapshot?.started_at !== next?.started_at || snapshot?.conversation !== next?.conversation) {
        boundTurn = null;
        signature = '';
        settled = false;
        setList(false);
      }
      snapshot = next;
      requestSync();
    },
    dispose() {
      observer.disconnect();
      cancelAnimationFrame(frame);
      clearInterval(interval);
      document.removeEventListener('click', rememberClick, true);
      document.removeEventListener('keydown', shortcuts, true);
      document.querySelectorAll('.agy-todo-source-hidden').forEach(node => node.classList.remove('agy-todo-source-hidden'));
      panel.remove();
      timer.remove();
      style.remove();
    },
  };
  requestSync();
})();
