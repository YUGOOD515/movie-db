// 设置页：连接 / 元数据 / AI 三个分区按项记录修改，底部状态栏统一保存，保存后立即生效，少数项提示重启；
// “数据”分区单独提交：勾选要彻底删除的数据，输入“清除”确认后执行。
(() => {
    const root = document.querySelector("[data-settings]");
    if (!root) return;
    const form = root.querySelector("[data-settings-form]");
    const tabs = [...root.querySelectorAll("[data-tab]")];
    const sections = [...root.querySelectorAll("[data-section]")];
    const inputs = [...form.querySelectorAll("[data-setting]")];
    const aiSwitch = form.querySelector("#set-ai-enabled");
    const aiBody = form.querySelector("[data-ai-body]");
    const SERVICE_GROUP = { alist: "alist", tmdb: "tmdb", llm: "llm", mdblist: "mdblist", ffprobe: "ffprobe", player: "player" };
    let state = null, baseline = {}, baselineAi = false, busy = false, leaving = false;
    const cleared = new Set();

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };
    const icon = name => { const node = el("i", `bi bi-${name}`); node.setAttribute("aria-hidden", "true"); return node; };
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

    function showSection(id, { focus = false } = {}) {
        if (!sections.some(section => section.dataset.section === id)) id = sections[0].dataset.section;
        sections.forEach(section => { section.hidden = section.dataset.section !== id; });
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
            const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
            if (!step) return;
            event.preventDefault();
            showSection(tabs[(index + step + tabs.length) % tabs.length].dataset.tab, { focus: true });
        });
    });
    addEventListener("hashchange", () => showSection(location.hash.slice(1)));

    // ------------------------------------------------------------ 状态

    function renderStatus(status) {
        for (const [group, info] of Object.entries(status || {})) {
            const node = root.querySelector(`[data-status="${group}"]`);
            if (!node) continue;
            node.dataset.state = info.state;
            node.querySelector("[data-status-text]").textContent = info.text;
        }
    }

    function showResult(service, result) {
        const box = root.querySelector(`[data-result="${service}"]`);
        if (!box) return;
        box.replaceChildren();
        box.className = "settings-result";
        if (!result) return;
        if (result.pending) {
            const spinner = el("span", "spinner-border spinner-border-sm");
            spinner.setAttribute("aria-hidden", "true");
            box.classList.add("is-pending");
            box.append(spinner, el("span", null, "正在检测…"));
            return;
        }
        const warn = result.ok && result.warnings && result.warnings.length;
        box.classList.add(result.ok ? (warn ? "is-warn" : "is-ok") : "is-fail");
        const head = el("p", "settings-result-head");
        head.append(icon(result.ok ? (warn ? "exclamation-circle" : "check-circle-fill") : "x-circle-fill"),
            el("strong", null, result.message || (result.ok ? "正常" : "失败")));
        if (result.ok && result.details && result.details.length) head.append(el("span", null, result.details.join(" · ")));
        box.append(head);
        for (const text of result.ok ? result.warnings || [] : []) box.append(el("p", "settings-result-note", text));
    }

    function testPayload(service) {
        const data = {};
        const group = root.querySelector(`[data-group="${SERVICE_GROUP[service]}"]`);
        for (const input of group.querySelectorAll("[data-setting]")) data[input.name] = valueOf(input);
        return data;
    }

    async function runTest(service, button) {
        button.disabled = true;
        showResult(service, { pending: true });
        let result;
        try { result = await request(`/settings/test/${service}`, testPayload(service)); }
        catch { result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" }; }
        button.disabled = false;
        showResult(service, result);
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
                first = first || input;
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

    let presetsReady = false;
    function setupPresets(presets) {
        const select = root.querySelector("[data-preset]"), keyLink = root.querySelector("[data-key-link]");
        const base = form.elements.namedItem("LLM_BASE_URL"), model = form.elements.namedItem("LLM_MODEL");
        const rpmRow = root.querySelector('[data-row="LLM_RPM"]');
        if (!presetsReady) select.replaceChildren(...presets.map(preset => new Option(preset.name, preset.id)), new Option("其他（自定义）", ""));
        const match = presets.find(preset => preset.base_url === base.value || (preset.id === "deepseek" && /\/\/api\.deepseek\.com(\/v1)?\/?$/.test(base.value)));
        select.value = match ? match.id : base.value ? "" : presets[0].id;
        let previous = match || null;
        const apply = fill => {
            const preset = presets.find(item => item.id === select.value);
            keyLink.hidden = !preset;
            if (preset) keyLink.href = preset.key_url;
            rpmRow.hidden = preset?.rpm === false;
            model.placeholder = preset ? preset.model : "";
            if (!fill || !preset) return;
            base.value = preset.base_url;
            if (!model.value || (previous && model.value === previous.model)) model.value = preset.model;
            previous = preset;
            refreshPending();
        };
        if (!presetsReady) select.addEventListener("change", () => apply(true));
        presetsReady = true;
        apply(!base.value && aiSwitch.checked);
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
        }
        if (aiSwitch) { aiSwitch.checked = Boolean(data.ai_enabled); baselineAi = aiSwitch.checked; syncAi(); setupPresets(data.presets); }
        renderStatus(data.status);
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
    const resetParts = reset.querySelector("[data-reset-parts]");
    const resetConfirm = reset.querySelector("[data-reset-confirm]");
    const resetSubmit = reset.querySelector("[data-reset-submit]");
    const resetResult = reset.querySelector("[data-reset-result]");
    let resetWord = "清除", resetBusy = false;

    const formatSize = bytes => {
        if (!bytes) return "";
        const units = ["B", "KB", "MB", "GB"];
        let value = bytes, unit = 0;
        while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
        return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
    };

    function renderReset(data) {
        if (!data) return;
        resetWord = data.confirm || resetWord;
        const checked = new Set([...resetParts.querySelectorAll("input:checked")].map(input => input.value));
        const first = !resetParts.childElementCount;
        resetParts.replaceChildren(...data.parts.map(part => {
            const row = el("div", "settings-row is-switch settings-reset-part");
            const label = el("div", "settings-row-label");
            const title = el("label", null);
            title.htmlFor = `reset-${part.id}`;
            title.append(icon(part.icon), el("span", null, part.label));
            const size = part.exists ? formatSize(part.size) : "";
            label.append(title, el("p", "settings-row-hint", part.exists ? (size ? `${part.hint} · ${size}` : part.hint) : `${part.hint} · 暂无数据`));
            const control = el("div", "settings-row-control");
            const check = el("div", "form-check m-0");
            const input = el("input", "form-check-input");
            Object.assign(input, { type: "checkbox", id: `reset-${part.id}`, value: part.id });
            input.checked = first || checked.has(part.id);
            check.append(input);
            control.append(check);
            row.append(label, control);
            return row;
        }));
        reset.querySelector("[data-reset-cloud]").hidden = !data.cloud;
        syncReset();
    }

    function selectedParts() {
        return [...resetParts.querySelectorAll("input:checked")].map(input => input.value);
    }

    function syncReset() {
        resetSubmit.disabled = resetBusy || !selectedParts().length || resetConfirm.value.trim() !== resetWord;
    }

    function showResetResult(ok, text) {
        resetResult.replaceChildren();
        resetResult.className = "settings-result";
        if (!text) return;
        resetResult.classList.add(ok ? "is-ok" : "is-fail");
        const head = el("p", "settings-result-head");
        head.append(icon(ok ? "check-circle-fill" : "x-circle-fill"), el("strong", null, text));
        resetResult.append(head);
    }

    async function clearData() {
        const parts = selectedParts();
        if (resetBusy || !parts.length) return;
        resetBusy = true;
        syncReset();
        showResetResult(true, "");
        const cloud = parts.includes("library") && state?.data?.cloud;
        StatusBar.progress("settings-reset", { text: cloud ? "正在清除数据并清空云端快照…" : "正在清除数据…" });
        let result;
        try { result = await request("/settings/clear", { parts, confirm: resetConfirm.value.trim() }); }
        catch { result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" }; }
        resetBusy = false;
        StatusBar.clear("settings-reset");
        if (result.ok && result.next) {
            leaving = true;
            location.assign(result.next);
            return;
        }
        resetConfirm.value = "";
        const cloudFailed = result.cloud === "failed" ? "云端快照没能清空，可稍后在云端控制台处理。" : "";
        if (result.ok) showResetResult(true, `已清除。${cloudFailed}`);
        else showResetResult(false, result.message || "清除失败");
        await load().catch(() => {});
    }

    reset.addEventListener("input", syncReset);
    reset.addEventListener("change", syncReset);
    reset.addEventListener("submit", event => { event.preventDefault(); clearData(); });

    // ------------------------------------------------------------ 重启

    function renderRestart(pending, supported) {
        if (!window.StatusBar) return;
        if (!pending || !pending.length) { StatusBar.clear("settings-restart"); return; }
        StatusBar.notice("settings-restart", {
            icon: "arrow-repeat",
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
            StatusBar.notice("settings-restart", { icon: "arrow-repeat", text: result.message || "重启失败" });
            return;
        }
        const target = new URL(result.url);
        const sameOrigin = target.origin === location.origin;
        if (await waitFor(target.origin, sameOrigin)) {
            location.assign(`${target.origin}/settings${location.hash}`);
        } else {
            StatusBar.notice("settings-restart", { icon: "exclamation-triangle-fill", text: "MovieDB 没有按时启动，请从开始菜单重新打开。" });
        }
    }

    // ------------------------------------------------------------ 事件

    form.addEventListener("input", refreshPending);
    form.addEventListener("change", refreshPending);
    form.addEventListener("submit", event => { event.preventDefault(); save(); });
    form.addEventListener("keydown", event => {
        if (event.key === "Enter" && event.target.matches("input:not([type=checkbox])") && dirtyCount()) { event.preventDefault(); save(); }
    });
    aiSwitch?.addEventListener("change", () => { syncAi(); if (aiSwitch.checked) setupPresets(state.presets); refreshPending(); });
    for (const button of root.querySelectorAll("[data-reveal]")) {
        button.addEventListener("click", () => {
            const input = button.parentElement.querySelector("input");
            const shown = input.type === "text";
            input.type = shown ? "password" : "text";
            button.querySelector("i").className = shown ? "bi bi-eye" : "bi bi-eye-slash";
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
