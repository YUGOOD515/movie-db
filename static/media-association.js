(() => {
    const root = document.getElementById("media-association");
    if (!root) return;
    const $ = selector => root.querySelector(selector);
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
    const icon = (name, classes = "") => { const node = el("i", `bi bi-${name} ${classes}`.trim()); node.setAttribute("aria-hidden", "true"); return node; };
    const spinner = () => { const node = el("span", "spinner-border spinner-border-sm text-body-secondary"); node.setAttribute("aria-hidden", "true"); return node; };
    // 菜单按视口定位，避免被列表或表格的滚动容器裁切。
    const fixedPopper = { popperConfig: config => ({ ...config, strategy: "fixed" }) };
    const rules = window.FilenameRules;
    const picker = document.getElementById("title-picker");
    const browser = document.getElementById("root-browser");
    const manual = document.querySelector("[data-manual-panel]");
    const panel = $("[data-discovery-panel]"), editor = $("[data-association-editor]");

    let records = [], ignored = [], roots = [], scan = {}, confirming = null, watching = false, pollTimer = null;
    let tab = null, listScroll = 0;
    const excluded = new Set(), expanded = new Set();
    let files = [], title = null, task = null, blocks = [], waitingForTitle = false;
    let useGuess = false, busy = false, taskVersion = 0, onlyErrors = false, ruleOpen = false, progress = null;
    let blockDragActive = false;

    async function json(url, payload) {
        const response = await fetch(url, payload === undefined ? {} : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
        let data;
        try { data = await response.json(); } catch { throw new Error("服务未返回有效数据，请稍后重试。"); }
        if (!response.ok && !data.rows) throw new Error(data.error || "请求失败，请重试。");
        return data;
    }
    const feedback = (text, category = "info") => window.showAppToast?.(text, category);

    const TABS = [["ready", "可确认"], ["review", "待核对"], ["unknown", "未识别"], ["ignored", "已忽略"]];
    const scanning = () => ["running", "recognizing"].includes(scan.status);
    const stateOf = item => item.recognition?.status || "unknown";
    const query = () => $("[data-discovery-filter]").value.trim().toLowerCase();
    const visibleRecords = () => records.filter(item => `${item.name} ${item.path} ${item.recognition?.title?.name || ""}`.toLowerCase().includes(query()));
    const kindLabel = type => type === "tv" ? "剧集" : "电影";

    function poster(found, fallback, classes = "discovery-poster") {
        const frame = el("span", classes);
        frame.append(icon(found ? (found.media_type === "tv" ? "tv" : "film") : fallback));
        if (found?.tmdb_id) {
            const image = el("img");
            image.alt = ""; image.loading = "lazy"; image.decoding = "async";
            image.src = `${root.dataset.posterUrl}?${new URLSearchParams({ type: found.media_type, tmdb_id: found.tmdb_id })}`;
            image.addEventListener("error", () => image.remove());
            frame.append(image);
        }
        return frame;
    }

    function aiTag() {
        const tag = el("span", "discovery-tag");
        tag.title = "AI 识别";
        tag.append(icon("stars"), document.createTextNode("AI"));
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
        if (result.title?.media_type === "tv") { if (result.coverage == null || result.coverage >= 1) list.push(`${result.episodes ?? count} 集`); }
        else if (!result.title || count > 1) list.push(`${count} 个视频`);
        if (result.versions > 1) list.push(`${result.versions} 个版本`);
        if (item.kind === "update") list.push("更新");
        return list;
    }

    // 待核对 / 未识别的原因，直接写在行内。
    function reasons(item, readyTitles) {
        const result = item.recognition || {}, count = item.files.length, list = [];
        if (result.not_media && !result.title) list.push("可能不是影视");
        if (!result.title) return list;
        if (result.coverage != null && result.coverage < 1) list.push(`${Math.round(result.coverage * count)}/${count} 集对上`);
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
        toggle.append(icon("three-dots"));
        const menu = el("ul", "dropdown-menu dropdown-menu-end media-menu");
        const entry = (label, symbol, action) => {
            const option = button("", action, "dropdown-item d-flex align-items-center gap-2");
            option.append(icon(symbol), el("span", "", label));
            const line = el("li"); line.append(option); menu.append(line);
        };
        entry(item.recognition?.title ? "更换作品" : "手动关联", "search", () => openTask(item, true));
        entry("重新识别", "arrow-repeat", () => recognizeAgain(item));
        const divider = el("li"); divider.append(el("hr", "dropdown-divider")); menu.append(divider);
        entry("忽略", "eye-slash", () => setIgnored(item, true));
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
        main.append(poster(found, first.kind === "folder" ? "folder2" : "file-earmark-play"));
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

    function ignoredRow(entry) {
        const row = el("li", "discovery-item");
        const main = el("span", "discovery-main");
        const tile = el("span", "discovery-poster"); tile.append(icon("eye-slash"));
        main.append(tile, textBlock(entry.name, "", [], entry.count ? [`${entry.count} 个视频`] : [], ""));
        const side = el("div", "discovery-side");
        side.append(button("恢复", () => setIgnored(entry, false), "btn btn-sm chip-btn discovery-action"));
        row.append(main, side);
        return row;
    }

    function renderTabs(counts) {
        const box = $("[data-discovery-tabs]");
        box.replaceChildren();
        for (const [key, label] of TABS) {
            if (key === "ignored" && !counts.ignored && tab !== "ignored") continue;
            const node = button("", () => { tab = key; renderList(); }, "btn btn-sm chip-btn discovery-tab");
            node.setAttribute("role", "tab");
            node.setAttribute("aria-selected", String(tab === key));
            node.classList.toggle("active", tab === key);
            node.append(el("span", "", label), el("span", "discovery-tab-count", String(counts[key])));
            box.append(node);
        }
        show(box, records.length > 0 || ignored.length > 0);
    }

    function renderList() {
        const visible = visibleRecords(), search = query();
        const byState = { ready: [], review: [], unknown: [] };
        for (const item of visible) byState[stateOf(item)]?.push(item);
        const hidden = ignored.filter(item => item.name.toLowerCase().includes(search));
        const counts = {
            ready: grouped(byState.ready).length, review: byState.review.length, unknown: byState.unknown.length, ignored: hidden.length,
        };
        // 当前分组清空后（例如全部确认完），自动切到下一个有内容的分组。
        const totals = { ready: 0, review: 0, unknown: 0, ignored: ignored.length };
        for (const item of records) totals[stateOf(item)] = (totals[stateOf(item)] || 0) + 1;
        if (!tab || !totals[tab]) tab = TABS.map(([key]) => key).find(key => totals[key]) || "ready";
        renderTabs(counts);

        const readyTitles = new Set(byState.ready.map(item => item.recognition.title.title_id));
        const container = $("[data-discovery-list]");
        container.replaceChildren();
        const list = el("ul", "discovery-list list-unstyled mb-0 squircle-card");
        if (tab === "ignored") hidden.forEach(entry => list.append(ignoredRow(entry)));
        else {
            const items = tab === "ready" ? byState.ready : byState[tab];
            const groups = tab === "ready" ? grouped(items) : items.map(item => ({ key: `p:${item.path}`, records: [item] }));
            for (const group of groups) list.append(...groupRows(group, tab, readyTitles));
        }
        if (list.childElementCount) container.append(list);

        $("[data-discovery-count]").textContent = records.length ? String(records.length) : "";
        const empty = $("[data-discovery-empty]");
        empty.querySelector("span").textContent = search && (records.length || ignored.length) ? "没有匹配的内容"
            : scanning() && !records.length ? "正在扫描…" : "全部已关联";
        show(empty, !list.childElementCount);
        const missing = $("[data-discovery-missing]");
        missing.querySelector("span").textContent = `${scan.missing || 0} 个源文件失效`;
        missing.title = "已入库的视频在 AList 中找不到，前往概览查看";
        show(missing, scan.missing > 0);
        renderBar();
    }

    function barStatus(bar, symbol, text, classes = "") {
        const status = el("span", `association-bar-status ${classes}`.trim());
        if (symbol === "spinner") status.append(spinner()); else if (symbol) status.append(icon(symbol));
        status.append(el("span", "text-truncate", text));
        bar.append(status);
        return status;
    }

    function renderBar() {
        const bar = $("[data-discovery-bar]");
        bar.replaceChildren();
        let visible = false;
        if (confirming) {
            visible = true;
            barStatus(bar, "spinner", `正在关联 ${confirming.done}/${confirming.total} 个视频`);
            const action = button("关联中…", () => {}, "btn btn-sm media-primary-action px-4");
            action.disabled = true;
            const line = el("span", "association-bar-progress");
            line.style.setProperty("--progress", `${confirming.done / confirming.total * 100}%`);
            bar.append(action, line);
        } else if (tab === "ready") {
            const groups = grouped(visibleRecords().filter(item => stateOf(item) === "ready"));
            if (groups.length) {
                visible = true;
                const picked = groups.filter(group => !excluded.has(group.key));
                const label = el("label", "association-bar-status");
                const all = el("input", "form-check-input mt-0");
                all.type = "checkbox";
                all.checked = picked.length === groups.length;
                all.indeterminate = picked.length > 0 && picked.length < groups.length;
                all.setAttribute("aria-label", "全选可确认项");
                all.addEventListener("change", () => {
                    for (const group of groups) { if (all.checked) excluded.delete(group.key); else excluded.add(group.key); }
                    renderList();
                });
                const videos = picked.reduce((sum, group) => sum + group.records.reduce((count, item) => count + item.files.length, 0), 0);
                label.append(all, el("span", "text-truncate", picked.length ? `已选 ${picked.length} 项 · ${videos} 个视频` : "未选择"));
                const action = button(picked.length ? `确认关联 ${picked.length} 项` : "确认关联", () => confirm(picked.flatMap(group => group.records)), "btn btn-sm media-primary-action px-4");
                action.disabled = !picked.length;
                bar.append(label, action);
            }
        } else if (tab === "unknown") {
            const junk = visibleRecords().filter(item => stateOf(item) === "unknown" && item.recognition?.not_media && !item.recognition?.title);
            if (junk.length) {
                visible = true;
                barStatus(bar, "slash-circle", `${junk.length} 项可能不是影视内容`);
                bar.append(button(`全部忽略`, () => ignoreAll(junk), "btn btn-sm chip-btn px-3"));
            }
        }
        show(bar, visible);
        syncBar();
    }

    // 底部操作栏出现时，全站提示条上移，避免互相遮挡。
    function syncBar() {
        const listBar = !panel.classList.contains("d-none") && !$("[data-discovery-bar]").classList.contains("d-none");
        document.body.classList.toggle("association-bar-active", listBar || !editor.classList.contains("d-none"));
    }

    function renderRoots() {
        const box = $("[data-discovery-roots]"), add = $("[data-root-add]");
        box.querySelectorAll(".root-chip").forEach(node => node.remove());
        for (const item of roots) {
            const chip = el("span", "root-chip");
            chip.title = item.auto ? `${item.path}（自动）` : item.path;
            chip.append(icon(item.auto ? "magic" : "folder2"), el("span", "text-truncate", item.path.split("/").filter(Boolean).pop() || "/"));
            if (!item.auto) {
                const remove = button("", () => updateRoot(item.path, "remove"), "root-remove");
                remove.setAttribute("aria-label", `移除 ${item.path}`);
                remove.append(icon("x"));
                chip.append(remove);
            }
            box.insertBefore(chip, add);
        }
    }

    function scanProgress() {
        const refresh = $("[data-discovery-refresh]");
        const running = scanning();
        refresh.disabled = running;
        refresh.classList.toggle("is-scanning", running);
        refresh.setAttribute("aria-busy", String(running));
        const status = $("[data-discovery-status]");
        let text = "";
        if (scan.status === "running") text = scan.total ? `扫描中 ${Math.round(scan.done / scan.total * 100)}%` : "扫描中…";
        else if (scan.status === "recognizing") text = `识别中 ${scan.recognized || 0}/${scan.to_recognize || 0}`;
        status.lastElementChild.textContent = text;
        show(status, !!text);
    }

    async function discover(start = false, refresh = false) {
        clearTimeout(pollTimer);
        if (start) { watching = true; scan = { ...scan, status: "running", done: 0, total: 0 }; scanProgress(); }
        try {
            const data = await json(root.dataset.discoveryUrl + (refresh ? "?refresh=1" : ""), start ? {} : undefined);
            const busyPaths = new Set(records.filter(item => item.busy).map(item => item.path));
            scan = data; roots = data.roots || []; ignored = data.ignored || [];
            records = data.records.map(item => busyPaths.has(item.path) ? { ...item, busy: true } : item);
            renderRoots(); renderList(); scanProgress();
            if (scanning()) { watching = true; pollTimer = setTimeout(() => discover(), 1500); }
            else if (watching || data.status === "error") {
                watching = false;
                const warnings = data.warnings || [];
                const ready = records.filter(item => stateOf(item) === "ready").length;
                const message = data.status === "error" ? data.error || "AList 扫描失败，请重试。"
                    : `扫描完成 · ${records.length} 项${ready ? ` · ${ready} 项可确认` : ""}${warnings.length ? ` · ${warnings.length} 项警告：${warnings.join("；")}` : ""}`;
                feedback(message, data.status === "error" ? "danger" : warnings.length ? "warning" : "success");
            }
            return data;
        } catch (error) {
            feedback(error.message, "danger");
            scan = { ...scan, status: "error" }; scanProgress();
            return null;
        }
    }

    // 同一集的多个版本不能放在同一批，按季集拆成若干轮依次关联。
    function confirmBatches(item) {
        const positions = item.recognition?.positions || {}, rounds = [];
        for (const file of item.files) {
            const [season = "", episode = ""] = positions[file.path] || [];
            const key = item.recognition?.title?.media_type === "tv" ? `${season}:${episode}` : file.path;
            let round = rounds.find(entry => !entry.keys.has(key));
            if (!round) { round = { keys: new Set(), items: [] }; rounds.push(round); }
            round.keys.add(key);
            round.items.push({ path: file.path, season_number: season ?? "", episode_number: episode ?? "" });
        }
        return rounds.flatMap(round => Array.from({ length: Math.ceil(round.items.length / 100) }, (_, index) => round.items.slice(index * 100, index * 100 + 100)));
    }

    async function confirm(list) {
        if (confirming || !list.length) return;
        // 进度按视频数计算：多来源合并的一行包含多个记录，按视频数才与“已选”提示一致。
        confirming = { done: 0, total: list.reduce((sum, item) => sum + item.files.length, 0) };
        renderList();
        let videos = 0;
        const failed = [];
        for (const item of list) {
            const imported = new Set();
            try {
                for (const items of confirmBatches(item)) {
                    const data = await json(root.dataset.completeUrl, { title_id: item.recognition.title.title_id, source_name: item.name, items });
                    if (!data.valid) throw new Error(data.error || "需核对");
                    items.forEach(entry => imported.add(entry.path));
                    videos += data.imported;
                }
            } catch { failed.push(item); }
            records = records.flatMap(record => {
                if (record !== item) return [record];
                const remaining = record.files.filter(file => !imported.has(file.path));
                if (!remaining.length) return [];
                return [{ ...record, files: remaining, recognition: { ...record.recognition, status: "review" } }];
            });
            confirming.done += item.files.length;
            renderList();
        }
        confirming = null;
        renderList();
        feedback(`已关联 ${videos} 个视频${failed.length ? ` · ${failed.length} 项移至待核对` : ""}`, failed.length ? "warning" : "success");
    }

    async function recognizeAgain(item) {
        item.busy = true; renderList();
        try {
            const result = await json(root.dataset.recognizeUrl, { path: item.path });
            records = records.map(record => record.path === item.path ? { ...record, recognition: result, busy: false } : record);
            feedback(result.title ? `识别为 ${result.title.name}` : "仍未识别，可手动关联", result.title ? "success" : "warning");
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

    async function updateRoot(path, action) {
        try {
            await json(root.dataset.rootsUrl, { path, action });
        } catch (error) { feedback(error.message, "danger"); return; }
        bootstrap.Modal.getInstance(browser)?.hide();
        discover(true);
    }

    async function browse(path) {
        const crumbs = $("[data-root-crumbs]"), folders = $("[data-root-folders]"), choose = $("[data-root-choose]");
        choose.disabled = true;
        folders.replaceChildren(el("li", "root-folders-status", "读取中…"));
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
            if (target === "/") { node.textContent = ""; node.append(icon("hdd-network")); node.setAttribute("aria-label", "根目录"); }
            if (current) node.setAttribute("aria-current", "location");
            item.append(node); crumbs.append(item);
        };
        crumb("/", "/", !parts.length);
        parts.forEach((part, index) => crumb(part, `/${parts.slice(0, index + 1).join("/")}`, index === parts.length - 1));
        folders.replaceChildren();
        for (const folder of data.folders) {
            const item = el("li");
            const node = button("", () => browse(folder.path), "root-folder btn text-start");
            node.append(icon("folder2"), el("span", "text-truncate flex-grow-1", folder.name), icon("chevron-right", "text-body-secondary"));
            item.append(node); folders.append(item);
        }
        if (!data.folders.length) folders.append(el("li", "root-folders-status", "没有子目录"));
        const added = roots.some(item => !item.auto && item.path === data.path);
        choose.disabled = data.path === "/" || added;
        choose.textContent = added ? "已添加" : "添加此目录";
    }

    // ---- 单项匹配 --------------------------------------------------------

    function invalidate() {
        for (const file of files) file.serverError = "";
    }

    function suggestSeason(file) {
        const match = file.path.slice(0, -file.name.length).match(/(?:season\s*|\bS|第)(\d{1,3})(?:季|\b)/i);
        return match ? match[1] : $("[data-default-season]").value;
    }

    // 识别阶段已推测出季集时优先采用；用户改动匹配规则后改用规则结果。
    function applyRule(fromRule = true) {
        if (fromRule === true) useGuess = false;
        invalidate();
        for (const file of files) {
            file.match = rules.parse(file.name, blocks);
            if (!file.manual) {
                const guess = useGuess ? file.guess : null;
                file.season = guess?.[0] ?? file.match?.season ?? suggestSeason(file);
                file.episode = guess?.[1] ?? file.match?.episode ?? "";
            }
        }
        renderFiles();
    }

    function setPreset(value) {
        const control = $("[data-rule-preset]");
        control.dataset.value = String(value);
        control.textContent = value === "custom" ? "自定义" : rules.presets[Number(value)].name;
        $("[data-rule-presets]").querySelectorAll("button").forEach(node => {
            const active = node.dataset.value === String(value);
            node.classList.toggle("active", active);
            node.setAttribute("aria-pressed", String(active));
        });
    }

    function bindBlockDrag(chip) {
        const container = $("[data-rule-blocks]");
        let drag = null;
        const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
        const reorder = reference => {
            if (reference === chip || chip.nextSibling === reference) return;
            const positions = new Map([...container.children].map(node => [node, node.getBoundingClientRect()]));
            container.insertBefore(chip, reference);
            for (const node of container.children) {
                node.getAnimations().forEach(animation => animation.cancel());
                if (node === chip || reducedMotion()) continue;
                const before = positions.get(node), after = node.getBoundingClientRect();
                const dx = before.left - after.left, dy = before.top - after.top;
                if (dx || dy) node.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }], {
                    duration: 190, easing: "cubic-bezier(.2,.8,.2,1)",
                });
            }
        };
        const finish = event => {
            if (!drag || event.pointerId !== drag.id) return;
            const state = drag;
            const changed = state.moved && event.type === "pointerup";
            drag = null;
            container.removeEventListener("pointermove", move);
            container.removeEventListener("pointerup", finish);
            container.removeEventListener("pointercancel", finish);
            container.removeEventListener("lostpointercapture", finish);
            document.removeEventListener("keydown", cancel);
            if (container.hasPointerCapture(event.pointerId)) container.releasePointerCapture(event.pointerId);
            if (!changed) for (const node of state.order) container.append(node);
            if (changed) {
                blocks = [...container.children].map(node => blocks[Number(node.dataset.blockIndex)]);
                setPreset("custom");
                applyRule();
            }
            const target = chip.getBoundingClientRect();
            const cleanup = () => {
                state.ghost.remove();
                container.classList.remove("is-dragging");
                blockDragActive = false;
                renderBlocks();
                container.querySelector(`[data-block-index="${blocks.indexOf(state.block)}"]`)?.focus({ preventScroll: true });
            };
            if (reducedMotion()) cleanup();
            else state.ghost.animate([
                { transform: state.ghost.style.transform },
                { transform: `translate(${target.left - state.rect.left}px, ${target.top - state.rect.top}px) scale(1)` },
            ], { duration: 160, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" }).finished.then(cleanup, cleanup);
        };
        const cancel = event => {
            if (event.key === "Escape" && drag) {
                event.preventDefault();
                finish({ pointerId: drag.id, type: "cancel" });
            }
        };
        chip.addEventListener("pointerdown", event => {
            if (busy || blockDragActive || !event.isPrimary || event.button !== 0 || event.target.closest("input, button")) return;
            const rect = chip.getBoundingClientRect(), ghost = chip.cloneNode(true);
            ghost.classList.add("rule-block--floating");
            ghost.tabIndex = -1;
            ghost.inert = true;
            ghost.setAttribute("aria-hidden", "true");
            Object.assign(ghost.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, transform: "translate(0, -6px) scale(1.035)" });
            document.body.append(ghost);
            drag = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false, rect, ghost, order: [...container.children], block: blocks[Number(chip.dataset.blockIndex)] };
            blockDragActive = true;
            chip.classList.add("rule-block--placeholder");
            container.classList.add("is-dragging");
            container.setPointerCapture(event.pointerId);
            container.addEventListener("pointermove", move);
            container.addEventListener("pointerup", finish);
            container.addEventListener("pointercancel", finish);
            container.addEventListener("lostpointercapture", finish);
            document.addEventListener("keydown", cancel);
            chip.focus({ preventScroll: true });
        });
        const move = event => {
            if (!drag || event.pointerId !== drag.id) return;
            drag.ghost.style.transform = `translate(${event.clientX - drag.x}px, ${event.clientY - drag.y - 6}px) scale(1.035)`;
            if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 5) return;
            drag.moved = true;
            const bounds = container.getBoundingClientRect();
            const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
            const target = [...container.children].find(node => x >= node.offsetLeft && x <= node.offsetLeft + node.offsetWidth && y >= node.offsetTop && y <= node.offsetTop + node.offsetHeight);
            if (!target || target === chip) return;
            reorder(x < target.offsetLeft + target.offsetWidth / 2 ? target : target.nextSibling);
        };
        chip.addEventListener("keydown", event => {
            if (event.target !== chip || busy || blockDragActive || !event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            const index = Number(chip.dataset.blockIndex);
            const next = index + (event.key === "ArrowLeft" ? -1 : 1);
            if (next < 0 || next >= blocks.length) return;
            [blocks[index], blocks[next]] = [blocks[next], blocks[index]];
            setPreset("custom"); renderBlocks(); applyRule();
            container.children[next].focus();
        });
    }

    function renderBlocks() {
        const container = $("[data-rule-blocks]");
        container.replaceChildren();
        blocks.forEach((block, index) => {
            const chip = el("div", `rule-block d-inline-flex align-items-center gap-1 border p-1 ${block.type === "season" || block.type === "episode" ? `rule-${block.type}` : "bg-body-tertiary"}`);
            chip.dataset.blockIndex = index;
            chip.tabIndex = 0;
            chip.setAttribute("role", "group");
            chip.setAttribute("aria-label", `第 ${index + 1} 块：${rules.labels[block.type]}，可拖动排序`);
            chip.setAttribute("aria-keyshortcuts", "Alt+ArrowLeft Alt+ArrowRight");
            const grip = el("i", "bi bi-grip-vertical text-body-secondary");
            grip.setAttribute("aria-hidden", "true");
            chip.append(grip);
            chip.append(el("span", "small px-1", rules.labels[block.type]));
            if (block.type === "literal") {
                const input = el("input", "form-control form-control-sm rule-literal");
                input.value = block.value;
                input.maxLength = 64;
                input.setAttribute("aria-label", `第 ${index + 1} 块特定字符`);
                input.addEventListener("input", () => { block.value = input.value; setPreset("custom"); applyRule(); });
                chip.append(input);
            }
            const remove = button("×", () => { blocks.splice(index, 1); setPreset("custom"); renderBlocks(); applyRule(); }, "btn btn-sm p-1");
            remove.setAttribute("aria-label", `移除第 ${index + 1} 块`);
            chip.append(remove); container.append(chip);
            bindBlockDrag(chip);
        });
        $("[data-rule-palette]").querySelectorAll("button").forEach(node => { node.disabled = blocks.length >= 16; });
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
            file.issue = file.serverError ? ["需检查", file.serverError] : null;
            file.target = null;
            if (!file.included || !title || title.media_type !== "tv" || file.issue) continue;
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
                invalidate(); updateFileStates();
                file.nodes.target.focus();
            }, "dropdown-item small text-wrap");
            const active = Number(file.season) === episode.season_number && Number(file.episode) === episode.episode_number;
            option.classList.toggle("active", active);
            option.setAttribute("aria-pressed", String(active));
            const item = el("li"); item.append(option); menu.append(item);
        }
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
                target.append(icon("exclamation-triangle-fill", "file-issue-icon"), el("span", "text-truncate", file.issue[0]));
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
            $("[data-rule-summary]").textContent = useGuess ? "季集按识别结果填写" : `季集按「${$("[data-rule-preset]").textContent}」识别`;
        }
        renderTaskBar({ selected, issues });
    }

    function renderTaskBar({ selected, issues } = stats()) {
        const status = $("[data-task-status]"), action = $("[data-task-complete]");
        status.replaceChildren();
        status.className = "association-bar-status";
        const say = (symbol, text) => { if (symbol === "spinner") status.append(spinner()); else if (symbol) status.append(icon(symbol)); status.append(el("span", "text-truncate", text)); };
        if (progress) {
            say("spinner", `关联中 ${progress.done}/${progress.total}`);
            action.textContent = "关联中…"; action.disabled = true;
            return;
        }
        if (!title) say(busy ? "spinner" : "", busy ? "读取作品信息…" : "先选择对应的电影或剧集");
        else if (!selected) say("", "未选择视频");
        else if (issues) {
            status.classList.add("is-warning");
            say("exclamation-triangle-fill", `${issues} 个文件需处理`);
            if (!onlyErrors) status.append(button("查看", locateIssue, "btn btn-link association-bar-link"));
        } else say("check2-circle", title.media_type === "tv" ? `${selected} 个视频全部对上` : `${selected} 个视频 → ${title.name}`);
        action.textContent = selected ? `关联 ${selected} 个` : "关联";
        action.disabled = busy || !title || !selected || issues > 0;
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
            check.addEventListener("change", () => { file.included = check.checked; invalidate(); updateFileStates(); });
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
                input.addEventListener("input", () => { file[field] = input.value; file.manual = true; invalidate(); updateFileStates(); });
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
            busy = false;
            renderBlocks(); applyRule(false);
            // 有文件没能读出集号时，才展开规则编辑器。
            setRuleOpen(title.media_type === "tv" && files.some(file => file.included && !validNumber(file.episode, 1)));
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
        const type = title?.media_type || task.recognition?.media_type || (task.suggestion || files.some(file => /S\d+E\d+|\d+x\d+|第\d+集|EP\d+/i.test(file.name)) || files.length > 1 ? "tv" : "movie");
        picker.querySelector(`[name="search_type"][value="${type}"]`).checked = true;
        bootstrap.Modal.getOrCreateInstance(picker).show();
    }

    function showView(view) {
        show(panel, view === "list");
        show(editor, view === "editor");
        if (manual) show(manual, view === "manual");
        syncBar();
    }

    function openTask(item, search = false) {
        if (!editor.classList.contains("d-none")) return;
        listScroll = window.scrollY;
        taskVersion++;
        task = item; title = null; busy = false; useGuess = false; onlyErrors = false; progress = null;
        const positions = item.recognition?.positions || {};
        files = [...item.files].sort((a, b) => a.path.localeCompare(b.path, "zh-CN", { numeric: true })).map(file => ({ ...file, included: true, season: "", episode: "", manual: false, guess: positions[file.path] || null }));
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
        const found = item.recognition?.title || item.suggestion;
        if (found && !search) chooseTitle(found);
        else pickTitle();
        window.scrollTo({ top: 0 });
    }

    function closeEditor() {
        taskVersion++; waitingForTitle = false; busy = false; progress = null;
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
        const chunks = [];
        for (let index = 0; index < chosen.length; index += 100) chunks.push(chosen.slice(index, index + 100));
        progress = { done: 0, total: chosen.length };
        lockEditor(true);
        renderTaskBar();
        let imported = 0, failed = false;
        try {
            for (const chunk of chunks) {
                const payload = { title_id: title.title_id, source_name: task.name, items: chunk.map(file => ({ path: file.path, season_number: file.season, episode_number: file.episode })) };
                const data = await json(root.dataset.completeUrl, payload);
                for (const row of data.rows || []) {
                    const file = files.find(item => item.path === row.path);
                    if (file) file.serverError = row.error || "";
                }
                if (!data.valid) {
                    failed = true;
                    feedback(data.error || "有文件需要处理，已在列表中标出。", "warning");
                    break;
                }
                const done = new Set(chunk.map(file => file.path));
                files = files.filter(file => !done.has(file.path));
                records = records.flatMap(record => {
                    const remaining = record.files.filter(file => !done.has(file.path));
                    return remaining.length ? [{ ...record, files: remaining }] : [];
                });
                imported += data.imported;
                progress.done += chunk.length;
                renderTaskBar();
            }
        } catch (error) { failed = true; feedback(error.message, "danger"); }
        progress = null;
        if (imported && !files.length) {
            busy = false;
            feedback(`已关联 ${imported} 个视频 · ${title.name}`, "success");
            closeEditor();
            return;
        }
        if (imported) {
            task = records.find(record => record.path === task.path) || task;
            feedback(`已关联 ${imported} 个视频，剩余 ${files.length} 个`, failed ? "warning" : "success");
        }
        lockEditor(false);
    }

    // ---- 事件 ------------------------------------------------------------

    [...rules.presets, { name: "自定义" }].forEach((preset, i) => {
        const value = i === rules.presets.length ? "custom" : String(i);
        const item = el("li");
        const option = button(preset.name, () => {
            if (busy || blockDragActive) return;
            setPreset(value);
            if (value !== "custom") {
                blocks = structuredClone(rules.presets[i].blocks); renderBlocks(); applyRule();
            }
        }, "dropdown-item");
        option.dataset.value = value;
        item.append(option); $("[data-rule-presets]").append(item);
    });
    setPreset(0);
    for (const [type, label] of Object.entries(rules.labels)) {
        const add = button("", () => {
            if (blocks.length >= 16 || blockDragActive) return;
            blocks.push({ type, value: "" }); setPreset("custom");
            renderBlocks(); applyRule();
        }, "btn btn-sm chip-btn rule-palette-btn");
        add.append(icon("plus"), document.createTextNode(label));
        $("[data-rule-palette]").append(add);
    }
    $("[data-default-season]").addEventListener("input", () => applyRule());
    $("[data-rule-toggle]").addEventListener("click", () => setRuleOpen(!ruleOpen));
    $("[data-rule-reset]").addEventListener("click", () => { files.forEach(file => { file.manual = false; }); applyRule(false); });
    $("[data-files-all]").addEventListener("change", event => { files.forEach(file => { file.included = event.target.checked; }); invalidate(); renderFiles(); });
    $("[data-files-errors]").addEventListener("click", () => { onlyErrors = !onlyErrors; updateFileStates(); });
    $("[data-task-pick]").addEventListener("click", pickTitle);
    $("[data-task-close]").addEventListener("click", closeEditor);
    $("[data-task-complete]").addEventListener("click", submit);
    picker.addEventListener("title:selected", event => {
        if (!waitingForTitle) return;
        event.preventDefault(); waitingForTitle = false; chooseTitle(event.detail);
    });
    picker.addEventListener("hidden.bs.modal", () => { waitingForTitle = false; });
    $("[data-discovery-refresh]").addEventListener("click", () => discover(true, true));
    $("[data-root-add]").addEventListener("click", () => {
        const first = roots.find(item => !item.auto) || roots[0];
        bootstrap.Modal.getOrCreateInstance(browser).show();
        browse(first ? first.path.split("/").slice(0, -1).join("/") || "/" : "/");
    });
    $("[data-root-choose]").addEventListener("click", () => updateRoot(browser.dataset.path, "add"));
    $("[data-discovery-filter]").addEventListener("input", renderList);
    $("[data-manual-open]").addEventListener("click", () => { showView("manual"); window.scrollTo({ top: 0 }); });
    $("[data-manual-upload]").addEventListener("click", () => { showView("manual"); document.getElementById("batch-json-file")?.click(); });
    document.querySelector("[data-manual-close]")?.addEventListener("click", () => { showView("list"); renderList(); });
    window.addEventListener("beforeunload", event => { if (busy || confirming) { event.preventDefault(); event.returnValue = ""; } });
    // 打开页面先显示已有结果（启动时的后台扫描或上次快照），没有结果时才开始扫描。
    discover().then(data => { if (data && (data.status === "idle" || data.restored)) discover(true); });
})();
