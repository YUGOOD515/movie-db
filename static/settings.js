// 设置页：连接 / 元数据 / AI 三个分区按项记录修改，底部状态栏统一保存，保存后立即生效，少数项提示重启；
// 元数据页底部的“清除数据”单独执行：勾选要彻底删除的数据，在确认弹窗里输入“清除”后执行。
(() => {
    const root = document.querySelector("[data-settings]");
    if (!root) return;
    const form = root.querySelector("[data-settings-form]");
    const tabs = [...root.querySelectorAll("[data-tab]")];
    const sections = [...root.querySelectorAll("[data-section]")];
    const inputs = [...form.querySelectorAll("[data-setting]")];
    const aiSwitch = form.querySelector("#set-ai-enabled");
    const aiBody = form.querySelector("[data-ai-body]");
    const imdbUpdate = form.querySelector("[data-imdb-update]");
    const statusLive = root.querySelector("[data-status-live]");
    const SERVICE_GROUP = { alist: "alist", tmdb: "tmdb", llm: "llm", mdblist: "mdblist", ffprobe: "ffprobe", player: "player" };
    let state = null, baseline = {}, baselineAi = false, busy = false, leaving = false;
    const cleared = new Set();

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };
    const icon = name => window.appIcon(name);
    const valueOf = input => input.type === "checkbox" ? input.checked : input.value.trim();
    const groupOf = input => input.closest("[data-group]")?.dataset.group;

    async function request(url, body) {
        const response = await fetch(url, {
            method: body === undefined ? "GET" : "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        let data;
        try { data = await response.json(); } catch { data = { ok: false, message: `请求失败（HTTP ${response.status}）` }; }
        return data;
    }

    // ------------------------------------------------------------ 分区

    // IMDb 更新独立于设置保存，轮询不覆盖尚未保存的表单；状态文字由服务端统一生成。
    let imdbTimer = null, imdbEtag = null;
    function renderImdb(data) {
        if (!imdbUpdate || !data?.index) return;
        const index = data.index;
        imdbUpdate.disabled = index.running;
        renderStatus({imdb: data.status});
        if (index.error && !index.running) paintStatus("imdb", "fail", `${data.status.text} · ${index.error}`);
        clearTimeout(imdbTimer);
        imdbTimer = setTimeout(pollImdb, index.running ? 1000 : 15000);
    }
    async function pollImdb() {
        if (document.hidden) { imdbTimer = setTimeout(pollImdb, 15000); return; }
        try {
            const response = await fetch("/settings/imdb-episodes", {headers: imdbEtag ? {"If-None-Match": imdbEtag} : {}});
            if (response.status !== 304 && response.ok) {
                imdbEtag = response.headers.get("ETag");
                renderImdb(await response.json());
                return;
            }
        } catch {}
        imdbTimer = setTimeout(pollImdb, 5000);
    }
    imdbUpdate?.addEventListener("click", async () => {
        imdbUpdate.disabled = true;
        try {
            const result = await request("/settings/imdb-episodes", {});
            if (!result.ok) throw new Error(result.message || "无法开始更新");
            imdbEtag = null;
            renderImdb(result);
        } catch (error) {
            imdbUpdate.disabled = false;
            paintStatus("imdb", "fail", error.message || "无法开始更新，请稍后重试。");
        }
    });

    function showSection(id, { focus = false } = {}) {
        if (!sections.some(section => section.dataset.section === id)) id = sections[0].dataset.section;
        const changed = sections.some(section => !section.hidden && section.dataset.section !== id);
        sections.forEach(section => { section.hidden = section.dataset.section !== id; });
        if (changed) form.scrollTo({ top: 0 });
        tabs.forEach(tab => {
            const active = tab.dataset.tab === id;
            tab.classList.toggle("active", active);
            tab.setAttribute("aria-selected", String(active));
            tab.tabIndex = active ? 0 : -1;
            if (active && focus) tab.focus();
        });
        if (location.hash.slice(1) !== id) history.replaceState(null, "", `#${id}`);
    }

    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => showSection(tab.dataset.tab));
        tab.addEventListener("keydown", event => {
            const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 1, ArrowUp: -1 }[event.key];
            if (event.key === "Home" || event.key === "End") {
                event.preventDefault();
                showSection(tabs[event.key === "Home" ? 0 : tabs.length - 1].dataset.tab, { focus: true });
                return;
            }
            if (!step) return;
            event.preventDefault();
            showSection(tabs[(index + step + tabs.length) % tabs.length].dataset.tab, { focus: true });
        });
    });
    addEventListener("hashchange", () => showSection(location.hash.slice(1)));
    const desktopNav = matchMedia("(min-width: 992px)");
    const syncNav = () => root.querySelector('[role="tablist"]').setAttribute("aria-orientation", desktopNav.matches ? "vertical" : "horizontal");
    desktopNav.addEventListener("change", syncNav);
    syncNav();

    // ------------------------------------------------------------ 状态

    // 每个分组标题下只有一行状态：平时是服务端给的状态，检测时换成检测进度和结果，重新读取设置后恢复。
    function paintStatus(group, state, text, notes = []) {
        const node = root.querySelector(`[data-status="${group}"]`);
        if (!node) return;
        node.dataset.state = state;
        node.querySelector("[data-status-text]").textContent = text;
        node.parentElement.querySelector("[data-status-notes]").replaceChildren(...notes.map(note => el("p", null, note)));
    }

    function renderStatus(status) {
        for (const [group, info] of Object.entries(status || {})) {
            // 未配置、已关闭等情况后面带上这一组的用途；正常时只写状态
            const desc = root.querySelector(`[data-status="${group}"]`)?.dataset.desc;
            paintStatus(group, info.state, info.state !== "ok" && desc ? `${info.text} · ${desc}` : info.text);
        }
    }

    function showTest(service, result) {
        const group = SERVICE_GROUP[service];
        const title = root.querySelector(`#group-${group}`)?.textContent || "";
        if (result.pending) {
            paintStatus(group, "busy", "正在检测…");
            statusLive.textContent = `${title}：正在检测`;
            return;
        }
        const warnings = result.ok ? result.warnings || [] : [];
        const text = [result.message || (result.ok ? "正常" : "失败"), ...(result.ok ? result.details || [] : [])].join(" · ");
        paintStatus(group, result.ok ? (warnings.length ? "warn" : "ok") : "fail", text, warnings);
        statusLive.textContent = [`${title}：${text}`, ...warnings].join(" ");
    }

    function testPayload(service) {
        const data = {};
        const group = root.querySelector(`[data-group="${SERVICE_GROUP[service]}"]`);
        for (const input of group.querySelectorAll("[data-setting]")) data[input.name] = valueOf(input);
        return data;
    }

    async function runTest(service, button) {
        button.disabled = true;
        showTest(service, { pending: true });
        let result;
        try { result = await request(`/settings/test/${service}`, testPayload(service)); }
        catch { result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" }; }
        button.disabled = false;
        showTest(service, result);
    }

    root.querySelectorAll("[data-test]").forEach(button => button.addEventListener("click", () => runTest(button.dataset.test, button)));

    // ------------------------------------------------------------ 修改记录

    function changes() {
        const values = {};
        for (const input of inputs) {
            if (input.dataset.secret !== undefined) {
                if (cleared.has(input.name)) values[input.name] = null;
                else if (input.value.trim()) values[input.name] = input.value.trim();
                continue;
            }
            const value = valueOf(input);
            if (value !== baseline[input.name]) values[input.name] = value;
        }
        return values;
    }

    function dirtyCount() {
        return Object.keys(changes()).length + (aiSwitch && aiSwitch.checked !== baselineAi ? 1 : 0);
    }

    function refreshPending() {
        const count = dirtyCount();
        for (const input of inputs) {
            const row = input.closest("[data-row]");
            if (!row) continue;
            const dirty = input.dataset.secret !== undefined
                ? cleared.has(input.name) || Boolean(input.value.trim())
                : valueOf(input) !== baseline[input.name];
            row.classList.toggle("is-dirty", dirty);
        }
        if (!window.StatusBar) return;
        if (count) {
            StatusBar.pending("settings", {
                text: `${count} 项修改未保存`,
                discard: { run: discard, disabled: busy },
                save: { run: save, disabled: busy },
            });
        } else {
            StatusBar.clear("settings");
        }
    }

    function clearErrors() {
        root.querySelectorAll("[data-error]").forEach(node => { node.textContent = ""; });
        root.querySelectorAll(".is-invalid").forEach(node => node.classList.remove("is-invalid"));
    }

    function showErrors(errors) {
        let first = null;
        for (const [name, message] of Object.entries(errors || {})) {
            const input = form.elements.namedItem(name);
            const box = root.querySelector(`[data-error="${name}"]`);
            if (box) box.textContent = message;
            if (input) {
                input.classList.add("is-invalid");
                input.closest("details")?.setAttribute("open", "");
                first = first || input.uiToggle || input;  // 下拉选择聚焦到可见的按钮
            }
        }
        if (first) {
            showSection(first.closest("[data-section]").dataset.section);
            first.focus({ preventScroll: true });
            first.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        }
    }

    function syncSecret(input) {
        const button = input.parentElement.querySelector("[data-clear]");
        const saved = state && state.saved[input.name];
        if (cleared.has(input.name)) {
            input.value = "";
            input.disabled = true;
            input.placeholder = "保存后清除";
        } else {
            input.disabled = false;
            input.placeholder = saved ? "已保存，留空保持不变" : "";
        }
        if (button) {
            button.hidden = !saved;
            button.textContent = cleared.has(input.name) ? "撤销" : "清除";
        }
    }

    function syncAi() {
        if (!aiSwitch) return;
        aiBody.hidden = !aiSwitch.checked;
        aiBody.closest("[data-group]").querySelector('[data-test="llm"]').hidden = !aiSwitch.checked;
    }

    // ------------------------------------------------------------ AI 平台预设

    // 平台、接口地址与模型的联动见 static/ai-presets.js：选平台后自动拉取模型列表并填好默认模型
    let aiPresets = null;
    function setupPresets(presets) {
        const model = form.elements.namedItem("LLM_MODEL");
        const modelLabel = root.querySelector('[data-row="LLM_MODEL"] .ui-row-label');
        if (!aiPresets) aiPresets = window.AiPresets.attach({
            presets, model,
            select: root.querySelector("[data-preset]"), keyLink: root.querySelector("[data-key-link]"),
            base: form.elements.namedItem("LLM_BASE_URL"), key: form.elements.namedItem("LLM_API_KEY"),
            url: "/settings/test/llm-models",
            hasSavedKey: () => Boolean(state?.saved.LLM_API_KEY) && !cleared.has("LLM_API_KEY"),
            onChange: refreshPending,
            onPreset: preset => { root.querySelector('[data-row="LLM_RPM"]').hidden = preset?.rpm === false; },
            onStatus: text => {
                let hint = modelLabel.querySelector("[data-model-status]");
                if (!hint) { hint = el("p", "ui-row-hint"); hint.dataset.modelStatus = ""; modelLabel.append(hint); }
                hint.textContent = text;
                hint.hidden = !text;
            },
        });
        if (aiSwitch.checked) aiPresets.activate(); else aiPresets.sync();
    }

    // ------------------------------------------------------------ 读取与保存

    function fill(data) {
        state = data;
        for (const input of inputs) {
            if (input.dataset.secret !== undefined) { input.value = ""; syncSecret(input); continue; }
            const value = data.values[input.name];
            if (input.type === "checkbox") input.checked = Boolean(value);
            else input.value = value ?? "";
            baseline[input.name] = valueOf(input);
            if (input.tagName === "SELECT") window.syncSelect?.(input);
        }
        if (aiSwitch) { aiSwitch.checked = Boolean(data.ai_enabled); baselineAi = aiSwitch.checked; syncAi(); setupPresets(data.presets); }
        renderStatus(data.status);
        renderImdb({ index: data.imdb, status: data.status.imdb });
        refreshPending();
        renderRestart(data.pending_restart, data.restart_supported);
        renderReset(data.data);
    }

    async function load() {
        const data = await request("/settings/state");
        if (data.ok === false) { window.StatusBar?.result("settings-load", { tone: "danger", text: data.message, duration: 0 }); return; }
        cleared.clear();
        fill(data);
    }

    function discard() {
        clearErrors();
        cleared.clear();
        fill(state);
    }

    async function save() {
        if (busy) return;
        busy = true;
        clearErrors();
        const body = { values: changes() };
        if (aiSwitch && aiSwitch.checked !== baselineAi) body.ai_enabled = aiSwitch.checked;
        else if (aiSwitch && aiSwitch.checked && Object.keys(body.values).some(name => name.startsWith("LLM_"))) body.ai_enabled = true;
        StatusBar.progress("settings", { text: "正在保存…" });
        let result;
        try { result = await request("/settings/save", body); }
        catch { result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" }; }
        busy = false;
        if (!result.ok) {
            refreshPending();
            showErrors(result.errors);
            StatusBar.result("settings-saved", { tone: "danger", text: result.message || "保存失败" });
            return;
        }
        await load();
        const changed = result.changed || [];
        const restartOnly = changed.length && changed.every(name => (result.restart || []).includes(name));
        StatusBar.result("settings-saved", {
            tone: "success",
            text: changed.length ? (restartOnly ? "已保存" : "已保存，设置已生效") : "没有需要保存的修改",
        });
    }

    // ------------------------------------------------------------ 清除数据

    const reset = root.querySelector("[data-reset]");
    const resetBar = reset.querySelector("[data-reset-bar]");
    const resetParts = reset.querySelector("[data-reset-parts]");
    const resetOpen = reset.querySelector("[data-reset-open]");
    const resetModal = document.querySelector("[data-reset-modal]");
    const resetConfirm = resetModal.querySelector("[data-reset-confirm]");
    const resetSubmit = resetModal.querySelector("[data-reset-submit]");
    const resetResult = resetModal.querySelector("[data-reset-result]");
    let resetWord = "清除", resetBusy = false;

    const formatSize = bytes => {
        const units = ["B", "KB", "MB", "GB"];
        let value = bytes, unit = 0;
        while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
        return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
    };

    function renderReset(data) {
        if (!data) return;
        resetWord = data.confirm || resetWord;
        resetModal.querySelector("[data-reset-word]").textContent = resetWord;
        const checked = new Set(selectedParts());
        // 占用大的排在前面；存储条的色段与下方各行顺序一致
        const parts = [...data.parts].sort((a, b) => b.size - a.size);
        const total = parts.reduce((sum, part) => sum + part.size, 0);
        reset.querySelector("[data-reset-total]").textContent = `共 ${formatSize(total)}`;
        resetBar.replaceChildren(...parts.filter(part => part.size).map(part => {
            const segment = el("div", "progress");
            segment.dataset.hue = part.hue;
            segment.style.width = `${part.size / total * 100}%`;
            segment.append(el("div", "progress-bar"));
            return segment;
        }));
        resetParts.replaceChildren(...parts.map(part => {
            // 整行是一个 label：点任何位置都能勾选；包含什么放在悬停提示和确认弹窗里
            const row = el("label", "ui-row is-action storage-row");
            row.dataset.hue = part.hue;
            row.title = part.hint;
            const name = el("span", "ui-row-label");
            const input = el("input", "form-check-input");
            Object.assign(input, { type: "checkbox", value: part.id, checked: checked.has(part.id) });
            name.append(input, el("span", "storage-dot"), el("span", "ui-row-title", part.label));
            row.append(name, el("span", "ui-row-control", part.exists ? formatSize(part.size) : "暂无数据"));
            return row;
        }));
        syncReset();
    }

    function selectedParts() {
        return [...resetParts.querySelectorAll("input:checked")].map(input => input.value);
    }

    function syncReset() {
        resetOpen.disabled = !selectedParts().length;
        resetSubmit.disabled = resetBusy || resetConfirm.value.trim() !== resetWord;
    }

    function showResetResult(result) {
        resetResult.replaceChildren();
        resetResult.className = "settings-result";
        if (!result) return;
        if (result.pending) {
            const spinner = el("span", "spinner-border spinner-border-sm");
            spinner.setAttribute("aria-hidden", "true");
            resetResult.classList.add("is-pending");
            resetResult.append(spinner, el("span", null, result.message));
            return;
        }
        resetResult.classList.add("is-fail");
        const head = el("p", "settings-result-head");
        head.append(icon("circle-x"), el("strong", null, result.message));
        resetResult.append(head);
    }

    // 打开确认弹窗时列出勾选的内容；警告和确认词只在这里出现
    resetModal.addEventListener("show.bs.modal", () => {
        const selected = new Set(selectedParts());
        resetModal.querySelector("[data-reset-summary]").replaceChildren(...state.data.parts.filter(part => selected.has(part.id)).map(part => {
            const item = el("li");
            item.append(el("strong", null, part.label), el("span", null, part.hint));
            return item;
        }));
        resetModal.querySelector("[data-reset-cloud]").hidden = !(state.data.cloud && selected.has("library"));
        resetConfirm.value = "";
        showResetResult(null);
        syncReset();
    });
    resetModal.addEventListener("shown.bs.modal", () => resetConfirm.focus());

    async function clearData() {
        const parts = selectedParts();
        if (resetBusy || !parts.length) return;
        resetBusy = true;
        syncReset();
        const cloud = parts.includes("library") && state?.data?.cloud;
        showResetResult({ pending: true, message: cloud ? "正在清除数据并清空云端快照…" : "正在清除数据…" });
        let result;
        try { result = await request("/settings/clear", { parts, confirm: resetConfirm.value.trim() }); }
        catch { result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" }; }
        resetBusy = false;
        if (result.ok && result.next) {
            leaving = true;
            location.assign(result.next);
            return;
        }
        if (result.ok) {
            const cloudFailed = result.cloud === "failed" ? "云端快照没能清空，可稍后在云端控制台处理。" : "";
            resetParts.querySelectorAll("input").forEach(input => { input.checked = false; });
            bootstrap.Modal.getInstance(resetModal)?.hide();
            StatusBar.result("settings-reset", { tone: cloudFailed ? "warning" : "success", text: `已清除。${cloudFailed}` });
        } else {
            showResetResult({ message: result.message || "清除失败" });
        }
        await load().catch(() => {});
    }

    reset.addEventListener("change", syncReset);
    resetConfirm.addEventListener("input", syncReset);
    resetSubmit.addEventListener("click", clearData);
    resetConfirm.addEventListener("keydown", event => { if (event.key === "Enter" && !resetSubmit.disabled) clearData(); });

    // ------------------------------------------------------------ 重启

    function renderRestart(pending, supported) {
        if (!window.StatusBar) return;
        if (!pending || !pending.length) { StatusBar.clear("settings-restart"); return; }
        StatusBar.notice("settings-restart", {
            icon: "refresh-cw",
            text: "端口修改需要重启 MovieDB 后生效",
            action: supported ? { label: "立即重启", run: restart } : null,
            detail: supported ? "" : "请手动重启",
        });
    }

    async function waitFor(url, sameOrigin) {
        const deadline = Date.now() + 30000;
        await new Promise(resolve => setTimeout(resolve, 1500));
        while (Date.now() < deadline) {
            try {
                if (sameOrigin) {
                    const response = await fetch("/settings/state", { headers: { Accept: "application/json" }, cache: "no-store" });
                    if (response.ok) return true;
                } else {
                    await fetch(`${url}/static/style.css`, { mode: "no-cors", cache: "no-store" });
                    return true;
                }
            } catch {}
            await new Promise(resolve => setTimeout(resolve, 800));
        }
        return false;
    }

    async function restart() {
        if (dirtyCount() && !confirm("还有未保存的修改，重启后会丢失。仍要重启吗？")) return;
        StatusBar.clear("settings");
        StatusBar.progress("settings-restart", { text: "正在重启 MovieDB…" });
        const result = await request("/settings/restart", {}).catch(() => ({ ok: false, message: "MovieDB 没有响应。" }));
        if (!result.ok) {
            StatusBar.notice("settings-restart", { icon: "refresh-cw", text: result.message || "重启失败" });
            return;
        }
        const target = new URL(result.url);
        const sameOrigin = target.origin === location.origin;
        if (await waitFor(target.origin, sameOrigin)) {
            location.assign(`${target.origin}/settings${location.hash}`);
        } else {
            StatusBar.notice("settings-restart", { icon: "triangle-alert", text: "MovieDB 没有按时启动，请从开始菜单重新打开。" });
        }
    }

    // ------------------------------------------------------------ 事件

    form.addEventListener("input", refreshPending);
    form.addEventListener("change", refreshPending);
    form.addEventListener("submit", event => { event.preventDefault(); save(); });
    form.addEventListener("keydown", event => {
        if (event.key !== "Enter" || !event.target.matches("input:not([type=checkbox])")) return;
        event.preventDefault();
        if (dirtyCount()) save();
    });
    aiSwitch?.addEventListener("change", () => { syncAi(); if (aiSwitch.checked) setupPresets(state.presets); refreshPending(); });
    for (const button of root.querySelectorAll("[data-reveal]")) {
        button.addEventListener("click", () => {
            const input = button.parentElement.querySelector("input");
            const shown = input.type === "text";
            input.type = shown ? "password" : "text";
            button.replaceChildren(icon(shown ? 'eye' : 'eye-off'));
            button.setAttribute("aria-pressed", String(!shown));
            button.setAttribute("aria-label", button.getAttribute("aria-label").replace(/^(显示|隐藏)/, shown ? "显示" : "隐藏"));
            button.title = shown ? "显示" : "隐藏";
        });
    }
    for (const button of root.querySelectorAll("[data-clear]")) {
        button.addEventListener("click", () => {
            const input = button.parentElement.querySelector("input");
            if (cleared.has(input.name)) cleared.delete(input.name); else cleared.add(input.name);
            syncSecret(input);
            refreshPending();
        });
    }
    // MovieDB 窗口（pywebview）里，程序路径可以用系统的文件选择框选择
    const pickers = [...root.querySelectorAll("[data-pick-path]")];
    const enablePickers = () => {
        if (!window.pywebview?.api?.pick_path) return;
        for (const button of pickers) {
            button.hidden = false;
            button.parentElement.classList.add("has-picker");
        }
    };
    for (const button of pickers) {
        button.addEventListener("click", async () => {
            const input = button.parentElement.querySelector("input");
            const picked = await window.pywebview.api.pick_path(button.dataset.pickPath, input.value.trim());
            if (!picked) return;
            input.value = picked;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.focus();
        });
    }
    if (pickers.length) {
        enablePickers();
        addEventListener("pywebviewready", enablePickers);
    }
    addEventListener("beforeunload", event => { if (!leaving && dirtyCount()) event.preventDefault(); });

    showSection(location.hash.slice(1));
    load().catch(() => window.StatusBar?.result("settings-load", { tone: "danger", text: "无法读取当前设置，请刷新页面。", duration: 0 }));
})();
