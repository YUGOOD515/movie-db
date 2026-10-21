// 首次使用引导与设置页：分步填写、逐项测试连接、保存到 .env。
(() => {
    const root = document.querySelector("[data-setup]");
    if (!root) return;
    const form = root.querySelector("[data-setup-form]");
    const panels = [...root.querySelectorAll("[data-step]")];
    const links = [...root.querySelectorAll("[data-step-link]")];
    const prev = root.querySelector("[data-prev]"), next = root.querySelector("[data-next]");
    const saveNow = root.querySelector("[data-save-now]");
    const firstRun = root.dataset.firstRun === "1";
    const field = name => form.elements.namedItem(name);
    const SERVICE_FIELDS = {
        alist: ["ALIST_ORIGIN", "ALIST_USERNAME", "ALIST_PASSWORD", "ALIST_PATH_PASSWORD"],
        tmdb: ["TMDB_TOKEN", "TMDB_PROXY"],
        llm: ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"],
        mdblist: ["MDBLIST_APIKEY"],
    };
    const STEP_SERVICE = ["alist", "tmdb", null, null];
    const tests = {};        // service -> {ok, warn, message}
    let state = null, step = 0, reached = 0, busy = false;

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };
    const icon = name => { const node = el("i", `bi bi-${name}`); node.setAttribute("aria-hidden", "true"); return node; };

    function payload() {
        const data = {};
        for (const input of form.querySelectorAll("input[name]")) {
            data[input.name] = input.type === "checkbox" ? input.checked : input.value.trim();
        }
        return data;
    }

    async function post(url, body) {
        const response = await fetch(url, {
            method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
        });
        let data = {};
        try { data = await response.json(); } catch { data = { ok: false, message: `请求失败（HTTP ${response.status}）` }; }
        return data;
    }

    // ------------------------------------------------------------ 结果显示

    function showResult(service, result) {
        const box = root.querySelector(`[data-result="${service}"]`);
        if (!box) return;
        box.replaceChildren();
        box.className = "setup-result";
        if (!result) return;
        if (result.pending) {
            box.classList.add("is-pending");
            const spinner = el("span", "spinner-border spinner-border-sm");
            spinner.setAttribute("aria-hidden", "true");
            box.append(spinner, el("span", null, result.message || "正在测试…"));
            return;
        }
        const warn = result.ok && result.warnings && result.warnings.length;
        box.classList.add(result.ok ? (warn ? "is-warn" : "is-ok") : "is-fail");
        const head = el("div", "setup-result-head");
        head.append(icon(result.ok ? (warn ? "exclamation-circle" : "check-circle-fill") : "x-circle-fill"),
            el("strong", null, result.message || (result.ok ? "已连接" : "失败")));
        if (result.ok && result.details && result.details.length) head.append(el("span", "setup-result-details", result.details.join(" · ")));
        box.append(head);
        for (const text of (result.ok ? result.warnings || [] : [])) box.append(el("p", "setup-result-note", text));
        if (result.skip) {
            const skip = el("button", "setup-skip", "跳过测试，继续");
            skip.type = "button";
            skip.addEventListener("click", () => { tests[service] = { ok: true, skipped: true }; go(step + 1); });
            box.append(skip);
        }
    }

    async function runTest(service) {
        if (busy) return tests[service];
        busy = true; render();
        showResult(service, { pending: true });
        let result;
        try {
            result = await post(`/setup/test/${service}`, payload());
        } catch {
            result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" };
        }
        busy = false;
        tests[service] = result;
        if (service === "tmdb" && result.network) root.querySelector('[data-more="proxy"]').open = true;
        showResult(service, result);
        render();
        return result;
    }

    // ------------------------------------------------------------ 步骤

    function stepDone(index) {
        const service = STEP_SERVICE[index];
        if (service) return Boolean(tests[service] && tests[service].ok);
        return firstRun && index < reached;
    }

    function render() {
        panels.forEach((panel, index) => { panel.hidden = index !== step; });
        links.forEach((link, index) => {
            const item = link.parentElement;
            item.classList.toggle("is-current", index === step);
            item.classList.toggle("is-done", index !== step && stepDone(index));
            link.disabled = busy || (firstRun && index > reached);
            if (index === step) link.setAttribute("aria-current", "step"); else link.removeAttribute("aria-current");
            const mark = link.querySelector("[data-step-mark]");
            mark.replaceChildren(index !== step && stepDone(index) ? icon("check-lg") : document.createTextNode(String(index + 1)));
        });
        prev.hidden = step === 0;
        prev.disabled = busy;
        next.disabled = busy;
        next.querySelector("span").textContent = step < panels.length - 1 ? "下一步"
            : firstRun ? "保存并添加片库目录" : "保存设置";
        next.querySelector("i").className = step < panels.length - 1 || firstRun ? "bi bi-arrow-right" : "bi bi-check-lg";
        if (saveNow) { saveNow.hidden = step === panels.length - 1; saveNow.disabled = busy; }
        if (step === panels.length - 1) renderChecklist();
    }

    function go(index) {
        step = Math.max(0, Math.min(panels.length - 1, index));
        reached = Math.max(reached, step);
        render();
        const first = panels[step].querySelector("input:not([type=checkbox]), select");
        if (first && window.matchMedia("(pointer: fine)").matches) first.focus({ preventScroll: true });
    }

    async function advance() {
        if (step === panels.length - 1) return save();
        const service = STEP_SERVICE[step];
        if (service && !(tests[service] && tests[service].ok)) {
            const result = await runTest(service);
            if (!result.ok) {
                // 服务暂时没开也允许先保存，之后到设置里再测
                if (service === "alist" && !result.network) showResult(service, { ...result, skip: true });
                return;
            }
        }
        go(step + 1);
    }

    // ------------------------------------------------------------ 检查清单

    function checkItem(status, title, detail) {
        const item = el("li", `setup-check is-${status}`);
        const mark = { ok: "check-circle-fill", warn: "exclamation-circle", off: "dash-circle", fail: "x-circle-fill", info: "info-circle" }[status];
        const text = el("div", "min-w-0");
        text.append(el("strong", null, title));
        if (detail) text.append(el("span", null, detail));
        item.append(icon(mark), text);
        return item;
    }

    function serviceCheck(service, title, configured) {
        const result = tests[service];
        if (result && result.skipped) return checkItem("warn", title, "未测试");
        if (result && result.ok) return checkItem(result.warnings && result.warnings.length ? "warn" : "ok", title,
            [result.message, ...(result.details || [])].join(" · "));
        if (result) return checkItem("fail", title, result.message);
        return checkItem(configured ? "info" : "warn", title, configured ? "已填写，未测试" : "未填写");
    }

    function renderChecklist() {
        const list = root.querySelector("[data-checklist]");
        const data = payload();
        const saved = name => Boolean(data[name] || (state && state.saved[name]));
        list.replaceChildren(
            serviceCheck("alist", "AList", data.ALIST_USERNAME && saved("ALIST_PASSWORD")),
            serviceCheck("tmdb", "TMDB", saved("TMDB_TOKEN")),
            data.ai_enabled ? serviceCheck("llm", "AI 识别", saved("LLM_API_KEY")) : checkItem("off", "AI 识别", "未开启，仅用文件名规则识别"),
            data.mdblist_enabled ? serviceCheck("mdblist", "多平台评分", saved("MDBLIST_APIKEY")) : checkItem("off", "多平台评分", "未开启"),
        );
        if (state) {
            const probe = state.ffprobe;
            list.append(probe.ok
                ? checkItem("ok", "ffprobe", `${probe.bundled ? "随安装包提供" : "已找到"}${probe.version ? ` · ${probe.version}` : ""}`)
                : checkItem("warn", "ffprobe", "未找到：导入时无法读取分辨率、HDR 与音轨。可安装 FFmpeg 并加入 PATH，或在 .env 中设置 FFPROBE_PATH。"));
            list.append(state.library.ready
                ? checkItem("ok", "媒体库", `${state.library.name} 已存在，保持不变`)
                : checkItem("info", "媒体库", `将新建 ${state.library.name}（${state.library.folder}）`));
        }
    }

    async function save() {
        if (busy) return;
        busy = true; render();
        showResult("save", { pending: true, message: "正在保存…" });
        let result;
        try {
            result = await post("/setup/save", payload());
        } catch {
            result = { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" };
        }
        busy = false;
        if (result.ok && firstRun && result.next) {
            showResult("save", { ok: true, message: "已保存", details: [result.library_created ? "媒体库已创建" : "正在打开媒体管理"] });
            window.location.assign(result.next);
            return;
        }
        if (result.ok) {
            showResult("save", { ok: true, message: "已保存", details: ["新设置已生效"] });
            for (const input of form.querySelectorAll("[data-secret]")) input.value = "";
            await load();
        } else {
            showResult("save", result);
            if (step !== panels.length - 1) go(panels.length - 1);
        }
        render();
        if (saveNow && step !== panels.length - 1) window.showAppToast?.(result.ok ? "设置已保存" : result.message, result.ok ? "success" : "warning");
    }

    // ------------------------------------------------------------ 字段联动

    function syncFeature(input) {
        const body = input.closest("[data-feature]").querySelector("[data-feature-body]");
        body.hidden = !input.checked;
        if (step === panels.length - 1) renderChecklist();
    }

    function setupPresets(presets, baseUrl) {
        const select = root.querySelector("[data-preset]"), keyLink = root.querySelector("[data-key-link]");
        select.replaceChildren(...presets.map(preset => new Option(preset.name, preset.id)), new Option("其他（自定义）", ""));
        // DeepSeek 旧配置可能带 /v1，同样归到 DeepSeek 预设。
        const match = presets.find(preset => preset.base_url === baseUrl || (preset.id === "deepseek" && /\/\/api\.deepseek\.com(\/v1)?\/?$/.test(baseUrl || "")));
        select.value = match ? match.id : baseUrl ? "" : presets[0].id;
        let previous = match || (baseUrl ? null : presets[0]);
        const apply = fill => {
            const preset = presets.find(item => item.id === select.value);
            keyLink.hidden = !preset;
            if (preset) keyLink.href = preset.key_url;
            // DeepSeek 只限并发不限每分钟次数，隐藏“每分钟请求上限”。
            root.querySelector("[data-rpm-field]").hidden = preset?.rpm === false;
            if (!fill || !preset) return;
            field("LLM_BASE_URL").value = preset.base_url;
            const model = field("LLM_MODEL");
            if (!model.value || (previous && model.value === previous.model)) model.value = preset.model;
            model.placeholder = preset.model;
            previous = preset;
            delete tests.llm; showResult("llm", null);
        };
        select.addEventListener("change", () => apply(true));
        apply(!baseUrl);
    }

    async function load() {
        state = await (await fetch("/setup/status", { headers: { Accept: "application/json" } })).json();
        if (state.ok === false) { showResult("save", state); return; }
        for (const [name, value] of Object.entries(state.values)) {
            const input = field(name);
            if (input && !input.matches(":focus")) input.value = value || "";
        }
        for (const [name, saved] of Object.entries(state.saved)) {
            const input = field(name);
            if (input) input.placeholder = saved ? "已保存，留空保持不变" : "";
        }
        if (state.saved.ALIST_PATH_PASSWORD) root.querySelector('[data-more="path-password"]').open = true;
        if (state.values.TMDB_PROXY) root.querySelector('[data-more="proxy"]').open = true;
        render();
    }

    form.addEventListener("input", event => {
        const name = event.target.name;
        for (const [service, names] of Object.entries(SERVICE_FIELDS)) {
            if (names.includes(name) && tests[service]) { delete tests[service]; showResult(service, null); render(); }
        }
    });
    form.addEventListener("keydown", event => {
        if (event.key === "Enter" && event.target.matches("input:not([type=checkbox])")) { event.preventDefault(); advance(); }
    });
    for (const input of form.querySelectorAll("[data-feature] input[type=checkbox]")) {
        input.addEventListener("change", () => syncFeature(input));
    }
    for (const button of root.querySelectorAll("[data-reveal]")) {
        button.addEventListener("click", () => {
            const input = button.previousElementSibling, shown = input.type === "text";
            input.type = shown ? "password" : "text";
            button.querySelector("i").className = shown ? "bi bi-eye" : "bi bi-eye-slash";
            button.title = shown ? "显示" : "隐藏";
        });
    }
    root.querySelectorAll("[data-test]").forEach(button => button.addEventListener("click", () => runTest(button.dataset.test)));
    links.forEach((link, index) => link.addEventListener("click", () => go(index)));
    prev.addEventListener("click", () => go(step - 1));
    next.addEventListener("click", advance);
    saveNow?.addEventListener("click", save);

    render();
    load().then(() => {
        const data = state.values;
        setupPresets(state.presets, data.LLM_BASE_URL);
        field("ai_enabled").checked = Boolean(state.saved.LLM_API_KEY || data.LLM_BASE_URL);
        field("mdblist_enabled").checked = Boolean(state.saved.MDBLIST_APIKEY);
        form.querySelectorAll("[data-feature] input[type=checkbox]").forEach(syncFeature);
        if (!firstRun) reached = panels.length - 1;
        render();
    }).catch(() => showResult("save", { ok: false, message: "无法读取当前设置，请刷新页面。" }));
})();
