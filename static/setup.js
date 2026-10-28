// 首次使用引导：开场 → 片源 →（需要时）海报与资料 → 完成；右侧海报墙随进度逐级亮起。
// TMDB 在开场时就用内置密钥自动检测：连得上就跳过这一步，AList 连接后直接保存。
// AI 识别与多平台评分不再占用引导步骤，完成页给出去设置页开启的入口。
(() => {
    const root = document.querySelector("[data-onboard]");
    if (!root) return;
    const form = root.querySelector("[data-onboard-form]");
    const scenes = Object.fromEntries([...root.querySelectorAll("[data-scene]")].map(node => [node.dataset.scene, node]));
    const tiles = [...root.querySelectorAll(".onboard-tile")];
    const progress = root.querySelector("[data-progress]");
    const STEPS = ["alist", "tmdb"];
    const SERVICE_FIELDS = {
        alist: ["ALIST_ORIGIN", "ALIST_USERNAME", "ALIST_PASSWORD", "ALIST_PATH_PASSWORD"],
        tmdb: ["TMDB_TOKEN", "TMDB_PROXY", "image_proxy"],
    };
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const field = name => form.elements.namedItem(name);
    const tests = {};
    let state = null, at = "welcome", busy = false, postersShown = false, tmdbCheck = Promise.resolve(null);

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };
    const icon = name => window.appIcon(name);

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

    // TMDB 连通且海报能加载时，不需要单独的 TMDB 步骤
    const tmdbReady = () => Boolean(tests.tmdb && tests.tmdb.ok && tests.tmdb.image_ok !== false);

    // 忙碌时锁住所有操作；触发的按钮原位显示转圈（宽度不变）
    function setBusy(value, button) {
        busy = value;
        root.querySelectorAll("[data-connect], [data-go]").forEach(node => { node.disabled = value; });
        if (button) button.toggleAttribute("aria-busy", value);
    }

    // ------------------------------------------------------------ 场景切换

    function syncProgress() {
        const index = STEPS.indexOf(at);
        // 只剩“片源”一步时不显示进度条
        progress.hidden = index < 0 || (at === "alist" && tmdbReady());
        progress.querySelectorAll("li").forEach((item, i) => {
            item.classList.toggle("is-current", i === index);
            item.classList.toggle("is-done", i < index);
            if (i === index) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
        });
    }

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
        syncProgress();
        const first = scene.querySelector("input:not([type=checkbox]):not([type=hidden])") || scene.querySelector(".media-primary-action");
        if (first && window.matchMedia("(pointer: fine)").matches) first.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
    }

    // ------------------------------------------------------------ 结果显示

    // 结果框优先在当前场景里找（保存结果在每个可能完成设置的场景里都有一个）
    function showResult(service, result) {
        const selector = `[data-result="${service}"]`;
        const box = scenes[at].querySelector(selector) || root.querySelector(selector);
        if (!box) return;
        box.replaceChildren();
        box.className = "onboard-result";
        if (!result) return;
        const warn = result.ok && result.warnings && result.warnings.length;
        box.classList.add(result.ok ? (warn ? "is-warn" : "is-ok") : "is-fail");
        const head = el("p", "onboard-result-head");
        head.append(icon(result.ok ? (warn ? "circle-alert" : "circle-check") : "circle-x"),
            el("strong", null, result.message || (result.ok ? "已连接" : "失败")));
        if (result.ok && result.details && result.details.length) head.append(el("span", "onboard-result-details", result.details.join(" · ")));
        box.append(head);
        for (const text of (result.ok ? result.warnings || [] : [])) box.append(el("p", "onboard-result-note", text));
        if (result.skip) {
            const skip = el("button", "onboard-skip", "跳过检测，先继续");
            skip.type = "button";
            skip.addEventListener("click", () => { tests[service] = { ok: true, skipped: true }; setLevel(1); afterAlist(skip); });
            box.append(skip);
        }
    }

    async function runTest(service, extra) {
        if (busy) return null;
        const button = root.querySelector(`[data-connect="${service}"]`);
        setBusy(true, button);
        showResult(service, null);
        const result = await post(`/setup/test/${service}`, payload(extra));
        setBusy(false, button);
        tests[service] = result;
        showResult(service, result);
        return result;
    }

    function setConnectLabel(service, text) {
        root.querySelector(`[data-connect="${service}"] [data-label]`).textContent = text;
    }

    // AList 之后：TMDB 已就绪就直接保存，否则进入 TMDB 步骤
    async function afterAlist(button) {
        await tmdbCheck;
        if (tmdbReady()) finish(button);
        else { prepareTmdb(); go("tmdb"); }
    }

    async function connect(service) {
        const button = root.querySelector(`[data-connect="${service}"]`);
        if (service === "alist" && tests.alist && tests.alist.ok) { afterAlist(button); return; }
        if (service === "tmdb" && tmdbReady()) { finish(button); return; }
        const result = await runTest(service, service === "tmdb" ? { with_posters: !postersShown } : undefined);
        if (!result) return;
        if (service === "alist") {
            if (!result.ok) {
                // AList 暂时没开也允许先往下走，之后到设置里再检测
                if (!result.network) showResult(service, { ...result, skip: true });
                return;
            }
            setLevel(1);
            await tmdbCheck;
            setConnectLabel("alist", tmdbReady() ? "完成设置" : "下一步");
            syncProgress();
            return;
        }
        tmdbResult(result);
        if (result.ok && result.image_ok === false) {
            root.querySelector("[data-image-proxy]").hidden = false;
            field("image_proxy").checked = true;
        }
        if (!result.ok && result.need_token) root.querySelector('[data-more="token"]').open = true;
        if (tmdbReady()) setConnectLabel("tmdb", "完成设置");
    }

    // 进入 TMDB 步骤前按开场检测的结果预先展开需要填写的部分
    function prepareTmdb() {
        const result = tests.tmdb;
        const lead = root.querySelector("[data-tmdb-lead]");
        if (!state || !state.tmdb_builtin || (result && result.need_token)) {
            root.querySelector('[data-more="token"]').open = true;
            lead.textContent = state && state.tmdb_builtin
                ? "MovieDB 内置的 TMDB 密钥暂时不可用，填写你自己的密钥即可连接。"
                : "这个版本没有内置 TMDB 密钥，填写你自己的密钥即可连接。";
        }
        if (result && result.ok && result.image_ok === false) {
            lead.textContent = "TMDB 已连通，但海报图片加载失败。开启公共图片加速，或填写代理后重试。";
            root.querySelector("[data-image-proxy]").hidden = false;
            field("image_proxy").checked = true;
            delete tests.tmdb;  // 换了图片线路，需要重新检测
        }
        if (result) showResult("tmdb", result);
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

    function tmdbResult(result) {
        if (result.ok && result.image_ok !== false) {
            setLevel(2);
            showPosters(result.posters || []);
        }
        const details = (result.details || []).join(" · ");
        check("tmdb", result.ok && result.image_ok !== false ? "ok" : "warn",
            result.ok ? (result.image_ok === false ? "TMDB 已连通，但海报图片加载失败，稍后可开启图片加速" : `TMDB 已连通${details ? `（${details}）` : ""}`)
                : result.need_token ? "需要填写 TMDB 密钥"
                    : result.network ? "暂时连不上 TMDB，稍后可以填写代理" : result.message || "TMDB 检测失败");
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

    async function finish(button) {
        if (busy) return;
        const busyText = button && button.querySelector(".onboard-btn-busy span:last-child");
        if (busyText) busyText.textContent = "正在保存…";
        setBusy(true, button);
        showResult("save", null);
        const result = await post("/setup/save", payload());
        setBusy(false, button);
        if (busyText) busyText.textContent = "正在连接…";
        if (!result.ok) { showResult("save", result); return; }
        showResult("save", null);
        setLevel(3);
        const link = root.querySelector("[data-next-link]");
        if (result.next) link.href = result.next;
        if (result.library_created === false) root.querySelector("[data-finale-text]").textContent = "设置已保存。接下来选择网盘里的影片目录，MovieDB 会识别并补全资料。";
        go("finale");
    }

    // ------------------------------------------------------------ 加载

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
        if (state.saved.TMDB_TOKEN) root.querySelector('[data-more="token"]').open = true;
        renderChecks();
        // 后台检测 TMDB（无需用户操作），结果决定是否需要 TMDB 步骤
        tmdbCheck = post("/setup/test/tmdb", payload({ with_posters: true })).then(result => {
            tests.tmdb = result;
            tmdbResult(result);
            syncProgress();
            return result;
        });
    }

    form.addEventListener("input", event => {
        const name = event.target.name;
        for (const [service, names] of Object.entries(SERVICE_FIELDS)) {
            if (!names.includes(name) || !tests[service]) continue;
            delete tests[service];
            showResult(service, null);
            setConnectLabel(service, "连接");
        }
    });
    form.addEventListener("keydown", event => {
        if (event.key !== "Enter" || !event.target.matches("input:not([type=checkbox])")) return;
        event.preventDefault();
        if (at === "alist" || at === "tmdb") connect(at);
    });
    form.addEventListener("submit", event => event.preventDefault());
    root.querySelectorAll("[data-reveal]").forEach(button => button.addEventListener("click", () => {
        const input = button.previousElementSibling, shown = input.type === "text";
        input.type = shown ? "password" : "text";
        button.replaceChildren(icon(shown ? 'eye' : 'eye-off'));
        button.setAttribute("aria-pressed", String(!shown));
        button.setAttribute("aria-label", button.getAttribute("aria-label").replace(/^(显示|隐藏)/, shown ? "显示" : "隐藏"));
        button.title = shown ? "显示" : "隐藏";
    }));
    root.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => {
        if (button.dataset.go === "tmdb") prepareTmdb();
        go(button.dataset.go);
    }));
    root.querySelectorAll("[data-connect]").forEach(button => button.addEventListener("click", () => connect(button.dataset.connect)));

    load().catch(() => {
        check("alist", "warn", "无法读取当前状态，请刷新页面");
        check("tmdb", "warn", "无法读取当前状态，请刷新页面");
    });
})();
