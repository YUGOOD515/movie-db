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
    const button = (text, action, classes = "btn btn-sm btn-outline-secondary") => {
        const node = el("button", classes, text);
        node.type = "button";
        node.addEventListener("click", action);
        return node;
    };
    const rules = window.FilenameRules;
    const picker = document.getElementById("title-picker");
    const correction = document.getElementById("episode-correction");
    let records = [], files = [], title = null, task = null, blocks = [], waitingForTitle = false;
    let busy = false, reviewed = false, taskVersion = 0, pollTimer = null;
    let discoveryNotice = "";
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

    function discoveryRows() {
        const query = $("[data-discovery-filter]").value.trim().toLowerCase();
        const visible = records.filter(item => `${item.name} ${item.path}`.toLowerCase().includes(query));
        const rows = $("[data-discovery-rows]");
        rows.replaceChildren();
        for (const item of visible) {
            const row = el("tr");
            const name = el("td", "ps-3");
            const heading = el("div", "d-flex align-items-center gap-2");
            heading.append(el("i", `bi ${item.kind === "folder" ? "bi-folder2" : "bi-file-earmark-play"} text-body-secondary`));
            heading.append(button(item.name, () => openTask(item), "btn btn-link text-body text-start text-break p-0 text-decoration-none"));
            name.append(heading);
            if (item.kind === "update") name.append(el("div", "small text-body-secondary mt-1", `待更新${item.suggestion ? ` · ${item.suggestion.name}` : ""}`));
            const date = el("td", "small text-body-secondary text-nowrap");
            date.textContent = item.added ? new Date(item.added).toLocaleDateString("zh-CN") : "时间未知";
            date.title = item.time_source === "modified" ? "AList 未提供新增时间，使用修改时间" : item.added;
            const action = el("td", "text-end pe-3");
            action.append(button("关联", () => openTask(item)));
            row.append(name, el("td", "small text-nowrap", String(item.files.length)), date, action);
            rows.append(row);
        }
        $("[data-discovery-count]").textContent = `${records.length} 项`;
        $("[data-discovery-empty]").textContent = query ? "没有匹配的内容" : "没有待关联的视频";
        show($("[data-discovery-empty]"), !visible.length);
    }

    function scanProgress(data = null) {
        const refresh = $("[data-discovery-refresh]");
        const running = data?.status === "running";
        refresh.disabled = running;
        refresh.classList.toggle("is-scanning", running);
        refresh.setAttribute("aria-busy", String(running));
        document.body.classList.toggle("discovery-progress-active", running);
        let toast = document.querySelector("[data-discovery-progress-toast]");
        if (!running) { toast?.classList.remove("rating-progress-toast--show"); return; }
        if (!toast) {
            toast = el("div", "rating-progress-toast");
            toast.dataset.discoveryProgressToast = "";
            toast.setAttribute("role", "status"); toast.setAttribute("aria-live", "polite");
            const header = el("div", "d-flex align-items-center justify-content-between gap-3 mb-2");
            header.append(el("span", "small fw-semibold", "正在扫描 AList"));
            const count = el("span", "small text-body-secondary"); count.dataset.scanCount = "";
            header.append(count);
            const progress = el("div", "progress");
            progress.setAttribute("role", "progressbar"); progress.setAttribute("aria-label", "AList 扫描进度");
            progress.setAttribute("aria-valuemin", "0"); progress.setAttribute("aria-valuemax", "100");
            progress.append(el("div", "progress-bar"));
            toast.append(header, progress); document.body.append(toast);
        }
        const total = Number(data.total) || 0, done = Number(data.done) || 0;
        const percent = total > 0 ? Math.max(0, Math.min(100, Math.round(done / total * 100))) : 0;
        toast.querySelector("[data-scan-count]").textContent = `${percent}%`;
        const bar = toast.querySelector(".progress-bar");
        bar.style.width = `${percent}%`;
        const progress = bar.parentElement;
        progress.setAttribute("aria-valuenow", String(percent));
        toast.classList.add("rating-progress-toast--show");
    }

    async function discover(start = false, refresh = false) {
        clearTimeout(pollTimer);
        if (refresh) discoveryNotice = "";
        if (start) scanProgress({ status: "running" });
        try {
            const data = await json(root.dataset.discoveryUrl + (refresh ? "?refresh=1" : ""), start ? {} : undefined);
            records = data.records;
            discoveryRows();
            $("[data-discovery-root]").textContent = data.root || "来自：分享";
            if (data.status === "running") {
                scanProgress(data);
                pollTimer = setTimeout(() => discover(), 1500);
            } else {
                scanProgress();
                const notice = JSON.stringify([data.status, data.records, data.error, data.warnings]);
                if (notice !== discoveryNotice) {
                    discoveryNotice = notice;
                    const warnings = data.warnings || [];
                    const message = data.status === "error" ? data.error || "AList 扫描失败，请重试。"
                        : `扫描完成 · ${records.length} 项待关联内容${warnings.length ? ` · ${warnings.length} 项扫描警告：${warnings.join("；")}` : ""}`;
                    feedback(message, data.status === "error" ? "danger" : warnings.length ? "warning" : "success");
                }
            }
        } catch (error) {
            feedback(error.message, "danger");
            scanProgress();
        }
    }

    function invalidate() {
        reviewed = false;
        for (const file of files) file.serverError = "";
        show($("[data-task-complete]"), false);
        show($("[data-task-edit]"), false);
        show($("[data-task-check]"), true);
    }

    function suggestSeason(file) {
        const match = file.path.slice(0, -file.name.length).match(/(?:season\s*|\bS|第)(\d{1,3})(?:季|\b)/i);
        return match ? match[1] : $("[data-default-season]").value;
    }

    function applyRule() {
        invalidate();
        for (const file of files) {
            file.match = rules.parse(file.name, blocks);
            if (!file.manual) {
                file.season = file.match?.season ?? suggestSeason(file);
                file.episode = file.match?.episode ?? "";
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
            if (busy || reviewed || blockDragActive || !event.isPrimary || event.button !== 0 || event.target.closest("input, button")) return;
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
            if (event.target !== chip || busy || reviewed || blockDragActive || !event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
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

    function computeErrors() {
        const positions = episodeMap(), counts = new Map();
        for (const file of files.filter(f => f.included)) {
            const key = `${Number(file.season)}:${Number(file.episode)}`;
            counts.set(key, (counts.get(key) || 0) + 1);
        }
        for (const file of files) {
            file.error = file.serverError || "";
            file.target = null;
            if (!file.included || !title || title.media_type !== "tv" || file.error) continue;
            const key = `${Number(file.season)}:${Number(file.episode)}`;
            if (!validNumber(file.season, 0) || !validNumber(file.episode, 1)) file.error = "待填写季集";
            else if (!positions.get(key)) file.error = "IMDb 无唯一对应季集";
            else if (counts.get(key) > 1) file.error = "重复季集";
            else file.target = positions.get(key);
        }
    }

    function targetSelector(file) {
        const dropdown = el("div", "dropdown");
        const control = el("button", "btn btn-sm btn-outline-secondary dropdown-toggle w-100 text-start text-truncate", "选择 IMDb 季集…");
        control.type = "button";
        control.id = "correction-target";
        control.dataset.bsToggle = "dropdown";
        control.setAttribute("aria-expanded", "false");
        control.setAttribute("aria-label", `选择 ${file.name} 的目标季集`);
        const menu = el("ul", "dropdown-menu squircle-card p-2 w-100");
        menu.dataset.correctionOptions = "";
        menu.setAttribute("aria-labelledby", control.id);
        let season = null;
        for (const episode of title.episodes) {
            if (episode.season_number == null || episode.episode_number == null) continue;
            if (season !== episode.season_number) {
                season = episode.season_number;
                const group = el("li");
                group.append(el("h3", "dropdown-header small mb-0", `第 ${season} 季`));
                menu.append(group);
            }
            const name = `E${episode.episode_number} · ${episode.name}`;
            const option = button(name, () => {
                file.season = episode.season_number; file.episode = episode.episode_number;
                file.manual = true;
                bootstrap.Dropdown.getOrCreateInstance(control).hide();
                invalidate(); renderFiles();
                bootstrap.Modal.getOrCreateInstance(correction).hide();
            }, "dropdown-item small text-wrap");
            const active = Number(file.season) === episode.season_number && Number(file.episode) === episode.episode_number;
            option.classList.toggle("active", active);
            option.setAttribute("aria-pressed", String(active));
            option.dataset.position = `${episode.season_number}:${episode.episode_number}`;
            if (active) control.textContent = `第 ${episode.season_number} 季 · ${name}`;
            const item = el("li"); item.append(option); menu.append(item);
        }
        dropdown.append(control, menu);
        return dropdown;
    }

    function correctTarget(file) {
        if (busy || reviewed || title?.media_type !== "tv") return;
        $("[data-correction-name]").textContent = file.name;
        $("[data-correction-reason]").textContent = file.error;
        const dropdown = targetSelector(file);
        dropdown.querySelector("button").disabled = true;
        $("[data-correction-target]").replaceChildren(dropdown);
        bootstrap.Modal.getOrCreateInstance(correction).show();
    }

    function locateIssue() {
        $("[data-files-errors]").checked = true;
        updateFileStates();
        const file = files.find(item => item.included && item.error);
        if (!file?.nodes) return;
        file.nodes.row.scrollIntoView({ block: "center" });
        (file.nodes.status.querySelector("button") || file.nodes.season).focus({ preventScroll: true });
    }

    function updateFileStates() {
        computeErrors();
        const onlyErrors = $("[data-files-errors]").checked;
        let issues = 0, selected = 0;
        for (const file of files) {
            if (file.included) { selected++; if (file.error) issues++; }
            const refs = file.nodes;
            if (!refs) continue;
            show(refs.row, !onlyErrors || !!file.error && file.included);
            refs.status.replaceChildren();
            const statusLine = el("div", "d-flex align-items-center gap-2");
            if (!file.included) statusLine.append(el("span", "text-body-secondary", "未选择"));
            else if (file.error) {
                const label = file.serverError ? "需检查" : "未匹配";
                const status = title?.media_type === "tv" && !reviewed
                    ? button(label, () => correctTarget(file), "btn btn-link text-body-secondary text-start text-truncate file-status-label p-0")
                    : el("span", "text-body-secondary", label);
                status.title = file.error;
                status.setAttribute("aria-label", `${file.name}：${file.error}`);
                status.disabled = busy;
                statusLine.append(status);
            } else {
                const name = title ? (file.target?.name || title.name) : "待匹配作品";
                const status = el("span", "text-body-secondary d-block text-truncate file-status-label", name);
                status.title = name;
                statusLine.append(status);
            }
            if (file.manual) {
                const icon = el("i", "bi bi-pencil text-body-secondary flex-shrink-0");
                icon.title = "已人工修正";
                icon.setAttribute("role", "img"); icon.setAttribute("aria-label", "已人工修正");
                statusLine.append(icon);
            }
            refs.status.append(statusLine);
            for (const input of [refs.season, refs.episode]) {
                input.setAttribute("aria-invalid", String(!!file.error && file.included));
            }
        }
        $("[data-files-count]").textContent = `已选 ${selected} / ${files.length}${issues ? ` · ${issues} 项需检查` : ""}${selected > 100 ? " · 单批最多 100 个视频" : ""}`;
        $("[data-files-all]").checked = selected === files.length;
        $("[data-files-all]").indeterminate = selected > 0 && selected < files.length;
        $("[data-task-check]").disabled = busy || !title || !selected || selected > 100 || issues > 0;
        show($("[data-rule-reset]"), files.some(f => f.manual) && !reviewed);
        const message = issues && !busy && !$("[data-association-editor]").classList.contains("d-none")
            ? `${issues} 个文件需检查，请${title?.media_type === "tv" ? "修正季集" : "检查文件"}或取消选择。` : "";
        window.setAppToastStatus?.(message, "warning", { label: "查看", run: locateIssue });
    }

    function renderFiles() {
        const table = $("[data-task-files]"); table.replaceChildren();
        const series = title?.media_type === "tv";
        root.querySelectorAll("[data-position-heading]").forEach(node => show(node, series));
        for (const file of files) {
            const row = el("tr"), checkCell = el("td"), name = el("td", "file-name");
            const check = el("input", "form-check-input"); check.type = "checkbox"; check.checked = file.included;
            check.disabled = reviewed || busy;
            check.setAttribute("aria-label", `选择 ${file.name}`);
            check.addEventListener("change", () => { file.included = check.checked; invalidate(); updateFileStates(); });
            checkCell.append(check);
            const filename = el("div", "text-break");
            if (series && file.match) for (const span of file.match.spans) filename.append(el(["season", "episode"].includes(span.type) ? "mark" : "span", ["season", "episode"].includes(span.type) ? `rule-${span.type}` : "", span.text));
            else filename.textContent = file.name;
            name.append(filename);
            const relative = file.path.slice(0, -file.name.length).replace(`${task.path}/`, "");
            if (relative && task.kind === "folder") name.append(el("div", "small text-body-secondary text-break mt-1", relative));
            const inputs = {};
            row.append(checkCell, name);
            for (const field of ["season", "episode"]) {
                const cell = el("td"), input = el("input", "form-control form-control-sm position-input bg-body-tertiary");
                input.type = "number"; input.min = field === "season" ? "0" : "1"; input.step = "1";
                input.value = file[field] ?? "";
                input.disabled = reviewed || busy;
                input.setAttribute("aria-label", `${file.name} ${field === "season" ? "季号" : "集号"}`);
                input.addEventListener("input", () => { file[field] = input.value; file.manual = true; invalidate(); updateFileStates(); });
                inputs[field] = input; cell.append(input); show(cell, series); row.append(cell);
            }
            const status = el("td", "small file-status"); row.append(status); table.append(row);
            file.nodes = { row, status, ...inputs };
        }
        updateFileStates();
    }

    function showAvailable() {
        const container = $("[data-available-episodes]"); container.replaceChildren();
        const episodes = (title?.episodes || []).filter(item => item.season_number != null);
        const seasons = [...new Set(episodes.map(item => item.season_number))].sort((a, b) => a - b);
        for (const season of seasons) {
            const column = el("section", "available-season flex-shrink-0");
            const heading = el("h5", "small fw-semibold mb-2", `第 ${season} 季`);
            const viewport = el("div", "available-episode-window");
            const list = el("ol", "available-episode-list list-unstyled mb-0 text-body-secondary");
            list.tabIndex = 0;
            list.setAttribute("aria-label", `第 ${season} 季全部集名`);
            for (const episode of episodes.filter(item => item.season_number === season).sort((a, b) => a.episode_number - b.episode_number)) {
                list.append(el("li", "py-1 text-break", `E${episode.episode_number ?? "?"} · ${episode.name}`));
            }
            viewport.append(list);
            column.append(heading, viewport); container.append(column);
        }
        show($("[data-available-panel]"), title?.media_type === "tv");
        if (title?.media_type === "tv" && !episodes.length) container.append(el("p", "text-body-secondary mb-0", "IMDb 暂无可用季集"));
    }

    async function chooseTitle(selection) {
        const version = ++taskVersion;
        busy = true; invalidate(); updateFileStates();
        $("[data-task-pick]").disabled = true;
        try {
            const data = await json(`${root.dataset.metadataUrl}?${new URLSearchParams({ title_id: selection.title_id })}`);
            if (version !== taskVersion) return;
            title = data;
            $("[data-task-title]").textContent = title.name;
            $("[data-task-title-meta]").textContent = [title.year, title.media_type === "tv" ? "剧集" : "电影", title.title_id].filter(Boolean).join(" · ");
            show($("[data-rule-panel]"), title.media_type === "tv");
            const seasons = [...new Set(title.episodes.map(item => item.season_number).filter(x => x !== null))].sort((a, b) => a - b);
            $("[data-default-season]").value = selection.season_number ?? (seasons.length === 1 ? seasons[0] : "");
            showAvailable();
            files.forEach(file => { file.manual = false; });
            let best = 0, score = -1;
            rules.presets.forEach((preset, index) => {
                const matches = files.filter(file => rules.parse(file.name, preset.blocks)).length;
                if (matches > score) { score = matches; best = index; }
            });
            setPreset(best);
            blocks = structuredClone(rules.presets[best].blocks);
            renderBlocks(); applyRule();
        } catch (error) {
            if (version === taskVersion) feedback(error.message, "danger");
        } finally {
            if (version === taskVersion) { busy = false; $("[data-task-pick]").disabled = false; renderFiles(); }
        }
    }

    function pickTitle() {
        waitingForTitle = true;
        document.getElementById("title-picker-heading").textContent = "重新搜索匹配";
        const query = picker.querySelector("[data-title-search-query]");
        query.value = task.name.replace(/\.[a-z0-9]{2,5}$/i, "").replace(/[._]/g, " ").slice(0, 100);
        query.dispatchEvent(new Event("input"));
        const type = title?.media_type || (task.suggestion || files.some(file => /S\d+E\d+|\d+x\d+|第\d+集|EP\d+/i.test(file.name)) || files.length > 1 ? "tv" : "movie");
        picker.querySelector(`[name="search_type"][value="${type}"]`).checked = true;
        bootstrap.Modal.getOrCreateInstance(picker).show();
    }

    function openTask(item) {
        taskVersion++;
        task = item; title = null; busy = false;
        files = [...item.files].sort((a, b) => a.path.localeCompare(b.path, "zh-CN", { numeric: true })).map(file => ({ ...file, included: true, season: "", episode: "", manual: false }));
        $("[data-task-name]").textContent = item.name;
        $("[data-task-path]").textContent = item.kind === "update" ? "已有内容更新" : `${files.length} 个视频`;
        $("[data-task-title]").textContent = "选择电影或剧集";
        $("[data-task-title-meta]").textContent = "";
        $("[data-task-pick]").disabled = false;
        $("[data-files-all]").disabled = false;
        $("[data-files-errors]").checked = false;
        show($("[data-rule-panel]"), false);
        show($("[data-available-panel]"), false);
        show($("[data-discovery-panel]"), false); show($("[data-association-editor]"), true);
        invalidate(); renderFiles();
        if (item.suggestion) chooseTitle(item.suggestion);
        else pickTitle();
    }

    function lockEditor(value) {
        busy = value;
        root.querySelectorAll("[data-association-editor] button, [data-association-editor] input, [data-association-editor] select").forEach(node => { node.disabled = value; });
        if (!value) { renderBlocks(); renderFiles(); }
    }

    async function submit(complete = false) {
        if (busy || !title || complete && !reviewed) return;
        const payload = { title_id: title.title_id, items: files.filter(file => file.included).map(file => ({ path: file.path, season_number: file.season, episode_number: file.episode })) };
        lockEditor(true);
        const action = $(complete ? "[data-task-complete]" : "[data-task-check]");
        action.textContent = complete ? "关联中…" : "检查中…";
        try {
            const data = await json(complete ? root.dataset.completeUrl : root.dataset.checkUrl, payload);
            for (const row of data.rows || []) {
                const file = files.find(item => item.path === row.path);
                if (file) file.serverError = row.error;
            }
            if (!data.valid) {
                reviewed = false;
                feedback(data.error || "请修正标记的记录后重新检查。", complete ? "danger" : "warning");
            } else if (complete) {
                const imported = new Set(payload.items.map(item => item.path));
                files = files.filter(file => !imported.has(file.path));
                records = records.flatMap(record => {
                    const remaining = record.files.filter(file => !imported.has(file.path));
                    return remaining.length ? [{ ...record, files: remaining }] : [];
                });
                reviewed = false;
                feedback(`已关联 ${data.imported} 个视频${files.length ? `，剩余 ${files.length} 个视频可继续处理。` : "。"}`, "success");
                discoveryRows();
            } else {
                reviewed = true;
                feedback(`检查通过 · ${payload.items.length} 个视频 → ${title.name}`, "success");
            }
        } catch (error) { feedback(error.message, "danger"); }
        finally {
            action.textContent = complete ? "完成关联" : "检查结果";
            lockEditor(false);
            show($("[data-task-check]"), !reviewed);
            show($("[data-task-complete]"), reviewed);
            show($("[data-task-edit]"), reviewed);
            show($("[data-rule-panel]"), title?.media_type === "tv" && !reviewed);
            $("[data-task-pick]").disabled = reviewed;
            $("[data-files-all]").disabled = reviewed;
        }
    }

    [...rules.presets, { name: "自定义" }].forEach((preset, i) => {
        const value = i === rules.presets.length ? "custom" : String(i);
        const item = el("li");
        const option = button(preset.name, () => {
            if (busy || reviewed || blockDragActive) return;
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
        $("[data-rule-palette]").append(button(`+ ${label}`, () => {
            if (blocks.length >= 16 || blockDragActive) return;
            blocks.push({ type, value: "" }); setPreset("custom");
            renderBlocks(); applyRule();
        }));
    }
    $("[data-default-season]").addEventListener("input", applyRule);
    $("[data-rule-reset]").addEventListener("click", () => { files.forEach(file => { file.manual = false; }); applyRule(); });
    $("[data-files-all]").addEventListener("change", event => { files.forEach(file => { file.included = event.target.checked; }); invalidate(); renderFiles(); });
    $("[data-files-errors]").addEventListener("change", updateFileStates);
    $("[data-task-pick]").addEventListener("click", pickTitle);
    $("[data-task-close]").addEventListener("click", () => {
        taskVersion++; waitingForTitle = false;
        window.setAppToastStatus?.();
        show($("[data-association-editor]"), false); show($("[data-discovery-panel]"), true);
        discover(true);
    });
    $("[data-task-check]").addEventListener("click", () => submit());
    $("[data-task-complete]").addEventListener("click", () => submit(true));
    $("[data-task-edit]").addEventListener("click", () => {
        invalidate(); show($("[data-rule-panel]"), title?.media_type === "tv");
        $("[data-task-pick]").disabled = false; $("[data-files-all]").disabled = false;
        renderFiles();
    });
    picker.addEventListener("title:selected", event => {
        if (!waitingForTitle) return;
        event.preventDefault(); waitingForTitle = false; chooseTitle(event.detail);
    });
    picker.addEventListener("hidden.bs.modal", () => { waitingForTitle = false; });
    correction.addEventListener("shown.bs.modal", () => {
        const control = $("#correction-target");
        control.disabled = false;
        control.focus();
    });
    correction.addEventListener("hidden.bs.modal", () => bootstrap.Dropdown.getInstance($("#correction-target"))?.dispose());
    $("[data-discovery-refresh]").addEventListener("click", () => discover(true, true));
    $("[data-discovery-filter]").addEventListener("input", discoveryRows);
    window.addEventListener("beforeunload", event => { if (busy) { event.preventDefault(); event.returnValue = ""; } });
    discover(true);
})();
