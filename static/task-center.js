// 后台任务：全站一个轻量轮询，任务本身都在服务端运行。
//
// 平时收起：固定在左上角菜单按钮旁的圆钮，所有页面都在同一位置，只在有任务时出现。
//   排队的任务叠成一串圆形海报（最多 3 张），正在处理的在最右、外圈是它的进度环；
//   它完成后向右滑出，后面的依次往右挪一格接上。
//   有事件（提交、完成、失败）时圆钮横向展开成胶囊，几秒后缩回，类似灵动岛的实时活动。
// 唤出：点圆钮，任务在圆钮下方排成一列胶囊卡片（左边海报，右边进度和状态）；点卡片原地展开当前进度：
//   关联分四步（核对资料 → 获取链接 → 读取参数 → 写入媒体库），展开时按需读取逐集详情（/manager/tasks/detail），
//   画成按季分行的小格；出错时才显示错误内容和重试等操作。扫描、评分、IMDb 只显示汇总里有的进度。
// 结束：成功的任务显示“完成”后向右滑出并清除；写入媒体库后就算完成，之后补海报、评分不再占位。
//   失败或需处理的任务留着、圆钮带提醒点，直到重试或忽略。
// 换页：先用上一页缓存的状态立即渲染，圆钮在新页面直接出现在原位。
//
// 对外：TaskCenter.arrive(groups, sources) 关联页确认后调用，海报从列表飞进圆钮；
//       progress / result / clear / has 供 StatusBar 的 task 层使用；open(id) 展开某个任务。
(() => {
    const root = document.querySelector('[data-task-center]');
    if (!root) return;
    const $ = selector => root.querySelector(selector);
    const orb = $('[data-task-orb]'), stack = $('[data-orb-stack]'), orbRing = $('[data-orb-ring]'), orbMore = $('[data-orb-more]');
    const orbTitle = $('[data-orb-title]'), orbCaption = $('[data-orb-caption]');
    const list = $('[data-task-list]');
    const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
    const SPRING = 'cubic-bezier(0.32, 0.72, 0, 1)';
    const STACK = 3;

    const ACTIVE = ['running', 'waiting', 'queued'];
    // 关联写入媒体库后就算完成：之后补海报、评分、预览图在后台继续，不再占着圆钮
    const written = task => task.kind === 'import' && task.files > 0 && task.core >= task.files;
    const active = task => ACTIVE.includes(task.state) && !written(task);
    const problem = task => ['failed', 'partial'].includes(task.state);
    const read = key => { try { const value = JSON.parse(sessionStorage.getItem(key) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; } };
    const save = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(Object.fromEntries(Object.entries(value).slice(-300)))); } catch {} };
    const seen = read('tasks-seen'), dismissed = read('tasks-dismissed'), posters = read('tasks-posters');
    const cached = read('tasks-state');
    let state = Array.isArray(cached.tasks) ? {tasks: cached.tasks, services: cached.services || [], active: !!cached.active} : {tasks: [], services: [], active: false};
    let etag = null, timer = null, inFlight = false, requested = false, stopped = false, stale = false;
    let open = false, expandedId = null, ready = false, liveTimer = null, hiding = null;
    let incoming = new Set();  // 正在飞进来的任务：它们的圆形海报等落地再显示，不播淡入
    const rows = new Map(), stackNodes = new Map(), local = new Map(), placeholders = new Map(), leaving = new Map(), pending = new Set(), errors = new Map();
    const signatures = new Map();
    // 关联任务的逐集详情：只在展开时读取，之后随轮询更新（ETag 不变就不重画）
    const details = new Map(), loadingDetails = new Set(), picked = new Map();

    const labels = {metadata: '核对资料', probe: '读取参数', write: '写入媒体库', retry: '等待重试', queued: '排队', scan: '扫描', recognizing: '识别',
        rating: '刷新评分', 'imdb-download': '下载单集数据', 'imdb-build': '建立单集索引', complete: '完成', failed: '失败', partial: '需处理', cancelled: '已停止'};
    const STEPS = ['核对资料', '获取链接', '读取参数', '写入媒体库'];
    const KIND_ICON = {import: 'film', scan: 'folder', rating: 'star', imdb: 'database', local: 'list-todo'};

    const el = (tag, className = '', text = '') => {
        const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
    };
    const icon = name => window.appIcon(name);
    const fingerprint = task => JSON.stringify([task.state, task.phase, task.detail, task.updated_at, task.done, task.total, task.issues]);
    const serviceName = service => state.services.find(item => item.service === service)?.name || service;
    const posterOf = task => task.poster ? root.dataset.imageBase + task.poster : posters[task.id] || '';
    const countLabel = (done, total) => total > 0 ? `${Math.min(done, total)}/${total}` : '';
    const titleUrl = task => {
        const titleId = task.kind === 'import' ? task.id.split(':')[1] : '';
        return /^tt\d+$/.test(titleId || '') && root.dataset.titleUrl ? root.dataset.titleUrl.replace('__ID__', titleId) : '';
    };

    // 关联四步：当前在第几步（0–3，4 表示已写入），以及这一步完成的比例
    function stepOf(task) {
        const part = (done, total) => total > 0 ? Math.max(0, Math.min(1, done / total)) : 0;
        if (written(task) || ['complete', 'partial'].includes(task.state)) return {index: 4, part: 0, count: ''};
        if (task.phase === 'write') return {index: 3, part: part(task.core, task.files), count: countLabel(task.core, task.files)};
        if (task.phase === 'probe') return {index: 2, part: part(task.done, task.total), count: countLabel(task.done, task.total)};
        if (task.linked > 0) return {index: 1, part: part(task.linked, task.files), count: countLabel(task.linked, task.files)};
        return {index: 0, part: 0, count: ''};
    }

    // 0–1；未知进度返回 null
    function ratio(task) {
        if (!active(task)) return 1;
        if (task.state !== 'running') return null;
        if (task.kind === 'import') { const step = stepOf(task); return Math.max(0.04, (step.index + step.part) / STEPS.length); }
        return task.total > 0 ? Math.max(0, Math.min(1, task.done / task.total)) : null;
    }

    function caption(task) {
        if (task.state === 'cancelled') return '已停止';
        if (leaving.has(task.id) || written(task) && !problem(task) || task.state === 'complete') return task.kind === 'import' ? (task.files > 0 ? `已入库 ${task.files} 个视频` : '关联完成') : '完成';
        if (task.state === 'waiting') return '等待重试';
        if (task.state === 'queued') return '排队中';
        if (task.state === 'failed') return task.kind === 'import' ? '关联失败' : '失败';
        if (task.state === 'partial') return '需处理';
        if (task.kind === 'import') { const step = stepOf(task); return [STEPS[step.index], step.count].filter(Boolean).join(' '); }
        return [labels[task.phase] || task.phase || '进行中', countLabel(task.done, task.total)].filter(Boolean).join(' ');
    }

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
    // 列表顺序：正在结束的、进行中的、排队的在前，需处理的在后
    const rank = task => leaving.has(task.id) ? 0 : task.state === 'running' && active(task) ? 1 : active(task) ? 2 : 3;
    function visible() {
        return all().filter(task => leaving.has(task.id) || (active(task) || problem(task)) && dismissed[task.id] !== fingerprint(task))
            .map((task, index) => [task, index]).sort(([a, i], [b, j]) => rank(a) - rank(b) || i - j).map(([task]) => task);
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
                    moment(task.name, task.state === 'cancelled' ? '已停止' : task.kind === 'import' ? '关联完成' : '完成', task.state === 'cancelled' ? '' : 'success');
                } else { dismissed[task.id] = fingerprint(task); changed = true; }
            }
            if (wasActive && problem(task)) moment(task.name, task.state === 'failed' ? (task.kind === 'import' ? '关联失败' : '失败') : '需处理', task.state === 'failed' ? 'danger' : 'warning');
            if (!active(task) && leaving.has(task.id) && problem(task)) { clearTimeout(leaving.get(task.id)); leaving.delete(task.id); }
            // 记的是界面上的状态：写入后仍在补海报的关联也记成完成，免得每次轮询都再判一次“刚完成”
            seen[task.id] = active(task) || problem(task) ? task.state : 'complete';
        }
        if (changed) save('tasks-dismissed', dismissed);
        save('tasks-seen', seen);
    }

    // 向右滑出：列表打开时是那张卡片（之后收起高度）；收起时是叠放里的那张海报，后面的往右接上。
    function leave(id, commit = () => { const task = all().find(item => item.id === id); if (task) dismiss(task); }) {
        clearTimeout(leaving.get(id));
        const finish = () => { leaving.delete(id); commit(); if (expandedId === id) expandedId = null; render(); };
        const card = rows.get(id)?.node, poster = stackNodes.get(id);
        const target = open ? card : poster;
        if (reduced() || root.hidden || !target?.isConnected) return finish();
        const animation = target.animate([
            {transform: 'translateX(0) scale(1)', opacity: 1, filter: 'blur(0px)'},
            {transform: `translateX(${open ? '3rem' : '1.75rem'}) scale(${open ? 0.98 : 0.8})`, opacity: 0, filter: 'blur(3px)'},
        ], {duration: 360, easing: 'cubic-bezier(0.55, 0, 0.75, 0.3)', fill: 'forwards'});
        animation.finished.then(() => {
            // 收起时：先保留这张透明的海报，让重画时记下其余海报的旧位置，再一起往右平移接上
            if (!open) return finish();
            card.animate([{height: `${card.offsetHeight}px`, marginBottom: '0px'}, {height: '0px', marginBottom: '-0.5rem'}], {duration: 200, easing: 'ease-out', fill: 'forwards'})
                .finished.then(() => { rows.delete(id); card.remove(); finish(); }, finish);
        }, finish);
    }

    // ---- 收起状态：圆钮 -----------------------------------------------------

    function thumb(task, className) {
        const frame = el('span', className);
        frame.append(icon(task ? KIND_ICON[task.kind] || 'list-todo' : 'hourglass'));
        const src = task?.kind === 'import' ? posterOf(task) : '';
        if (src) {
            const img = el('img'); img.alt = ''; img.decoding = 'async'; img.src = src;
            img.addEventListener('error', () => { img.remove(); frame.classList.remove('has-image'); });
            frame.append(img);
            frame.classList.add('has-image');
        }
        return frame;
    }
    const artClass = task => task?.kind === 'import' ? 'island-poster' : 'island-glyph';
    const artKey = task => JSON.stringify(task ? [task.kind, posterOf(task)] : ['services']);

    function ring(task) {
        if (!orbRing.firstChild) orbRing.innerHTML = '<svg viewBox="0 0 36 36"><circle class="ring-track" cx="18" cy="18" r="16"/><circle class="ring-value" cx="18" cy="18" r="16" pathLength="100"/></svg>';
        const value = task ? ratio(task) : null;
        const running = task && active(task);
        orbRing.classList.toggle('is-indeterminate', Boolean(running && value === null && task.state === 'running'));
        orbRing.style.setProperty('--ring', !task ? 0 : running ? Math.round((value ?? 0) * 100) : 100);
        orbRing.dataset.tone = !task ? 'warning' : leaving.has(task.id) ? 'success' : task.state === 'failed' ? 'danger' : problem(task) ? 'warning' : '';
    }

    // 叠放的海报：DOM 顺序从后往前（最右是最前一张）。位置变化用 FLIP 平移过去，新来的从左边淡入。
    function renderStack(tasks) {
        const shown = tasks.length ? tasks.slice(0, STACK) : [null];
        const ids = shown.map(task => task?.id || 'services');
        const before = new Map([...stackNodes].map(([id, node]) => [id, node.getBoundingClientRect().left]));
        for (const [id, node] of stackNodes) if (!ids.includes(id)) { node.remove(); stackNodes.delete(id); }
        const fresh = [];
        [...shown].reverse().forEach((task, index) => {
            const id = task?.id || 'services';
            let node = stackNodes.get(id);
            if (!node) { node = el('span', 'orb-stack-item'); node.dataset.id = id; stackNodes.set(id, node); fresh.push(node); }
            const key = artKey(task);
            if (node.dataset.key !== key) { node.dataset.key = key; node.replaceChildren(thumb(task, artClass(task))); }
            if (stack.children[index] !== node) stack.insertBefore(node, stack.children[index] || null);
        });
        stack.lastElementChild.append(orbRing);
        ring(shown[0]);
        orbMore.textContent = tasks.length > STACK ? `+${tasks.length - STACK}` : '';
        if (!ready || reduced() || orb.hidden) return;
        for (const [id, node] of stackNodes) {
            const from = before.get(id);
            if (from === undefined) continue;
            const delta = from - node.getBoundingClientRect().left;
            if (Math.abs(delta) > 0.5) node.animate([{transform: `translateX(${delta}px)`}, {transform: 'translateX(0)'}], {duration: 420, easing: SPRING});
        }
        fresh.filter(node => !incoming.has(node.dataset.id)).forEach(node => node.animate([{opacity: 0, transform: 'translateX(-0.75rem) scale(0.6)'}, {opacity: 1, transform: 'none'}], {duration: 380, easing: SPRING}));
    }

    function headline(tasks) {
        if (!tasks.length) return ['接口等待恢复', state.services.map(item => item.name).join('、')];
        if (tasks.length === 1) return [tasks[0].name, caption(tasks[0])];
        const running = tasks.filter(active).length, problems = tasks.filter(task => problem(task) && !leaving.has(task.id)).length;
        const parts = [running ? `${running} 进行中` : '', problems ? `${problems} 需处理` : ''].filter(Boolean).join(' · ');
        return [tasks.every(task => task.kind === 'import') ? `关联 ${tasks.length} 部` : `${tasks.length} 项任务`, parts || caption(tasks[0])];
    }

    // 实时活动：圆钮横向展开成胶囊显示这件事，几秒后缩回。
    function moment(title, text, tone = '') {
        if (!ready || open) return;
        clearTimeout(liveTimer);
        orbTitle.textContent = title; orbCaption.textContent = text;
        orb.dataset.moment = tone;
        orb.classList.add('is-live');
        liveTimer = setTimeout(() => { orb.classList.remove('is-live'); delete orb.dataset.moment; liveTimer = null; render(); }, 2800);
    }

    function renderHosts(tasks) {
        const showing = tasks.length > 0 || state.services.length > 0;
        const alert = stale ? 'warning' : tasks.some(task => task.state === 'failed' && !leaving.has(task.id)) ? 'danger' : tasks.some(task => problem(task) && !leaving.has(task.id)) ? 'warning' : '';
        let [title, text] = headline(tasks);
        if (stale) text = '连接中断，正在重试';
        orb.dataset.alert = alert;
        if (!liveTimer) { orbTitle.textContent = title; orbCaption.textContent = text; }
        orb.setAttribute('aria-label', `后台任务：${title}，${text}。点击${open ? '收起' : '查看'}`);
        orb.title = tasks.map(task => `${task.name} · ${caption(task)}`).join('\n');
        if (showing) {
            hiding?.cancel(); hiding = null;
            root.hidden = false;
            if (orb.hidden) {
                orb.hidden = false;
                if (ready && !reduced()) orb.animate([{opacity: 0, transform: 'scale(0.4)'}, {opacity: 1, transform: 'scale(1)'}], {duration: 420, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)'});
            }
            renderStack(tasks);
        } else if (!orb.hidden && !hiding) {
            if (open) setOpen(false, {focus: false});
            const done = () => { hiding = null; orb.hidden = true; root.hidden = true; stack.replaceChildren(); stackNodes.clear(); };
            if (!ready || reduced()) return done();
            hiding = orb.animate([{opacity: 1, transform: 'scale(1)'}, {opacity: 0, transform: 'scale(0.6)'}], {duration: 240, easing: 'ease-in', fill: 'forwards'});
            hiding.finished.then(() => { hiding?.cancel(); done(); }, () => {});
        }
    }

    // ---- 展开状态：胶囊卡片 -------------------------------------------------

    function progressTrack(task, track) {
        const value = ratio(task);
        track.className = `task-progress${value === null ? ' is-indeterminate' : ''}`;
        const percent = Math.round((value ?? 0) * 100);
        track.style.setProperty('--progress', value === null ? '25%' : `${percent}%`);
        track.setAttribute('role', 'progressbar');
        track.setAttribute('aria-label', task.name);
        track.setAttribute('aria-valuetext', caption(task));
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
        if (value === null) track.removeAttribute('aria-valuenow'); else track.setAttribute('aria-valuenow', percent);
        return percent;
    }

    function renderCard(task) {
        let row = rows.get(task.id);
        if (!row) {
            const node = el('li', 'island-card'); node.dataset.taskId = task.id;
            const head = el('button', 'island-card-head'); head.type = 'button';
            const detailId = `island-task-${rows.size}-${Date.now().toString(36)}`;
            head.setAttribute('aria-controls', detailId);
            const text = el('span', 'island-card-text'), top = el('span', 'island-card-top');
            const name = el('span', 'island-card-name'), value = el('span', 'island-card-value'), meta = el('span', 'island-card-caption'), track = el('span');
            top.append(name, value);
            text.append(top, meta, track);
            head.append(el('span'), text);
            head.addEventListener('click', () => toggleTask(task.id));
            const detail = el('div', 'island-card-detail'); detail.id = detailId; detail.hidden = true;
            // 完成时在百分比的位置换成“打开作品页”（链接不能放进按钮里，所以叠在卡片右上角）
            const link = el('a', 'island-card-link', '打开作品页'); link.hidden = true;
            node.append(head, link, detail);
            row = {node, head, name, value, meta, track, link, detail}; rows.set(task.id, row);
        }
        const finishing = leaving.has(task.id) || !active(task) && !problem(task);
        row.node.classList.toggle('is-problem', problem(task) && !finishing);
        row.node.classList.toggle('is-failed', task.state === 'failed');
        row.node.classList.toggle('is-done', finishing);
        const key = artKey(task);
        if (row.head.dataset.key !== key) { row.head.dataset.key = key; row.head.firstChild.replaceWith(thumb(task, artClass(task))); }
        // 完成的任务只剩一行“关联完成”，不再展开
        const expanded = expandedId === task.id && !finishing;
        row.name.textContent = task.name;
        row.meta.textContent = caption(task);
        const percent = progressTrack(task, row.track);
        const running = active(task) && !finishing && task.state === 'running';
        // 排队、等待重试时没有进度可画。展开时也保留这条总进度，卡片头部高度不变，展开时内容不会跳
        row.track.hidden = !running;
        row.value.textContent = running && ratio(task) !== null ? `${percent}%` : '';
        row.head.setAttribute('aria-expanded', String(expanded));
        row.head.setAttribute('aria-label', `${task.name}，${row.meta.textContent}，${expanded ? '收起' : '展开'}详情`);
        const href = finishing && task.state !== 'cancelled' ? titleUrl(task) : '';
        row.link.hidden = !href;
        if (href) { row.link.href = href; row.link.setAttribute('aria-label', `打开作品页：${task.name}`); }
        row.detail.hidden = !expanded;
        if (expanded) renderDetail(task, row.detail);
        return row.node;
    }

    function renderSteps(task) {
        const step = stepOf(task);
        const steps = el('ol', 'island-steps');
        steps.setAttribute('aria-label', '关联进度');
        STEPS.forEach((label, index) => {
            const stepState = index < step.index ? 'done' : index === step.index ? 'active' : 'pending';
            const item = el('li', 'island-step'); item.dataset.state = stepState;
            const bar = el('span', 'island-step-bar');
            if (stepState === 'active') bar.style.setProperty('--part', `${Math.max(6, Math.round(step.part * 100))}%`);
            // “获取链接”“读取参数”按文件计数，标在步骤名下面
            const count = (index === 1 || index === 2) && stepState !== 'pending' && task.files > 1 ?
                (stepState === 'done' ? countLabel(task.files, task.files) : step.count || countLabel(0, task.files)) : '';
            const text = el('span', 'island-step-label', label);
            if (count) text.append(el('span', 'island-step-count', count));
            item.append(bar, text);
            item.setAttribute('aria-label', `${label}：${{done: '已完成', active: '进行中', pending: '未开始'}[stepState]}${count ? ` ${count}` : ''}`);
            steps.append(item);
        });
        return steps;
    }

    const actionType = task => task.localAction ? 'retry' : task.cancel_ids?.length ? 'cancel' :
        task.retry_ids?.length || problem(task) && ['scan', 'rating', 'imdb'].includes(task.kind) ? 'retry' : null;

    // 扫描、评分、IMDb：只用汇总里已有的字段，没有的不编
    function otherLine(task) {
        if (task.kind === 'scan') {
            if (task.phase === 'recognizing') return [task.total ? `已识别 ${countLabel(task.done, task.total)}` : '', task.detail].filter(Boolean).join(' · ');
            return task.total ? `已扫描目录 ${countLabel(task.done, task.total)}` : '正在列出目录';
        }
        if (task.kind === 'rating') return [task.detail ? `正在刷新：${task.detail}` : '', countLabel(task.done, task.total)].filter(Boolean).join(' · ');
        return task.detail || '';
    }

    const FILE_STATE = {done: '完成', active: '进行中', pending: '等待', failed: '失败', skipped: '未写入'};
    const fileLabel = file => [file.position, file.name, file.detail].filter(Boolean).join(' · ');

    // 逐集小格：每个视频一格，按季分行；悬停或点按在下面一行显示文件名和读到的参数，默认那一行是各状态的计数
    function fileGrid(task, files) {
        const wrap = el('div', 'island-files');
        const grid = el('div', 'island-files-grid');
        const seasons = new Map();
        files.forEach((file, index) => {
            const key = file.season ?? '';
            if (!seasons.has(key)) seasons.set(key, []);
            seasons.get(key).push([file, index]);
        });
        for (const [season, items] of seasons) {
            const line = el('div', 'island-files-row');
            line.append(el('span', 'island-files-season', season === '' ? '其他' : `S${season}`));
            const cells = el('span', 'island-files-cells');
            items.forEach(([file, index]) => {
                // 不进 Tab 顺序（一部剧可能有上百格）；屏幕阅读器仍能逐格读到
                const cell = el('button', 'island-file'); cell.type = 'button'; cell.tabIndex = -1;
                cell.dataset.state = file.state; cell.dataset.index = index;
                cell.setAttribute('aria-label', `${fileLabel(file)}（${FILE_STATE[file.state] || ''}）`);
                cell.setAttribute('aria-pressed', String(picked.get(task.id) === index));
                cells.append(cell);
            });
            line.append(cells);
            grid.append(line);
        }
        const counts = Object.entries(FILE_STATE).map(([key, name]) => [name, files.filter(file => file.state === key).length])
            .filter(([, count]) => count).map(([name, count]) => `${name} ${count}`).join(' · ');
        const info = el('p', 'island-files-info text-truncate');
        const show = index => {
            const file = files[index];
            info.textContent = file ? fileLabel(file) : counts;
            info.title = info.textContent;
        };
        show(picked.get(task.id));
        grid.addEventListener('pointerover', event => { const cell = event.target.closest('.island-file'); if (cell) show(Number(cell.dataset.index)); });
        grid.addEventListener('pointerleave', () => show(picked.get(task.id)));
        grid.addEventListener('click', event => {
            const cell = event.target.closest('.island-file');
            if (!cell) return;
            const index = Number(cell.dataset.index);
            if (picked.get(task.id) === index) picked.delete(task.id); else picked.set(task.id, index);
            grid.querySelectorAll('.island-file').forEach(node => node.setAttribute('aria-pressed', String(picked.get(task.id) === Number(node.dataset.index))));
            show(picked.get(task.id));
        });
        wrap.append(grid, info);
        return wrap;
    }

    // 展开内容只放“现在怎么样”：第一行是类型 · 年份 · 视频数；进行中给四步、正在处理的文件和逐集小格，
    // 等待给接口、原因、倒计时和第几次重试，出错按集列原因并配重试 / 忽略。
    function renderDetail(task, target) {
        const info = task.kind === 'import' ? details.get(task.id)?.data : null;
        const signature = JSON.stringify([task, pending.has(task.id), errors.get(task.id), leaving.has(task.id), info, picked.get(task.id)]);
        if (signatures.get(task.id) === signature && target.childElementCount) return;
        signatures.set(task.id, signature);
        const focusedRole = target.contains(document.activeElement) ? document.activeElement.dataset.role : null;
        target.replaceChildren();
        const finishing = leaving.has(task.id) || !active(task) && !problem(task);
        const files = info?.files || [];
        if (info?.meta) target.append(el('p', 'island-meta text-truncate', info.meta));
        if (task.kind === 'import' && task.state === 'running' && active(task)) target.append(renderSteps(task));
        if (active(task)) {
            const current = task.current?.length ? task.current.join('、') + (task.current_more ? ` 等 ${task.current.length + task.current_more} 个` : '') : '';
            const line = info?.current ? `正在读取 ${info.current}` : current ? `正在处理：${current}` :
                task.kind !== 'import' ? otherLine(task) : task.placeholder ? '已提交，等待开始' : '';
            if (line) { const node = el('p', 'text-truncate', line); node.title = line; target.append(node); }
            for (const [service, wait] of Object.entries(task.waits || {})) {
                const note = el('p', 'text-warning-emphasis', `${serviceName(service)} · ${wait.reason}`);
                if (wait.until || task.retry_at) { note.append(' · '); note.append(countdown(wait.until || task.retry_at)); }
                if (task.attempts > 1) note.append(` · 第 ${task.attempts} 次重试`);
                target.append(note);
            }
        }
        // 电影只有一个视频：不画格子，写成一行“文件名 · 参数”（出错时原因写在下面的错误列表里，这里只留文件名）
        if (files.length === 1 && !finishing) {
            const file = files[0];
            const line = el('p', 'text-truncate', ['done', 'active'].includes(file.state) ? fileLabel({...file, position: ''}) : file.name);
            line.title = line.textContent; target.append(line);
        }
        else if (files.length > 1 && !finishing) target.append(fileGrid(task, files));
        if (problem(task) && !finishing) {
            // 多个视频才需要“已入库 10 集，2 集失败”这样的汇总；单个视频看卡片上的“关联失败”就够了
            if (info?.outcome && files.length > 1) target.append(el('p', 'island-outcome', info.outcome));
            const failedFiles = files.filter(file => file.state === 'failed');
            const positions = new Set(failedFiles.map(file => file.position).filter(Boolean));
            const reasons = new Set(failedFiles.map(file => file.detail));
            const issues = [
                ...failedFiles.slice(0, 5).map(file => files.length === 1 ? file.detail : `${file.position || file.name} · ${file.detail}`),
                ...(failedFiles.length > 5 ? [`另有 ${failedFiles.length - 5} 个视频失败`] : []),
                // 汇总里的问题与按集列出的重复时跳过（同一集或同一原因）
                ...(task.issues || []).filter(issue => !positions.has(issue.label) && !reasons.has(issue.reason))
                    .map(issue => [issue.label, issue.service && serviceName(issue.service), issue.reason].filter(Boolean).join(' · ')),
            ].filter(Boolean);
            if (issues.length) { const box = el('ul', 'island-issues'); [...new Set(issues)].forEach(issue => box.append(el('li', '', issue))); target.append(box); }
        }
        if (errors.get(task.id)) { const error = el('p', 'text-danger-emphasis', errors.get(task.id)); error.setAttribute('role', 'alert'); target.append(error); }
        const actions = el('div', 'island-actions');
        if (problem(task) && !finishing) {
            const ignore = el('button', 'btn btn-sm chip-btn', '忽略'); ignore.type = 'button'; ignore.dataset.role = 'ignore';
            ignore.setAttribute('aria-label', `忽略任务：${task.name}`);
            ignore.addEventListener('click', () => leave(task.id));
            actions.append(ignore);
        }
        const type = actionType(task);
        if (type && !finishing && !(type === 'cancel' && !active(task))) {
            const button = el('button', `btn btn-sm ${type === 'cancel' ? 'chip-btn' : 'media-primary-action is-compact'}`, task.actionLabel || (type === 'cancel' ? '停止关联' : '重试'));
            button.type = 'button'; button.dataset.role = 'action'; button.disabled = pending.has(task.id);
            button.addEventListener('click', () => action(task, type));
            actions.append(button);
        }
        if (actions.childElementCount) target.append(actions);
        if (!target.childElementCount) target.append(el('p', '', caption(task)));
        if (focusedRole) (target.querySelector(`[data-role="${focusedRole}"]:not(:disabled)`) || rows.get(task.id)?.head)?.focus({preventScroll: true});
        updateClocks();
    }

    function renderList(tasks) {
        if (!open) return;
        const ids = new Set(tasks.map(task => task.id));
        for (const [id, row] of rows) if (!ids.has(id) && !row.node.getAnimations().length) { row.node.remove(); rows.delete(id); signatures.delete(id); details.delete(id); picked.delete(id); }
        const nodes = [];
        if (state.services.length) {
            const note = list.querySelector('.island-note') || el('li', 'island-note');
            note.replaceChildren(icon('hourglass'), el('span', '', state.services.map(service => `${service.name} · ${service.reason}`).join('；')));
            const until = Math.min(...state.services.map(service => service.until || Infinity));
            if (Number.isFinite(until)) note.append(countdown(until));
            nodes.push(note);
        } else list.querySelector('.island-note')?.remove();
        nodes.push(...tasks.map(renderCard));
        nodes.forEach((node, index) => { if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
        updateClocks();
    }

    // 高度变化（展开 / 收起、详情读到后多出逐集小格）：记下每张卡片原来的高度，重画后从旧高度过渡到新高度
    // （卡片 overflow: hidden），下面的卡片跟着平滑移动。
    function resize(change) {
        const before = new Map([...rows].map(([key, row]) => [key, row.node.offsetHeight]));
        change();
        if (reduced()) return Promise.resolve();
        const animations = [];
        for (const [key, item] of rows) {
            const from = before.get(key), to = item.node.offsetHeight;
            if (from && Math.abs(from - to) > 1) animations.push(item.node.animate([{height: `${from}px`}, {height: `${to}px`}], {duration: 340, easing: SPRING}));
        }
        return Promise.all(animations.map(animation => animation.finished));
    }

    // 展开后展开的内容稍后淡入，动画结束后再把展开的卡片滚进可见范围。
    function toggleTask(id) {
        const finished = resize(() => { expandedId = expandedId === id ? null : id; render(); });
        const row = expandedId && rows.get(expandedId);
        const reveal = () => row?.node.scrollIntoView({block: 'nearest', behavior: reduced() ? 'auto' : 'smooth'});
        if (row && !reduced()) row.detail.animate([{opacity: 0, transform: 'translateY(-0.25rem)'}, {opacity: 1, transform: 'none'}], {duration: 260, delay: 80, easing: 'ease-out', fill: 'backwards'});
        finished.then(reveal, () => {});
        if (expandedId) loadDetail(expandedId);
    }

    // 只给展开着的关联任务读取逐集详情；带 ETag，没变化时服务端回 304。
    async function loadDetail(id) {
        const task = all().find(item => item.id === id);
        if (!task || task.kind !== 'import' || task.placeholder || !root.dataset.detailUrl || loadingDetails.has(id)) return;
        loadingDetails.add(id);
        const cached = details.get(id);
        try {
            const response = await fetch(`${root.dataset.detailUrl}?id=${encodeURIComponent(id)}`, {headers: cached?.etag ? {'If-None-Match': cached.etag} : {}, cache: 'no-cache'});
            if (response.status === 404) details.delete(id);
            else if (response.ok) {
                details.set(id, {data: await response.json(), etag: response.headers.get('ETag')});
                // 第一次读到时多出几行，按高度过渡展开；之后的更新格子数不变，直接重画
                if (open && expandedId === id) cached ? render() : resize(render);
            }
        } catch {} finally { loadingDetails.delete(id); }
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
    }

    // ---- 唤出 / 收起 -------------------------------------------------------
    // 卡片从圆钮下方依次落下（轻微缩放 + 模糊散开），收起时反向收回圆钮。

    function setOpen(value, {focus = true} = {}) {
        if (open === value) return;
        open = value;
        orb.setAttribute('aria-expanded', String(value));
        list.getAnimations({subtree: true}).forEach(animation => animation.cancel());
        if (value) {
            clearTimeout(liveTimer); liveTimer = null; orb.classList.remove('is-live'); delete orb.dataset.moment;
            list.hidden = false;
            render();
            if (!reduced()) [...list.children].forEach((node, index) => node.animate([
                {opacity: 0, transform: 'translateY(-0.75rem) scale(0.94)', filter: 'blur(4px)'},
                {opacity: 1, transform: 'none', filter: 'blur(0px)'},
            ], {duration: 380, delay: Math.min(index, 6) * 40, easing: SPRING, fill: 'backwards'}));
            if (focus) list.querySelector('.island-card-head')?.focus({preventScroll: true});
            refresh();
            return;
        }
        expandedId = null;
        const done = () => {
            if (open) return;
            list.hidden = true;
            list.replaceChildren(); rows.clear(); signatures.clear();
            render();
        };
        if (reduced()) done();
        else {
            const nodes = [...list.children];
            const animations = nodes.map((node, index) => node.animate([
                {opacity: 1, transform: 'none'}, {opacity: 0, transform: 'translateY(-0.5rem) scale(0.96)'},
            ], {duration: 180, delay: Math.min(nodes.length - 1 - index, 4) * 20, easing: 'ease-in', fill: 'forwards'}));
            Promise.all(animations.map(animation => animation.finished)).then(done, done);
        }
        render();
        if (focus && !orb.hidden) orb.focus({preventScroll: true});
    }

    orb.addEventListener('click', () => setOpen(!open));
    document.addEventListener('pointerdown', event => {
        if (open && !list.contains(event.target) && !orb.contains(event.target)) setOpen(false, {focus: false});
    }, true);
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && open) { event.stopPropagation(); setOpen(false); } });
    list.addEventListener('focusout', event => {
        if (open && event.relatedTarget && !list.contains(event.relatedTarget) && !orb.contains(event.relatedTarget)) setOpen(false, {focus: false});
    });

    // ---- 海报飞入 ---------------------------------------------------------
    // 沿一条二次贝塞尔曲线（CSS offset-path）飞向圆钮里它将占的那张圆形海报：先轻微抬起，途中加速并带运动模糊与拉伸，
    // 落点前减速、收成圆形；身后跟两道淡残影。落地时圆钮被“吸”得一鼓再回弹。

    const motionPath = CSS.supports?.('offset-path', 'path("M 0 0 L 1 1")');

    function fly(sources, ids) {
        const flights = sources.slice(0, 6).filter(({rect}) => rect.width && rect.bottom > 0 && rect.top < innerHeight);
        const hidden = new Set();
        const settle = () => { incoming = new Set(); hidden.forEach(node => { node.style.opacity = ''; }); };
        if (reduced() || root.hidden || orb.hidden || !flights.length || !stack.firstElementChild) return settle();
        let landed = 0;
        flights.forEach((source, index) => {
            const {rect} = source;
            // 按作品对上它的任务；没有作品 ID 时按顺序对应
            const id = source.titleId ? ids.find(item => item.startsWith(`import:${source.titleId}:`)) : ids[index];
            // 圆钮里放不下的（排在第 4 个以后）飞向最左边那张
            const own = stackNodes.get(id);
            const node = open ? list : own || stack.firstElementChild;
            if (own && !open) { own.style.opacity = '0'; hidden.add(own); }
            const slot = node.getBoundingClientRect();
            const size = open ? 48 : slot.height - 10;
            const scale = size / rect.height;
            const x0 = rect.left + rect.width / 2, y0 = rect.top + rect.height / 2;
            const x1 = open ? slot.left + 26 : slot.left + slot.width / 2, y1 = open ? slot.top + 34 : slot.top + slot.height / 2;
            // 控制点：先沿原方向离开，再拐向落点，形成一条“被吸过去”的弧线
            const cx = x0 + (x1 - x0) * 0.12, cy = y0 + (y1 - y0) * 0.88 - Math.sign(y1 - y0 || 1) * 24;
            const endRadius = open ? `${14 / scale}px` : '50%';
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
                // 圆形落点要正方形副本；卡片落点保持海报比例
                const width = open ? rect.width : rect.height;
                Object.assign(clone.style, {width: `${width}px`, height: `${rect.height}px`, opacity: String(opacity)});
                if (motionPath) Object.assign(clone.style, {left: '0px', top: '0px', offsetPath: `path("M ${x0} ${y0} Q ${cx} ${cy} ${x1} ${y1}")`, offsetRotate: '0deg', offsetAnchor: 'center'});
                else Object.assign(clone.style, {left: `${x0 - width / 2}px`, top: `${rect.top}px`});
                if (extraBlur) clone.classList.add('is-ghost');
                if (!open) clone.classList.add('is-round');
                document.body.append(clone);
                const steps = extraBlur ? frames.map(frame => ({...frame, filter: frame.filter.replace(/blur\(([\d.]+)px\)/, (_, value) => `blur(${Number(value) + extraBlur}px)`)})) : frames;
                return clone.animate(steps, {duration: 760, delay: index * 80 + delay, easing: 'cubic-bezier(0.45, 0, 0.2, 1)', fill: 'both'}).finished.then(() => clone.remove(), () => clone.remove());
            };
            make(0.16, 4, 70); make(0.32, 2, 35);
            make(1, 0, 0).then(() => { if (++landed === flights.length) land(); });
        });
        function land() {
            settle();
            orb.animate([{transform: 'scale(1)'}, {transform: 'scale(1.12, 0.9)', offset: 0.3}, {transform: 'scale(0.97, 1.03)', offset: 0.65}, {transform: 'scale(1)'}], {duration: 460, easing: 'ease-out'});
            stack.animate([{filter: 'brightness(1.6)'}, {filter: 'brightness(1)'}], {duration: 420, easing: 'ease-out'});
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
                if (open && expandedId) loadDetail(expandedId);
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
        open(id) { setOpen(true, {focus: false}); if (id && expandedId !== id) toggleTask(id); },
        // 确认关联后：先放占位任务让圆钮立即出现，再让海报从列表飞进来。
        // groups: [{id, titleId, name, files, poster}]；sources: [{rect, src, titleId}]
        arrive(groups, sources = []) {
            for (const group of groups) {
                if (!state.tasks.some(task => task.id === group.id)) placeholders.set(group.id, {id: group.id, kind: 'import', name: group.name, state: 'queued', phase: 'queued', done: 0, total: 0, core: 0, files: group.files, placeholder: true});
                if (group.poster && !posters[group.id]) posters[group.id] = group.poster;
                delete dismissed[group.id];
                seen[group.id] = 'queued';
            }
            save('tasks-posters', posters); save('tasks-seen', seen); save('tasks-dismissed', dismissed);
            const ids = groups.map(group => group.id);
            if (sources.length) incoming = new Set(ids);
            render();
            fly(sources, ids);
            if (groups.length) setTimeout(() => moment(groups.length > 1 ? `${groups[0].name} 等 ${groups.length} 部` : groups[0].name, '已加入关联'), reduced() ? 0 : 900);
            refresh();
            setTimeout(() => { if (placeholders.size) { placeholders.clear(); render(); } }, 30000);
        },
        // 成功的结果：圆钮上正显示这项进度时原地变成“完成”再滑出，否则只弹提示；失败或需处理的结果留着，带上重试等操作。
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
