(() => {
    const root = document.getElementById("media-association");
    if (!root) return;
    const workspace = root.closest("[data-association-workspace]") || root;
    const $ = selector => workspace.querySelector(selector);
    const show = (node, visible) => node.classList.toggle("d-none", !visible);
    const el = (tag, classes = "", text = "") => {
        const node = document.createElement(tag);
        node.className = classes;
        node.textContent = text;
        return node;
    };
    const button = (text, action, classes = "btn btn-sm chip-btn") => {
        const node = el("button", classes, text);
        node.type = "button";
        node.addEventListener("click", action);
        return node;
    };
    const icon = (name, classes = '') => window.appIcon(name, classes);
    const spinner = () => { const node = el("span", "spinner-border spinner-border-sm text-body-secondary"); node.setAttribute("aria-hidden", "true"); return node; };
    // 菜单按视口定位，避免被列表或表格的滚动容器裁切。
    const fixedPopper = { popperConfig: config => ({ ...config, strategy: "fixed" }) };
    const rules = window.FilenameRules;
    const picker = document.getElementById("title-picker");
    const browser = document.getElementById("root-browser");
    const panel = $("[data-discovery-panel]"), editor = $("[data-association-editor]");

    let records = [], ignored = [], roots = [], scan = {}, confirming = null, pollTimer = null;
    let tab = null, listScroll = 0, rootsSignature = null;
    let sourcePath = new URL(location.href).searchParams.get("source") || "/";
    let linkedRecords = [], linkedLoading = false, linkedError = "";
    const excluded = new Set(), expanded = new Set();
    let files = [], title = null, task = null, blocks = [], waitingForTitle = false;
    let useGuess = false, busy = false, taskVersion = 0, onlyErrors = false, ruleOpen = false, progress = null;
    let blockDragActive = false;
    const importJobs = new Map(), importErrors = new Map();
    let jobLoading = false;
    let jobTimer = null;
    // 本次打开页面后提交的关联（task_id → 作品名）：用来告诉用户进度在哪看、完成后去哪看
    const submitted = new Map();
    const firstRun = root.hasAttribute("data-first-run");
    const pendingPaths = () => new Set([...importJobs.values()].filter(job => ["queued", "running"].includes(job.state)).flatMap(job => job.paths));
    // 识别规则：季号来源、各文件夹的季号与集号偏移、当前样例。
    let seasonSource = "file", sampleIndex = 0, pendingMarks = null, suggestion = null;
    const adjust = new Map();

    async function json(url, payload) {
        const response = await fetch(url, payload === undefined ? { cache: "no-store" } : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
        let data;
        try { data = await response.json(); } catch { throw new Error("服务未返回有效数据，请稍后重试。"); }
        if (!response.ok && !data.rows) throw new Error(data.error || "请求失败，请重试。");
        return data;
    }
    const feedback = (text, category = "info") => window.showAppToast?.(text, category);

    function updateJobs(jobs, restoring = false) {
        for (const job of jobs) {
            const previous = importJobs.get(job.id);
            if (restoring && !["queued", "running"].includes(job.state)) continue;
            importJobs.set(job.id, job);
            if (restoring && job.task_id) submitted.set(job.task_id, job.name || job.title_id || "");
            if (["queued", "running"].includes(job.state)) job.paths.forEach(path => importErrors.delete(path));
            else if (!previous || ["queued", "running"].includes(previous.state)) {
                if (job.state === "complete" || (job.state === "cancelled" && job.progress.core >= job.total)) {
                    const done = new Set(job.paths);
                    records = records.flatMap(record => {
                        const remaining = record.files.filter(file => !done.has(file.path));
                        return remaining.length ? [{ ...record, files: remaining }] : [];
                    });
                } else job.paths.forEach(path => importErrors.set(path, job.error || (job.state === "cancelled" ? "关联已停止，可重新提交。" : "关联失败，请核对后重试。")));
            }
        }
        renderList();
        if (jobs.some(job => job.state === "complete")) loadLinked();
    }

    // Paths are fetched once; subsequent state changes come from the global summary.
    async function pollJobs(restoring = false) {
        if (jobLoading) return;
        jobLoading = true;
        clearTimeout(jobTimer);
        try { updateJobs((await json(root.dataset.jobsUrl)).jobs, restoring); }
        catch { jobTimer = setTimeout(() => pollJobs(restoring), 5000); }
        finally { jobLoading = false; }
    }

    window.addEventListener('tasks:update', event => {
        const jobs = event.detail.tasks.flatMap(task => task.jobs || []);
        const known = jobs.filter(job => {const previous=importJobs.get(job.id);return previous && (previous.state!==job.state || previous.progress.core!==job.core || previous.error!==job.error);}).map(job => {
            const previous = importJobs.get(job.id);
            return {...previous, state:job.state, error:job.error, progress:{...previous.progress,core:job.core}, result:{imported:job.core,warning:job.warning}};
        });
        if (known.length) updateJobs(known);
        if (jobs.some(job => !importJobs.has(job.id) && ['running','queued'].includes(job.state))) pollJobs(true);
    });
    window.addEventListener('tasks:action', event => { if (event.detail.kind === 'scan') discover(); });

    // 提交前记下海报在屏幕上的位置，提交成功后交给悬浮岛做飞入动画。
    const capture = nodes => nodes.filter(Boolean).map(node => ({
        rect: node.getBoundingClientRect(),
        src: node.querySelector("img")?.currentSrc || "",
        titleId: node.dataset.titleId || "",
    }));

    async function enqueue(selections, sources = []) {
        const data = await json(root.dataset.jobsUrl, { selections });
        updateJobs(data.jobs);
        // 确认后的反馈：任务进入左上角的后台任务圆钮，海报从列表飞进去
        const groups = new Map();
        for (const job of data.jobs) {
            const group = groups.get(job.task_id) || { id: job.task_id, titleId: job.title_id, name: job.name || job.title_id, files: 0,
                poster: sources.find(source => source.titleId === job.title_id && source.src)?.src || "" };
            group.files += job.total;
            groups.set(job.task_id, group);
            submitted.set(job.task_id, group.name);
        }
        renderList();
        window.TaskCenter?.arrive([...groups.values()], sources);
    }

    function progressSpec(total) {
        return { text: "正在提交关联", detail: `${total} 个视频` };
    }

    // 页头的状态切换；“已忽略”不在这里，单独放在左栏底部
    const TABS = [["ready", "可确认"], ["review", "待核对"], ["unknown", "未识别"], ["linked", "已关联"]];
    const scanning = () => ["running", "recognizing"].includes(scan.status);
    const stateOf = item => item.recognition?.status || "unknown";
    const query = () => $("[data-discovery-filter]").value.trim().toLowerCase();
    const noDrives = () => !document.querySelector("[data-drive-summary]");
    const isPassthroughPath = path => {
        if (!path.startsWith("/")) return false;
        const mount = path.split("/")[1];
        const owner = workspace.querySelector(`[data-drive-card="${CSS.escape(mount)}"]`);
        return !owner || owner.dataset.sourceProvider === "*";
    };
    const matchesSource = path => sourcePath === "/" || (sourcePath === "*" ? isPassthroughPath(path)
        : path === sourcePath || path.startsWith(sourcePath + "/"));
    const availableRecords = () => {
        const pending = pendingPaths();
        return records.flatMap(item => {
            const remaining = item.files.filter(file => !pending.has(file.path) && matchesSource(file.path));
            if (!remaining.length) return [];
            const error = remaining.map(file => importErrors.get(file.path)).find(Boolean);
            return [{ ...item, files: remaining, ...(error ? { importError: error, recognition: { ...item.recognition, status: "review" } } : {}) }];
        });
    };
    const visibleRecords = () => availableRecords().filter(item => `${item.name} ${item.path} ${item.recognition?.title?.name || ""}`.toLowerCase().includes(query()));
    const kindLabel = type => type === "tv" ? "剧集" : "电影";

    function syncSource() {
        // 右侧在看某个网盘的详情时，左栏只高亮那个网盘，来源筛选的选中态先收起
        const filtering = $("[data-drive-details-panel]").classList.contains("d-none");
        // 看“已忽略”时只高亮左栏的已忽略，来源的选中态先收起
        workspace.querySelectorAll("[data-source-path]").forEach(node => {
            const active = filtering && tab !== "ignored" && node.dataset.sourcePath === sourcePath;
            node.setAttribute("aria-pressed", String(active));
        });
        $("[data-source-ignored]").setAttribute("aria-pressed", String(filtering && tab === "ignored"));
        const label = tab === "ignored" ? "已忽略" : sourcePath === "/" ? "全部来源" : sourcePath === "*" ? "默认（透传）" : sourcePath;
        $("[data-source-mobile-label]").textContent = label;
    }

    function backToList() {
        if (currentView() !== "list") { taskVersion++; waitingForTitle = false; showView("list"); }
        const sidebar = document.getElementById("association-sources");
        if (matchMedia("(max-width: 991.98px)").matches) bootstrap.Offcanvas.getInstance(sidebar)?.hide();
    }

    function selectSource(path) {
        if (busy || confirming) { feedback("当前操作完成后再切换来源。", "info"); return; }
        sourcePath = path;
        // 从“已忽略”回到来源：状态重新按有内容的第一项选
        if (tab === "ignored") tab = null;
        const url = new URL(location.href);
        if (path === "/") url.searchParams.delete("source"); else url.searchParams.set("source", path);
        history.replaceState(history.state, "", url);
        backToList(); syncSource(); renderList();
    }

    function openIgnored() {
        if (busy || confirming) { feedback("当前操作完成后再切换。", "info"); return; }
        tab = "ignored";
        backToList(); syncSource(); renderList();
    }

    async function loadLinked() {
        if (linkedLoading) return;
        linkedLoading = true;
        try { linkedRecords = (await json(root.dataset.linkedUrl)).records; linkedError = ""; }
        catch (error) { linkedError = error.message; }
        finally { linkedLoading = false; renderList(); }
    }

    const visibleLinked = () => linkedRecords.flatMap(record => {
        const matching = record.files.filter(file => matchesSource(file.path));
        const search = query();
        if (!matching.length || !`${record.name} ${record.title_id} ${matching.map(file => file.path).join(" ")}`.toLowerCase().includes(search)) return [];
        return [{ ...record, files: matching }];
    });

    function renderSourceFolders() {
        const slots = [...workspace.querySelectorAll("[data-source-folders]")];
        slots.forEach(slot => slot.replaceChildren());
        // 只显示用户保存的关联根目录，不从媒体文件路径推导、展开子目录。
        const paths = new Set(roots.filter(item => !item.auto).map(item => item.path));
        for (const path of [...paths].sort((a, b) => a.localeCompare(b, "zh-CN", { numeric: true }))) {
            const mount = "/" + path.split("/")[1];
            const slot = slots.find(node => node.dataset.sourceFolders === mount)
                || slots.find(node => node.dataset.sourceFolders === "*") || slots.find(node => node.dataset.sourceFolders === "/");
            if (!slot) continue;
            // 目录不带图标，文字与上方网盘名称对齐
            const choice = el("button", "btn source-choice source-linked-folder w-100 text-start");
            choice.type = "button"; choice.dataset.sourcePath = path; choice.dataset.sourceFolder = "true";
            choice.title = path;
            choice.setAttribute("aria-label", `选择关联目录：${path}`);
            const label = slot.dataset.sourceFolders === mount ? path.slice(mount.length + 1) || "根目录" : path;
            choice.append(el("span", "min-w-0", label.replaceAll("/", " / ")));
            slot.append(choice);
        }
        syncSource();
    }

    function openLinked(record) {
        listScroll = window.scrollY;
        $("[data-linked-title]").textContent = record.name;
        $("[data-linked-summary]").textContent = `${record.year || ""} · ${kindLabel(record.media_type)} · ${record.files.length} 个视频`;
        $("[data-linked-detail]").href = record.detail_url;
        const list = $("[data-linked-files]");
        list.replaceChildren();
        for (const file of record.files) {
            const row = el("div", "linked-file");
            const text = el("div", "min-w-0");
            text.append(el("div", "fw-medium text-break", file.name), el("div", "small text-body-secondary text-break mt-1", file.path));
            const target = el("div", "linked-target");
            target.append(el("div", "fw-medium", file.position), el("div", "small text-body-secondary text-break", file.target));
            row.append(text, target); list.append(row);
        }
        showView("linked");
        $("[data-linked-title]").scrollIntoView({ block: "nearest" });
    }

    function linkedRow(record) {
        const row = el("li", "discovery-item");
        const main = button("", () => openLinked(record), "discovery-main btn text-start");
        main.append(poster(record, "film"), textBlock(record.name, [record.year, kindLabel(record.media_type)].filter(Boolean).join(" · "), [], [`${record.files.length} 个视频`, "已关联"], ""));
        row.append(main, button("查看映射", () => openLinked(record), "btn btn-sm chip-btn flex-shrink-0"));
        return row;
    }

    function poster(found, fallback, classes = "discovery-poster") {
        const frame = el("span", classes);
        if (found?.title_id) frame.dataset.titleId = found.title_id;
        frame.append(icon(found ? (found.media_type === "tv" ? "tv" : "film") : fallback));
        // 已入库的作品带本地海报（found.poster）；只有库里还没有的才按 TMDB ID 现查
        if (found?.poster || found?.tmdb_id) {
            const image = el("img");
            image.alt = ""; image.loading = "lazy"; image.decoding = "async";
            image.src = found.poster || `${root.dataset.posterUrl}?${new URLSearchParams({ type: found.media_type, tmdb_id: found.tmdb_id })}`;
            image.addEventListener("error", () => image.remove());
            frame.append(image);
        }
        return frame;
    }

    function aiTag() {
        const tag = el("span", "discovery-tag");
        tag.title = "AI 识别";
        tag.append(icon("sparkles"), document.createTextNode("AI"));
        return tag;
    }

    // ---- 待关联列表 ------------------------------------------------------

    // 识别为同一作品的多个来源目录合并成一行。
    function grouped(items) {
        const groups = new Map();
        for (const item of items) {
            const id = item.recognition?.title?.title_id;
            const key = id ? `t:${id}` : `p:${item.path}`;
            if (!groups.has(key)) groups.set(key, { key, records: [] });
            groups.get(key).records.push(item);
        }
        return [...groups.values()];
    }

    function facts(item) {
        const result = item.recognition || {}, count = item.files.length, list = [];
        if (result.title?.media_type === "tv") { list.push(result.coverage == null || result.coverage >= 1 ? `${result.episodes ?? count} 集` : `${count} 个视频`); }
        else if (!result.title || count > 1) list.push(`${count} 个视频`);
        if (result.versions > 1) list.push(`${result.versions} 个版本`);
        if (item.kind === "update") list.push("更新");
        return list;
    }

    // 待核对 / 未识别的原因，直接写在行内。
    function reasons(item, readyTitles) {
        const result = item.recognition || {}, count = item.files.length, list = [];
        if (item.importError) list.push(item.importError);
        if (result.issues?.length) list.push(...result.issues);
        if (result.ai_error) list.push("AI 暂不可用，保留已有结果");
        else if (result.ai_deferred) list.push("AI 暂缓，可稍后重新识别");
        if (result.not_media && !result.title) list.push("可能不是影视");
        if (!result.title) return list;
        if (result.coverage != null && result.coverage < 1 && (!result.missing_episode_ids || result.unmatched_episodes)) list.push(`${Math.round(result.coverage * count)}/${count} 集对上`);
        if (readyTitles.has(result.title.title_id)) list.push("与可确认项重复");
        if (!list.length && result.alternatives?.length && result.confidence < 0.8) list.push("有同名作品");
        if (!list.length && result.confidence < 0.8) list.push("把握不大");
        return list;
    }

    function textBlock(name, sub, tags, details, source) {
        const text = el("span", "discovery-text");
        const heading = el("span", "discovery-title");
        heading.append(el("span", "text-truncate", name));
        if (sub) heading.append(el("span", "discovery-year", sub));
        const meta = el("span", "discovery-meta");
        for (const tag of tags) meta.append(el("span", "discovery-fact is-warning", tag));
        for (const fact of details) meta.append(el("span", "discovery-fact", fact));
        if (source) meta.append(el("span", "discovery-source text-truncate", source));
        text.append(heading);
        if (meta.childElementCount) text.append(meta);
        return text;
    }

    function recordText(item, tags) {
        const found = item.recognition?.title;
        const name = found ? found.name : item.recognition?.query || item.name;
        const sub = found ? [found.year, kindLabel(found.media_type)].filter(Boolean).join(" · ") : "";
        return textBlock(name, sub, tags, facts(item), found || name !== item.name ? item.name : "");
    }

    function rowMenu(item) {
        const dropdown = el("div", "dropdown");
        const toggle = el("button", "btn icon-btn discovery-icon-btn");
        toggle.type = "button"; toggle.dataset.bsToggle = "dropdown";
        toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-label", `${item.name} 更多操作`);
        toggle.disabled = !!confirming || !!item.busy;
        toggle.append(icon("ellipsis"));
        const menu = el("ul", "dropdown-menu dropdown-menu-end media-menu");
        const entry = (label, symbol, action) => {
            const option = button("", action, "dropdown-item d-flex align-items-center gap-2");
            option.append(icon(symbol), el("span", "", label));
            const line = el("li"); line.append(option); menu.append(line);
        };
        entry(item.recognition?.title ? "更换作品" : "手动关联", "search", () => openTask(item, true));
        entry("重新识别", "refresh-cw", () => recognizeAgain(item));
        const divider = el("li"); divider.append(el("hr", "dropdown-divider")); menu.append(divider);
        entry("忽略", "eye-off", () => setIgnored(item, true));
        dropdown.append(toggle, menu);
        bootstrap.Dropdown.getOrCreateInstance(toggle, fixedPopper);
        return dropdown;
    }

    function actionButton(item) {
        const result = item.recognition || {};
        const skip = result.not_media && !result.title;
        const node = button(skip ? "忽略" : result.title ? "核对" : "关联", () => skip ? setIgnored(item, true) : openTask(item), "btn btn-sm chip-btn discovery-action");
        node.disabled = !!confirming || !!item.busy;
        return node;
    }

    function selectBox(group, label) {
        const check = el("input", "form-check-input discovery-check");
        check.type = "checkbox";
        check.checked = !excluded.has(group.key);
        check.disabled = !!confirming;
        check.setAttribute("aria-label", `选择 ${label}`);
        check.addEventListener("change", () => { if (check.checked) excluded.delete(group.key); else excluded.add(group.key); renderBar(); });
        return check;
    }

    function groupRows(group, state, readyTitles) {
        const [first] = group.records, multi = group.records.length > 1;
        const found = first.recognition?.title;
        const row = el("li", "discovery-item");
        row.dataset.state = state;
        row.dataset.key = group.key;
        if (group.records.some(item => item.busy)) row.classList.add("is-busy");
        const label = found ? found.name : first.name;
        if (state === "ready") row.append(selectBox(group, label));
        const open = expanded.has(group.key);
        const main = button("", () => {
            if (!multi) return openTask(first);
            if (open) expanded.delete(group.key); else expanded.add(group.key);
            renderList();
        }, "discovery-main btn text-start");
        if (multi) main.setAttribute("aria-expanded", String(open));
        main.append(poster(found, first.kind === "folder" ? "folder" : "file-video"));
        if (multi) {
            const videos = group.records.reduce((sum, item) => sum + item.files.length, 0);
            const tags = state === "ready" ? [] : [...new Set(group.records.flatMap(item => reasons(item, readyTitles)))];
            main.append(textBlock(found.name, [found.year, kindLabel(found.media_type)].filter(Boolean).join(" · "), tags, [`${group.records.length} 个来源`, `${videos} 个视频`], ""));
        } else {
            main.append(recordText(first, state === "ready" ? [] : reasons(first, readyTitles)));
        }
        const side = el("div", "discovery-side");
        if (group.records.some(item => item.recognition?.method === "ai")) side.append(aiTag());
        if (group.records.some(item => item.busy) || !first.recognition && scanning()) side.append(spinner());
        if (multi) side.append(icon(open ? "chevron-up" : "chevron-down", "discovery-chevron"), el("span", "discovery-menu-spacer"));
        else {
            if (state === "ready") side.append(icon("chevron-right", "discovery-chevron"));
            else side.append(actionButton(first));
            side.append(rowMenu(first));
        }
        row.append(main, side);
        const rows = [row];
        if (multi && open) {
            for (const item of group.records) {
                const sub = el("li", "discovery-item discovery-subitem");
                const subMain = button("", () => openTask(item), "discovery-main btn text-start");
                subMain.append(textBlock(item.name, "", state === "ready" ? [] : reasons(item, readyTitles), facts(item), ""));
                const subSide = el("div", "discovery-side");
                if (item.busy) subSide.append(spinner());
                subSide.append(state === "ready" ? icon("chevron-right", "discovery-chevron") : actionButton(item), rowMenu(item));
                sub.append(subMain, subSide);
                rows.push(sub);
            }
        }
        return rows;
    }

    // 已忽略的目录：所属网盘的图标（取左栏同一个）、目录名和完整路径
    function sourceLogo(path) {
        const mount = path.startsWith("/") ? path.split("/")[1] : "";
        const owner = mount && workspace.querySelector(`[data-drive-card="${CSS.escape(mount)}"] [data-source-logo]`)
            || (isPassthroughPath(path) && workspace.querySelector('[data-drive-card="*"] [data-source-logo]'));
        const tile = el("span", "discovery-logo");
        tile.append(owner ? owner.firstElementChild.cloneNode(true) : icon("folder"));
        return tile;
    }

    function ignoredRow(entry) {
        const row = el("li", "discovery-item");
        const main = el("span", "discovery-main");
        main.append(sourceLogo(entry.path), textBlock(entry.name, "", [], entry.count ? [`${entry.count} 个视频`] : [], entry.path));
        const side = el("div", "discovery-side");
        side.append(button("恢复", () => setIgnored(entry, false), "btn btn-sm chip-btn discovery-action"));
        row.append(main, side);
        return row;
    }

    function renderTabs(counts) {
        const box = $("[data-discovery-tabs]");
        for (const [key, label] of TABS) {
            let node = box.querySelector(`[data-tab="${key}"]`);
            if (!node) {
                node = button("", () => { tab = key; renderList(); if (key === "linked") loadLinked(); }, "btn btn-sm discovery-tab");
                node.dataset.tab = key; node.setAttribute("role", "tab");
                node.id = `association-tab-${key}`;
                node.setAttribute("aria-controls", "association-results");
                node.append(el("span", "", label), el("span", "discovery-tab-count"));
                box.append(node);
            }
            node.setAttribute("aria-selected", String(tab === key));
            node.classList.toggle("active", tab === key);
            node.tabIndex = tab === key ? 0 : -1;
            node.querySelector('.discovery-tab-count').textContent = counts[key] ? String(counts[key]) : "";
        }
        // 左栏的“已忽略”：没有忽略项时收起（正在看的时候保留）
        const ignoredChoice = $("[data-source-ignored]");
        ignoredChoice.hidden = !ignored.length && tab !== "ignored";
        ignoredChoice.querySelector("[data-ignored-count]").textContent = ignored.length ? String(ignored.length) : "";
    }

    function renderList() {
        const visible = visibleRecords(), search = query();
        const byState = { ready: [], review: [], unknown: [] };
        for (const item of visible) byState[stateOf(item)]?.push(item);
        const hidden = ignored.filter(item => `${item.name} ${item.path}`.toLowerCase().includes(search));
        const linked = visibleLinked();
        const counts = {
            ready: grouped(byState.ready).length, review: byState.review.length, unknown: byState.unknown.length, linked: linked.length, ignored: hidden.length,
        };
        // Keep the user's selected group even when it is empty or scanning.
        if (!tab) tab = TABS.map(([key]) => key).find(key => counts[key]) || "ready";
        renderTabs(counts); syncSource();
        $("[data-discovery-list]").setAttribute("aria-labelledby", `association-tab-${tab}`);

        const readyTitles = new Set(byState.ready.map(item => item.recognition.title.title_id));
        const container = $("[data-discovery-list]");
        container.replaceChildren();
        const list = el("ul", "discovery-list list-unstyled mb-0 squircle-card");
        if (tab === "ignored") hidden.forEach(entry => list.append(ignoredRow(entry)));
        else if (tab === "linked") linked.forEach(record => list.append(linkedRow(record)));
        else {
            const items = tab === "ready" ? byState.ready : byState[tab];
            const groups = tab === "ready" ? grouped(items) : items.map(item => ({ key: `p:${item.path}`, records: [item] }));
            for (const group of groups) list.append(...groupRows(group, tab, readyTitles));
        }
        if (list.childElementCount) container.append(list);

        if (tab === "linked" && linkedError) {
            const error = el("div", "alert alert-warning py-2", `无法更新已关联内容：${linkedError}`);
            error.setAttribute("role", "alert"); container.prepend(error);
        }
        const empty = $("[data-discovery-empty]");
        const loading = tab === "linked" ? linkedLoading : scanning() || !scan.status;
        const message = loading ? (tab === "linked" ? "正在加载已关联内容…" : scan.status === 'recognizing' ? '正在识别，识别好的作品会陆续出现在这里…' : '正在扫描，找到的作品会陆续出现在这里…')
            : noDrives() && !records.length && tab !== 'linked' ? '还没有网盘。先添加网盘，再选择存放影视文件的目录，扫描后就能在这里确认关联。'
            : scan.status === 'error' ? (scan.error || '扫描失败，请重试') : search ? '没有匹配的内容'
            : scan.status === 'cancelled' && !records.length && tab !== 'linked' ? '扫描已停止，还没有找到作品。'
            : pendingPaths().size ? '正在关联…'
            : {ready:'暂无可确认项', review:'暂无待核对项', unknown:'暂无未识别项', linked:'此范围暂无已关联内容', ignored:'暂无已忽略项'}[tab];
        empty.querySelector("[data-discovery-empty-text]").textContent = message;
        const marker = empty.querySelector('[data-discovery-empty-icon]');
        marker.replaceChildren(loading ? spinner() : icon(scan.status === 'error' && !noDrives() ? 'circle-alert' : 'inbox'));
        // 新用户还没有网盘：空状态里直接给“添加网盘”，打开左栏“＋”里的同一个弹窗
        let addDrive = empty.querySelector('[data-empty-add-drive]');
        if (!loading && noDrives() && !records.length && tab !== 'linked') {
            if (!addDrive) {
                addDrive = button('添加网盘', () => document.querySelector('[data-drive-add]')?.click(), 'btn btn-sm media-primary-action is-compact ms-auto flex-shrink-0');
                addDrive.dataset.emptyAddDrive = '';
                empty.append(addDrive);
            }
        } else addDrive?.remove();
        container.setAttribute('aria-busy', String(loading));
        show(empty, !list.childElementCount);
        const missing = $("[data-discovery-missing]");
        missing.querySelector("span").textContent = `${scan.missing || 0} 个源文件失效`;
        missing.title = "已入库的视频在网盘中找不到，前往个性化页查看";
        show(missing, scan.missing > 0);
        renderSlow(); renderProgress(); renderNext(); renderGuide();
        renderBar();
    }

    // 底部状态栏（static/status-bar.js）：列表与编辑器共用一个 "association" 条目，按当前视图切换内容。
    const bar = window.StatusBar;
    const currentView = () => !editor.classList.contains("d-none") ? "editor" : !panel.classList.contains("d-none") ? "list" : "other";

    // 可确认项的勾选：列表上方的就近操作条与底部状态栏共用同一份选择
    function readySelection() {
        const groups = grouped(visibleRecords().filter(item => stateOf(item) === "ready"));
        const picked = groups.filter(group => !excluded.has(group.key));
        const videos = picked.reduce((sum, group) => sum + group.records.reduce((count, item) => count + item.files.length, 0), 0);
        return { groups, picked, text: picked.length ? `已选 ${picked.length} 项 · ${videos} 个视频` : "未选择",
            label: picked.length ? `确认关联 ${picked.length} 项` : "确认关联" };
    }
    function selectAllReady(checked) {
        for (const group of readySelection().groups) { if (checked) excluded.delete(group.key); else excluded.add(group.key); }
        renderList();
    }
    function confirmPicked() {
        const { picked } = readySelection();
        confirm(picked.flatMap(group => group.records), picked.map(group => group.key));
    }

    function renderConfirm() {
        const box = $("[data-discovery-confirm]");
        const selection = readySelection();
        const visible = tab === "ready" && (selection.groups.length > 0 || !!confirming);
        show(box, visible);
        if (!visible) return;
        const all = box.querySelector("[data-confirm-all]"), run = box.querySelector("[data-confirm-run]");
        all.checked = selection.picked.length > 0 && selection.picked.length === selection.groups.length;
        all.indeterminate = selection.picked.length > 0 && selection.picked.length < selection.groups.length;
        all.disabled = !!confirming;
        box.querySelector("[data-confirm-text]").textContent = confirming ? `正在提交 ${confirming} 个视频…` : selection.text;
        run.textContent = selection.label;
        run.disabled = !!confirming || !selection.picked.length;
    }

    // 列表上方的确认条还在视野里时，底部状态栏不再重复一份；滚动到看不见它时才由状态栏接手
    let confirmInView = false;
    new IntersectionObserver(([entry]) => {
        if (confirmInView === entry.isIntersecting) return;
        confirmInView = entry.isIntersecting;
        renderBar();
    }).observe($("[data-discovery-confirm]"));

    function renderBar() {
        renderConfirm();
        if (currentView() !== "list") return;
        if (tab === "ready" && confirmInView && !$("[data-discovery-confirm]").classList.contains("d-none")) {
            bar.clear("association");
            return;
        }
        if (confirming) {
            bar.progress("association", progressSpec(confirming));
            return;
        }
        if (tab === "ready") {
            const selection = readySelection(), { groups, picked } = selection;
            if (groups.length) {
                bar.selection("association", {
                    text: selection.text,
                    checked: picked.length === groups.length,
                    indeterminate: picked.length > 0 && picked.length < groups.length,
                    toggleLabel: "全选可确认项",
                    onToggle: selectAllReady,
                    action: { label: selection.label, disabled: !picked.length, run: confirmPicked },
                });
                return;
            }
        } else if (tab === "unknown") {
            const junk = visibleRecords().filter(item => stateOf(item) === "unknown" && item.recognition?.not_media && !item.recognition?.title);
            if (junk.length) {
                bar.notice("association", { icon: "ban", text: `${junk.length} 项可能不是影视内容`, action: { label: "全部忽略", run: () => ignoreAll(junk) } });
                return;
            }
        }
        bar.clear("association");
    }

    function syncBar() {
        const view = currentView();
        if (view === "list") renderBar();
        else if (view === "editor") renderTaskBar();
        else bar.clear("association");
    }

    // ---- 扫描进度、提交去向、首次指引 --------------------------------------

    // 本页观察到的扫描节奏：用来估算剩余时间、判断“很久没有新进度”。打开页面时扫描已在进行，也从这一刻起算。
    const pace = { phase: null, started: 0, base: 0, value: null, changed: 0 };
    const scanCounts = () => scan.status === "recognizing" ? { done: scan.recognized || 0, total: scan.to_recognize || 0 } : { done: scan.done || 0, total: scan.total || 0 };
    const duration = seconds => seconds >= 3600 ? `${Math.floor(seconds / 3600)} 小时 ${Math.round(seconds % 3600 / 60)} 分` : seconds >= 90 ? `${Math.round(seconds / 60)} 分钟` : `${Math.max(1, Math.round(seconds))} 秒`;

    function trackPace() {
        const now = Date.now();
        if (!scanning()) { pace.phase = null; return; }
        const counts = scanCounts();
        if (pace.phase !== scan.status) Object.assign(pace, { phase: scan.status, started: now, base: counts.done, value: null });
        const value = `${scan.status}:${counts.done}:${counts.total}:${scan.recognized || 0}:${records.length}`;
        if (value !== pace.value) Object.assign(pace, { value, changed: now });
    }

    // 粗略的剩余时间：本页看到的速度 × 剩余数量；看到的进度太少时不估
    function remaining() {
        const { done, total } = scanCounts();
        const seconds = (Date.now() - pace.started) / 1000, gained = done - pace.base;
        if (!total || done >= total || seconds < 8 || gained < 2) return "";
        const left = (total - done) * seconds / gained;
        return left < 60 ? "不到 1 分钟" : `约还需 ${duration(left)}`;
    }

    function renderProgress() {
        const box = $("[data-scan-progress]");
        const running = scanning(), stopped = scan.status === "cancelled";
        trackPace();
        show(box, running || stopped);
        if (!running && !stopped) return;
        const { done, total } = scanCounts();
        const recognizing = scan.status === "recognizing";
        const phase = stopped ? "扫描已停止" : recognizing ? "正在识别作品" : total ? "正在扫描目录" : "正在读取目录列表";
        const marker = box.querySelector("[data-scan-icon]");
        if (marker.dataset.phase !== String(stopped)) { marker.dataset.phase = String(stopped); marker.replaceChildren(stopped ? icon("circle-alert") : spinner()); }
        box.querySelector("[data-scan-phase]").textContent = phase;
        const count = stopped || !total ? "" : recognizing ? `${Math.min(done, total)}/${total} 项` : `${Math.min(done, total)}/${total} 个目录`;
        box.querySelector("[data-scan-count]").textContent = count;
        box.querySelector("[data-scan-time]").textContent = running ? remaining() : "";
        show(box.querySelector("[data-scan-stop]"), running);
        show(box.querySelector("[data-scan-restart]"), stopped);
        const progressBar = box.querySelector("[data-scan-bar]"), fill = progressBar.firstElementChild;
        show(progressBar, running);
        const known = total > 0, percent = known ? Math.round(Math.min(done, total) / total * 100) : 100;
        fill.style.width = `${percent}%`;
        fill.classList.toggle("progress-bar-striped", !known);
        fill.classList.toggle("progress-bar-animated", !known);
        if (known) progressBar.setAttribute("aria-valuenow", String(percent)); else progressBar.removeAttribute("aria-valuenow");
        progressBar.setAttribute("aria-valuetext", [phase, count].filter(Boolean).join(" "));
        const found = records.length;
        const detail = stopped ? (found ? `已找到的 ${found} 项保留在下方，可以先确认关联，之后再重新扫描。` : "还没有找到作品，可以重新扫描。")
            : recognizing ? [scan.ai_pending ? `AI 识别中 ${scan.ai_pending} 项` : "", scan.verifying ? `TMDB 核对中 ${scan.verifying} 项` : "", "识别好的作品会陆续出现在“可确认”里"].filter(Boolean).join(" · ")
            : [found ? `已找到 ${found} 项` : "", scan.to_recognize ? `已识别 ${Math.min(scan.recognized || 0, scan.to_recognize)}/${scan.to_recognize}` : "", "大目录第一次扫描可能需要几分钟"].filter(Boolean).join(" · ");
        box.querySelector("[data-scan-detail]").textContent = detail;
        // 限速或长时间没有新进度：写明在等什么，免得以为断网了
        const wait = box.querySelector("[data-scan-wait]"), services = running ? scan.services || [] : [];
        const now = Date.now() / 1000, quiet = (Date.now() - pace.changed) / 1000;
        let note = "";
        if (services.length) {
            note = services.map(service => {
                const left = service.until ? Math.ceil(service.until - now) : 0;
                return `${service.name}${service.reason ? `：${service.reason}` : "：限速"}${left > 0 ? `，约 ${duration(left)}后继续` : "，稍后自动继续"}`;
            }).join("；") + "。这是服务限速，不是断网，扫描会自动继续。";
        } else if (running && pace.changed && quiet >= 20) {
            note = `网盘响应较慢，已 ${duration(quiet)}没有新进度，扫描仍在进行。`;
        }
        wait.querySelector("span").textContent = note;
        show(wait, !!note);
    }

    // 提交后的去向：关联在后台进行（左上角圆钮），完成后给出“去首页观看”
    function submittedSummary() {
        const jobs = [...importJobs.values()];
        const result = { total: submitted.size, running: 0, queued: 0, done: [], failed: 0 };
        for (const [id, name] of submitted) {
            const own = jobs.filter(job => job.task_id === id);
            if (!own.length || own.some(job => job.state === "running")) result.running++;
            else if (own.some(job => job.state === "queued")) result.queued++;
            else if (own.some(job => job.state === "complete" || job.state === "cancelled" && job.progress?.core >= job.total)) result.done.push(name);
            else result.failed++;
        }
        result.active = result.running + result.queued;
        return result;
    }
    const homeLink = (label, primary) => {
        const link = el("a", primary ? "btn btn-sm media-primary-action is-compact" : "btn btn-sm chip-btn");
        link.href = root.dataset.homeUrl;
        link.append(icon("play"), el("span", "", label));
        return link;
    };
    const progressButton = () => button("查看进度", () => window.TaskCenter?.open());
    const linkedButton = () => button("查看已关联", () => { tab = "linked"; renderList(); loadLinked(); });

    function renderNext() {
        const box = $("[data-association-next]"), summary = submittedSummary();
        // 首次使用时同样的信息写在顶部指引里
        if (firstRun || !summary.total) { show(box, false); return; }
        const actions = box.querySelector("[data-next-actions]");
        let text, mark;
        if (summary.active) {
            const parts = [summary.running ? `${summary.running} 部正在关联` : "", summary.queued ? `${summary.queued} 部排队中` : ""].filter(Boolean).join("，");
            text = `已提交 ${summary.total} 部：${parts}。进度在左上角的圆钮里，完成后可在首页观看。`;
            mark = "busy";
        } else {
            const names = summary.done.slice(0, 2).map(name => `《${name}》`).join("") + (summary.done.length > 2 ? ` 等 ${summary.done.length} 部` : "");
            text = summary.done.length ? `${names}已关联完成${summary.failed ? `，${summary.failed} 部未完成，可在“待核对”里重新提交` : ""}，现在可以去首页观看了。`
                : `${summary.failed} 部关联未完成，可在“待核对”里重新提交。`;
            mark = summary.done.length ? "done" : "problem";
        }
        const textNode = box.querySelector("[data-next-text]");
        if (textNode.textContent !== text) textNode.textContent = text;
        if (box.dataset.tone !== mark) {
            box.dataset.tone = mark;
            box.querySelector("[data-next-icon]").replaceChildren(mark === "busy" ? spinner() : icon(mark === "done" ? "circle-check" : "triangle-alert"));
            if (mark === "busy") actions.replaceChildren(progressButton(), homeLink("去首页", false));
            else {
                const close = button("", () => { submitted.clear(); renderNext(); }, "btn icon-btn discovery-icon-btn");
                close.setAttribute("aria-label", "关闭提示"); close.append(icon("x"));
                actions.replaceChildren(...(mark === "done" ? [homeLink("去首页观看", true), linkedButton()] : []), close);
            }
        }
        show(box, true);
    }

    // 首次指引：四步提示浮在当前要点的按钮或区域旁（Bootstrap Popover），步骤变了就换一个指向。
    // 指向的元素看不见时（手机上左栏收在抽屉里）改指“选择来源”按钮；进入核对、连接详情等视图时先收起。
    const GUIDE = ["root", "scan", "confirm", "watch"];
    const guide = $("[data-association-guide]");
    let guidePop = null, guideTarget = null, guidePlacement = "", guideClosed = false;
    const visibleNode = node => node && node.isConnected && node.getClientRects().length > 0
        && (node.checkVisibility ? node.checkVisibility({ visibilityProperty: true }) : node.offsetParent !== null);
    const firstVisible = (...selectors) => selectors.map(selector => typeof selector === "string" ? document.querySelector(selector) : selector).find(visibleNode) || null;

    function hideGuide() {
        guidePop?.dispose();
        guidePop = null; guideTarget = null;
    }

    // 弹窗打开时提示先收起，免得叠在弹窗上面
    // 弹窗从 show 事件起到 hidden 事件止都算打开（淡入期间还没有 .show 类）
    let modalCount = 0;
    const modalOpen = () => modalCount > 0;

    function placeGuide(target, placement) {
        if (guideClosed || currentView() !== "list" || modalOpen()) { hideGuide(); return; }
        target = visibleNode(target) ? target : firstVisible('[data-bs-target="#association-sources"]');
        if (!target) { hideGuide(); return; }
        if (target === guideTarget && placement === guidePlacement && guidePop) { guidePop.update(); return; }
        hideGuide();
        guideTarget = target; guidePlacement = placement;
        guide.classList.remove("d-none");
        guidePop = new bootstrap.Popover(target, {
            content: guide, html: true, sanitize: false, trigger: "manual", customClass: "guide-popover",
            template: '<div class="popover" role="dialog" aria-label="开始使用"><div class="popover-arrow"></div><div class="popover-body"></div></div>',
            offset: [0, 12], popperConfig: config => ({ ...config, placement }),
        });
        guidePop.show();
    }

    function renderGuide() {
        if (!guide) return;
        const summary = submittedSummary();
        const counts = { ready: 0, other: 0 };
        for (const item of availableRecords()) counts[stateOf(item) === "ready" ? "ready" : "other"]++;
        let step, text, actions = [], target = null, placement = "right-start";
        const chooseRoot = () => button("选择目录", addRoot, "btn btn-sm media-primary-action is-compact");
        const firstDrive = () => firstVisible("[data-drive-summary]:not(.is-disabled) [data-drive-details-open]", "[data-drive-details-open]");
        if (summary.total) {
            step = "watch";
            if (summary.active) {
                text = "正在后台关联：进度在这个圆钮里，可以点开查看或停止。完成后就能在首页看到。"; actions = [progressButton()];
                target = firstVisible("[data-task-orb]"); placement = "bottom-start";
            } else if (summary.done.length) {
                text = "关联完成！去首页看看你的影视库吧。以后添加了新视频，回到这里重新扫描即可。"; actions = [homeLink("去首页观看", true)];
                target = firstVisible("[data-task-orb]", ".site-menu-toggle"); placement = "bottom-start";
            } else {
                step = "confirm"; text = "关联没有完成，失败原因写在“待核对”里，核对后重新提交。";
                if (tab !== "review") actions = [button("查看待核对", () => { tab = "review"; renderList(); }, "btn btn-sm media-primary-action is-compact")];
                target = firstVisible('[data-tab="review"]'); placement = "bottom-end";
            }
        } else if (noDrives()) {
            step = "root"; text = "先添加网盘：点这里登录你的网盘，或添加本机文件夹。";
            actions = [button("添加网盘", () => document.querySelector("[data-drive-add]")?.click(), "btn btn-sm media-primary-action is-compact")];
            target = firstVisible(".source-add-btn");
        } else if (!scan.status) {
            step = "root"; text = "正在读取扫描目录…";
        } else if (!roots.length && !records.length && !scanning()) {
            step = "root"; text = "选择网盘里存放电影、剧集的文件夹，MovieDB 会扫描其中的视频并自动识别。"; actions = [chooseRoot()];
            target = firstDrive();
        } else if (counts.ready) {
            step = "confirm";
            text = `识别好了 ${counts.ready} 项：勾选要加入的作品，再点“确认关联”。只勾选不会加入媒体库。`;
            if (tab !== "ready") {
                actions = [button("查看可确认", () => { tab = "ready"; renderList(); })];
                target = firstVisible('[data-tab="ready"]');
            } else target = firstVisible("[data-confirm-run]", '[data-tab="ready"]');
            placement = "bottom-end";
        } else if (scanning()) {
            step = "scan"; text = "正在扫描和识别。识别好的作品会陆续出现在“可确认”里，不需要等到全部结束。";
            target = firstVisible("[data-scan-progress]"); placement = "bottom-end";
        } else if (counts.other) {
            step = "confirm"; text = "没有能直接确认的作品：到“待核对”“未识别”里点“核对”或“关联”，逐个确认。";
            target = firstVisible(`[data-tab="${tab === "unknown" ? "unknown" : "review"}"]`); placement = "bottom-end";
        } else if (scan.status === "cancelled") {
            step = "scan"; text = "扫描已停止。点“重新扫描”继续。";
            target = firstVisible("[data-scan-restart]"); placement = "bottom-end";
        } else if (scan.status === "error") {
            step = "scan"; text = "扫描没有成功，原因见右侧。检查网盘连接后点这里重新扫描。";
            target = firstVisible("[data-discovery-refresh]");
        } else {
            step = "root"; text = "所选目录里没有找到视频。换一个存放影视文件的目录试试。"; actions = [chooseRoot()];
            target = firstVisible("[data-source-folder]") || firstDrive();
        }
        const current = GUIDE.indexOf(step), finished = step === "watch" && !summary.active && summary.done.length > 0;
        guide.querySelectorAll("[data-guide-step]").forEach(node => {
            const index = GUIDE.indexOf(node.dataset.guideStep);
            node.dataset.state = index < current || finished ? "done" : index === current ? "current" : "pending";
            if (index === current) node.setAttribute("aria-current", "step"); else node.removeAttribute("aria-current");
        });
        const label = `第 ${current + 1} 步 · ${guide.querySelector(`[data-guide-step="${step}"]`).title}`;
        const labelNode = guide.querySelector("[data-guide-label]");
        if (labelNode.textContent !== label) labelNode.textContent = label;
        const textNode = guide.querySelector("[data-guide-text]");
        if (textNode.textContent !== text) textNode.textContent = text;
        const slot = guide.querySelector("[data-guide-action]");
        const signature = actions.map(node => node.textContent).join("|");
        if (slot.dataset.signature !== signature) { slot.dataset.signature = signature; slot.replaceChildren(...actions); }
        placeGuide(target, placement);
    }
    guide?.querySelector("[data-guide-close]").addEventListener("click", () => { guideClosed = true; hideGuide(); });
    document.addEventListener("association:view", () => renderGuide());
    document.addEventListener("show.bs.modal", () => { modalCount++; hideGuide(); });
    document.addEventListener("hidden.bs.modal", () => { modalCount = Math.max(0, modalCount - 1); renderGuide(); });

    async function stopScan() {
        const stop = $("[data-scan-stop]");
        stop.disabled = true;
        try {
            await json(root.dataset.discoveryUrl, { action: "cancel" });
            feedback("已停止扫描，已找到的结果保留在列表里。", "info");
        } catch (error) { feedback(error.message, "danger"); }
        finally { stop.disabled = false; }
        await discover();
        window.TaskCenter?.refresh();
    }

    function renderRoots() {
        const signature = JSON.stringify(roots);
        if (signature === rootsSignature) return;
        rootsSignature = signature;
        workspace.querySelectorAll(".root-chip").forEach(node => node.remove());
        for (const item of roots) {
            const owners = [...workspace.querySelectorAll("[data-source-roots]")].filter(node => node.dataset.sourceRoots === "*" ? isPassthroughPath(item.path)
                : item.path === node.dataset.sourceRoots || item.path.startsWith(node.dataset.sourceRoots + "/"));
            for (const target of owners) {
            const chip = el("div", "root-chip source-root");
            chip.title = item.auto ? `${item.path}（自动）` : item.path;
            const name = button('', () => selectSource(item.path), 'btn source-choice min-w-0 flex-grow-1');
            name.dataset.sourcePath = item.path;
            name.setAttribute('aria-label', `选择扫描目录：${item.path}`);
            name.append(icon(item.auto ? "wand-sparkles" : "folder"), el("span", "text-truncate", item.path.split("/").filter(Boolean).pop() || "/"));
            chip.append(name);
            const browseButton = button("", () => openBrowser(item.path), "btn icon-btn source-root-action");
            browseButton.setAttribute("aria-label", `浏览目录：${item.path}`);
            browseButton.append(icon("folder")); chip.append(browseButton);
            if (!item.auto) {
                const remove = button("", () => updateRoot(item.path, "remove"), "btn icon-btn source-root-action");
                remove.setAttribute("aria-label", `移除 ${item.path}`);
                remove.append(icon("x"));
                chip.append(remove);
            }
            target.append(chip);
            }
        }
        renderSourceFolders();
        syncSource();
    }

    function scanProgress() {
        const refresh = $("[data-discovery-refresh]");
        const running = scanning();
        refresh.disabled = running;
        refresh.classList.toggle("is-scanning", running);
        refresh.setAttribute("aria-busy", String(running));
    }

    let discoveryTag = null;
    const schedulePoll = () => {
        if (scanning()) pollTimer = setTimeout(() => discover(false, false, true), document.hidden ? 15000 : currentView() === 'list' ? 1500 : 5000);
    };

    // polling：定时轮询带上次的 ETag，内容没变（304）时不重绘列表。
    async function discover(start = false, refresh = false, polling = false) {
        clearTimeout(pollTimer);
        if (start) { scan = { ...scan, status: "running", done: 0, total: 0 }; renderList(); scanProgress(); }
        try {
            const url = root.dataset.discoveryUrl + (refresh ? "?refresh=1" : "");
            let data;
            if (start) {
                data = await json(url, {});
                discoveryTag = null;
            } else {
                const response = await fetch(url, { cache: "no-store", headers: polling && discoveryTag ? { "If-None-Match": discoveryTag } : {} });
                if (response.status === 304) { schedulePoll(); return scan; }
                try { data = await response.json(); } catch { throw new Error("服务未返回有效数据，请稍后重试。"); }
                if (!response.ok) throw new Error(data.error || "请求失败，请重试。");
                discoveryTag = response.headers.get("ETag");
            }
            const busyPaths = new Set(records.filter(item => item.busy).map(item => item.path));
            scan = data;
            // A new scan has no roots until the drive responds; retain the last known directories.
            if (data.roots?.length || !scanning() && data.status !== 'error') roots = data.roots || [];
            ignored = data.ignored || [];
            records = data.records.map(item => busyPaths.has(item.path) ? { ...item, busy: true } : item);
            renderRoots(); renderList(); scanProgress();
            schedulePoll();
            if (start) window.TaskCenter?.refresh();
            return data;
        } catch (error) {
            feedback(error.message, "danger");
            scan = { ...scan, status: "error" }; renderList(); scanProgress();
            return null;
        }
    }

    function toItems(item) {
        const positions = item.recognition?.positions || {};
        return item.files.map(file => {
            const [season = "", episode = ""] = positions[file.path] || [];
            return { path: file.path, season_number: season ?? "", episode_number: episode ?? "" };
        });
    }

    async function confirm(list, keys = []) {
        if (confirming || !list.length) return;
        const sources = capture(keys.map(key => $(`[data-discovery-list] .discovery-item[data-key="${CSS.escape(key)}"] .discovery-poster`)));
        // 进度按视频数计算：多来源合并的一行包含多个记录，按视频数才与“已选”提示一致。
        confirming = list.reduce((sum, item) => sum + item.files.length, 0);
        renderList();
        try {
            await enqueue(list.map(item => ({ title_id: item.recognition.title.title_id, source_name: item.name,
                items: toItems(item) })), sources);
        } catch (error) { feedback(error.message, "danger"); }
        finally {
            confirming = null;
            renderList();
        }
    }

    async function recognizeAgain(item) {
        records = records.map(record => record.path === item.path ? { ...record, busy: true } : record);
        renderList();
        try {
            const result = await json(root.dataset.recognizeUrl, { path: item.path });
            records = records.map(record => record.path === item.path ? { ...record, recognition: result, busy: false } : record);
            const aiNote = result.ai_error ? `（AI 未生效：${result.ai_error}）` : result.ai_deferred ? "（AI 限流，暂用规则结果）" : "";
            feedback((result.title ? `识别为 ${result.title.name}` : "仍未识别，可手动关联") + aiNote, result.title && !aiNote ? "success" : "warning");
        } catch (error) {
            feedback(error.message, "danger");
        } finally {
            item.busy = false;
            records = records.map(record => record.path === item.path ? { ...record, busy: false } : record);
            renderList();
        }
    }

    async function setIgnored(item, value, quiet = false) {
        try {
            await json(root.dataset.ignoreUrl, { path: item.path, name: item.name, ignored: value });
        } catch (error) { feedback(error.message, "danger"); return false; }
        if (value) {
            records = records.filter(record => record.path !== item.path);
            ignored = [{ path: item.path, name: item.name, count: item.files.length }, ...ignored];
            if (!quiet) { renderList(); feedback(`已忽略 ${item.name}`, "info"); }
        } else {
            ignored = ignored.filter(entry => entry.path !== item.path);
            discover(true);
        }
        return true;
    }

    async function ignoreAll(list) {
        let count = 0;
        for (const item of list) count += await setIgnored(item, true, true) ? 1 : 0;
        renderList();
        feedback(`已忽略 ${count} 项`, "info");
    }

    const slowFolders = () => (scan.slow_empty || []).filter(item => !ignored.some(entry => entry.path === item.path))
        .sort((a, b) => b.seconds - a.seconds);
    // 没有视频、却扫了很久的顶层目录：扫描结束后在左上角圆钮里提示，点“以后跳过”写进“已忽略”（随时可恢复），
    // 结果也在圆钮里原地显示“完成”。同一批目录只提示一次。
    const SLOW_KEY = "slow-folders";
    let slowSignature = "";
    function renderSlow() {
        const center = window.TaskCenter;
        if (!center) return;
        const slow = scanning() ? [] : slowFolders();
        const signature = slow.map(item => item.path).join("\n");
        if (signature === slowSignature) return;
        slowSignature = signature;
        if (!slow.length) { center.clear(SLOW_KEY); return; }
        const seconds = slow.reduce((sum, item) => sum + item.seconds, 0);
        const names = slow.slice(0, 2).map(item => `“${item.name}”`).join("、") + (slow.length > 2 ? ` 等 ${slow.length} 个目录` : "");
        center.result(SLOW_KEY, {
            text: `${names}没有视频`, tone: "warning",
            detail: `累计扫描 ${seconds >= 60 ? `${Math.round(seconds / 60)} 分钟` : `${seconds} 秒`}。以后扫描可以跳过，跳过的目录放在“已忽略”里，随时可恢复。`,
            action: { label: "以后跳过", run: skipSlow },
        });
    }
    async function skipSlow() {
        const list = slowFolders();
        window.TaskCenter?.progress(SLOW_KEY, { text: `正在跳过 ${list.length} 个目录` });
        let count = 0;
        for (const item of list) count += await setIgnored({ path: item.path, name: item.name, files: [] }, true, true) ? 1 : 0;
        slowSignature = "";
        renderList();
        window.TaskCenter?.result(SLOW_KEY, { text: `以后扫描跳过 ${count} 个目录`, tone: "success" });
    }

    async function updateRoot(path, action) {
        try {
            const data = await json(root.dataset.rootsUrl, { path, action });
            roots = data.roots.map(path => ({path, auto:false}));
            if (action === "remove" && sourcePath === path) selectSource("/");
            renderRoots();
        } catch (error) { feedback(error.message, "danger"); return; }
        bootstrap.Modal.getInstance(browser)?.hide();
        const data = await discover(true);
        if (data?.rescan) feedback("目录已更新，当前扫描结束后会自动重新扫描。", "info");
    }

    async function browse(path) {
        const crumbs = $("[data-root-crumbs]"), folders = $("[data-root-folders]"), choose = $("[data-root-choose]");
        choose.disabled = true;
        const loading = el("li", "root-folders-status");
        loading.append(spinner(), el("span", "ms-2", "读取中…"));
        folders.replaceChildren(loading);
        let data;
        try { data = await json(`${root.dataset.browseUrl}?${new URLSearchParams({ path })}`); }
        catch (error) { folders.replaceChildren(el("li", "root-folders-status", error.message)); return; }
        browser.dataset.path = data.path;
        crumbs.replaceChildren();
        const parts = data.path.split("/").filter(Boolean);
        const crumb = (label, target, current) => {
            const item = el("li", "d-flex align-items-center gap-1");
            if (target !== "/") item.append(icon("chevron-right", "root-crumb-sep"));
            const node = button(label, () => browse(target), "btn btn-link root-crumb p-0");
            if (target === "/") { node.textContent = ""; node.append(icon("network")); node.setAttribute("aria-label", "根目录"); }
            if (current) node.setAttribute("aria-current", "location");
            item.append(node); crumbs.append(item);
        };
        crumb("/", "/", !parts.length);
        parts.forEach((part, index) => crumb(part, `/${parts.slice(0, index + 1).join("/")}`, index === parts.length - 1));
        folders.replaceChildren();
        for (const folder of data.folders) {
            const item = el("li");
            const node = button("", () => browse(folder.path), "root-folder btn text-start");
            node.append(icon("folder"), el("span", "text-truncate flex-grow-1", folder.name), icon("chevron-right", "text-body-secondary"));
            item.append(node); folders.append(item);
        }
        if (!data.folders.length) folders.append(el("li", "root-folders-status", "没有子目录"));
        const added = roots.some(item => !item.auto && item.path === data.path);
        choose.disabled = data.path === "/" || added;
        choose.textContent = added ? "已添加" : "添加此目录";
        $("[data-root-target]").textContent = data.path === "/" ? "进入一个目录后添加" : added ? "这个目录已在扫描列表中" : `将扫描 ${parts[parts.length - 1]} 及其子目录`;
    }

    // 添加扫描目录：从当前来源的挂载开始浏览；看“全部来源”且只有一个网盘时直接进入那个网盘，省一次点击
    const addRoot = () => {
        const mounts = [...workspace.querySelectorAll("[data-drive-card][data-drive-summary]")].map(node => node.dataset.driveCard).filter(mount => mount !== "*");
        openBrowser(sourcePath === "*" ? "/" : sourcePath === "/" && mounts.length === 1 ? `/${mounts[0]}` : sourcePath);
    };

    function openBrowser(path) {
        const sidebar = document.getElementById("association-sources");
        const open = () => { bootstrap.Modal.getOrCreateInstance(browser).show(); browse(path); };
        if (sidebar.classList.contains("show") && matchMedia("(max-width: 991.98px)").matches) {
            sidebar.addEventListener("hidden.bs.offcanvas", open, { once: true });
            bootstrap.Offcanvas.getInstance(sidebar)?.hide();
        } else open();
    }

    // ---- 单项匹配 --------------------------------------------------------

    const folderOf = file => file.path.slice(0, Math.max(0, file.path.length - file.name.length - 1));
    function folderLabel(folder) {
        if (folder === task.path || !folder.startsWith(`${task.path}/`)) return folder.split("/").pop() || folder;
        return folder.slice(task.path.length + 1);
    }
    function folderGroups() {
        const groups = new Map();
        for (const file of files) {
            if (!groups.has(file.folder)) groups.set(file.folder, { key: file.folder, label: folderLabel(file.folder), files: [] });
            groups.get(file.folder).files.push(file);
        }
        return [...groups.values()];
    }
    const fallbackSeason = () => $("[data-default-season]").value;
    const numberOrNull = value => value === "" || value == null || !Number.isFinite(Number(value)) ? null : Number(value);

    // 未经文件夹调整的季集：文件名规则（或识别阶段的推测）+ 季号来源。
    function rawPosition(file) {
        const guess = useGuess ? file.guess : null;
        const folder = file.folderSeason?.season ?? null;
        let season;
        if (seasonSource === "fixed") season = fallbackSeason();
        else if (seasonSource === "folder") season = folder ?? fallbackSeason();
        else season = guess?.[0] ?? file.match?.season ?? folder ?? fallbackSeason();
        return { season, episode: guess?.[1] ?? file.match?.episode ?? "" };
    }

    // 识别阶段已推测出季集时优先采用；用户改动匹配规则后改用规则结果。
    function applyRule(fromRule = true) {
        if (fromRule === true) useGuess = false;
        for (const file of files) {
            file.match = rules.parse(file.name, blocks);
            file.raw = rawPosition(file);
            if (file.manual) continue;
            const change = adjust.get(file.folder) || {};
            const season = numberOrNull(change.season), offset = numberOrNull(change.offset) || 0;
            file.season = season ?? file.raw.season;
            file.episode = file.raw.episode === "" || file.raw.episode == null ? "" : Number(file.raw.episode) + offset;
        }
        renderFiles();
    }

    function useBlocks(next) {
        blocks = next;
        const index = rules.presetIndex(blocks);
        setPreset(index >= 0 ? index : "custom");
        pendingMarks = null;
        renderBlocks(); applyRule();
    }

    function setPreset(value) {
        const control = $("[data-rule-preset]");
        control.dataset.value = String(value);
        control.replaceChildren(document.createTextNode(value === "custom" ? "常用格式" : rules.presets[Number(value)].name), icon("chevron-down"));
        $("[data-rule-presets]").querySelectorAll("button").forEach(node => {
            const active = node.dataset.value === String(value);
            node.classList.toggle("active", active);
            node.setAttribute("aria-pressed", String(active));
        });
    }

    // 规则片段：规则摘要本身就是编辑器。点片段改类型 / 文字 / 位置，拖动排序，末尾“+”添加。
    const PIECE_TEXT = { any: "…", digits: "数字", separator: "分隔符", letters: "文字", space: "空格", season: "季", episode: "集" };
    const PIECE_TYPES = ["season", "episode", "literal", "digits", "letters", "separator", "space", "any"];
    const PIECE_HINT = { any: "任意长度的任意字符", digits: "一段数字（不作季集）", literal: "必须原样出现的文字", separator: "空格、点、下划线或横线", season: "读作季号的数字", episode: "读作集号的数字", letters: "字母或汉字", space: "空白" };
    let patternKey = "";

    const pieceText = block => block.type === "literal" ? (block.value.replace(/ /g, "␣") || "（空）") : PIECE_TEXT[block.type];
    const patternBox = () => $("[data-rule-pattern]");
    const pieceNodes = () => [...patternBox().querySelectorAll(":scope > .rule-piece-wrap")];

    function changeBlocks(next, focusIndex = null, openMenu = false) {
        blocks = next;
        setPreset(rules.presetIndex(blocks) >= 0 ? rules.presetIndex(blocks) : "custom");
        pendingMarks = null;
        renderBlocks(); applyRule();
        if (focusIndex == null) return;
        const piece = patternBox().querySelector(`.rule-piece[data-block="${focusIndex}"]`);
        if (!piece) return;
        piece.focus({ preventScroll: true });
        // 等本次点击冒泡完（Bootstrap 会在 document 上收起其他菜单）再打开新片段的菜单。
        if (openMenu) setTimeout(() => {
            if (!piece.isConnected) return;
            bootstrap.Dropdown.getOrCreateInstance(piece, fixedPopper).show();
            piece.parentElement.querySelector(".rule-piece-literal input")?.focus();
        }, 0);
    }

    function pieceIssue() {
        const count = type => blocks.filter(block => block.type === type).length;
        if (!count("episode")) return "规则里需要一个“集”";
        if (count("episode") > 1) return "只能有一个“集”";
        if (count("season") > 1) return "只能有一个“季”";
        return "";
    }

    function typeOptions(menu, current, pick) {
        const taken = type => (type === "season" || type === "episode") && blocks.some((block, index) => block.type === type && index !== current);
        const head = el("li"); head.append(el("h6", "dropdown-header", current == null ? "添加片段" : "类型"));
        menu.append(head);
        for (const type of PIECE_TYPES) {
            if (current == null && taken(type)) continue;
            const active = current != null && blocks[current].type === type;
            const option = button("", () => pick(type), `dropdown-item rule-piece-option${active ? " active" : ""}`);
            option.disabled = taken(type);
            if (option.disabled) option.title = `已有${rules.labels[type]}`;
            option.setAttribute("aria-pressed", String(active));
            const sample = el("span", `rule-piece-sample${type === "season" || type === "episode" ? ` rule-${type}` : type === "literal" ? " is-literal" : type === "any" ? " is-any" : ""}`, type === "literal" ? "Aa" : PIECE_TEXT[type]);
            const label = el("span", "rule-piece-option-text");
            label.append(el("span", "", rules.labels[type]), el("small", "", PIECE_HINT[type]));
            option.append(sample, label);
            if (active) option.append(icon("check", "ms-auto"));
            const line = el("li"); line.append(option); menu.append(line);
        }
    }

    function pieceMenu(index) {
        const block = blocks[index];
        const menu = el("ul", "dropdown-menu media-menu rule-piece-menu");
        // 顶部一行：位置与删除；固定文字在下面直接改；再往下是类型。
        const tools = el("li", "rule-piece-tools");
        const tool = (label, symbol, action, disabled = false, classes = "") => {
            const node = button("", action, `btn icon-btn rule-piece-tool ${classes}`.trim());
            node.append(icon(symbol));
            node.setAttribute("aria-label", label);
            node.title = label;
            node.disabled = disabled;
            return node;
        };
        const swap = step => () => {
            const next = blocks.slice();
            [next[index], next[index + step]] = [next[index + step], next[index]];
            changeBlocks(next, index + step, true);
        };
        tools.append(
            el("span", "rule-piece-tools-label", `第 ${index + 1} 段`),
            tool("左移", "arrow-left", swap(-1), index === 0),
            tool("右移", "arrow-right", swap(1), index === blocks.length - 1),
            tool("删除", "trash-2", () => changeBlocks(blocks.filter((_, i) => i !== index), Math.min(index, blocks.length - 2)), false, "is-danger"),
        );
        menu.append(tools);
        if (block.type === "literal") {
            const line = el("li", "rule-piece-literal");
            const input = el("input", "form-control form-control-sm");
            input.value = block.value;
            input.maxLength = 64;
            input.placeholder = "要匹配的文字";
            input.setAttribute("aria-label", `第 ${index + 1} 段固定文字`);
            input.addEventListener("input", () => { block.value = input.value; setPreset(rules.presetIndex(blocks) >= 0 ? rules.presetIndex(blocks) : "custom"); applyRule(); });
            input.addEventListener("keydown", event => {
                if (event.key === "Enter") { event.preventDefault(); bootstrap.Dropdown.getInstance(menu.previousElementSibling)?.hide(); }
            });
            line.append(input); menu.append(line);
        }
        const divider = el("li"); divider.append(el("hr", "dropdown-divider")); menu.append(divider);
        typeOptions(menu, index, type => {
            if (type === block.type) return;
            const next = blocks.slice();
            next[index] = { type, value: type === "literal" ? (block.value || "") : "" };
            changeBlocks(next, index, type === "literal");
        });
        return menu;
    }

    function renderPattern(force = false) {
        const box = patternBox();
        const key = JSON.stringify([busy, blocks.map(block => block.type)]);
        // 结构不变时只更新文字：打开的菜单与输入框保持不动。
        if (!force && key === patternKey && box.children.length) {
            pieceNodes().forEach((wrap, index) => {
                const piece = wrap.querySelector(".rule-piece"), block = blocks[index];
                if (piece && block) { piece.textContent = pieceText(block); piece.classList.toggle("is-empty", block.type === "literal" && !block.value); }
            });
        } else {
            patternKey = key;
            box.replaceChildren();
            blocks.forEach((block, index) => {
                const wrap = el("span", "dropdown rule-piece-wrap");
                wrap.dataset.blockIndex = index;
                const kind = block.type === "season" || block.type === "episode" ? ` rule-${block.type}` : block.type === "literal" ? " is-literal" : block.type === "any" ? " is-any" : "";
                const piece = el("button", `rule-piece${kind}${block.type === "literal" && !block.value ? " is-empty" : ""}`, pieceText(block));
                piece.type = "button";
                piece.disabled = busy;
                piece.dataset.block = index;
                piece.dataset.bsToggle = "dropdown";
                piece.setAttribute("aria-expanded", "false");
                piece.setAttribute("aria-label", `第 ${index + 1} 段：${rules.labels[block.type]}${block.type === "literal" ? `“${block.value}”` : ""}，点击修改`);
                piece.setAttribute("aria-describedby", "rule-drag-help");
                piece.setAttribute("aria-keyshortcuts", "Alt+ArrowLeft Alt+ArrowRight");
                piece.title = `${rules.labels[block.type]}${block.type === "literal" ? `：${block.value}` : ""} · 点击修改，拖动排序`;
                piece.addEventListener("pointerenter", () => linkBlock(index, true));
                piece.addEventListener("pointerleave", () => linkBlock(index, false));
                wrap.append(piece, pieceMenu(index));
                bootstrap.Dropdown.getOrCreateInstance(piece, fixedPopper);
                box.append(wrap);
                bindPieceDrag(wrap);
            });
            const addWrap = el("span", "dropdown rule-piece-add-wrap");
            const add = el("button", "rule-piece-add");
            add.type = "button";
            add.disabled = busy || blocks.length >= 16;
            add.dataset.bsToggle = "dropdown";
            add.setAttribute("aria-expanded", "false");
            add.setAttribute("aria-label", "添加片段");
            add.title = blocks.length >= 16 ? "最多 16 段" : "添加片段";
            add.append(icon("plus"));
            const menu = el("ul", "dropdown-menu media-menu rule-piece-menu");
            typeOptions(menu, null, type => changeBlocks([...blocks, { type, value: "" }], blocks.length, type === "literal"));
            addWrap.append(add, menu);
            bootstrap.Dropdown.getOrCreateInstance(add, fixedPopper);
            box.append(addWrap);
            const warn = el("span", "rule-pattern-warn d-none");
            warn.append(icon("triangle-alert"), el("span"));
            box.append(warn, el("span", "rule-pattern-tip", "点片段可修改，按住拖动调整顺序"));
        }
        const issue = pieceIssue(), warn = box.querySelector(".rule-pattern-warn");
        if (warn) { warn.lastChild.textContent = issue; show(warn, !!issue); show(box.querySelector(".rule-pattern-tip"), !issue); }
        box.setAttribute("aria-label", `当前规则：${blocks.map(block => block.type === "literal" ? block.value : rules.labels[block.type]).join(" ")}`);
    }

    function renderBlocks() { renderPattern(true); }

    // 拖动排序：按下后移动超过 5px 才进入拖动，否则仍是普通点击（打开菜单）。
    function bindPieceDrag(wrap) {
        const container = patternBox();
        const piece = wrap.querySelector(".rule-piece");
        let drag = null, press = null;
        const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
        const addSlot = () => container.querySelector(".rule-piece-add-wrap");
        const reorder = reference => {
            if (reference === wrap || wrap.nextSibling === reference) return;
            const nodes = pieceNodes();
            const positions = new Map(nodes.map(node => [node, node.getBoundingClientRect()]));
            container.insertBefore(wrap, reference);
            for (const node of nodes) {
                node.getAnimations().forEach(animation => animation.cancel());
                if (node === wrap || reducedMotion()) continue;
                const before = positions.get(node), after = node.getBoundingClientRect();
                const dx = before.left - after.left, dy = before.top - after.top;
                if (dx || dy) node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }], { duration: 190, easing: "cubic-bezier(.2,.8,.2,1)" });
            }
        };
        const stopListening = () => {
            document.removeEventListener("pointermove", move);
            document.removeEventListener("pointerup", finish);
            document.removeEventListener("pointercancel", finish);
            document.removeEventListener("keydown", cancel);
        };
        const begin = event => {
            const rect = wrap.getBoundingClientRect(), ghost = wrap.cloneNode(true);
            ghost.querySelector(".dropdown-menu")?.remove();
            ghost.classList.add("rule-piece--floating");
            ghost.inert = true;
            ghost.setAttribute("aria-hidden", "true");
            Object.assign(ghost.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
            document.body.append(ghost);
            bootstrap.Dropdown.getInstance(piece)?.hide();
            drag = { ...press, rect, ghost, order: pieceNodes(), block: blocks[Number(wrap.dataset.blockIndex)] };
            blockDragActive = true;
            wrap.classList.add("rule-piece--placeholder");
            container.classList.add("is-dragging");
            move(event);
        };
        const move = event => {
            if (!press || event.pointerId !== press.id) return;
            if (!drag) {
                if (Math.hypot(event.clientX - press.x, event.clientY - press.y) < 5) return;
                begin(event);
                return;
            }
            event.preventDefault();
            drag.ghost.style.transform = `translate(${event.clientX - drag.x}px, ${event.clientY - drag.y - 4}px) scale(1.06)`;
            const target = pieceNodes().find(node => {
                const box = node.getBoundingClientRect();
                return event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top - 4 && event.clientY <= box.bottom + 4;
            });
            if (!target || target === wrap) return;
            const box = target.getBoundingClientRect();
            reorder(event.clientX < box.left + box.width / 2 ? target : target.nextSibling);
        };
        const finish = event => {
            if (!press || event.pointerId !== press.id) return;
            press = null;
            stopListening();
            const state = drag;
            drag = null;
            if (!state) return;
            const changed = event.type === "pointerup";
            if (!changed) for (const node of state.order) container.insertBefore(node, addSlot());
            // 拖动结束后浏览器还会补发一次 click，吞掉它，避免顺手打开菜单。
            container.addEventListener("click", swallow => { swallow.stopPropagation(); swallow.preventDefault(); }, { capture: true, once: true });
            setTimeout(() => container.dispatchEvent(new Event("click")), 0);
            const target = wrap.getBoundingClientRect();
            const cleanup = () => {
                state.ghost.remove();
                container.classList.remove("is-dragging");
                wrap.classList.remove("rule-piece--placeholder");
                blockDragActive = false;
                if (changed) {
                    const next = pieceNodes().map(node => blocks[Number(node.dataset.blockIndex)]);
                    changeBlocks(next, next.indexOf(state.block));
                } else piece.focus({ preventScroll: true });
            };
            if (reducedMotion()) cleanup();
            else state.ghost.animate([
                { transform: state.ghost.style.transform },
                { transform: `translate(${target.left - state.rect.left}px, ${target.top - state.rect.top}px)` },
            ], { duration: 160, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" }).finished.then(cleanup, cleanup);
        };
        const cancel = event => {
            if (event.key === "Escape" && drag) { event.preventDefault(); finish({ pointerId: press.id, type: "cancel" }); }
        };
        piece.addEventListener("pointerdown", event => {
            if (busy || blockDragActive || !event.isPrimary || event.button !== 0) return;
            press = { id: event.pointerId, x: event.clientX, y: event.clientY };
            document.addEventListener("pointermove", move);
            document.addEventListener("pointerup", finish);
            document.addEventListener("pointercancel", finish);
            document.addEventListener("keydown", cancel);
        });
        piece.addEventListener("keydown", event => {
            if (busy || blockDragActive || !event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            const index = Number(wrap.dataset.blockIndex), next = index + (event.key === "ArrowLeft" ? -1 : 1);
            if (next < 0 || next >= blocks.length) return;
            const list = blocks.slice();
            [list[index], list[next]] = [list[next], list[index]];
            changeBlocks(list, next);
        });
    }

    function episodeMap() {
        const map = new Map();
        for (const episode of title?.episodes || []) {
            const key = `${episode.season_number}:${episode.episode_number}`;
            if (map.has(key)) map.set(key, null);
            else map.set(key, episode);
        }
        return map;
    }
    const validNumber = (value, minimum) => /^\d{1,5}$/.test(String(value)) && Number(value) >= minimum;
    const pad = value => String(value).padStart(2, "0");
    const ISSUES = { empty: ["待填写", "请填写季号和集号"], absent: ["无此集", "TMDB 中没有这一集"] };

    function computeErrors() {
        const positions = episodeMap(), counts = new Map();
        for (const file of files.filter(f => f.included)) {
            const key = `${Number(file.season)}:${Number(file.episode)}`;
            counts.set(key, (counts.get(key) || 0) + 1);
        }
        for (const file of files) {
            file.copies = 1;
            file.issue = null;
            file.target = null;
            if (!file.included || !title || title.media_type !== "tv") continue;
            const key = `${Number(file.season)}:${Number(file.episode)}`;
            if (!validNumber(file.season, 0) || !validNumber(file.episode, 1)) file.issue = ISSUES.empty;
            else if (!positions.get(key)) file.issue = ISSUES.absent;
            else file.target = positions.get(key);
            // 同一集的多个文件按多版本关联，与一键确认保持一致。
            file.copies = counts.get(key) || 1;
        }
    }

    function fillTargetMenu(menu, file) {
        menu.replaceChildren();
        let season = null;
        for (const episode of title.episodes) {
            if (episode.season_number == null || episode.episode_number == null) continue;
            if (season !== episode.season_number) {
                season = episode.season_number;
                const group = el("li");
                group.append(el("h3", "dropdown-header small mb-0", `第 ${season} 季`));
                menu.append(group);
            }
            const option = button(`E${pad(episode.episode_number)} · ${episode.name}`, () => {
                file.season = episode.season_number; file.episode = episode.episode_number; file.manual = true;
                file.nodes.season.value = file.season; file.nodes.episode.value = file.episode;
                updateFileStates();
                file.nodes.target.focus();
            }, "dropdown-item small text-wrap");
            const active = Number(file.season) === episode.season_number && Number(file.episode) === episode.episode_number;
            option.classList.toggle("active", active);
            option.setAttribute("aria-pressed", String(active));
            const item = el("li"); item.append(option); menu.append(item);
        }
    }


    // ---- 识别规则面板：样例、规则摘要、季号来源、文件夹调整、结果 ----------------

    function sampleFile() {
        if (!files.length) return null;
        sampleIndex = Math.min(Math.max(sampleIndex, 0), files.length - 1);
        return files[sampleIndex];
    }

    function linkBlock(index, on) {
        $("[data-rule-sample]").querySelectorAll(`[data-span="${index}"]`).forEach(node => node.classList.toggle("is-linked", on));
        $("[data-rule-pattern]").querySelectorAll(`[data-block="${index}"]`).forEach(node => node.classList.toggle("is-linked", on));
    }

    function sampleMarks(file) {
        if (pendingMarks?.file === file) return pendingMarks.marks;
        return (file.match?.spans || []).filter(span => span.type === "season" || span.type === "episode")
            .map(({ type, start, end }) => ({ type, start, end }));
    }

    function markSample(file, start, end, type) {
        if (busy) return;
        let marks = sampleMarks(file).filter(mark => mark.start !== start);
        if (type !== "none") marks = marks.filter(mark => mark.type !== type).concat({ type, start, end });
        if (!marks.some(mark => mark.type === "episode")) {
            pendingMarks = { file, marks };
            renderRulePanel();
            return;
        }
        const next = rules.buildRule(file.name, marks);
        if (!next) { feedback("无法根据这个标记生成规则，请换一段数字试试。", "warning"); return; }
        useBlocks(next);
    }

    function digitToken(file, segment, marks) {
        const mark = marks.find(item => item.start === segment.start && item.end === segment.end);
        const wrap = el("span", "dropdown d-inline-block");
        const token = el("button", `rule-token is-digit${mark ? ` rule-${mark.type}` : ""}`, segment.text);
        token.type = "button";
        token.dataset.bsToggle = "dropdown";
        token.setAttribute("aria-expanded", "false");
        if (segment.span != null) token.dataset.span = segment.span;
        token.setAttribute("aria-label", `${segment.text}${mark ? `（${mark.type === "season" ? "季号" : "集号"}）` : ""}，设为季号或集号`);
        if (mark) token.dataset.role = mark.type === "season" ? "季" : "集";
        const menu = el("ul", "dropdown-menu media-menu");
        const option = (label, type) => {
            const node = button(label, () => markSample(file, segment.start, segment.end, type), "dropdown-item small");
            const line = el("li"); line.append(node); menu.append(line);
        };
        if (mark?.type !== "episode") option("设为集号", "episode");
        if (mark?.type !== "season") option("设为季号", "season");
        if (mark?.type === "season") option("不作为季号", "none");
        wrap.append(token, menu);
        bootstrap.Dropdown.getOrCreateInstance(token, fixedPopper);
        return wrap;
    }

    function renderSample() {
        const file = sampleFile(), box = $("[data-rule-sample]");
        box.replaceChildren();
        $("[data-sample-index]").textContent = files.length ? `${sampleIndex + 1}/${files.length}` : "";
        root.querySelectorAll("[data-sample-step]").forEach(node => { node.disabled = busy || files.length < 2; });
        if (!file) return;
        // 文件夹行：季号来自文件夹时标出读到的位置。
        const folder = el("div", "rule-sample-folder");
        folder.append(icon("folder"));
        const label = folderLabel(file.folder), hit = rules.folderSeason(label);
        if (hit && seasonSource !== "fixed") {
            folder.append(el("span", "", label.slice(0, hit.start)), el("mark", `rule-season${seasonSource === "folder" ? "" : " is-soft"}`, label.slice(hit.start, hit.end)), el("span", "", label.slice(hit.end)));
        } else folder.append(el("span", "", label));
        const name = el("div", "rule-sample-name");
        const marks = sampleMarks(file), spans = file.match?.spans || [];
        const cuts = new Set([0, file.name.length]);
        for (const span of spans) { cuts.add(span.start); cuts.add(span.end); }
        for (const run of file.name.matchAll(/[0-9]+/g)) { cuts.add(run.index); cuts.add(run.index + run[0].length); }
        // 扩展名里的数字（mp4）不可点。
        const dot = file.name.lastIndexOf("."), ext = dot > 0 && file.name.length - dot <= 6 ? dot : file.name.length;
        const points = [...cuts].sort((a, b) => a - b);
        for (let i = 0; i < points.length - 1; i++) {
            const start = points[i], end = points[i + 1], text = file.name.slice(start, end);
            const index = spans.findIndex(span => span.start <= start && span.end >= end && span.end > span.start);
            const segment = { start, end, text, span: index >= 0 ? index : null };
            if (/^[0-9]+$/.test(text) && start < ext) name.append(digitToken(file, segment, marks));
            else {
                const node = el("span", "rule-token", text);
                if (segment.span != null) {
                    node.dataset.span = segment.span;
                    node.addEventListener("pointerenter", () => linkBlock(segment.span, true));
                    node.addEventListener("pointerleave", () => linkBlock(segment.span, false));
                }
                name.append(node);
            }
        }
        box.append(folder, name);
        const hint = $("[data-sample-hint]");
        hint.className = "rule-hint";
        if (pendingMarks?.file === file) hint.textContent = "再点一段数字设为集号";
        else if (!file.match) {
            hint.textContent = validNumber(file.episode, 1) ? "规则读不出，暂用自动识别结果 · 点数字设为集号" : "规则读不出这个文件 · 点数字设为季号或集号";
            hint.classList.add("is-warning");
        }
        else hint.textContent = "点数字可改为季号或集号";
    }

    function renderSeasonSource(groups) {
        $("[data-season-source]").querySelectorAll("button").forEach(node => {
            const active = node.dataset.value === seasonSource;
            node.classList.toggle("active", active);
            node.setAttribute("aria-pressed", String(active));
        });
        $("[data-season-fallback]").textContent = seasonSource === "fixed" ? "" : "读不出时用";
        const single = groups.length === 1;
        show($("[data-single-offset]"), single);
        if (single) {
            const input = $("[data-offset-input]");
            if (document.activeElement !== input) input.value = adjust.get(groups[0].key)?.offset ?? "";
            input.dataset.folder = groups[0].key;
        }
    }

    function renderFolders(groups) {
        const box = $("[data-rule-folders]");
        show(box, groups.length > 1);
        if (groups.length < 2) { box.replaceChildren(); return; }
        // 输入框正在编辑时只更新占位提示，避免打断输入。
        if (box.contains(document.activeElement)) {
            for (const group of groups) {
                const input = box.querySelector(`[data-folder-season="${CSS.escape(group.key)}"]`);
                if (input) input.placeholder = String(group.files[0]?.raw?.season ?? "");
            }
            return;
        }
        box.replaceChildren();
        const head = el("div", "rule-folder rule-folder-head");
        head.append(el("span", "", "文件夹"), el("span", "", "季号"), el("span", "", "集号偏移"));
        box.append(head);
        for (const group of groups) {
            const row = el("div", "rule-folder");
            const name = el("span", "rule-folder-name");
            name.append(el("span", "text-truncate", group.label), el("span", "rule-folder-count", `${group.files.length} 个`));
            name.title = group.label;
            const change = adjust.get(group.key) || {};
            const season = el("input", "form-control form-control-sm rule-number");
            season.type = "number"; season.min = "0"; season.step = "1";
            season.value = change.season ?? "";
            season.placeholder = String(group.files[0]?.raw?.season ?? "");
            season.dataset.folderSeason = group.key;
            season.setAttribute("aria-label", `${group.label} 的季号`);
            const offset = el("input", "form-control form-control-sm rule-number");
            offset.type = "number"; offset.step = "1"; offset.placeholder = "+0";
            offset.value = change.offset ?? "";
            offset.setAttribute("aria-label", `${group.label} 的集号偏移`);
            const update = () => setAdjust(group.key, { season: season.value, offset: offset.value });
            season.addEventListener("input", update); offset.addEventListener("input", update);
            const seasonCell = el("label", "rule-inline small"); seasonCell.append("第", season, "季");
            const offsetCell = el("label", "rule-inline small"); offsetCell.append("+", offset);
            row.append(name, seasonCell, offsetCell);
            box.append(row);
        }
    }

    function setAdjust(key, change) {
        const value = { season: change.season ?? "", offset: change.offset ?? "" };
        if (value.season === "" && (value.offset === "" || Number(value.offset) === 0)) adjust.delete(key);
        else adjust.set(key, value);
        applyRule(false);
    }

    // 有“无此集”且各文件夹像是连续编号时，给出一键顺延的建议。
    function computeSuggestion(groups) {
        if (title?.media_type !== "tv" || groups.length < 2) return null;
        if (!files.some(file => file.included && file.issue === ISSUES.absent)) return null;
        const input = groups.map(group => {
            const chosen = group.files.filter(file => file.included && validNumber(file.raw?.episode, 1));
            return { key: group.key, season: numberOrNull(chosen[0]?.raw.season), episodes: chosen.map(file => Number(file.raw.episode)) };
        });
        const plan = rules.continuePlan(input, title.episodes);
        if (!plan) return null;
        const same = plan.every(step => {
            const group = groups.find(item => item.key === step.key);
            return group.files.every(file => !file.included || (Number(file.season) === step.season && Number(file.episode) === Number(file.raw.episode) + step.offset));
        });
        return same ? null : plan;
    }

    function renderSuggestion(groups) {
        const box = $("[data-rule-suggest]");
        suggestion = computeSuggestion(groups);
        box.replaceChildren();
        show(box, !!suggestion);
        if (!suggestion) return;
        const seasons = new Set(title.episodes.filter(item => item.season_number > 0).map(item => item.season_number));
        const body = el("div", "rule-suggest-body");
        body.append(el("strong", "", "可能是连续编号"), el("span", "", `TMDB 中这部剧有 ${seasons.size} 季，文件夹可按顺序接上：`));
        const list = el("ul", "rule-suggest-list");
        for (const step of suggestion) {
            const group = groups.find(item => item.key === step.key);
            const line = el("li");
            line.append(el("span", "text-truncate", group.label), icon("arrow-right"), el("span", "rule-suggest-target", `第 ${step.season} 季第 ${step.first}–${step.last} 集`));
            list.append(line);
        }
        body.append(list);
        const apply = button("按此调整", () => {
            for (const step of suggestion) {
                const raw = groups.find(item => item.key === step.key).files.find(file => file.raw?.season !== "")?.raw.season;
                adjust.set(step.key, { season: Number(raw) === step.season ? "" : String(step.season), offset: step.offset ? String(step.offset) : "" });
                if (!adjust.get(step.key).season && !adjust.get(step.key).offset) adjust.delete(step.key);
            }
            applyRule(false);
            feedback("已按连续编号调整季集", "success");
        }, "btn btn-sm media-primary-action px-3");
        const head = el("div", "rule-suggest-icon"); head.append(icon("lightbulb"));
        box.append(head, body, apply);
    }

    function renderResult() {
        const box = $("[data-rule-result]");
        box.replaceChildren();
        const chosen = files.filter(file => file.included);
        const unread = chosen.filter(file => !validNumber(file.season, 0) || !validNumber(file.episode, 1));
        const found = chosen.filter(file => file.target);
        const missing = chosen.length - unread.length - found.length;
        const part = (symbol, text, classes = "", action = null) => {
            const node = action ? button("", action, `rule-result-item btn btn-link ${classes}`) : el("span", `rule-result-item ${classes}`);
            node.append(icon(symbol), el("span", "", text));
            box.append(node);
        };
        part("files", `${chosen.length} 个文件`);
        part(unread.length ? "triangle-alert" : "check", unread.length ? `${unread.length} 个读不出季集` : "季集全部读出", unread.length ? "is-warning" : "is-ok",
            unread.length ? () => { sampleIndex = files.indexOf(unread.find(file => !file.match) || unread[0]); renderRulePanel(); } : null);
        part(missing > 0 ? "triangle-alert" : "check", missing > 0 ? `${missing} 个在 TMDB 找不到` : `${found.length} 个都在 TMDB 找到`, missing > 0 ? "is-warning" : "is-ok",
            missing > 0 ? locateIssue : null);
    }

    function renderRulePanel() {
        if (title?.media_type !== "tv" || !task) return;
        const groups = folderGroups();
        renderSample(); renderPattern(); renderSeasonSource(groups); renderFolders(groups); renderSuggestion(groups); renderResult();
    }

    function stats() {
        let selected = 0, issues = 0;
        for (const file of files) if (file.included) { selected++; if (file.issue) issues++; }
        return { selected, issues };
    }

    function updateFileStates() {
        computeErrors();
        const series = title?.media_type === "tv";
        const { selected, issues } = stats();
        if (!issues) onlyErrors = false;
        for (const file of files) {
            const refs = file.nodes;
            if (!refs) continue;
            const problem = file.included && !!file.issue;
            refs.row.classList.toggle("has-issue", problem);
            refs.row.classList.toggle("is-excluded", !file.included);
            show(refs.row, !onlyErrors || problem);
            for (const input of [refs.season, refs.episode]) input.setAttribute("aria-invalid", String(problem));
            const target = refs.target;
            target.replaceChildren();
            target.disabled = busy || !file.included || !series;
            target.classList.toggle("dropdown-toggle", series && file.included);
            if (!file.included) target.append(el("span", "text-body-secondary", "不关联"));
            else if (file.issue) {
                target.append(icon("triangle-alert", "file-issue-icon"), el("span", "text-truncate", file.issue[0]));
                target.title = file.issue[1];
            } else {
                const name = series ? `S${pad(file.season)}E${pad(file.episode)} · ${file.target?.name || ""}` : title ? title.name : "待选择作品";
                target.append(el("span", "text-truncate", name));
                if (series && file.copies > 1) target.append(el("span", "target-copies", `${file.copies} 个版本`));
                target.title = name;
            }
            if (file.manual && file.included) {
                const mark = icon("pencil", "text-body-secondary flex-shrink-0");
                mark.removeAttribute("aria-hidden"); mark.setAttribute("role", "img"); mark.title = "已手动修改"; mark.setAttribute("aria-label", "已手动修改");
                target.append(mark);
            }
            target.setAttribute("aria-label", `${file.name}：${target.textContent}`);
        }
        $("[data-files-count]").textContent = `已选 ${selected}/${files.length}`;
        $("[data-files-all]").checked = selected === files.length;
        $("[data-files-all]").indeterminate = selected > 0 && selected < files.length;
        const filter = $("[data-files-errors]");
        filter.textContent = onlyErrors ? "显示全部" : `只看问题 ${issues}`;
        filter.classList.toggle("active", onlyErrors);
        filter.setAttribute("aria-pressed", String(onlyErrors));
        show(filter, issues > 0 || onlyErrors);
        show($("[data-rule-reset]"), files.some(f => f.manual));
        if (series) {
            const source = useGuess ? "季集按识别结果填写" : "季集按文件名规则读取";
            $("[data-rule-summary]").textContent = adjust.size ? `${source}，已按文件夹调整` : source;
            renderRulePanel();
        }
        renderTaskBar({ selected, issues });
    }

    function renderTaskBar({ selected, issues } = stats()) {
        if (currentView() !== "editor") return;
        if (progress) {
            bar.progress("association", progressSpec(progress));
            return;
        }
        const action = { label: selected ? `关联 ${selected} 个` : "关联", run: submit, disabled: busy || !title || !selected || issues > 0 };
        if (!title) bar.check("association", { state: busy ? "loading" : "idle", text: busy ? "读取作品信息…" : "先选择对应的电影或剧集", action });
        else if (!selected) bar.check("association", { text: "未选择视频", action });
        else if (issues) bar.check("association", { state: "warning", text: `${issues} 个文件需处理`, link: onlyErrors ? null : { label: "查看", run: locateIssue }, action });
        else bar.check("association", { state: "ok", text: title.media_type === "tv" ? `${selected} 个视频全部对上` : `${selected} 个视频 → ${title.name}`, action });
    }

    function locateIssue() {
        onlyErrors = true;
        updateFileStates();
        const file = files.find(item => item.included && item.issue);
        if (!file?.nodes) return;
        file.nodes.row.scrollIntoView({ block: "center" });
        (title?.media_type === "tv" ? file.nodes.episode : file.nodes.target).focus({ preventScroll: true });
    }

    function renderFiles() {
        const table = $("[data-task-files]");
        table.replaceChildren();
        const series = title?.media_type === "tv";
        root.querySelectorAll("[data-position-heading]").forEach(node => show(node, series));
        for (const file of files) {
            const row = el("tr"), checkCell = el("td", "file-check"), name = el("td", "file-name");
            const check = el("input", "form-check-input"); check.type = "checkbox"; check.checked = file.included;
            check.disabled = busy;
            check.setAttribute("aria-label", `选择 ${file.name}`);
            check.addEventListener("change", () => { file.included = check.checked; updateFileStates(); });
            checkCell.append(check);
            const filename = el("div", "text-break");
            if (series && file.match) for (const span of file.match.spans) filename.append(el(["season", "episode"].includes(span.type) ? "mark" : "span", ["season", "episode"].includes(span.type) ? `rule-${span.type}` : "", span.text));
            else filename.textContent = file.name;
            name.append(filename);
            const relative = file.path.slice(0, -file.name.length).replace(`${task.path}/`, "");
            if (relative && task.kind === "folder") name.append(el("div", "small text-body-secondary text-break mt-1", relative));
            const position = el("td", "file-position"), pair = el("div", "position-pair"), inputs = {};
            for (const field of ["season", "episode"]) {
                const input = el("input", "form-control form-control-sm position-input");
                input.type = "number"; input.min = field === "season" ? "0" : "1"; input.step = "1";
                input.value = file[field] ?? "";
                input.disabled = busy;
                input.setAttribute("aria-label", `${file.name} ${field === "season" ? "季号" : "集号"}`);
                input.addEventListener("input", () => { file[field] = input.value; file.manual = true; updateFileStates(); });
                inputs[field] = input;
                pair.append(el("span", "position-prefix", field === "season" ? "S" : "E"), input);
            }
            position.append(pair);
            show(position, series);
            const targetCell = el("td", "file-target"), dropdown = el("div", "dropdown");
            const target = el("button", "btn btn-sm target-btn");
            target.type = "button";
            if (series) {
                target.dataset.bsToggle = "dropdown";
                target.setAttribute("aria-expanded", "false");
                const menu = el("ul", "dropdown-menu dropdown-menu-end media-menu target-menu");
                dropdown.append(target, menu);
                bootstrap.Dropdown.getOrCreateInstance(target, fixedPopper);
                target.addEventListener("show.bs.dropdown", () => fillTargetMenu(menu, file));
                target.addEventListener("shown.bs.dropdown", () => menu.querySelector(".active")?.scrollIntoView({ block: "center" }));
            } else dropdown.append(target);
            targetCell.append(dropdown);
            row.append(checkCell, name, position, targetCell);
            table.append(row);
            file.nodes = { row, target, ...inputs };
        }
        updateFileStates();
    }

    function setTitleCard() {
        $("[data-task-title]").textContent = title ? title.name : "选择电影或剧集";
        let meta = "";
        if (title) {
            const parts = [title.year, kindLabel(title.media_type)];
            if (title.media_type === "tv") {
                const episodes = title.episodes.filter(item => item.season_number != null);
                const seasons = new Set(episodes.map(item => item.season_number));
                parts.push(episodes.length ? `${seasons.size} 季 ${episodes.length} 集` : "暂无季集");
            }
            meta = parts.filter(Boolean).join(" · ");
        }
        $("[data-task-title-meta]").textContent = meta;
        const link = $("[data-task-tmdb]");
        if (title?.tmdb_id) {
            link.href = `https://www.themoviedb.org/${title.media_type}/${title.tmdb_id}`;
            link.setAttribute("aria-label", `在 TMDb 查看 ${title.name}`);
        }
        show(link, !!title?.tmdb_id);
        const frame = poster(title, "film", "match-poster");
        frame.dataset.taskPoster = "";
        $("[data-task-poster]").replaceWith(frame);
        const pick = $("[data-task-pick]");
        pick.replaceChildren(icon(title ? "arrow-left-right" : "search"), document.createTextNode(title ? "更换" : "选择作品"));
    }

    function setRuleOpen(value) {
        ruleOpen = value;
        show($("[data-rule-editor]"), value);
        const toggle = $("[data-rule-toggle]");
        toggle.setAttribute("aria-expanded", String(value));
        toggle.classList.toggle("active", value);
    }

    async function chooseTitle(selection) {
        const version = ++taskVersion;
        busy = true; title = null; setTitleCard(); renderFiles();
        $("[data-task-pick]").disabled = true;
        try {
            const data = await json(`${root.dataset.metadataUrl}?${new URLSearchParams({ title_id: selection.title_id })}`);
            if (version !== taskVersion) return;
            const known = task.recognition?.title?.title_id === data.title_id ? task.recognition.title.tmdb_id : null;
            title = { ...data, tmdb_id: data.tmdb_id || selection.tmdb_id || known || null };
            setTitleCard();
            show($("[data-rule-panel]"), title.media_type === "tv");
            const seasons = [...new Set(title.episodes.map(item => item.season_number).filter(x => x !== null))].sort((a, b) => a - b);
            $("[data-default-season]").value = selection.season_number ?? (seasons.length === 1 ? seasons[0] : "");
            files.forEach(file => { file.manual = false; });
            let best = 0, score = -1;
            rules.presets.forEach((preset, index) => {
                const matches = files.filter(file => rules.parse(file.name, preset.blocks)).length;
                if (matches > score) { score = matches; best = index; }
            });
            setPreset(best);
            blocks = structuredClone(rules.presets[best].blocks);
            useGuess = files.some(file => file.guess?.[1] != null);
            adjust.clear(); pendingMarks = null;
            const fromName = files.some(file => file.guess?.[0] != null || rules.parse(file.name, blocks)?.season != null);
            seasonSource = fromName || !files.some(file => file.folderSeason) ? (fromName ? "file" : "fixed") : "folder";
            busy = false;
            renderBlocks(); applyRule(false);
            const unread = files.findIndex(file => !file.match);
            sampleIndex = unread >= 0 ? unread : 0;
            // 有文件读不出集号，或可以按连续编号修正时，才展开识别规则。
            renderRulePanel();
            setRuleOpen(title.media_type === "tv" && (files.some(file => file.included && !validNumber(file.episode, 1)) || !!suggestion));
        } catch (error) {
            if (version === taskVersion) feedback(error.message, "danger");
        } finally {
            if (version === taskVersion) { busy = false; $("[data-task-pick]").disabled = false; renderFiles(); }
        }
    }

    function pickTitle() {
        waitingForTitle = true;
        document.getElementById("title-picker-heading").textContent = "选择对应作品";
        const search = picker.querySelector("[data-title-search-query]");
        search.value = (task.recognition?.query || task.name.replace(/\.[a-z0-9]{2,5}$/i, "").replace(/[._]/g, " ")).slice(0, 100);
        search.dispatchEvent(new Event("input"));
        const type = title?.media_type || task.recognition?.media_type || (files.some(file => /S\d+E\d+|\d+x\d+|第\d+集|EP\d+/i.test(file.name)) || files.length > 1 ? "tv" : "movie");
        picker.querySelector(`[name="search_type"][value="${type}"]`).checked = true;
        bootstrap.Modal.getOrCreateInstance(picker).show();
    }

    function showView(view) {
        show(panel, view === "list");
        show(editor, view === "editor");
        // 页头的筛选和状态切换只作用于列表
        $("[data-discovery-controls]").classList.toggle("invisible", view !== "list");
        show($("[data-linked-editor]"), view === "linked");
        show($("[data-drive-details-panel]"), view === "drive");
        document.dispatchEvent(new CustomEvent("association:view", {detail:{view}}));
        syncSource();
        syncBar();
    }

    function openTask(item, search = false) {
        if (!editor.classList.contains("d-none")) return;
        listScroll = window.scrollY;
        taskVersion++;
        task = item; title = null; busy = false; useGuess = false; onlyErrors = false; progress = null;
        const positions = item.recognition?.positions || {};
        files = [...item.files].sort((a, b) => a.path.localeCompare(b.path, "zh-CN", { numeric: true })).map(file => ({ ...file, included: true, season: "", episode: "", manual: false, guess: positions[file.path] || null }));
        for (const file of files) {
            file.folder = folderOf(file);
            const relative = file.folder.startsWith(`${item.path}/`) ? file.folder.slice(item.path.length + 1).split("/").reverse() : [];
            file.folderSeason = [...relative, file.folder.split("/").pop()].map(part => rules.folderSeason(part)).find(Boolean) || null;
        }
        adjust.clear(); pendingMarks = null; sampleIndex = 0;
        show($("[data-task-ai]"), item.recognition?.method === "ai");
        $("[data-task-name]").textContent = item.name;
        const path = $("[data-task-path]");
        path.textContent = `${files.length} 个视频${item.kind === "update" ? " · 已有内容更新" : ""} · ${item.path}`;
        path.title = item.path;
        $("[data-task-pick]").disabled = false;
        $("[data-files-all]").disabled = false;
        show($("[data-rule-panel]"), false);
        $("[data-rule-summary]").textContent = "";
        setRuleOpen(false);
        setTitleCard();
        showView("editor");
        renderFiles();
        const found = item.recognition?.title;
        if (found && !search) chooseTitle(found);
        else pickTitle();
        window.scrollTo({ top: 0 });
    }

    function closeEditor() {
        taskVersion++; waitingForTitle = false; busy = false; progress = null;
        // 提交成功后也要解开 lockEditor(true) 锁住的控件，否则下一次打开编辑器时返回、规则、季号等按钮仍是禁用的
        editor.querySelectorAll("button, input, select").forEach(node => { node.disabled = false; });
        showView("list");
        renderList();
        window.scrollTo({ top: listScroll });
    }

    function lockEditor(value) {
        busy = value;
        editor.querySelectorAll("button, input, select").forEach(node => { node.disabled = value; });
        if (!value) { renderBlocks(); renderFiles(); }
    }

    // 一步完成：提交时由服务端校验，有问题的文件直接标在表格里。
    async function submit() {
        if (busy || !title) return;
        const chosen = files.filter(file => file.included);
        if (!chosen.length) return;
        progress = chosen.length;
        const sources = capture([editor.querySelector(".match-poster")]);
        lockEditor(true);
        renderTaskBar();
        try {
            await enqueue([{ title_id: title.title_id, source_name: task.name,
                items: chosen.map(file => ({ path: file.path, season_number: file.season, episode_number: file.episode })) }], sources);
            closeEditor();
        } catch (error) {
            feedback(error.message, "danger");
            progress = null;
            lockEditor(false);
        }
    }

    // ---- 事件 ------------------------------------------------------------

    rules.presets.forEach((preset, i) => {
        const value = String(i);
        const item = el("li");
        const option = button(preset.name, () => {
            if (busy || blockDragActive) return;
            useBlocks(structuredClone(rules.presets[i].blocks));
        }, "dropdown-item");
        option.dataset.value = value;
        item.append(option); $("[data-rule-presets]").append(item);
    });
    setPreset(0);
    $("[data-default-season]").addEventListener("input", () => applyRule(false));
    editor.querySelectorAll("[data-sample-step]").forEach(node => node.addEventListener("click", () => {
        if (!files.length) return;
        sampleIndex = (sampleIndex + Number(node.dataset.sampleStep) + files.length) % files.length;
        pendingMarks = null; renderRulePanel();
    }));
    $("[data-season-source]").querySelectorAll("button").forEach(node => node.addEventListener("click", () => {
        if (busy || seasonSource === node.dataset.value) return;
        seasonSource = node.dataset.value; applyRule(false);
    }));
    $("[data-offset-input]").addEventListener("input", event => {
        const key = event.target.dataset.folder;
        if (key == null) return;
        setAdjust(key, { season: adjust.get(key)?.season ?? "", offset: event.target.value });
    });
    $("[data-rule-toggle]").addEventListener("click", () => setRuleOpen(!ruleOpen));
    $("[data-rule-reset]").addEventListener("click", () => { files.forEach(file => { file.manual = false; }); applyRule(false); });
    $("[data-files-all]").addEventListener("change", event => { files.forEach(file => { file.included = event.target.checked; }); renderFiles(); });
    $("[data-files-errors]").addEventListener("click", () => { onlyErrors = !onlyErrors; updateFileStates(); });
    $("[data-task-pick]").addEventListener("click", pickTitle);
    $("[data-task-close]").addEventListener("click", closeEditor);
    picker.addEventListener("title:selected", event => {
        if (!waitingForTitle) return;
        event.preventDefault(); waitingForTitle = false; chooseTitle(event.detail);
    });
    picker.addEventListener("hidden.bs.modal", () => { waitingForTitle = false; });
    $("[data-discovery-refresh]").addEventListener("click", () => discover(true, true));
    $("[data-scan-stop]").addEventListener("click", stopScan);
    $("[data-scan-restart]").addEventListener("click", () => discover(true, true));
    $("[data-confirm-all]").addEventListener("change", event => selectAllReady(event.target.checked));
    $("[data-confirm-run]").addEventListener("click", confirmPicked);
    // 剩余时间、限速倒计时、“多久没有新进度”每秒更新（轮询遇到 304 时列表不会重画）
    setInterval(() => { if (!document.hidden && scanning() && currentView() === "list") renderProgress(); }, 1000);
    $("[data-source-ignored]").addEventListener("click", openIgnored);
    workspace.addEventListener("click", event => {
        const browseButton = event.target.closest("[data-root-open]");
        if (browseButton) openBrowser(browseButton.dataset.rootOpen);
        const choice = event.target.closest("[data-source-path]");
        // 动态扫描目录自身已绑定选择事件，挂载与“全部来源”通过事件委托处理。
        if (choice && !choice.closest(".source-root")) {
            selectSource(choice.dataset.sourcePath);
        }
    });
    document.addEventListener("drives:updated", () => { rootsSignature = null; renderRoots(); renderList(); discover(); });
    document.addEventListener("drives:details", () => { rootsSignature = null; showView("drive"); renderRoots(); });
    document.addEventListener("drives:details-closed", () => showView("list"));
    $("[data-drive-details-close]").addEventListener("click", () => { showView("list"); renderList(); });
    $("[data-linked-close]").addEventListener("click", () => { showView("list"); renderList(); window.scrollTo({ top: listScroll }); });
    $("[data-discovery-tabs]").addEventListener("keydown", event => {
        const nodes = [...event.currentTarget.querySelectorAll('[role="tab"]')];
        const index = nodes.indexOf(event.target);
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (index < 0 || !step) return;
        event.preventDefault(); const next = nodes[(index + step + nodes.length) % nodes.length]; next.click(); next.focus();
    });
    $("[data-root-choose]").addEventListener("click", () => updateRoot(browser.dataset.path, "add"));
    $("[data-discovery-filter]").addEventListener("input", renderList);
    window.addEventListener("beforeunload", event => { if (busy || confirming) { event.preventDefault(); event.returnValue = ""; } });
    // 引导页完成后带 add_root=1 跳转过来：直接打开“添加目录”，并从地址栏去掉该参数。
    const pageUrl = new URL(location.href), wantsRoot = pageUrl.searchParams.has("add_root");
    if (wantsRoot) { pageUrl.searchParams.delete("add_root"); history.replaceState(history.state, "", pageUrl); }
    // 打开页面先显示已有结果（启动时的后台扫描或上次快照），没有结果时才开始扫描。
    syncSource(); renderList(); loadLinked();
    discover().then(data => {
        pollJobs(true);
        // 引导页过来的新用户：选定目录后 updateRoot 才开始扫描，不在弹窗选目录的同时抢跑一次空扫
        if (wantsRoot) { addRoot(); return; }
        // 还没有任何网盘时不自动扫描：扫了也只会报“没有目录”，空状态会引导先添加网盘；
        // 首次使用还没选目录时同样不盲扫，顶部指引会让用户先选目录
        if (firstRun && !roots.length) return;
        if (data && (data.status === "idle" || data.restored) && !noDrives()) discover(true);
    });
})();
