// 首次使用引导：开场 → 片源 → 海报与资料 → AI 识别 → 评分 → 完成；右侧海报墙随进度逐级亮起。
// 每一步都由用户点“下一步”前进，连接成功只更新按钮和状态行，不自动跳转。
(() => {
    const root = document.querySelector("[data-onboard]");
    if (!root) return;
    const form = root.querySelector("[data-onboard-form]");
    const scenes = Object.fromEntries([...root.querySelectorAll("[data-scene]")].map(node => [node.dataset.scene, node]));
    const tiles = [...root.querySelectorAll(".onboard-tile")];
    const progress = root.querySelector("[data-progress]");
    const STEPS = ["alist", "tmdb", "ai", "ratings"];
    const NEXT = { alist: "tmdb", tmdb: "ai", ai: "ratings" };
    const SERVICE_FIELDS = {
        alist: ["ALIST_ORIGIN", "ALIST_USERNAME", "ALIST_PASSWORD", "ALIST_PATH_PASSWORD"],
        tmdb: ["TMDB_TOKEN", "TMDB_PROXY"],
        llm: ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"],
        mdblist: ["MDBLIST_APIKEY"],
    };
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const field = name => form.elements.namedItem(name);
    const tests = {};
    let state = null, at = "welcome", busy = false, postersShown = false;

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };
    const icon = name => { const node = el("i", `bi bi-${name}`); node.setAttribute("aria-hidden", "true"); return node; };

    function payload(extra = {}) {
        const data = {};
        for (const input of form.querySelectorAll("input[name]")) {
            data[input.name] = input.type === "checkbox" ? input.checked : input.value.trim();
        }
        return { ...data, ...extra };
    }

    async function post(url, body) {
        try {
            const response = await fetch(url, {
                method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify(body),
            });
            try { return await response.json(); } catch { return { ok: false, message: `请求失败（HTTP ${response.status}）` }; }
        } catch {
            return { ok: false, message: "MovieDB 没有响应，请确认程序仍在运行。" };
        }
    }

    function setLevel(level) {
        root.dataset.level = String(Math.max(Number(root.dataset.level) || 0, level));
    }

    // 忙碌时锁住所有操作；触发的按钮原位显示转圈（宽度不变）
    function setBusy(value, button) {
        busy = value;
        root.querySelectorAll("[data-connect], [data-finish], [data-next], [data-skip], [data-go], [data-test]").forEach(node => { node.disabled = value; });
        if (button) button.toggleAttribute("aria-busy", value);
    }

    // ------------------------------------------------------------ 场景切换

    function go(name) {
        if (!scenes[name] || name === at) return;
        scenes[at].hidden = true;
        at = name;
        root.dataset.at = name;
        const scene = scenes[name];
        scene.hidden = false;
        scene.classList.remove("is-entering");
        void scene.offsetWidth;  // 重新触发进场动画
        scene.classList.add("is-entering");
        const index = STEPS.indexOf(name);
        progress.hidden = index < 0;
        progress.querySelectorAll("li").forEach((item, i) => {
            item.classList.toggle("is-current", i === index);
            item.classList.toggle("is-done", i < index);
            if (i === index) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
        });
        const first = scene.querySelector("input:not([type=checkbox]):not([type=hidden])") || scene.querySelector(".media-primary-action");
        if (first && window.matchMedia("(pointer: fine)").matches) first.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
    }

    // ------------------------------------------------------------ 结果显示

    function showResult(service, result) {
        const box = root.querySelector(`[data-result="${service}"]`);
        if (!box) return;
        box.replaceChildren();
        box.className = "onboard-result";
        if (!result) return;
        const warn = result.ok && result.warnings && result.warnings.length;
        box.classList.add(result.ok ? (warn ? "is-warn" : "is-ok") : "is-fail");
        const head = el("p", "onboard-result-head");
        head.append(icon(result.ok ? (warn ? "exclamation-circle" : "check-circle-fill") : "x-circle-fill"),
            el("strong", null, result.message || (result.ok ? "已连接" : "失败")));
        if (result.ok && result.details && result.details.length) head.append(el("span", "onboard-result-details", result.details.join(" · ")));
        box.append(head);
        for (const text of (result.ok ? result.warnings || [] : [])) box.append(el("p", "onboard-result-note", text));
        if (result.skip) {
            const skip = el("button", "onboard-skip", "跳过检测，先继续");
            skip.type = "button";
            skip.addEventListener("click", () => { tests[service] = { ok: true, skipped: true }; setLevel(1); go(NEXT[service]); });
            box.append(skip);
        }
    }

    async function runTest(service, extra) {
        if (busy) return null;
        const button = root.querySelector(`[data-connect="${service}"], [data-test="${service}"]`);
        setBusy(true, button);
        showResult(service, null);
        const result = await post(`/setup/test/${service}`, payload(extra));
        setBusy(false, button);
        tests[service] = result;
        if (service === "tmdb" && result.network) root.querySelector('[data-more="proxy"]').open = true;
        showResult(service, result);
        return result;
    }

    function setConnectLabel(service, text) {
        root.querySelector(`[data-connect="${service}"] [data-label]`).textContent = text;
    }

    async function connect(service) {
        const passed = tests[service] && tests[service].ok;
        if (passed) { go(NEXT[service]); return; }
        const result = await runTest(service, service === "tmdb" ? { with_posters: !postersShown } : undefined);
        if (!result) return;
        if (!result.ok) {
            // AList 暂时没开也允许先往下走，之后到设置里再检测
            if (service === "alist" && !result.network) showResult(service, { ...result, skip: true });
            return;
        }
        setConnectLabel(service, "下一步");
        if (service === "alist") { setLevel(1); return; }
        setLevel(2);
        showPosters(result.posters || []);
    }

    // ------------------------------------------------------------ 海报墙

    function showPosters(names) {
        if (!names.length || postersShown) return;
        postersShown = true;
        const order = tiles.map((tile, index) => index).sort(() => Math.random() - 0.5);
        tiles.forEach((tile, index) => {
            const img = tile.querySelector("img");
            img.addEventListener("load", () => {
                img.hidden = false;
                tile.style.setProperty("--delay", `${order.indexOf(index) * 28}ms`);
                tile.classList.add("is-lit");
            }, { once: true });
            img.src = `/setup/poster/w342/${encodeURIComponent(names[index % names.length])}`;
        });
    }

    // ------------------------------------------------------------ 开场检测

    function check(name, status, text) {
        const item = root.querySelector(`[data-check="${name}"]`);
        item.dataset.state = status;
        item.querySelector("[data-check-text]").textContent = text;
    }

    function renderChecks() {
        const origin = state.values.ALIST_ORIGIN.replace(/^https?:\/\//, "");
        check("alist", state.alist_running ? "ok" : "warn",
            state.alist_running ? `AList 正在运行（${origin}）` : "没有检测到 AList，下一步可以填写它的地址");
        const probe = state.ffprobe;
        check("ffprobe", probe.ok ? "ok" : "warn", probe.ok
            ? `ffprobe ${probe.bundled ? "随安装包提供" : "已就绪"}${probe.version ? ` · ${probe.version}` : ""}`
            : "未找到 ffprobe，导入时不读取分辨率、HDR 与音轨");
        check("library", "ok", state.library.ready ? `媒体库 ${state.library.name} 已存在` : `将新建媒体库 ${state.library.name}`);
    }

    // ------------------------------------------------------------ 保存

    // AI 步：开启时先在本地确认必填项，免得到最后一步保存才报错
    function nextFromAi() {
        if (busy) return;
        if (field("ai_enabled").checked) {
            const missing = !field("LLM_API_KEY").value.trim() && !state?.saved.LLM_API_KEY ? "填写 API Key，或点“跳过”稍后再开。"
                : !field("LLM_MODEL").value.trim() || !field("LLM_BASE_URL").value.trim() ? "请在“自定义模型与地址”中填写模型和接口地址。" : "";
            if (missing) {
                if (missing.includes("模型")) root.querySelector('[data-more="llm-custom"]').open = true;
                showResult("llm", { ok: false, message: missing });
                return;
            }
        }
        go("ratings");
    }

    function skip(step) {
        const input = field(step === "ai" ? "ai_enabled" : "mdblist_enabled");
        input.checked = false;
        syncFeature(input);
        if (step === "ai") go("ratings"); else finish();
    }

    async function finish() {
        if (busy) return;
        const button = root.querySelector("[data-finish]");
        setBusy(true, button);
        showResult("save", null);
        const result = await post("/setup/save", payload());
        setBusy(false, button);
        if (!result.ok) { showResult("save", result); return; }
        showResult("save", null);
        setLevel(3);
        const link = root.querySelector("[data-next-link]");
        if (result.next) link.href = result.next;
        if (result.library_created === false) root.querySelector("[data-finale-text]").textContent = "设置已保存。接下来选择网盘里的影片目录，MovieDB 会识别并补全资料。";
        go("finale");
    }

    // ------------------------------------------------------------ 字段联动

    function syncFeature(input) {
        input.closest("[data-feature]").querySelector("[data-feature-body]").hidden = !input.checked;
    }

    function syncModelHint() {
        const model = field("LLM_MODEL").value.trim();
        root.querySelector("[data-model-hint]").textContent = model ? model : "";
    }

    function setupPresets(presets, baseUrl) {
        const select = root.querySelector("[data-preset]"), keyLink = root.querySelector("[data-key-link]");
        const custom = root.querySelector('[data-more="llm-custom"]');
        select.replaceChildren(...presets.map(preset => new Option(preset.name, preset.id)), new Option("其他（自定义）", ""));
        const match = presets.find(preset => preset.base_url === baseUrl || (preset.id === "deepseek" && /\/\/api\.deepseek\.com(\/v1)?\/?$/.test(baseUrl || "")));
        select.value = match ? match.id : baseUrl ? "" : presets[0].id;
        let previous = match || (baseUrl ? null : presets[0]);
        const apply = fill => {
            const preset = presets.find(item => item.id === select.value);
            keyLink.hidden = !preset;
            if (preset) keyLink.href = preset.key_url;
            // DeepSeek 只限并发不限每分钟次数
            root.querySelector("[data-rpm-field]").hidden = preset?.rpm === false;
            // 预设平台的模型和地址自动填好并收起；选“其他（自定义）”时展开让用户填写
            if (!preset) custom.open = true;
            if (fill) { delete tests.llm; showResult("llm", null); }
            if (!fill || !preset) { syncModelHint(); return; }
            field("LLM_BASE_URL").value = preset.base_url;
            const model = field("LLM_MODEL");
            if (!model.value || (previous && model.value === previous.model)) model.value = preset.model;
            model.placeholder = preset.model;
            previous = preset;
            syncModelHint();
        };
        select.addEventListener("change", () => apply(true));
        apply(!baseUrl);
    }

    async function load() {
        state = await (await fetch("/setup/status", { headers: { Accept: "application/json" } })).json();
        if (state.ok === false) throw new Error(state.message);
        for (const [name, value] of Object.entries(state.values)) {
            const input = field(name);
            if (input) input.value = value || "";
        }
        for (const [name, saved] of Object.entries(state.saved)) {
            const input = field(name);
            if (input && saved) input.placeholder = "已保存，留空保持不变";
        }
        if (state.saved.ALIST_PATH_PASSWORD) root.querySelector('[data-more="path-password"]').open = true;
        if (state.values.TMDB_PROXY) root.querySelector('[data-more="proxy"]').open = true;
        setupPresets(state.presets, state.values.LLM_BASE_URL);
        // 可选步骤默认打开开关，直接展示要填的内容；不需要就点“跳过”
        field("ai_enabled").checked = true;
        field("mdblist_enabled").checked = true;
        form.querySelectorAll("[data-feature] input[type=checkbox]").forEach(syncFeature);
        renderChecks();
    }

    form.addEventListener("input", event => {
        const name = event.target.name;
        if (name === "LLM_MODEL") syncModelHint();
        for (const [service, names] of Object.entries(SERVICE_FIELDS)) {
            if (!names.includes(name) || !tests[service]) continue;
            delete tests[service];
            showResult(service, null);
            if (service === "alist" || service === "tmdb") setConnectLabel(service, "连接");
        }
    });
    form.addEventListener("keydown", event => {
        if (event.key !== "Enter" || !event.target.matches("input:not([type=checkbox])")) return;
        event.preventDefault();
        if (at === "alist" || at === "tmdb") connect(at);
        else if (at === "ai") nextFromAi();
        else if (at === "ratings") finish();
    });
    form.addEventListener("submit", event => event.preventDefault());
    form.querySelectorAll("[data-feature] input[type=checkbox]").forEach(input => input.addEventListener("change", () => syncFeature(input)));
    root.querySelectorAll("[data-reveal]").forEach(button => button.addEventListener("click", () => {
        const input = button.previousElementSibling, shown = input.type === "text";
        input.type = shown ? "password" : "text";
        button.querySelector("i").className = shown ? "bi bi-eye" : "bi bi-eye-slash";
        button.title = shown ? "显示" : "隐藏";
    }));
    root.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => go(button.dataset.go)));
    root.querySelectorAll("[data-connect]").forEach(button => button.addEventListener("click", () => connect(button.dataset.connect)));
    root.querySelectorAll("[data-test]").forEach(button => button.addEventListener("click", () => runTest(button.dataset.test)));
    root.querySelector("[data-next]").addEventListener("click", nextFromAi);
    root.querySelectorAll("[data-skip]").forEach(button => button.addEventListener("click", () => skip(button.dataset.skip)));
    root.querySelector("[data-finish]").addEventListener("click", finish);

    load().catch(() => {
        check("alist", "warn", "无法读取当前状态，请刷新页面");
    });
})();
