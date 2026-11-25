(() => {
  window.__agyProgress?.dispose();

  const groups = 'button[data-testid="worked-for-collapsible"], button[data-testid="progress-updates-collapsible"]';
  const toolRows = 'button[data-testid="tool-group-collapsible"], [role="button"][aria-expanded]';
  const legacyRunning = '[data-testid="ephemeral-tool-status"]';
  const runningLabel = /^(?:正在运行|运行中|执行中|running|executing)(?:$|\s)/i;
  const finishedLabel = /^(?:已运行|已执行|ran|executed|failed|error|失败|运行失败|已失败)(?:$|\s)/i;
  const failedLabel = /^(?:运行失败|执行失败|已失败|失败|failed|error)(?:$|[\s:：])/i;
  const canceledLabel = /^(?:已取消|已停止|cancell?ed|interrupted)(?:$|\s)/i;
  const nativeElapsed = '[data-testid="tool-group-elapsed"], time';
  const permissions = '[data-testid="inline-permission-card"]';
  const questions = ':is([data-testid="ask-question-option"], [data-testid="ask-question-writein"], [data-testid="ask-question-dismiss-button"]):not([disabled], [aria-disabled="true"])';
  const verificationCommand = /(?:^|[;&|\n])\s*(?:[\w./-]*\/)?(?:python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest)|pytest|go\s+test|cargo\s+test|(?:npm|pnpm|yarn)\s+(?:run\s+)?test|(?:mvnw?|gradlew?)\s+(?:test|verify|check)|node\s+--test|vitest|jest)(?=\s|$|[;&|])/i;
  const verificationSummary = /跑测|运行.{0,12}(?:测试|校验)|测试结果|验证结果/;
  const taskLabels = {pending: '待办', running: '进行中', done: '完成', blocked: '阻塞', recheck: '待复核', unfinished: '未确认', canceled: '已停止'};
  const taskIcons = {pending: '○', running: '●', done: '✓', blocked: '!', recheck: '↻', unfinished: '○', canceled: '–'};
  const phaseLabels = {
    done: '已完成', canceled: '已停止', ended: '本轮结束 · 清单未确认', failed: '本轮执行出错',
    limit: '达到执行步数上限', waiting: '等待你处理', unknown: '执行状态未确认',
    background: '后台任务仍在运行', blocked: '遇到阻塞'
  };
  let snapshot = null;
  let boundTurn = null;
  let frame = 0;
  let signature = '';
  let listOpen = false;
  let settled = false;
  let currentGroups = [];
  let currentTools = [];
  let currentResponse = null;
  let attentionTarget = null;
  let reviewTarget = null;
  let updateNotice = null;

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
  const actions = element('div', '', 'agy-progress-actions');
  const outcomeStatus = element('span', '', 'agy-progress-outcome');
  const attention = element('button', '', 'agy-progress-attention');
  const attentionDetail = element('span', '', 'agy-progress-attention-detail');
  const failure = element('button', '查看失败记录', 'agy-progress-failure');
  const verification = element('button', '验证输出');
  const review = element('button', '审阅改动');
  for (const button of [attention, failure, verification, review]) button.type = 'button';
  actions.append(outcomeStatus, attention, attentionDetail, failure, verification, review);
  panel.append(header, list, fallback, actions);

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
    const controls = currentGroups.length ? currentGroups : currentTools;
    const expand = controls.some(row => row.getAttribute('aria-expanded') === 'false');
    for (const row of controls) {
      if (row.isConnected && row.getAttribute('aria-expanded') !== String(expand)) row.click();
    }
    requestSync();
  }

  taskToggle.addEventListener('click', () => setList(!listOpen));
  toolToggle.addEventListener('click', toggleTools);
  attention.addEventListener('click', () => reveal(attentionTarget));
  failure.addEventListener('click', () => openToolEvidence(isFailedTool));
  verification.addEventListener('click', () => openToolEvidence(isVerificationTool));
  review.addEventListener('click', () => {
    if (reviewTarget?.isConnected && visible(reviewTarget)) reviewTarget.click();
  });

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

  function isFailedTool(row) {
    return failedLabel.test(toolStatus(row));
  }

  function isVerificationTool(row) {
    const label = row.firstElementChild;
    const summary = label?.getAttribute('title') || label?.textContent || '';
    const command = summary.trim().replace(/^(?:ran|executed|failed|已运行|已执行|失败)\s+/i, '');
    return verificationCommand.test(command) || verificationSummary.test(summary);
  }

  function findTool(response, predicate) {
    return [...(response?.querySelectorAll(toolRows) ?? [])].findLast(row => visible(row) && predicate(row));
  }

  function reveal(target) {
    if (!target?.isConnected || !visible(target)) return;
    target.scrollIntoView({block: 'center'});
    const focus = target.matches('button, input, [role="button"], [tabindex]') ? target :
      target.querySelector('button:not([disabled]), input:not([disabled])');
    focus?.focus({preventScroll: true});
  }

  function openToolEvidence(predicate) {
    const response = currentResponse;
    const group = currentGroups.at(-1);
    const show = () => {
      if (response !== currentResponse || !response?.isConnected) return;
      const row = findTool(response, predicate);
      if (row?.getAttribute('aria-expanded') === 'false') row.click();
      reveal(row || group);
    };
    if (findTool(response, predicate)) {
      show();
      return;
    }
    for (const button of currentGroups) {
      if (button.getAttribute('aria-expanded') === 'false') button.click();
    }
    requestAnimationFrame(show);
  }

  function renderTasks(items, phase) {
    const finished = items.filter(item => item.state === 'done').length;
    const focus = items.find(item => item.state === 'running' || item.state === 'blocked');
    const terminal = ['done', 'canceled', 'ended', 'failed', 'limit'].includes(phase);
    if (terminal && !settled) setList(false);
    settled = terminal;
    taskToggle.hidden = !items.length;
    current.textContent = phaseLabels[phase] || focus?.label || '进行中';
    current.title = current.textContent;
    count.textContent = `${finished}/${items.length}`;
    count.setAttribute('aria-label', `已完成 ${finished} 项，共 ${items.length} 项`);

    meter.replaceChildren(...items.map(item => {
      const segment = element('span');
      segment.dataset.state = item.state;
      return segment;
    }));
    list.replaceChildren(...items.map(item => {
      let state = item.state;
      if (state === 'running') {
        if (phase === 'canceled') state = 'canceled';
        else if (terminal || phase === 'unknown') state = 'unfinished';
      }
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

  function mountTimer(since, mode) {
    if (!Number.isFinite(since)) {
      timer.remove();
      return;
    }
    timer.dataset.mode = mode;
    clock.dataset.since = String(since);
    if (timer.parentElement !== fallback) fallback.append(timer);
    applyTimer();
  }

  function sync() {
    frame = 0;
    const turn = newestTurn();
    const response = turn?.querySelector('[data-testid="agent-response"]');
    const pane = turn?.closest('[data-testid="conversation-view"]');
    const sameConversation = snapshot && location.pathname === `/c/${snapshot.conversation}`;
    if (sameConversation && response && (!boundTurn || snapshot.active)) boundTurn = turn;
    const data = sameConversation && boundTurn === turn ? snapshot : null;
    const items = data?.items || [];
    const generating = [...(pane || document).querySelectorAll('[data-testid="input-cancel-button"]')].some(visible);
    currentResponse = response;
    currentGroups = [...(response?.querySelectorAll(groups) ?? [])].filter(visible);

    const rows = [...(response?.querySelectorAll(toolRows) ?? [])].map(row => [row, toolStatus(row)]);
    currentTools = rows.filter(([row, status]) => visible(row) &&
      (row.dataset.testid === 'tool-group-collapsible' || runningLabel.test(status) ||
        finishedLabel.test(status) || canceledLabel.test(status))).map(([row]) => row);
    const toolControls = currentGroups.length ? currentGroups : currentTools;
    const opened = toolControls.filter(row => row.getAttribute('aria-expanded') === 'true').length;
    toolToggle.hidden = !toolControls.length;
    toolToggle.setAttribute('aria-pressed', opened && opened < toolControls.length ? 'mixed' : String(Boolean(opened)));

    const approval = [
      ...(turn?.querySelectorAll(permissions) ?? []),
      ...(pane?.querySelectorAll(`[data-testid="inline-permission-slot"] ${permissions}`) ?? [])
    ].find(visible);
    const question = [...(turn?.querySelectorAll(questions) ?? [])].find(visible);
    attentionTarget = approval || question || null;
    attention.hidden = !attentionTarget;
    const attentionText = approval ? '查看待授权操作' : '回答待确认问题';
    if (attention.textContent !== attentionText) attention.textContent = attentionText;
    const pendingTitle = approval?.querySelector('[data-testid="permission-title"]') ||
      (question && [...(turn?.querySelectorAll('[data-testid="ask-question-header-text"]') ?? [])].findLast(visible));
    const pendingText = pendingTitle?.textContent.trim() || '';
    attention.title = pendingText || attentionText;
    attentionDetail.hidden = !pendingText;
    if (attentionDetail.textContent !== pendingText) attentionDetail.textContent = pendingText;

    const canceled = !generating && (data?.outcome === 'canceled' ||
      Boolean(response?.querySelector('[data-testid="agent-status-canceled"]')));
    const background = !canceled && Boolean(data?.background_count || data?.waiting_background);
    const complete = data?.done || (items.length && items.every(item => item.state === 'done'));
    const blocked = items.some(item => item.state === 'blocked');
    const live = generating || Number.isFinite(data?.running_start);
    let phase = 'unknown';
    if (attentionTarget) phase = 'waiting';
    else if (canceled) phase = 'canceled';
    else if (data?.outcome === 'failed' || data?.outcome === 'limit') phase = data.outcome;
    else if (live) phase = blocked ? 'blocked' : 'running';
    else if (background) phase = 'background';
    else if (complete) phase = 'done';
    else if (blocked) phase = 'blocked';
    else if (data?.outcome === 'ended' || (data?.started_at && !data.active)) phase = 'ended';

    const nativeClock = [...(response?.querySelectorAll(nativeElapsed) ?? [])].some(node => {
      const row = node.closest(toolRows + ', ' + legacyRunning);
      return visible(node) && row && runningLabel.test(toolStatus(row));
    });
    const fallbackSince = attentionTarget ? null : live ?
      (nativeClock ? null : data?.running_start) : background ? data?.background_start : null;
    const useFallback = Number.isFinite(fallbackSince);
    fallback.hidden = !useFallback;
    if (useFallback) {
      const label = live ? '工具运行中' : `后台运行 · ${data.background_count} 项`;
      if (fallbackLabel.textContent !== label) fallbackLabel.textContent = label;
    }

    const failedRow = findTool(response, isFailedTool);
    const validationRow = findTool(response, isVerificationTool);
    failure.hidden = !(failedRow || data?.had_failure) || !toolControls.length;
    verification.hidden = !(validationRow || data?.has_validation) || !toolControls.length;
    reviewTarget = [...(turn?.querySelectorAll('[data-testid="changed-files-card-review"]') ?? [])].find(visible);
    review.hidden = !reviewTarget;
    outcomeStatus.hidden = Boolean(items.length || !['failed', 'limit', 'canceled', 'background'].includes(phase) ||
      (phase === 'background' && useFallback));
    const outcomeText = phaseLabels[phase] || '';
    if (outcomeStatus.textContent !== outcomeText) outcomeStatus.textContent = outcomeText;
    actions.hidden = [outcomeStatus, attention, failure, verification, review].every(entry => entry.hidden);

    if (response && (items.length || currentTools.length > 1 || useFallback || attentionTarget || !failure.hidden || !verification.hidden ||
        background || phase === 'failed' || phase === 'limit' || phase === 'canceled')) {
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
    mountTimer(fallbackSince, live ? 'foreground' : 'background');
  }

  function requestSync() {
    if (!frame) frame = requestAnimationFrame(sync);
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
  const observer = new MutationObserver(records => {
    if (records.some(record => !panel.contains(record.target) && !updateNotice?.contains(record.target))) requestSync();
  });
  observer.observe(document.body, {
    childList: true, subtree: true, characterData: true, attributes: true,
    attributeFilter: ['aria-expanded', 'aria-label', 'aria-disabled', 'disabled', 'hidden', 'title']
  });
  const interval = setInterval(applyTimer, 250);
  document.addEventListener('keydown', shortcuts, true);

  window.__agyProgress = {
    showUpdate(version) {
      if (typeof version !== 'string' || !/^v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$/.test(version) ||
          document.visibilityState !== 'visible' || !document.hasFocus()) return false;
      if (updateNotice?.isConnected && updateNotice.dataset.version === version) return true;
      updateNotice?.remove();
      updateNotice = element('aside', '', 'agy-update-notice');
      updateNotice.dataset.version = version;
      updateNotice.setAttribute('aria-label', '执行进度插件更新');
      const title = element('strong', `执行进度 ${version} 可更新`);
      const actions = element('div', '', 'agy-update-actions');
      const link = element('a', '查看更新');
      link.href = `https://github.com/hou3301-byte/antigravity-agent-progress/releases/tag/${version}`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const later = element('button', '稍后');
      later.type = 'button';
      for (const control of [link, later]) {
        control.addEventListener('click', () => updateNotice.remove(), {once: true});
      }
      actions.append(link, later);
      updateNotice.append(title, actions);
      document.body.append(updateNotice);
      return true;
    },
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
      document.removeEventListener('keydown', shortcuts, true);
      document.querySelectorAll('.agy-todo-source-hidden').forEach(node => node.classList.remove('agy-todo-source-hidden'));
      panel.remove();
      timer.remove();
      updateNotice?.remove();
      style.remove();
    },
  };
  requestSync();
})();
