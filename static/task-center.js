// One lightweight observer for the whole page; provider work stays on the server.
(() => {
    const root = document.querySelector('[data-task-center]');
    if (!root) return;
    const list = root.querySelector('[data-task-list]');
    const content = root.querySelector('.collapse');
    const sidebar = root.closest('.offcanvas');
    const indicator = document.querySelector('[data-task-indicator]');
    const toggle = root.querySelector('[data-bs-toggle]');
    const clearButton = root.querySelector('[data-task-clear]');
    const dialog = document.querySelector('[data-task-dialog]');
    const drawer = bootstrap.Offcanvas.getOrCreateInstance(dialog);
    const dialogTitle = dialog.querySelector('[data-task-dialog-title]');
    const dialogStatus = dialog.querySelector('[data-task-dialog-status]');
    const dialogProgress = dialog.querySelector('[data-task-dialog-progress]');
    const dialogBody = dialog.querySelector('[data-task-dialog-body]');
    const dialogActions = dialog.querySelector('[data-task-dialog-actions]');
    const dialogAction = dialog.querySelector('[data-task-dialog-action]');
    const dialogError = dialog.querySelector('[data-task-dialog-error]');
    const dialogClose = dialog.querySelector('[data-bs-dismiss]');
    const dialogPill = dialog.querySelector('[data-task-pill]');
    const dialogMeta = dialog.querySelector('[data-task-meta]');
    const dialogPoster = dialog.querySelector('[data-task-poster]');
    const openTitle = dialog.querySelector('[data-task-open-title]');
    const details = new Map(), detailEtag = {};
    let detailLoading = false, detailMissing = false;
    const rows = new Map(), local = new Map(), pending = new Set();
    const active = task => ['running', 'waiting', 'queued'].includes(task.state);
    const problem = task => ['failed', 'partial'].includes(task.state);
    const read = key => { try { const value=JSON.parse(sessionStorage.getItem(key) || '{}');return value && typeof value==='object' && !Array.isArray(value) ? value : {}; } catch { return {}; } };
    const save = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch {} };
    const seen = read('tasks-seen'), dismissed = read('tasks-dismissed');
    let round = read('tasks-round'), state = {tasks: [], services: [], active: false};
    let etag = null, timer = null, inFlight = false, requested = false, stopped = false;
    let selectedId = null, detailSignature = null, returnToSidebar = false;
    // 与任务详情的步骤名一致（viewmodels/tasks.py）
    const labels = {metadata:'核对资料', probe:'读取参数', write:'写入媒体库', image:'补海报', rating:'补评分', derive:'生成预览',
        enrich:'补齐资料', retry:'等待重试', queued:'排队', scan:'扫描', recognizing:'识别', complete:'完成', failed:'失败', partial:'需处理', cancelled:'已停止'};
    const el = (tag, className='', text='') => {
        const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
    };
    const fingerprint = task => JSON.stringify([task.state, task.updated_at, task.done, task.total, task.issues]);
    const icon = name => window.appIcon(name);

    function countdown(until) {
        const time = el('time', 'task-countdown'); time.dataset.until = until; return time;
    }

    function updateClocks() {
        if (document.hidden) return;
        document.querySelectorAll('[data-task-center] [data-until], [data-task-dialog] [data-until]').forEach(node => {
            const seconds = Math.max(0, Math.ceil(Number(node.dataset.until) - Date.now() / 1000));
            node.textContent = seconds > 3600 ? `${Math.ceil(seconds / 3600)}h` : seconds > 90 ? `${Math.ceil(seconds / 60)}m` : `${seconds}s`;
            node.title = seconds ? `约 ${seconds} 秒后可重试` : '等待下一次状态更新';
        });
    }

    async function action(task, type) {
        if (pending.has(task.id)) return;
        pending.add(task.id);
        dialogError.hidden = true;
        render();
        try {
            if (task.localAction) await task.localAction();
            else {
                const url = task.kind === 'scan' ? root.dataset.scanUrl + '?refresh=1' : task.kind === 'rating' ? root.dataset.ratingUrl : root.dataset.jobsUrl;
                const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                    body:JSON.stringify(task.kind === 'import' ? {action:type, ids:type === 'cancel' ? task.cancel_ids : task.retry_ids} : {})});
                if (!response.ok) { const data = await response.json(); throw new Error(data.error || '操作失败，请重试'); }
            }
            delete dismissed[task.id];
            if (task.kind === 'import' && type === 'retry') {
                round.ids=[...new Set([...(round.ids||[]),task.id])];round.active=true;save('tasks-round',round);
            }
            window.dispatchEvent(new CustomEvent('tasks:action', {detail:{kind:task.kind, action:type}}));
            refresh();
        } catch (error) {
            if (selectedId === task.id) { dialogError.textContent = error.message; dialogError.hidden = false; }
            else window.showAppToast?.(error.message, 'danger');
        }
        finally {
            pending.delete(task.id);
            render();
            if (!stopped && selectedId === task.id && dialog.classList.contains('show') && [document.body, dialog, dialogAction].includes(document.activeElement)) {
                (dialogActions.hidden ? dialogClose : dialogAction).focus({preventScroll:true});
            }
        }
    }

    const allTasks = () => [...state.tasks, ...local.values()];
    const selectedTask = () => allTasks().find(task => task.id === selectedId);
    const actionType = task => task.localAction ? 'retry' : task.cancel_ids?.length ? 'cancel' :
        task.retry_ids?.length || problem(task) && ['scan','rating'].includes(task.kind) ? 'retry' : null;
    const phaseLabel = task => labels[task.phase] || task.phase || '进行中';
    const statusLabel = task => task.state === 'waiting' ? '待重试' : task.state === 'queued' ? '排队' : labels[task.state] || '进行中';
    const countLabel = task => task.total > 0 ? `${task.done}/${task.total}` : '';

    function caption(task, compact = false) {
        if (task.state !== 'running') return statusLabel(task);
        if (compact) return task.kind === 'import' || task.phase === 'recognizing' ? phaseLabel(task) : countLabel(task) || '进行中';
        return [phaseLabel(task), countLabel(task)].filter(Boolean).join(' ');
    }

    function progress(task, track = el('span')) {
        const known = task.total > 0;
        const running = task.state === 'running';
        track.className = `task-progress${running && !known ? ' is-indeterminate' : ''}`;
        const percent = task.state === 'complete' ? 100 : known ? Math.round(Math.max(0, Math.min(1, task.done / task.total)) * 100) : 0;
        track.style.setProperty('--progress', running && !known ? '25%' : `${percent}%`);
        track.setAttribute('role', 'progressbar');
        track.setAttribute('aria-label', task.kind === 'import' && running ? `${task.name}：${phaseLabel(task)}阶段进度` : task.name);
        track.setAttribute('aria-valuetext', caption(task));
        track.removeAttribute('aria-valuenow');
        if (known || task.state === 'complete') track.setAttribute('aria-valuenow', percent);
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
        return track;
    }

    function renderTask(task) {
        let row = rows.get(task.id);
        if (!row) {
            const node = el('div', 'task-entry'); node.dataset.taskId = task.id;
            const open = el('button', 'task-open'); open.type = 'button';
            open.setAttribute('aria-haspopup', 'dialog'); open.setAttribute('aria-controls', dialog.id);
            const status = el('span', 'task-state'), name = el('span', 'task-name'), track = el('span'), meta = el('span', 'task-meta');
            status.setAttribute('aria-hidden', 'true');
            open.append(status, name, track, meta);
            open.addEventListener('click', () => showDetails(task.id));
            const clear = el('button', 'task-clear-one', '清除'); clear.type = 'button'; clear.hidden = true;
            clear.addEventListener('click', () => {
                const current = allTasks().find(item => item.id === task.id);
                if (current?.state !== 'complete') return;
                const next = node.nextElementSibling?.querySelector('.task-open') || node.previousElementSibling?.querySelector('.task-open') || toggle;
                dismissed[task.id] = fingerprint(current);
                save('tasks-dismissed', Object.fromEntries(Object.entries(dismissed).slice(-500)));
                render(); next.focus({preventScroll:true});
            });
            node.append(open, clear);
            row = {node, open, status, name, track, meta, clear}; rows.set(task.id, row);
        }
        row.node.classList.toggle('is-problem', problem(task));
        row.node.classList.toggle('is-failed', task.state === 'failed');
        const symbol = task.state === 'running' ? 'loader-circle' : task.state === 'complete' ? 'circle-check' :
            task.state === 'failed' ? 'circle-alert' : task.state === 'partial' ? 'triangle-alert' : task.state === 'cancelled' ? 'ban' : 'hourglass';
        if (row.status.dataset.symbol !== symbol) {
            row.status.replaceChildren(icon(symbol)); row.status.dataset.symbol = symbol;
        }
        row.status.classList.toggle('is-running', task.state === 'running');
        row.open.setAttribute('aria-label', `${task.name}，${caption(task)}，查看详情`);
        row.open.title = `${task.name} · ${caption(task)}`;
        row.name.textContent = task.name;
        row.meta.textContent = task.state === 'complete' ? '' : caption(task, true);
        row.clear.hidden = task.state !== 'complete';
        row.clear.setAttribute('aria-label', `清除任务：${task.name}`);
        progress(task, row.track);
        return row.node;
    }

    const STATE_ICON = {done:'circle-check', active:'loader-circle', pending:'clock', failed:'circle-alert', skipped:'ban'};
    const PILL = {running:'busy', waiting:'warn', queued:'warn', complete:'ok', partial:'warn', failed:'fail', cancelled:'off'};

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

    // 关联四步：一条分段进度，标签与分段左对齐
    function renderSteps(steps) {
        const list = el('ol', 'task-steps');
        list.setAttribute('aria-label', '关联进度');
        for (const step of steps) {
            const item = el('li', 'task-step'); item.dataset.state = step.state;
            const label = el('span', 'task-step-label', step.label);
            if (step.count) label.append(el('span', 'task-step-count', step.count));
            item.append(el('span', 'task-step-bar'), label);
            item.setAttribute('aria-label', `${step.label}：${{done:'已完成', active:'进行中', failed:'失败', pending:'未开始'}[step.state] || ''}${step.count ? ` ${step.count}` : ''}`);
            list.append(item);
        }
        return list;
    }

    function renderRows(items, render) {
        const list = el('ul', 'ui-card task-rows');
        for (const item of items) list.append(render(item));
        return list;
    }

    function renderImport(task, detail) {
        dialogBody.append(renderSteps(detail.steps));
        const waits = Object.entries(task?.waits || {});
        if (task?.retry_at && active(task) || waits.length) {
            const note = el('p', 'task-note');
            note.append(icon('hourglass'));
            const text = el('span', '', waits.map(([service, wait]) => `${state.services.find(item => item.service === service)?.name || service} · ${wait.reason}`).join('；') || '等待重试');
            note.append(text);
            const until = task.retry_at || waits[0]?.[1].until;
            if (until) note.append(countdown(until));
            dialogBody.append(note);
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
        dialogBody.append(files);
        if (detail.issues.length) {
            const issues = section('需要处理');
            issues.append(renderRows(detail.issues, issue => {
                const row = el('li', 'task-row'); row.dataset.state = 'failed';
                const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-hint', issue));
                row.append(stateIcon('failed'), text); return row;
            }));
            dialogBody.append(issues);
        }
        const enrich = section('之后补齐', '', '海报、评分与预览图在关联完成后继续获取，不影响播放。');
        enrich.append(renderRows(detail.enrich, item => {
            const row = el('li', 'task-row is-compact'); row.dataset.state = item.state;
            const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-title', item.label));
            row.append(stateIcon(item.state), text, el('span', 'task-row-meta', item.text));
            return row;
        }));
        dialogBody.append(enrich);
    }

    function renderGeneric(task) {
        if (task.state === 'running' || task.state === 'complete') dialogProgress.append(progress(task));
        const facts = [];
        if (task.state !== 'running' && task.total > 0) facts.push(['已处理', countLabel(task)]);
        if (task.current?.length) facts.push(['正在处理', task.current.join('、') + (task.current_more ? ` 等 ${task.current.length + task.current_more} 项` : '')]);
        if (task.detail) facts.push(['当前详情', task.detail]);
        if (task.attempts > 1) facts.push(['尝试次数', String(task.attempts)]);
        if (facts.length) dialogBody.append(renderRows(facts, ([label, value]) => {
            const row = el('li', 'task-row is-compact');
            const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-hint', label));
            row.append(text, el('span', 'task-row-meta text-break', value)); return row;
        }));
        for (const [service, wait] of Object.entries(task.waits || {})) {
            const name = state.services.find(item => item.service === service)?.name || service;
            const line = el('p', 'task-note', `${name} · ${wait.reason} `);
            if (wait.until) line.append(countdown(wait.until));
            dialogBody.append(line);
        }
        const issues = (task.issues || []).filter(issue => issue.reason);
        if (issues.length) {
            const block = section('需要处理');
            block.append(renderRows(issues, issue => {
                const row = el('li', 'task-row'); row.dataset.state = 'failed';
                const text = el('div', 'task-row-text'); text.append(el('p', 'task-row-hint', [issue.label, issue.service, issue.reason].filter(Boolean).join(' · ')));
                row.append(stateIcon('failed'), text); return row;
            }));
            dialogBody.append(block);
        }
    }

    function renderPoster(detail) {
        const image = detail?.poster;
        if (dialogPoster.dataset.src === (image || '')) return;
        dialogPoster.dataset.src = image || '';
        dialogPoster.replaceChildren(icon(detail?.media_type === 'series' ? 'tv' : 'film'));
        if (image) {
            const img = el('img'); img.alt = ''; img.decoding = 'async';
            img.src = dialog.dataset.imageBase + image; img.addEventListener('error', () => img.remove());
            dialogPoster.append(img);
        }
    }

    function renderDetails() {
        if (!selectedId) return;
        const task = selectedTask();
        const detail = details.get(selectedId);
        const signature = JSON.stringify([task, detail, pending.has(selectedId)]);
        if (signature === detailSignature) return;
        detailSignature = signature;
        const actionFocused = document.activeElement === dialogAction;
        dialogBody.replaceChildren(); dialogProgress.replaceChildren();
        if (!task && !detail) {
            dialogStatus.textContent = detailMissing ? '任务已结束或已移除' : '正在读取…';
            dialogPill.dataset.state = 'off';
            dialogActions.hidden = true;
            if (actionFocused) dialogClose.focus({preventScroll:true});
            return;
        }
        dialogTitle.textContent = detail?.name || task.name;
        dialogMeta.textContent = detail?.meta || (task ? {scan:'扫描媒体目录', rating:'刷新评分', local:'本页操作'}[task.kind] || '' : '');
        dialogMeta.hidden = !dialogMeta.textContent;
        dialogStatus.textContent = detail && (!task || !active(task)) ? detail.state_text : caption(task);
        dialogPill.dataset.state = detail && (!task || !active(task)) ? detail.pill : PILL[task.state] || 'off';
        renderPoster(detail);
        dialogPoster.hidden = !(detail || task?.kind === 'import');
        if (detail) renderImport(task, detail); else renderGeneric(task);
        const type = task ? actionType(task) : null;
        dialogAction.hidden = !type;
        dialogAction.replaceChildren(el('span', '', task?.actionLabel || (type === 'cancel' ? '停止关联' : '重试')));
        dialogAction.classList.toggle('media-primary-action', type === 'retry');
        dialogAction.classList.toggle('chip-btn', type === 'cancel');
        dialogAction.classList.toggle('text-danger', type === 'cancel');
        dialogAction.disabled = !!task && pending.has(task.id);
        openTitle.hidden = !detail?.title_url;
        if (detail?.title_url) openTitle.href = detail.title_url;
        dialogActions.hidden = dialogAction.hidden && openTitle.hidden;
        if (actionFocused && dialogAction.hidden) dialogClose.focus({preventScroll:true});
    }

    // 只在抽屉打开、且是导入任务时读取详情（文件列表与探测结果不进侧栏轮询）
    async function loadDetail() {
        const id = selectedId;
        if (!id || !id.startsWith('import:') || detailLoading) return;
        detailLoading = true;
        try {
            const url = `${dialog.dataset.detailUrl}?${new URLSearchParams({id})}`;
            const response = await fetch(url, {headers: detailEtag[id] ? {'If-None-Match': detailEtag[id]} : {}, cache:'no-cache'});
            if (response.status === 404) { details.delete(id); detailMissing = true; }
            else if (response.ok) { details.set(id, await response.json()); detailEtag[id] = response.headers.get('ETag'); detailMissing = false; }
        } catch {}
        finally { detailLoading = false; if (selectedId === id) renderDetails(); }
    }

    function showDetails(id) {
        selectedId = id; detailSignature = null; dialogError.hidden = true; detailMissing = false;
        renderDetails(); updateClocks(); loadDetail();
        returnToSidebar = sidebar.classList.contains('show');
        // Bootstrap's two focus traps must not run at the same time.
        if (returnToSidebar) {
            sidebar.addEventListener('hidden.bs.offcanvas', () => drawer.show(), {once:true});
            bootstrap.Offcanvas.getOrCreateInstance(sidebar).hide();
        } else drawer.show();
    }

    dialogAction.addEventListener('click', () => {
        const task = selectedTask();
        if (task && actionType(task)) action(task, actionType(task));
    });
    dialog.addEventListener('shown.bs.offcanvas', refresh);
    dialog.addEventListener('hidden.bs.offcanvas', () => {
        const id = selectedId;
        selectedId = null; detailSignature = null; details.delete(id);
        if (returnToSidebar) {
            sidebar.addEventListener('shown.bs.offcanvas', () => requestAnimationFrame(() => {
                (rows.get(id)?.open || toggle).focus({preventScroll:true});
            }), {once:true});
            bootstrap.Offcanvas.getOrCreateInstance(sidebar).show();
        }
        returnToSidebar = false;
    });

    function notifications(tasks) {
        const imports = tasks.filter(task=>task.kind==='import');
        const running = imports.filter(active);
        if (running.length) {
            round.ids = [...new Set([...(round.ids || []),...running.map(task=>task.id)])]; round.active=true;
        } else if (round.active) {
            const finished = imports.filter(task=>round.ids?.includes(task.id) || task.jobs?.some(job=>round.jobs?.includes(job.id)));
            if (finished.length) window.showAppToast?.(`关联结束 · ${finished.reduce((n,t)=>n+t.core,0)} 个视频${finished.some(problem) ? ' · 有项目需处理' : ''}`,finished.some(problem)?'warning':'success');
            round={};
        }
        for (const task of tasks) {
            if (task.kind !== 'import' && ['running','waiting','queued'].includes(seen[task.id]) && !active(task)) window.showAppToast?.(`${task.name}${problem(task)?'有项目需处理':'完成'}`,problem(task)?'warning':'success');
            seen[task.id]=task.state;
        }
        save('tasks-round',round); save('tasks-seen',Object.fromEntries(Object.entries(seen).slice(-500)));
    }

    function render() {
        const all = [...state.tasks,...local.values()];
        let reset = false;
        for (const task of all) if (active(task) && dismissed[task.id]) {delete dismissed[task.id];reset=true;}
        if (reset) save('tasks-dismissed',dismissed);
        const tasks = all.filter(task=>active(task) || dismissed[task.id] !== fingerprint(task));
        tasks.sort((a,b)=>(active(a)?0:problem(a)?1:2)-(active(b)?0:problem(b)?1:2));
        const ids = new Set(tasks.map(task=>task.id));
        for (const [id,row] of rows) if (!ids.has(id)) {row.node.remove();rows.delete(id);}
        tasks.forEach((task,index)=>{const node=renderTask(task);if(list.children[index]!==node)list.insertBefore(node,list.children[index]||null);});
        root.querySelector('[data-task-empty]').hidden = !!tasks.length;
        const running = tasks.filter(active).length, problems = tasks.filter(problem).length;
        const summary = root.querySelector('[data-task-summary]');
        summary.textContent = running + problems || tasks.length || '';
        summary.title = [running ? `${running} 进行中` : '',problems ? `${problems} 需处理` : ''].filter(Boolean).join(' · ') || '已完成';
        summary.setAttribute('aria-label', summary.title);
        summary.classList.toggle('is-problem', !!problems);
        clearButton.hidden = !tasks.some(task=>!active(task)&&!problem(task));
        const services = root.querySelector('[data-task-services]'); services.replaceChildren();
        for (const service of state.services) { const row=el('div','task-service'); row.append(icon('hourglass'),el('span','',`${service.name} · ${service.reason}`),countdown(service.until)); services.append(row); }
        indicator.hidden = !running && !problems && !state.services.length;
        indicator.dataset.state = problems || state.services.length ? 'warning' : 'running';
        indicator.title = problems ? `${problems} 项需要处理` : state.services.length ? '部分接口等待恢复' : `${running} 项进行中`;
        renderDetails();
        updateClocks();
    }

    clearButton.addEventListener('click',()=>{
        for (const task of [...state.tasks,...local.values()]) if (!active(task)&&!problem(task)) dismissed[task.id]=fingerprint(task);
        save('tasks-dismissed',Object.fromEntries(Object.entries(dismissed).slice(-500))); render(); toggle.focus();
    });

    function delay() { return document.hidden ? 15000 : !state.active ? 20000 : dialog.classList.contains('show') || sidebar.classList.contains('show') && content.classList.contains('show') ? 1000 : 4000; }
    async function poll() {
        clearTimeout(timer);
        if (stopped) return;
        if (inFlight) {requested=true;return;}
        inFlight=true;requested=false;
        try {
            const response=await fetch(root.dataset.statusUrl,{headers:etag?{'If-None-Match':etag}:{},cache:'no-cache'});
            if (response.status !== 304) {
                if (!response.ok) throw new Error('任务状态暂不可用');
                state=await response.json(); etag=response.headers.get('ETag'); notifications(state.tasks);render();
                if (selectedId && dialog.classList.contains('show')) loadDetail();
                window.dispatchEvent(new CustomEvent('tasks:update',{detail:state}));
            }
            root.removeAttribute('data-stale');
        } catch {root.dataset.stale='';root.querySelector('[data-task-summary]').textContent='连接中断';indicator.hidden=false;indicator.dataset.state='warning';}
        finally {inFlight=false;if(!stopped)timer=setTimeout(poll,requested?0:delay());}
    }
    function refresh() {clearTimeout(timer);poll();}
    sidebar.addEventListener('shown.bs.offcanvas',refresh);
    content.addEventListener('shown.bs.collapse',()=>{refresh();list.firstElementChild?.scrollIntoView({block:'nearest'});});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
    window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);clearInterval(clock);});
    window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;clock=setInterval(updateClocks,1000);refresh();}});
    let clock=setInterval(updateClocks,1000);
    window.TaskCenter = {
        refresh, has:key=>local.has(key), clear:key=>{local.delete(key);render();},
        open:id=>showDetails(id), caption:task=>caption(task), active,
        watchJobs(ids) {round.jobs=[...new Set([...(round.jobs||[]),...ids])];round.active=true;save('tasks-round',round);refresh();},
        result(key,spec) {const previous=local.get(key);local.set(key,{id:key,kind:'local',name:spec.text,state:spec.tone==='danger'?'failed':spec.tone==='warning'?'partial':'complete',phase:'complete',issues:spec.detail?[{reason:spec.detail}]:[],localAction:spec.action?.run,actionLabel:spec.action?.label});if(!previous || previous.name!==spec.text)window.showAppToast?.(spec.text,spec.tone);render();return key;},
        progress(key,spec) {local.set(key,{id:key,kind:'local',name:spec.text,state:'running',phase:'',done:spec.done||0,total:spec.total||0,detail:spec.detail});render();return key;},
    };
    refresh();
})();
