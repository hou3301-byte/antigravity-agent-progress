(() => {
  window.__agyProgress?.dispose();

  const groups = 'button[data-testid="worked-for-collapsible"], button[data-testid="progress-updates-collapsible"]';
  const taskLabels = {pending: '待办', running: '进行中', done: '完成', blocked: '阻塞', paused: '已暂停'};
  const taskIcons = {pending: '○', running: '', done: '✓', blocked: '!', paused: 'Ⅱ'};
  const manuallyToggled = new WeakSet();
  const attempts = new WeakMap();
  const nativeStarts = new WeakMap();
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
  taskCard.setAttribute('aria-label', '任务进度');
  panel.append(taskCard);

  const timerWrap = document.createElement('span');
  timerWrap.className = 'agy-live-timer';
  const timerDot = document.createElement('span');
  timerDot.className = 'agy-live-dot';
  timerDot.setAttribute('aria-hidden', 'true');
  const timerClock = document.createElement('time');
  timerWrap.append(timerDot, timerClock);

  function element(tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  }

  function normalizeText(text) {
    return (text || '').replace(/^\s*(?:\[[ /xX]\]\s*)?/, '').replace(/\s+/g, ' ').trim();
  }

  function formatElapsed(seconds) {
    const whole = Math.max(0, Math.floor(seconds));
    if (whole < 60) return `${whole}s`;
    const mins = Math.floor(whole / 60);
    const secs = String(whole % 60).padStart(2, '0');
    return `${mins}m ${secs}s`;
  }

  function render() {
    const {items, active} = snapshot;
    const finished = items.filter(item => item.state === 'done').length;
    const header = document.createElement('header');
    header.append(element('strong', '任务进度'), element('span', `${finished} / ${items.length}`, 'agy-progress-status'));

    const meter = document.createElement('progress');
    meter.className = 'agy-progress-meter';
    meter.max = items.length;
    meter.value = finished;
    meter.setAttribute('aria-label', '任务完成度');

    const tasks = document.createElement('ul');
    for (const item of items) {
      const state = item.state === 'running' && !active ? 'paused' : item.state;
      const mark = element('span', taskIcons[state] ?? '○', 'agy-task-icon');
      mark.dataset.state = state;
      mark.setAttribute('aria-hidden', 'true');

      const label = element('span', '', 'agy-task-label');
      label.append(mark, element('span', item.label));

      const status = element('span', taskLabels[state] || '已暂停', 'agy-progress-status');
      status.dataset.state = state;

      const line = document.createElement('li');
      line.append(label, status);
      tasks.append(line);
    }
    taskCard.replaceChildren(header, meter, tasks);
  }

  function newestTurn() {
    const viewports = [...document.querySelectorAll('[data-testid="autoscroll-viewport"]')]
      .filter(viewport => viewport.getClientRects().length && viewport.querySelector('[data-testid="user-message"]'));
    if (viewports.length !== 1) return null;
    const users = viewports[0].querySelectorAll('[data-testid="user-message"]');
    return users[users.length - 1]?.parentElement || null;
  }

  function syncSourceLists(response, items) {
    const toHide = new Set();
    if (response && items.length) {
      for (const ul of response.querySelectorAll('ul')) {
        if (panel.contains(ul)) continue;
        const lis = [...ul.children].filter(node => node.tagName === 'LI');
        if (lis.length !== items.length) continue;
        if (lis.every((li, index) => normalizeText(li.textContent) === normalizeText(items[index].label))) {
          toHide.add(ul);
        }
      }
    }
    for (const old of document.querySelectorAll('.agy-todo-source-hidden')) {
      if (!toHide.has(old)) old.classList.remove('agy-todo-source-hidden');
    }
    for (const ul of toHide) {
      if (!ul.classList.contains('agy-todo-source-hidden')) ul.classList.add('agy-todo-source-hidden');
    }
  }

  function nativeLabelText(node) {
    return normalizeText([...node.childNodes].filter(child => child !== timerWrap).map(child => child.textContent).join(''));
  }

  function applyTimer() {
    if (!timerWrap.isConnected || !timerClock.dataset.since) return;
    const elapsed = Date.now() / 1000 - Number(timerClock.dataset.since);
    const value = formatElapsed(elapsed);
    if (timerClock.textContent !== value) timerClock.textContent = value;
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
    const sameConv = Boolean(snapshot && location.pathname === `/c/${snapshot.conversation}`);
    const hasItems = Boolean(sameConv && snapshot.items.length);
    if (sameConv && response && !boundTurn) boundTurn = turn;

    const showingCard = Boolean(hasItems && !snapshot.done && response && boundTurn === turn);
    if (showingCard) {
      const viewport = response.closest('[data-testid="autoscroll-viewport"]');
      const following = viewport && viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 64;
      const next = JSON.stringify({items: snapshot.items, active: snapshot.active, done: snapshot.done});
      const changed = signature !== next;
      if (changed) {
        render();
        signature = next;
      }
      const moved = response.lastElementChild !== panel;
      if (moved) response.append(panel);
      if (following && (changed || moved)) viewport.scrollTop = viewport.scrollHeight;
    } else {
      panel.remove();
    }

    syncSourceLists(hasItems && response && boundTurn === turn ? response : null, hasItems ? snapshot.items : []);

    const native = response ? [...response.querySelectorAll('[data-testid="ephemeral-tool-status"]')].pop() : null;
    if (native) {
      let since = sameConv && Number.isFinite(snapshot.running_start) ? snapshot.running_start : null;
      if (since === null) {
        const labelKey = nativeLabelText(native);
        const stored = nativeStarts.get(native);
        if (!stored || stored.label !== labelKey) {
          since = Date.now() / 1000;
          nativeStarts.set(native, {label: labelKey, start: since});
        } else {
          since = stored.start;
        }
      }
      timerClock.dataset.since = String(since);
      if (timerWrap.parentElement !== native) native.append(timerWrap);
    } else {
      timerWrap.remove();
    }

    applyTimer();
  }

  function requestSync() {
    if (!frame) frame = requestAnimationFrame(sync);
  }

  function rememberClick(event) {
    const button = event.target.closest?.(groups);
    if (button && event.isTrusted) manuallyToggled.add(button);
  }

  const observer = new MutationObserver(requestSync);
  observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-expanded']});
  const interval = setInterval(applyTimer, 250);
  document.addEventListener('click', rememberClick, true);

  window.__agyProgress = {
    publish(next) {
      if (snapshot?.started_at !== next?.started_at || snapshot?.conversation !== next?.conversation) {
        boundTurn = null;
        signature = '';
      }
      snapshot = next;
      requestSync();
    },
    dispose() {
      observer.disconnect();
      cancelAnimationFrame(frame);
      clearInterval(interval);
      document.removeEventListener('click', rememberClick, true);
      document.querySelectorAll('.agy-todo-source-hidden').forEach(node => node.classList.remove('agy-todo-source-hidden'));
      panel.remove();
      timerWrap.remove();
      style.remove();
    },
  };
  requestSync();
})();
