(() => {
  window.__agyProgress?.dispose();

  const groups = 'button[data-testid="worked-for-collapsible"], button[data-testid="progress-updates-collapsible"]';
  const taskLabels = {pending: '待办', running: '进行中', done: '完成', blocked: '阻塞'};
  const taskIcons = {pending: '○', running: '◉', done: '✓', blocked: '!'};
  const toolLabels = {running: '调用中', done: '已返回', error: '出错', interrupted: '已中断'};
  const manuallyToggled = new WeakSet();
  const attempts = new WeakMap();
  let snapshot = null;
  let boundTurn = null;
  let signature = '';
  let frame = 0;

  const style = document.createElement('style');
  style.textContent = /*__AGY_PROGRESS_CSS__*/ '';
  document.head.append(style);

  const panel = document.createElement('div');
  panel.className = 'agy-progress-panel';
  const taskCard = document.createElement('section');
  taskCard.className = 'agy-progress-card';
  taskCard.setAttribute('aria-label', '执行进度');
  const activity = document.createElement('details');
  activity.className = 'agy-tool-activity';
  const summary = document.createElement('summary');
  const recent = document.createElement('ul');
  recent.className = 'agy-tool-list';
  recent.setAttribute('aria-label', '最近工具调用');
  activity.append(summary, recent);
  panel.append(taskCard, activity);
  const badge = document.createElement('time');
  badge.className = 'agy-progress-native';

  function element(tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  }

  function statusText(text, state, tool) {
    const node = element('span', text, 'agy-progress-status');
    node.dataset.state = state;
    if (tool && Number.isFinite(tool.start)) {
      if (tool.state === 'running') {
        const timer = document.createElement('time');
        timer.dataset.since = tool.start;
        node.append(' ', timer);
      } else if (Number.isFinite(tool.end)) {
        node.append(` ${Math.max(0, tool.end - tool.start).toFixed(1)} 秒`);
      }
    }
    return node;
  }

  function render() {
    const {items, tools, active} = snapshot;
    taskCard.hidden = !items.length;
    if (items.length) {
      const finished = items.filter(item => item.state === 'done').length;
      const header = document.createElement('header');
      header.append(element('strong', '执行进度'), statusText(`${finished} / ${items.length} 项完成`, 'summary'));
      const meter = document.createElement('progress');
      meter.className = 'agy-progress-meter';
      meter.max = items.length;
      meter.value = finished;
      meter.setAttribute('aria-label', '任务完成度');
      const tasks = document.createElement('ul');
      for (const item of items) {
        const state = item.state === 'running' && !active ? 'paused' : item.state;
        const mark = element('span', taskIcons[state] || 'Ⅱ', 'agy-task-icon');
        mark.dataset.state = state;
        mark.setAttribute('aria-hidden', 'true');
        const label = element('span', '', 'agy-task-label');
        label.append(mark, element('span', item.label));
        const line = document.createElement('li');
        line.append(label, statusText(taskLabels[state] || '已暂停', state));
        tasks.append(line);
      }
      taskCard.replaceChildren(header, meter, tasks);
    }
    activity.hidden = !tools.length;
    if (tools.length) {
      const running = tools.filter(tool => tool.state === 'running');
      const latest = running[running.length - 1] || tools[tools.length - 1];
      const preview = element('span', '', 'agy-tool-recent');
      preview.append(element('code', latest.name), statusText(toolLabels[latest.state], latest.state, latest));
      if (running.length > 1) preview.append(statusText(`+${running.length - 1}`, 'running'));
      summary.replaceChildren(element('span', '工具活动', 'agy-tool-label'), preview);
      recent.replaceChildren();
      for (const tool of tools.slice(-8)) {
        const line = document.createElement('li');
        line.append(element('code', tool.name), statusText(toolLabels[tool.state], tool.state, tool));
        recent.append(line);
      }
    }
  }

  function newestTurn() {
    const viewports = [...document.querySelectorAll('[data-testid="autoscroll-viewport"]')]
      .filter(viewport => viewport.getClientRects().length && viewport.querySelector('[data-testid="user-message"]'));
    if (viewports.length !== 1) return null;
    const users = viewports[0].querySelectorAll('[data-testid="user-message"]');
    return users[users.length - 1]?.parentElement || null;
  }

  function applyTimers() {
    for (const timer of [...panel.querySelectorAll('time[data-since]'), badge]) {
      if (!timer.isConnected) continue;
      const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(timer.dataset.since)));
      const value = `${seconds} 秒`;
      if (timer.textContent !== value) timer.textContent = value;
    }
  }

  function sync() {
    frame = 0;
    for (const button of document.querySelectorAll(groups)) {
      if (!button.getClientRects().length || button.getAttribute('aria-expanded') !== 'false' ||
          manuallyToggled.has(button)) continue;
      const count = attempts.get(button) || 0;
      if (count < 2) {
        attempts.set(button, count + 1);
        button.click();
      }
    }

    const turn = newestTurn();
    const response = turn?.querySelector('[data-testid="agent-response"]');
    const hasData = snapshot && location.pathname === `/c/${snapshot.conversation}` &&
      (snapshot.items.length || snapshot.tools.length);
    if (hasData && response && !boundTurn) boundTurn = turn;
    const showing = hasData && response && boundTurn === turn;
    if (showing) {
      const viewport = response.closest('[data-testid="autoscroll-viewport"]');
      const following = viewport && viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 64;
      const next = JSON.stringify(snapshot);
      const changed = signature !== next;
      if (changed) {
        render();
        signature = next;
      }
      const moved = response.lastElementChild !== panel;
      if (moved) response.append(panel);
      if (following && (changed || moved)) viewport.scrollTop = viewport.scrollHeight;
      const running = snapshot.tools.filter(tool => tool.state === 'running');
      const native = [...response.querySelectorAll('[data-testid="ephemeral-tool-status"]')].pop();
      if (snapshot.active && running.length === 1 && native && Number.isFinite(running[0].start)) {
        badge.dataset.since = running[0].start;
        if (badge.parentElement !== native) native.append(badge);
      } else {
        badge.remove();
      }
    } else {
      panel.remove();
      badge.remove();
    }

    for (const frame of document.querySelectorAll('[data-testid="agent-embed-iframe"]')) {
      const current = showing && snapshot.run && response.contains(frame) &&
        (frame.getAttribute('srcdoc') || '').includes(`data-agent-progress-card="${snapshot.run}"`);
      frame.closest('[data-testid="agent-embed"]')?.classList.toggle('agy-progress-replaced', Boolean(current));
    }
    applyTimers();
  }

  function requestSync() {
    if (!frame) frame = requestAnimationFrame(sync);
  }

  function rememberClick(event) {
    const button = event.target.closest?.(groups);
    if (button && event.isTrusted) manuallyToggled.add(button);
  }

  const observer = new MutationObserver(requestSync);
  observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-expanded', 'srcdoc']});
  const interval = setInterval(applyTimers, 250);
  document.addEventListener('click', rememberClick, true);
  window.__agyProgress = {
    publish(next) {
      if (snapshot?.started_at !== next?.started_at || snapshot?.conversation !== next?.conversation) {
        boundTurn = null;
        activity.open = false;
      }
      snapshot = next;
      requestSync();
    },
    dispose() {
      observer.disconnect();
      cancelAnimationFrame(frame);
      clearInterval(interval);
      document.removeEventListener('click', rememberClick, true);
      document.querySelectorAll('.agy-progress-replaced').forEach(node => node.classList.remove('agy-progress-replaced'));
      panel.remove();
      badge.remove();
      style.remove();
    },
  };
  requestSync();
})();
