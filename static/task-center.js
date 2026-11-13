// 后台任务：全站一个轻量轮询，任务本身都在服务端运行。
//
// 平时收起：固定在左上角菜单按钮旁的小圆钮（带进度环），所有页面都在同一位置，只在有任务时出现。
//   有事件（提交、完成、失败）时圆钮横向展开成胶囊，几秒后缩回，类似灵动岛的实时活动。
// 唤出：点圆钮，面板从圆钮原地展开（手机上是底部抽屉）；点某个任务在面板内展开详情。
// 结束：成功的任务显示“完成”后向右滑出并清除；失败或需处理的任务留着、圆钮带提醒点，直到重试或忽略。
// 换页：先用上一页缓存的状态立即渲染；圆钮有固定的 view-transition-name，跨页转场时停在原地。
//
// 对外：TaskCenter.arrive(groups, sources) 关联页确认后调用，海报从列表飞进圆钮；
//       progress / result / clear / has 供 StatusBar 的 task 层使用；open(id) 展开某个任务。
(() => {
    const root = document.querySelector('[data-task-center]');
    if (!root) return;
    const $ = selector => root.querySelector(selector);
    const orb = $('[data-task-orb]'), orbArt = $('[data-orb-art]'), orbRing = $('[data-orb-ring]'), orbBadge = $('[data-orb-badge]');
    const orbTitle = $('[data-orb-title]'), orbCaption = $('[data-orb-caption]');
    const panel = $('[data-island-panel]'), scrim = $('[data-island-scrim]'), list = $('[data-task-list]'), empty = $('[data-task-empty]');
    const summaryNode = $('[data-island-summary]'), servicesNode = $('[data-task-services]');
    const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
    const sheetMode = () => innerWidth < 576;
    const SPRING = 'cubic-bezier(0.32, 0.72, 0, 1)';

    const ACTIVE = ['running', 'waiting', 'queued'];
    const active = task => ACTIVE.includes(task.state);
    const problem = task => ['failed', 'partial'].includes(task.state);
    const read = key => { try { const value = JSON.parse(sessionStorage.getItem(key) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; } };
    const save = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(Object.fromEntries(Object.entries(value).slice(-300)))); } catch {} };
    const seen = read('tasks-seen'), dismissed = read('tasks-dismissed'), posters = read('tasks-posters');
    const cached = read('tasks-state');
    let state = Array.isArray(cached.tasks) ? {tasks: cached.tasks, services: cached.services || [], active: !!cached.active} : {tasks: [], services: [], active: false};
    let etag = null, timer = null, inFlight = false, requested = false, stopped = false, stale = false;
    let open = false, expandedId = null, detailSignature = null, detailLoading = false, detailMissing = false, ready = false;
    let liveTimer = null;
    const rows = new Map(), local = new Map(), placeholders = new Map(), leaving = new Map(), pending = new Set(), errors = new Map();
    const details = new Map(), detailEtag = {};

    // 与任务详情的步骤名一致（viewmodels/tasks.py）
    const labels = {metadata:'核对资料', probe:'读取参数', write:'写入媒体库', image:'补海报', rating:'补评分', derive:'生成预览',
        enrich:'补齐资料', retry:'等待重试', queued:'排队', scan:'扫描', recognizing:'识别', 'imdb-download':'下载单集数据', 'imdb-build':'建立单集索引', complete:'完成', failed:'失败', partial:'需处理', cancelled:'已停止'};
    const KIND_ICON = {import:'film', scan:'folder', rating:'star', imdb:'database', local:'list-todo'};
    const KIND_META = {scan:'扫描媒体目录', rating:'刷新评分', imdb:'IMDb 单集数据', local:'本页操作'};
    const STATE_ICON = {done:'circle-check', active:'loader-circle', pending:'clock', failed:'circle-alert', skipped:'ban'};

    const el = (tag, className = '', text = '') => {
        const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
    };
    const icon = name => window.appIcon(name);
    const fingerprint = task => JSON.stringify([task.state, task.phase, task.detail, task.updated_at, task.done, task.total, task.issues]);
    const serviceName = service => state.services.find(item => item.service === service)?.name || service;
    const posterOf = task => task.poster ? root.dataset.imageBase + task.poster : posters[task.id] || '';

    const phaseLabel = task => labels[task.phase] || task.phase || '进行中';
    const statusLabel = task => task.state === 'waiting' ? '待重试' : task.state === 'queued' ? '排队' : labels[task.state] || '进行中';
    const countLabel = task => task.total > 0 ? `${task.done}/${task.total}` : '';
    function caption(task) {
        if (task.state !== 'running') return statusLabel(task);
        return [phaseLabel(task), countLabel(task)].filter(Boolean).join(' ');
    }
    // 0–1；未知进度返回 null
    const ratio = task => !active(task) ? 1 : task.state === 'running' && task.total > 0 ? Math.max(0, Math.min(1, task.done / task.total)) : null;

    function countdown(until) {
        const time = el('time', 'task-countdown'); time.dataset.until = until; return time;
    }
    function updateClocks() {
        if (document.hidden) return;
        root.querySelectorAll('[data-until]').forEach(node => {
            const seconds = Math.max(0, Math.ceil(Number(node.dataset.until) - Date.now() / 1000));
            node.textContent = seconds > 3600 ? `${Math.ceil(seconds / 3600)}h` : seconds > 90 ? `${Math.ceil(seconds / 60)}m` : `${seconds}s`;
            node.title = seconds ? `约 ${seconds} 秒后可重试` : '等待下一次状态更新';
        });
    }

    // ---- 哪些任务留着 -------------------------------------------------------

    function all() {
        const known = new Set(state.tasks.map(task => task.id));
        for (const id of placeholders.keys()) if (known.has(id)) placeholders.delete(id);
        return [...state.tasks, ...placeholders.values(), ...local.values()];
    }
    function visible() {
        return all().filter(task => leaving.has(task.id) || (active(task) || problem(task)) && dismissed[task.id] !== fingerprint(task))
            .sort((a, b) => (problem(a) && !leaving.has(a.id)) - (problem(b) && !leaving.has(b.id)));
    }
    function dismiss(task) {
        dismissed[task.id] = fingerprint(task); save('tasks-dismissed', dismissed);
    }

    // 本次浏览中看着结束的任务：先显示“完成”，再滑出；之前就已结束的直接清掉。
    function observe() {
        let changed = false;
        for (const task of all()) {
            if (active(task) && dismissed[task.id]) { delete dismissed[task.id]; changed = true; }
            const wasActive = ACTIVE.includes(seen[task.id]);
            if (!active(task) && !problem(task) && dismissed[task.id] !== fingerprint(task) && !leaving.has(task.id)) {
                if (wasActive) {
                    leaving.set(task.id, setTimeout(() => leave(task.id), 2400));
                    moment(task.name, task.kind === 'import' ? '关联完成' : '完成', 'success');
                } else { dismissed[task.id] = fingerprint(task); changed = true; }
            }
            if (wasActive && problem(task)) moment(task.name, task.state === 'failed' ? (task.kind === 'import' ? '关联失败' : '失败') : '需处理', task.state === 'failed' ? 'danger' : 'warning');
            if (!active(task) && leaving.has(task.id) && problem(task)) { clearTimeout(leaving.get(task.id)); leaving.delete(task.id); }
            seen[task.id] = task.state;
        }
        if (changed) save('tasks-dismissed', dismissed);
        save('tasks-seen', seen);
    }

    // 向右滑出：面板打开时是列表里那一行；只剩它一个时是圆钮本身。
    function leave(id, commit = () => { const task = all().find(item => item.id === id); if (task) dismiss(task); }) {
        clearTimeout(leaving.get(id));
        const finish = animation => {
            leaving.delete(id); commit();
            if (expandedId === id) expandedId = null;
            render(); animation?.cancel();
        };
        const tasks = visible();
        const row = rows.get(id)?.node;
        if (reduced() || root.hidden || !tasks.some(item => item.id === id)) return finish();
        let target = null, distance = '2.5rem';
        if (open) { target = row; distance = '3rem'; }
        else if (tasks.length <= 1) { target = orb; distance = '1.5rem'; }
        if (!target || target.hidden) return finish();
        const animation = target.animate([{transform: 'translateX(0)', opacity: 1, filter: 'blur(0px)'}, {transform: `translateX(${distance})`, opacity: 0, filter: 'blur(2px)'}],
            {duration: 340, easing: 'cubic-bezier(0.55, 0, 0.75, 0.3)', fill: 'forwards'});
        animation.finished.then(() => {
            if (target !== row) return finish(animation);
            const height = row.offsetHeight;
            row.animate([{height: `${height}px`}, {height: '0px'}], {duration: 180, easing: 'ease-out'}).finished.then(() => finish(animation), () => finish(animation));
        }, () => finish());
    }

    // ---- 收起状态：圆钮 -----------------------------------------------------

    function ring(node, tasks, tone) {
        if (!node.firstChild) {
            node.innerHTML = '<svg viewBox="0 0 36 36"><circle class="ring-track" cx="18" cy="18" r="16"/><circle class="ring-value" cx="18" cy="18" r="16" pathLength="100"/></svg><span class="ring-icon"></span>';
        }
        // 进度取各任务的平均值（已结束的算满）；进行中的任务都不知道总数时转圈。
        const running = tasks.some(active);
        const known = tasks.map(ratio).filter(value => value !== null);
        const percent = known.length ? Math.round(known.reduce((sum, value) => sum + value, 0) / known.length * 100) : 0;
        node.classList.toggle('is-indeterminate', running && !tasks.some(task => active(task) && ratio(task) !== null));
        node.style.setProperty('--ring', running ? Math.max(percent, 4) : 100);
        node.dataset.tone = tone;
        const symbol = {danger: 'circle-alert', warning: 'triangle-alert', success: 'circle-check', waiting: 'hourglass'}[tone] || '';
        const holder = node.querySelector('.ring-icon');
        if (holder.dataset.symbol !== symbol) { holder.replaceChildren(...(symbol ? [icon(symbol)] : [])); holder.dataset.symbol = symbol; }
    }

    // 环的状态：还有任务在跑时只显示进度；全部停下后才显示失败、需处理或完成。
    function alertOf(tasks) {
        const live = tasks.filter(task => !leaving.has(task.id));
        return live.some(task => task.state === 'failed') ? 'danger' : live.some(problem) ? 'warning' : '';
    }
    function toneOf(tasks) {
        if (!tasks.length) return 'waiting';
        if (tasks.some(active)) return tasks.every(task => task.state === 'waiting') ? 'waiting' : '';
        return alertOf(tasks) || 'success';
    }

    function summary(tasks) {
        const running = tasks.filter(active).length, problems = tasks.filter(task => problem(task) && !leaving.has(task.id)).length;
        const done = tasks.length - running - problems;
        return [running ? `${running} 进行中` : '', problems ? `${problems} 需处理` : '', done ? `${done} 完成` : ''].filter(Boolean).join(' · ');
    }

    function thumb(task, className) {
        const frame = el('span', className);
        frame.dataset.id = task.id;
        frame.append(icon(KIND_ICON[task.kind] || 'list-todo'));
        const src = task.kind === 'import' ? posterOf(task) : '';
        if (src) {
            const img = el('img'); img.alt = ''; img.decoding = 'async'; img.src = src;
            img.addEventListener('error', () => img.remove());
            frame.append(img);
            frame.classList.add('has-image');
        }
        return frame;
    }

    function renderArt(node, tasks, limit) {
        // 进行中的、有海报的排在前面
        const rank = task => (active(task) ? 0 : 2) + (posterOf(task) ? 0 : 1);
        const imports = tasks.filter(task => task.kind === 'import').sort((a, b) => rank(a) - rank(b)).slice(0, limit);
        const items = imports.length ? imports : tasks.slice(0, 1);
        const key = JSON.stringify(items.map(task => [task.id, posterOf(task), task.kind]));
        if (node.dataset.key === key) return;
        node.dataset.key = key;
        node.replaceChildren(...items.map(task => thumb(task, task.kind === 'import' ? 'island-poster' : 'island-glyph')));
    }

    function headline(tasks) {
        if (!tasks.length) return ['接口等待恢复', state.services.map(item => item.name).join('、')];
        if (tasks.length === 1) {
            const [task] = tasks;
            return [task.name, leaving.has(task.id) ? (task.kind === 'import' ? '关联完成' : '完成') : caption(task)];
        }
        return [tasks.every(task => task.kind === 'import') ? `关联 ${tasks.length} 部` : `${tasks.length} 项任务`, summary(tasks)];
    }

    // 实时活动：圆钮横向展开成胶囊显示这件事，几秒后缩回。
    function moment(title, text, tone = '') {
        if (!ready || open) return;
        clearTimeout(liveTimer);
        orbTitle.textContent = title; orbCaption.textContent = text;
        orb.dataset.moment = tone;
        orb.classList.add('is-live');
        liveTimer = setTimeout(() => { orb.classList.remove('is-live'); delete orb.dataset.moment; liveTimer = null; renderHosts(visible()); }, 2800);
    }

    function renderHosts(tasks) {
        const showing = tasks.length > 0 || state.services.length > 0;
        const tone = toneOf(tasks), alert = stale ? 'warning' : alertOf(tasks);
        let [title, text] = headline(tasks);
        if (stale) text = '连接中断，正在重试';
        const label = `后台任务：${title}，${text}。点击查看`;

        renderArt(orbArt, tasks, 1);
        ring(orbRing, tasks, tone);
        orb.dataset.alert = alert;
        orbBadge.textContent = tasks.length > 1 ? String(tasks.length) : '';
        if (!liveTimer) { orbTitle.textContent = title; orbCaption.textContent = text; }
        orb.setAttribute('aria-label', label);
        orb.title = tasks.map(task => `${task.name} · ${caption(task)}`).join('\n');

        if (showing && orb.hidden) {
            orb.hidden = false;
            if (ready && !reduced()) orb.animate([{opacity: 0, transform: 'scale(0.4)'}, {opacity: 1, transform: 'scale(1)'}], {duration: 420, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)'});
        } else if (!showing && !orb.hidden) orb.hidden = true;
        root.hidden = !showing && !open;
        if (!showing && open) setOpen(false, {focus: false, instant: true});
    }

    // ---- 展开状态：任务列表与详情 -------------------------------------------

    function progressTrack(task, track = el('span')) {
        const value = ratio(task);
        track.className = `task-progress${value === null ? ' is-indeterminate' : ''}`;
        const percent = Math.round((value ?? 0) * 100);
        track.style.setProperty('--progress', value === null ? '25%' : `${percent}%`);
        track.setAttribute('role', 'progressbar');
        track.setAttribute('aria-label', task.name);
        track.setAttribute('aria-valuetext', caption(task));
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
        if (value === null) track.removeAttribute('aria-valuenow'); else track.setAttribute('aria-valuenow', percent);
        return track;
    }

    function renderRow(task) {
        let row = rows.get(task.id);
        if (!row) {
            const node = el('li', 'island-task'); node.dataset.taskId = task.id;
            const head = el('button', 'island-task-head'); head.type = 'button';
            const detailId = `island-task-${rows.size}-${Date.now().toString(36)}`;
            head.setAttribute('aria-controls', detailId);
            const media = el('span', 'island-task-media');
            const text = el('span', 'island-task-text'), name = el('span', 'island-task-name'), meta = el('span', 'island-task-caption');
            const track = el('span');
            text.append(name, meta, track);
            const status = el('span', 'island-task-state'); status.setAttribute('aria-hidden', 'true');
            head.append(media, text, status);
            head.addEventListener('click', () => toggleTask(task.id));
            const detail = el('div', 'island-task-detail'); detail.id = detailId; detail.hidden = true;
            node.append(head, detail);
            row = {node, head, media, name, meta, track, status, detail}; rows.set(task.id, row);
        }
        const finishing = leaving.has(task.id);
        row.node.classList.toggle('is-problem', problem(task) && !finishing);
        row.node.classList.toggle('is-failed', task.state === 'failed');
        row.node.classList.toggle('is-done', finishing);
        const key = JSON.stringify([task.kind, posterOf(task)]);
        if (row.media.dataset.key !== key) {
            row.media.dataset.key = key;
            row.media.replaceChildren(thumb(task, task.kind === 'import' ? 'island-poster' : 'island-glyph'));
        }
        const symbol = task.state === 'running' ? 'loader-circle' : finishing || task.state === 'complete' ? 'circle-check' :
            task.state === 'failed' ? 'circle-alert' : task.state === 'partial' ? 'triangle-alert' : task.state === 'cancelled' ? 'ban' : 'hourglass';
        if (row.status.dataset.symbol !== symbol) { row.status.replaceChildren(icon(symbol)); row.status.dataset.symbol = symbol; }
        row.status.classList.toggle('is-running', task.state === 'running');
        row.name.textContent = task.name;
        row.meta.textContent = finishing ? '完成' : [caption(task), task.kind === 'import' && task.files ? `${task.files} 个视频` : ''].filter(Boolean).join(' · ');
        row.head.setAttribute('aria-expanded', String(expandedId === task.id));
        row.head.setAttribute('aria-label', `${task.name}，${row.meta.textContent}，${expandedId === task.id ? '收起' : '展开'}详情`);
        progressTrack(task, row.track);
        row.track.hidden = !active(task) && !finishing;
        row.detail.hidden = expandedId !== task.id;
        return row.node;
    }

    function renderList(tasks) {
        const ids = new Set(tasks.map(task => task.id));
        for (const [id, row] of rows) if (!ids.has(id)) { row.node.remove(); rows.delete(id); }
        tasks.forEach((task, index) => { const node = renderRow(task); if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
        empty.hidden = tasks.length > 0;
        summaryNode.textContent = summary(tasks);
        servicesNode.replaceChildren(...state.services.map(service => {
            const line = el('p', 'task-note');
            line.append(icon('hourglass'), el('span', '', `${service.name} · ${service.reason}`), countdown(service.until));
            return line;
        }));
    }

    function stateIcon(stateName) {
        const node = el('span', 'task-state-icon'); node.dataset.state = stateName;
        node.append(icon(STATE_ICON[stateName] || 'clock')); node.setAttribute('aria-hidden', 'true');
        return node;
    }
    function section(title, count, hint) {
        const node = el('section', 'task-section');
        const head = el('h3', 'task-section-title', title);
        if (count) head.append(el('span', 'task-section-count', String(count)));
        node.append(head);
        if (hint) node.append(el('p', 'task-section-hint', hint));
        return node;
    }
    function renderRows(items, render) {
        const rowsList = el('ul', 'ui-card task-rows');
        for (const item of items) rowsList.append(render(item));
        return rowsList;
    }
    // 关联四步：一条分段进度，标签与分段左对齐
    function renderSteps(steps) {
        const steps_ = el('ol', 'task-steps');
        steps_.setAttribute('aria-label', '关联进度');
        for (const step of steps) {
            const item = el('li', 'task-step'); item.dataset.state = step.state;
            const label = el('span', 'task-step-label', step.label);
            if (step.count) label.append(el('span', 'task-step-count', step.count));
            item.append(el('span', 'task-step-bar'), label);
            item.setAttribute('aria-label', `${step.label}：${{done:'已完成', active:'进行中', failed:'失败', pending:'未开始'}[step.state] || ''}${step.count ? ` ${step.count}` : ''}`);
            steps_.append(item);
        }
        return steps_;
    }

    function renderImport(target, task, detail) {
        target.append(renderSteps(detail.steps));
        const waits = Object.entries(task?.waits || {});
        if (task?.retry_at && active(task) || waits.length) {
            const note = el('p', 'task-note');
            note.append(icon('hourglass'), el('span', '', waits.map(([service, wait]) => `${serviceName(service)} · ${wait.reason}`).join('；') || '等待重试'));
            const until = task.retry_at || waits[0]?.[1].until;
            if (until) note.append(countdown(until));
            target.append(note);
        }
        const files = section('视频', detail.files.length);
        files.append(renderRows(detail.files, file => {
            const row = el('li', 'task-row'); row.dataset.state = file.state;
            const text = el('div', 'task-row-text');
            const name = el('p', 'task-row-title', file.position ? `${file.position} · ${file.name}` : file.name);
            name.title = file.name;
            text.append(name, el('p', 'task-row-hint', file.detail));
            row.append(stateIcon(file.state), text);
            return row;
        }));
        target.append(files);
        if (detail.issues.length) {
            const issues = section('需要处理');
            issues.append(renderRows(detail.issues, issue => {
                const row = el('li', 'task-row'); row.dataset.state = 'failed';
                const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-hint', issue));
                row.append(stateIcon('failed'), text); return row;
            }));
            target.append(issues);
        }
        const enrich = section('之后补齐', '', '海报、评分与预览图在关联完成后继续获取，不影响播放。');
        enrich.append(renderRows(detail.enrich, item => {
            const row = el('li', 'task-row is-compact'); row.dataset.state = item.state;
            const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-title', item.label));
            row.append(stateIcon(item.state), text, el('span', 'task-row-meta', item.text));
            return row;
        }));
        target.append(enrich);
    }

    function renderGeneric(target, task) {
        const facts = [];
        if (task.total > 0) facts.push(['已处理', countLabel(task)]);
        if (task.current?.length) facts.push(['正在处理', task.current.join('、') + (task.current_more ? ` 等 ${task.current.length + task.current_more} 项` : '')]);
        if (task.detail) facts.push(['当前详情', task.detail]);
        if (task.attempts > 1) facts.push(['尝试次数', String(task.attempts)]);
        if (facts.length) target.append(renderRows(facts, ([label, value]) => {
            const row = el('li', 'task-row is-compact');
            const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-hint', label));
            row.append(text, el('span', 'task-row-meta text-break', value)); return row;
        }));
        for (const [service, wait] of Object.entries(task.waits || {})) {
            const line = el('p', 'task-note', `${serviceName(service)} · ${wait.reason} `);
            if (wait.until) line.append(countdown(wait.until));
            target.append(line);
        }
        const issues = (task.issues || []).filter(issue => issue.reason);
        if (issues.length) {
            const block = section('需要处理');
            block.append(renderRows(issues, issue => {
                const row = el('li', 'task-row'); row.dataset.state = 'failed';
                const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-hint', [issue.label, issue.service, issue.reason].filter(Boolean).join(' · ')));
                row.append(stateIcon('failed'), text); return row;
            }));
            target.append(block);
        }
        if (!target.childElementCount) target.append(el('p', 'island-detail-empty', task.placeholder ? '已提交，等待开始…' : caption(task)));
    }

    const actionType = task => task.localAction ? 'retry' : task.cancel_ids?.length ? 'cancel' :
        task.retry_ids?.length || problem(task) && ['scan', 'rating', 'imdb'].includes(task.kind) ? 'retry' : null;

    function renderDetail() {
        const row = expandedId && rows.get(expandedId);
        if (!row) return;
        const task = all().find(item => item.id === expandedId);
        const detail = details.get(expandedId);
        const signature = JSON.stringify([task, detail, pending.has(expandedId), errors.get(expandedId), leaving.has(expandedId), detailMissing]);
        if (signature === detailSignature) return;
        detailSignature = signature;
        const focusedRole = row.detail.contains(document.activeElement) ? document.activeElement.dataset.role : null;
        const target = row.detail;
        target.replaceChildren();
        if (!task) return;
        const meta = detail?.meta || KIND_META[task.kind] || '';
        if (meta) target.append(el('p', 'island-detail-meta', meta));
        if (detail) renderImport(target, task, detail);
        else if (task.kind === 'import' && !task.placeholder && !detailMissing) target.append(el('p', 'island-detail-empty', '正在读取…'));
        else renderGeneric(target, task);
        if (errors.get(task.id)) {
            const error = el('p', 'small text-danger-emphasis mt-3 mb-0', errors.get(task.id)); error.setAttribute('role', 'alert'); target.append(error);
        }
        const actions = el('div', 'island-detail-actions');
        if (detail?.title_url) {
            const link = el('a', 'btn btn-sm chip-btn'); link.href = detail.title_url; link.dataset.role = 'open';
            link.append(icon('external-link'), el('span', '', '打开作品页')); actions.append(link);
        }
        if (problem(task) && !leaving.has(task.id)) {
            const ignore = el('button', 'btn btn-sm chip-btn', '忽略'); ignore.type = 'button'; ignore.dataset.role = 'ignore';
            ignore.setAttribute('aria-label', `忽略任务：${task.name}`);
            ignore.addEventListener('click', () => leave(task.id));
            actions.append(ignore);
        }
        const type = actionType(task);
        if (type && !leaving.has(task.id)) {
            const button = el('button', `btn btn-sm ${type === 'cancel' ? 'chip-btn text-danger' : 'media-primary-action is-compact'}`, task.actionLabel || (type === 'cancel' ? '停止关联' : '重试'));
            button.type = 'button'; button.dataset.role = 'action'; button.disabled = pending.has(task.id);
            button.addEventListener('click', () => action(task, type));
            actions.append(button);
        }
        if (actions.childElementCount) target.append(actions);
        if (focusedRole) (target.querySelector(`[data-role="${focusedRole}"]:not(:disabled)`) || row.head).focus({preventScroll: true});
        updateClocks();
    }

    function toggleTask(id) {
        expandedId = expandedId === id ? null : id;
        detailSignature = null; detailMissing = false;
        render();
        if (expandedId) { loadDetail(); rows.get(id)?.node.scrollIntoView({block: 'nearest', behavior: reduced() ? 'auto' : 'smooth'}); }
    }

    // 只在任务展开、且是关联任务时读取详情（文件列表与探测结果不进汇总轮询）
    async function loadDetail() {
        const id = expandedId;
        if (!id || !id.startsWith('import:') || placeholders.has(id) || detailLoading) return;
        detailLoading = true;
        try {
            const response = await fetch(`${root.dataset.detailUrl}?${new URLSearchParams({id})}`, {headers: detailEtag[id] ? {'If-None-Match': detailEtag[id]} : {}, cache: 'no-cache'});
            if (response.status === 404) { details.delete(id); detailMissing = true; }
            else if (response.ok) {
                const data = await response.json();
                details.set(id, data); detailEtag[id] = response.headers.get('ETag'); detailMissing = false;
                if (data.poster && !posters[id]) { posters[id] = root.dataset.imageBase + data.poster; save('tasks-posters', posters); }
            }
        } catch {}
        finally { detailLoading = false; if (expandedId === id) render(); }
    }

    async function action(task, type) {
        if (pending.has(task.id)) return;
        pending.add(task.id); errors.delete(task.id);
        render();
        try {
            if (task.localAction) await task.localAction();
            else {
                const url = task.kind === 'imdb' ? '/settings/imdb-episodes' : task.kind === 'scan' ? root.dataset.scanUrl + '?refresh=1' : task.kind === 'rating' ? root.dataset.ratingUrl : root.dataset.jobsUrl;
                const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(task.kind === 'import' ? {action: type, ids: type === 'cancel' ? task.cancel_ids : task.retry_ids} : {})});
                if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || '操作失败，请重试'); }
            }
            delete dismissed[task.id]; save('tasks-dismissed', dismissed);
            if (type === 'retry') seen[task.id] = 'queued';
            window.dispatchEvent(new CustomEvent('tasks:action', {detail: {kind: task.kind, action: type}}));
            refresh();
        } catch (error) {
            errors.set(task.id, error.message);
            if (!open || expandedId !== task.id) window.showAppToast?.(error.message, 'danger');
        } finally {
            pending.delete(task.id);
            render();
        }
    }

    function render() {
        const tasks = visible();
        renderHosts(tasks);
        renderList(tasks);
        renderDetail();
        updateClocks();
    }

    // ---- 唤出 / 收起 -------------------------------------------------------
    // 桌面：面板左上角与圆钮重合，从圆钮原地向右下长出来，clip-path 从圆形变到面板。
    // 手机：底部抽屉，带遮罩。

    // 圆钮的布局位置：不受它自己的弹出、回弹动画（transform）影响。
    function orbRect() {
        const top = orb.offsetTop, left = orb.offsetLeft, width = orb.offsetWidth, height = orb.offsetHeight;
        return {top, left, width, height, right: left + width, bottom: top + height};
    }

    function place() {
        const sheet = sheetMode();
        panel.classList.toggle('is-sheet', sheet);
        panel.style.top = panel.style.left = '';
        if (sheet) return;
        const rect = orbRect();
        panel.style.top = `${rect.top}px`;
        panel.style.left = `${Math.max(8, rect.left)}px`;
    }

    function morph(from, opening) {
        const content = [...panel.children];
        if (panel.classList.contains('is-sheet')) {
            scrim.animate([{opacity: 0}, {opacity: 1}], {duration: 280, direction: opening ? 'normal' : 'reverse', fill: 'both'});
            return panel.animate([{transform: 'translateY(100%)'}, {transform: 'translateY(0)'}], {duration: opening ? 420 : 280, easing: opening ? SPRING : 'ease-in', direction: opening ? 'normal' : 'reverse', fill: 'both'});
        }
        const target = panel.getBoundingClientRect();
        const radius = parseFloat(getComputedStyle(panel).borderTopLeftRadius) || 24;
        const inset = `inset(${Math.max(0, from.top - target.top)}px ${Math.max(0, target.right - from.right)}px ${Math.max(0, target.bottom - from.bottom)}px ${Math.max(0, from.left - target.left)}px round ${from.height / 2}px)`;
        const full = `inset(0px 0px 0px 0px round ${radius}px)`;
        content.forEach(node => node.animate(opening ? [{opacity: 0}, {opacity: 0, offset: 0.25}, {opacity: 1}] : [{opacity: 1}, {opacity: 0, offset: 0.5}, {opacity: 0}], {duration: opening ? 420 : 280, easing: 'ease-out'}));
        return panel.animate(opening ? [{clipPath: inset}, {clipPath: full}] : [{clipPath: full}, {clipPath: inset}], {duration: opening ? 460 : 300, easing: SPRING});
    }

    function setOpen(value, {focus = true, instant = false} = {}) {
        if (open === value) return;
        open = value;
        orb.setAttribute('aria-expanded', String(value));
        if (value) {
            clearTimeout(liveTimer); liveTimer = null; orb.classList.remove('is-live');
            const from = orbRect();
            root.hidden = false;
            panel.hidden = false;
            render();
            place();
            scrim.hidden = !panel.classList.contains('is-sheet');
            // 桌面上面板盖在圆钮的位置，圆钮先藏起来（手机抽屉在底部，圆钮照常显示）
            orb.classList.toggle('is-covered', !panel.classList.contains('is-sheet'));
            root.classList.add('is-open');
            if (!reduced() && !instant) morph(from, true);
            if (focus) panel.focus({preventScroll: true});
            refresh();
            return;
        }
        expandedId = null; detailSignature = null;
        const done = () => {
            if (open) return;
            panel.hidden = true; scrim.hidden = true;
            scrim.getAnimations().forEach(animation => animation.cancel());
            panel.getAnimations().forEach(animation => animation.cancel());
            render();
        };
        root.classList.remove('is-open');
        render();
        const reveal = () => { orb.classList.remove('is-covered'); done(); };
        if (reduced() || instant || root.hidden || orb.hidden) reveal();
        else morph(orbRect(), false).finished.then(reveal, reveal);
        if (focus && !orb.hidden) orb.focus({preventScroll: true});
    }

    orb.addEventListener('click', () => setOpen(!open));
    scrim.addEventListener('click', () => setOpen(false, {focus: false}));
    $('[data-island-collapse]').addEventListener('click', () => setOpen(false));
    document.addEventListener('pointerdown', event => {
        if (open && !panel.contains(event.target) && !orb.contains(event.target)) setOpen(false, {focus: false});
    }, true);
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && open) { event.stopPropagation(); setOpen(false); } });
    panel.addEventListener('focusout', event => {
        if (open && event.relatedTarget && !panel.contains(event.relatedTarget) && !orb.contains(event.relatedTarget)) setOpen(false, {focus: false});
    });
    addEventListener('resize', () => { if (open) place(); });

    // ---- 海报飞入 ---------------------------------------------------------
    // 沿一条二次贝塞尔曲线（CSS offset-path）飞向左上角的圆钮：先轻微抬起，途中加速并带运动模糊与拉伸，
    // 落点前减速、收成圆形；身后跟两道淡残影。落地时圆钮被“吸”得一鼓再回弹。

    const motionPath = CSS.supports?.('offset-path', 'path("M 0 0 L 1 1")');

    function fly(sources) {
        if (reduced() || root.hidden || !sources.length) return;
        const slotNode = open ? list : orbArt;
        const slot = slotNode.getBoundingClientRect();
        if (!slot.width) return;
        const round = !open;
        slotNode.style.opacity = '0';
        const flights = sources.slice(0, 6).filter(({rect}) => rect.width && rect.bottom > 0 && rect.top < innerHeight);
        if (!flights.length) { slotNode.style.opacity = ''; return; }
        let landed = 0;
        flights.forEach((source, index) => {
            const {rect} = source;
            const scale = slot.height / rect.height;
            const x0 = rect.left + rect.width / 2, y0 = rect.top + rect.height / 2;
            const x1 = slot.left + Math.min(slot.height * 2 / 3, slot.width) / 2 + Math.min(index, 2) * 3, y1 = slot.top + slot.height / 2;
            // 控制点：先沿原方向离开，再拐向落点，形成一条“被吸过去”的弧线
            const cx = x0 + (x1 - x0) * 0.12, cy = y0 + (y1 - y0) * 0.88 - Math.sign(y1 - y0 || 1) * 24;
            const endRadius = round ? '50%' : `${6 / scale}px`;
            const mid = (1 + scale) / 2;
            const frames = motionPath ? [
                {offsetDistance: '0%', transform: 'scale(1)', filter: 'blur(0px)', borderRadius: '8px'},
                {offset: 0.14, offsetDistance: '3%', transform: 'scale(1.06)', filter: 'blur(0px)'},
                {offset: 0.55, offsetDistance: '58%', transform: `scale(${mid * 0.96}, ${mid * 1.1})`, filter: 'blur(3px)'},
                {offset: 0.86, offsetDistance: '95%', transform: `scale(${scale * 1.08})`, filter: 'blur(0.8px)'},
                {offsetDistance: '100%', transform: `scale(${scale})`, filter: 'blur(0px)', borderRadius: endRadius},
            ] : [
                {transform: 'translate(0, 0) scale(1)', filter: 'blur(0px)'},
                {offset: 0.55, transform: `translate(${(x1 - x0) * 0.5}px, ${(y1 - y0) * 0.5 - 32}px) scale(${mid})`, filter: 'blur(3px)'},
                {transform: `translate(${x1 - x0}px, ${y1 - y0}px) scale(${scale})`, filter: 'blur(0px)', borderRadius: endRadius},
            ];
            const make = (opacity, extraBlur, delay) => {
                const clone = el('span', 'island-flyer');
                if (source.src) { const img = el('img'); img.alt = ''; img.src = source.src; clone.append(img); }
                else clone.append(icon('film'));
                Object.assign(clone.style, {width: `${rect.width}px`, height: `${rect.height}px`, opacity: String(opacity)});
                if (motionPath) Object.assign(clone.style, {left: '0px', top: '0px', offsetPath: `path("M ${x0} ${y0} Q ${cx} ${cy} ${x1} ${y1}")`, offsetRotate: '0deg', offsetAnchor: 'center'});
                else Object.assign(clone.style, {left: `${rect.left}px`, top: `${rect.top}px`});
                if (extraBlur) clone.classList.add('is-ghost');
                document.body.append(clone);
                const steps = extraBlur ? frames.map(frame => ({...frame, filter: frame.filter.replace(/blur\(([\d.]+)px\)/, (_, value) => `blur(${Number(value) + extraBlur}px)`)})) : frames;
                return clone.animate(steps, {duration: 760, delay: index * 80 + delay, easing: 'cubic-bezier(0.45, 0, 0.2, 1)', fill: 'both'}).finished.then(() => clone.remove(), () => clone.remove());
            };
            make(0.16, 4, 70); make(0.32, 2, 35);
            make(1, 0, 0).then(() => { if (++landed === flights.length) land(); });
        });
        function land() {
            slotNode.style.opacity = '';
            const target = open ? panel : orb;
            target.animate([{transform: 'scale(1)'}, {transform: 'scale(1.14, 0.9)', offset: 0.3}, {transform: 'scale(0.96, 1.04)', offset: 0.65}, {transform: 'scale(1)'}], {duration: 460, easing: 'ease-out'});
            slotNode.animate([{filter: 'brightness(1.6)'}, {filter: 'brightness(1)'}], {duration: 420, easing: 'ease-out'});
        }
    }

    // ---- 轮询 -------------------------------------------------------------

    function delay() { return document.hidden ? 15000 : open ? 1000 : state.active || placeholders.size ? 3000 : 20000; }
    async function poll() {
        clearTimeout(timer);
        if (stopped) return;
        if (inFlight) { requested = true; return; }
        inFlight = true; requested = false;
        try {
            const response = await fetch(root.dataset.statusUrl, {headers: etag ? {'If-None-Match': etag} : {}, cache: 'no-cache'});
            if (response.status !== 304) {
                if (!response.ok) throw new Error('任务状态暂不可用');
                state = await response.json(); etag = response.headers.get('ETag');
                try { sessionStorage.setItem('tasks-state', JSON.stringify(state)); } catch {}
                stale = false; ready = true; observe(); render();
                if (expandedId && open) loadDetail();
                window.dispatchEvent(new CustomEvent('tasks:update', {detail: state}));
            } else if (stale) { stale = false; render(); }
        } catch { stale = true; render(); }
        finally { inFlight = false; ready = true; if (!stopped) timer = setTimeout(poll, requested ? 0 : delay()); }
    }
    function refresh() { clearTimeout(timer); poll(); }
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); clearInterval(clock); });
    window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; clock = setInterval(updateClocks, 1000); refresh(); } });
    let clock = setInterval(updateClocks, 1000);

    window.TaskCenter = {
        refresh, active, caption,
        has: key => local.has(key),
        clear: key => { if (local.delete(key)) render(); },
        open(id) { setOpen(true); if (id && expandedId !== id) toggleTask(id); },
        // 确认关联后：先放占位任务让圆钮立即出现，再让海报从列表飞进来。
        // groups: [{id, name, files, poster}]；sources: [{rect, src}]
        arrive(groups, sources = []) {
            for (const group of groups) {
                if (!state.tasks.some(task => task.id === group.id)) placeholders.set(group.id, {id: group.id, kind: 'import', name: group.name, state: 'queued', phase: 'queued', done: 0, total: 0, files: group.files, placeholder: true});
                if (group.poster && !posters[group.id]) posters[group.id] = group.poster;
                delete dismissed[group.id];
                seen[group.id] = 'queued';
            }
            save('tasks-posters', posters); save('tasks-seen', seen); save('tasks-dismissed', dismissed);
            render();
            fly(sources);
            if (groups.length) setTimeout(() => moment(groups.length > 1 ? `${groups[0].name} 等 ${groups.length} 部` : groups[0].name, '已加入关联'), reduced() ? 0 : 900);
            refresh();
            setTimeout(() => { if (placeholders.size) { placeholders.clear(); render(); } }, 30000);
        },
        // 成功的结果：岛上正显示这项进度时原地变成“完成”再滑出，否则只弹提示；失败或需处理的结果留着，带上重试等操作。
        result(key, spec) {
            if (spec.tone !== 'danger' && spec.tone !== 'warning') {
                const previous = local.get(key);
                if (previous && active(previous) && !root.hidden) {
                    local.set(key, {...previous, name: spec.text, state: 'complete', phase: 'complete'});
                    clearTimeout(leaving.get(key));
                    leaving.set(key, setTimeout(() => leave(key, () => local.delete(key)), 2400));
                    moment(spec.text, '完成', 'success');
                } else {
                    local.delete(key);
                    window.showAppToast?.(spec.text, spec.tone || 'success');
                }
                render();
                return key;
            }
            local.set(key, {id: key, kind: 'local', name: spec.text, state: spec.tone === 'danger' ? 'failed' : 'partial', phase: spec.tone === 'danger' ? 'failed' : 'partial',
                issues: spec.detail ? [{reason: spec.detail}] : [], localAction: spec.action?.run, actionLabel: spec.action?.label});
            delete dismissed[key];
            render();
            moment(spec.text, '需处理', spec.tone);
            return key;
        },
        progress(key, spec) {
            local.set(key, {id: key, kind: 'local', name: spec.text, state: 'running', phase: '', done: spec.done || 0, total: spec.total || 0, detail: spec.detail});
            seen[key] = 'running';
            render();
            return key;
        },
    };
    // 先用上一页缓存的状态立即画出来（不播动画、不判定结束），再去取最新状态。
    render();
    refresh();
})();
