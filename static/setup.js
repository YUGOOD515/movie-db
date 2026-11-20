// 首次使用引导：开场 → 片源 →（需要时）海报与资料 → AI 识别 → 多平台评分 → 完成；右侧海报墙随进度逐级亮起。
// TMDB 在开场时就用内置密钥自动检测：连得上就跳过这一步。AI 识别与多平台评分可以跳过，但都会正常展示。
// 片源一步：上方横排选网盘，能扫码的默认扫码（点选即出二维码），其他登录方式和高级项用按钮原位切换。
(() => {
    const root = document.querySelector("[data-onboard]");
    if (!root) return;
    const form = root.querySelector("[data-onboard-form]");
    const scenes = Object.fromEntries([...root.querySelectorAll("[data-scene]")].map(node => [node.dataset.scene, node]));
    const tiles = [...root.querySelectorAll(".onboard-tile")];
    const progress = root.querySelector("[data-progress]");
    const STEPS = ["drive", "tmdb", "ai", "mdblist"];
    const SERVICE_FIELDS = {
        drive: [],
        tmdb: ["TMDB_TOKEN", "TMDB_PROXY", "image_proxy"],
        llm: ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"],
        mdblist: ["MDBLIST_APIKEY"],
    };
    const CONNECT_LABELS = { tmdb: "连接", llm: "连接并继续", mdblist: "连接并完成" };
    // 连接成功、最后要写入的可选服务（跳过的不写，保留已有配置）
    const enabled = { ai: false, mdblist: false };
    let aiPresets = null;
    const useExistingButton = root.querySelector("[data-drive-use-existing]");
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
        root.querySelectorAll("[data-connect], [data-go], [data-skip]").forEach(node => { node.disabled = value; });
        if (button) button.toggleAttribute("aria-busy", value);
    }

    // ------------------------------------------------------------ 场景切换

    function syncProgress() {
        // TMDB 自动连通时不占步骤
        const steps = STEPS.filter(step => step !== "tmdb" || !tmdbReady());
        const index = steps.indexOf(at);
        progress.hidden = index < 0;
        progress.querySelectorAll("li").forEach(item => {
            const i = steps.indexOf(item.dataset.progressStep);
            item.hidden = i < 0;
            item.classList.toggle("is-current", i === index);
            item.classList.toggle("is-done", i >= 0 && i < index);
            if (i === index) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
        });
    }

    function go(name) {
        if (!scenes[name] || name === at) return;
        root.querySelectorAll("dialog[open]").forEach(node => node.close());  // 换步骤时弹窗一并关掉
        scenes[at].hidden = true;
        at = name;
        root.dataset.at = name;
        const scene = scenes[name];
        scene.hidden = false;
        scene.classList.remove("is-entering");
        void scene.offsetWidth;  // 重新触发进场动画
        scene.classList.add("is-entering");
        syncProgress();
        if (name === "drive") { syncDriveView(); syncNav(); }  // 隐藏时量不到横排宽度，显示后再算翻页按钮
        if (name === "ai") aiPresets?.activate();
        const first = scene.querySelector("input:not([type=checkbox]):not([type=hidden])") || scene.querySelector(".media-primary-action");
        if (first && window.matchMedia("(pointer: fine)").matches) first.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
    }

    // ------------------------------------------------------------ 结果显示

    // 结果框优先在当前场景里找（保存结果在每个可能完成设置的场景里都有一个）
    function showResult(service, result) {
        const selector = `[data-result="${service}"]`;
        const box = root.querySelector(`dialog[open] ${selector}`) || scenes[at].querySelector(selector) || root.querySelector(selector);
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
            skip.addEventListener("click", () => { tests[service] = { ok: true, skipped: true }; setLevel(1); afterDrive(); });
            box.append(skip);
        }
    }

    async function runTest(service, extra) {
        if (busy) return null;
        const button = connectButton(service);
        setBusy(true, button);
        showResult(service, null);
        const result = await post(`/setup/test/${service}`, payload(extra));
        setBusy(false, button);
        tests[service] = result;
        showResult(service, result);
        return result;
    }

    function setConnectLabel(service, text) {
        root.querySelectorAll(`[data-connect="${service}"] [data-label]`).forEach(label => { label.textContent = text; });
    }
    const connectButton = service => root.querySelector(`dialog[open] [data-connect="${service}"]`) || root.querySelector(`[data-connect="${service}"]`);

    async function afterDrive() {
        await tmdbCheck;
        if (tmdbReady()) go("ai");
        else { prepareTmdb(); go("tmdb"); }
    }

    // AI 与评分：连接成功后进入下一步；成功但带提醒（例如模型名不在列表里）时先停下，再点一次继续
    async function connectOptional(service) {
        const button = root.querySelector(`[data-connect="${service}"]`);
        const key = service === "llm" ? "ai" : "mdblist";
        const next = () => service === "llm" ? go("mdblist") : finish(button);
        if (tests[service]?.ok) { enabled[key] = true; next(); return; }
        const result = await runTest(service);
        if (!result?.ok) return;
        enabled[key] = true;
        if (result.warnings?.length) setConnectLabel(service, "继续");
        else next();
    }

    function skip(step) {
        enabled[step] = false;
        if (step === "ai") go("mdblist");
        else finish(root.querySelector(`[data-skip="${step}"]`));
    }

    async function connect(service) {
        if (service === "drive") {
            root.querySelector("[data-drive-save]").click();
            return;
        }
        if (service === "llm" || service === "mdblist") { connectOptional(service); return; }
        const button = root.querySelector(`[data-connect="${service}"]`);
        if (service === "tmdb" && tmdbReady()) { go("ai"); return; }
        const result = await runTest(service, { with_posters: !postersShown });
        if (!result) return;
        tmdbResult(result);
        if (result.ok && result.image_ok === false) {
            root.querySelector("[data-image-proxy]").hidden = false;
            field("image_proxy").checked = true;
        }
        if (!result.ok && result.need_token && !tmdbDialog.open) tokenInline();
        if (!tmdbReady()) return;
        // 在弹窗里连通：关掉弹窗直接进入下一步；在页面上连通时按钮变成“继续”
        if (tmdbDialog.open) {
            tmdbDialog.close();
            await Promise.race([wallShown, new Promise(resolve => setTimeout(resolve, 2500))]);
            go("ai");
        }
        else setConnectLabel("tmdb", "继续");
    }

    // 自己的 TMDB 密钥：平时在居中弹窗里；内置密钥不可用、必须填写时把这块直接放进表单
    const tmdbDialog = root.querySelector("[data-tmdb-dialog]");
    const tmdbToken = root.querySelector("[data-tmdb-token]");
    const tokenOpen = root.querySelector("[data-tmdb-token-open]");
    function tokenInline() {
        if (tmdbDialog.open) tmdbDialog.close();
        root.querySelector("[data-tmdb-token-slot]").append(tmdbToken);
        tokenOpen.hidden = true;
    }
    tokenOpen.addEventListener("click", () => {
        tmdbDialog.showModal();
        field("TMDB_TOKEN").focus();
    });
    tmdbDialog.querySelectorAll("[data-tmdb-dialog-close]").forEach(button => button.addEventListener("click", () => tmdbDialog.close()));
    tmdbDialog.addEventListener("click", event => { if (event.target === tmdbDialog) tmdbDialog.close(); });
    tmdbDialog.addEventListener("close", () => showResult("tmdb", tests.tmdb || null));

    // 进入 TMDB 步骤前按开场检测的结果预先展开需要填写的部分
    function prepareTmdb() {
        const result = tests.tmdb;
        const lead = root.querySelector("[data-tmdb-lead]");
        if (!state || !state.tmdb_builtin || (result && result.need_token)) {
            tokenInline();
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

    // ------------------------------------------------------------ 片源：网盘横排与登录方式

    const picker = root.querySelector("[data-drive-picker]");
    const track = picker.querySelector("[data-picker-track]");
    const driveForm = root.querySelector("[data-drive-form]");
    const kindSelect = driveForm.querySelector("[data-drive-kind]");
    const saveButton = driveForm.querySelector("[data-drive-save]");
    const resultBox = driveForm.querySelector("[data-drive-result]");
    const otherButton = driveForm.querySelector("[data-setup-other]");
    const advancedButton = driveForm.querySelector("[data-setup-advanced-open]");
    const importBox = root.querySelector("[data-drive-import-inline]");
    const dialog = driveForm.querySelector("[data-setup-dialog]");
    const dialogBody = dialog.querySelector("[data-setup-dialog-body]");
    const dialogFoot = dialog.querySelector("[data-setup-dialog-foot]");
    const dialogSwitch = dialog.querySelector("[data-setup-dialog-switch]");
    let brand = null, qrKind = "", dialogKind = "";
    // 搬进弹窗的节点原来的位置：关闭时按占位放回
    const homes = new Map();
    const brandKinds = () => brand ? brand.dataset.kinds.split(" ") : [kindSelect.value];
    const fieldsOf = kind => driveForm.querySelector(`[data-drive-fields="${kind}"]`);

    function setKind(kind) {
        if (kindSelect.value === kind) return;
        kindSelect.value = kind;
        kindSelect.dispatchEvent(new Event("change"));  // drives.js 切换字段、停止旧的二维码轮询
        qrKind = "";
    }

    // “其他登录方式”里有哪些：同一家网盘的另一种登录方式（夸克 Cookie、123 开放平台、AList 导入），以及默认方式的手动填写凭据
    function otherOptions() {
        const kinds = brandKinds(), options = [];
        const alt = (brand?.dataset.altLabel || "").replace(/^改(用|为)\s*/, "");
        if (brand?.dataset.brand === "alist") options.push({ kind: "import", label: alt });
        else if (kinds[1]) options.push({ kind: kinds[1], label: alt });
        const first = fieldsOf(kinds[0]);
        if (first.dataset.login !== "form" && first.querySelector("[data-setup-more]")) options.push({ kind: kinds[0], label: "手动填写凭据" });
        return options;
    }

    function selectBrand(item, focus = false) {
        brand = item;
        track.querySelectorAll("[data-brand]").forEach(node => node.setAttribute("aria-checked", String(node === item)));
        track.querySelectorAll("[data-brand]").forEach(node => { node.tabIndex = node === item ? 0 : -1; });
        setKind(brandKinds()[0]);
        item.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" });
        if (focus) item.focus();
        syncDriveView();
    }

    // 主页面：只有默认登录方式。扫码 / 授权成功后自动保存，不显示“检测并保存”；表单类网盘的高级项放在“高级设置”弹窗里
    function syncDriveView() {
        if (dialog.open) return;
        const kind = kindSelect.value, fields = fieldsOf(kind), login = fields.dataset.login;
        fields.querySelector("[data-setup-primary]")?.removeAttribute("hidden");
        fields.querySelector("[data-setup-more]")?.setAttribute("hidden", "");
        fields.querySelector("[data-setup-advanced]")?.setAttribute("hidden", "");
        saveButton.hidden = login !== "form";
        otherButton.hidden = !otherOptions().length;
        advancedButton.hidden = !(login === "form" && fields.querySelector("[data-setup-advanced]"));
        // 停在扫码视图时自动获取二维码
        if (login === "qr" && at === "drive" && qrKind !== kind) {
            qrKind = kind;
            fields.querySelector("[data-drive-qr-start]").click();
        }
    }

    // 把节点搬进弹窗，原处留一个同高的占位，背后的页面不跳动
    function borrow(node, target) {
        if (homes.has(node)) { target.append(node); return; }
        const hold = document.createElement("div");
        hold.style.height = `${node.offsetHeight}px`;
        hold.hidden = node.hidden || !node.offsetHeight;
        node.replaceWith(hold);
        homes.set(node, hold);
        target.append(node);
    }
    function giveBack(node) {
        const hold = homes.get(node);
        if (!hold) return;
        hold.replaceWith(node);
        homes.delete(node);
    }

    // 弹窗里显示一种配置：整份字段（不含二维码 / 授权按钮）、高级项、检测结果和“检测并保存”
    function showDialogKind(kind) {
        if (dialogKind && dialogKind !== kind) giveBack(dialogKind === "import" ? importBox : fieldsOf(dialogKind));
        dialogKind = kind;
        dialogSwitch.querySelectorAll("button").forEach(button => button.classList.toggle("active", button.dataset.kind === kind));
        if (kind === "import") {
            importBox.hidden = false;
            borrow(importBox, dialogBody);
            saveButton.hidden = true;
            return;
        }
        setKind(kind);
        const fields = fieldsOf(kind);
        fields.querySelector("[data-setup-primary]")?.setAttribute("hidden", "");
        fields.querySelector("[data-setup-more]")?.removeAttribute("hidden");
        fields.querySelector("[data-setup-advanced]")?.removeAttribute("hidden");
        borrow(fields, dialogBody);
        borrow(resultBox, dialogBody);
        saveButton.hidden = false;
        borrow(saveButton, dialogFoot);
        resultBox.replaceChildren();
        resultBox.className = "onboard-result";
        fields.querySelector("input:not([type=hidden]), textarea, select")?.focus({ preventScroll: true });
    }

    function openDialog(title, options) {
        dialog.querySelector("[data-setup-dialog-title]").textContent = title;
        dialogSwitch.replaceChildren(...options.map(option => {
            const button = el("button", "btn btn-sm", option.label);
            button.type = "button";
            button.dataset.kind = option.kind;
            button.addEventListener("click", () => showDialogKind(option.kind));
            return button;
        }));
        dialogSwitch.hidden = options.length < 2;
        dialog.showModal();
        showDialogKind(options[0].kind);
    }

    // 关闭（取消、Esc、点遮罩、保存成功）：字段、结果和按钮放回原处，回到这家网盘的默认登录方式
    dialog.addEventListener("close", () => {
        if (dialogKind === "import") { giveBack(importBox); importBox.hidden = true; }
        else if (dialogKind) giveBack(fieldsOf(dialogKind));
        giveBack(resultBox);
        giveBack(saveButton);
        dialogKind = "";
        resultBox.replaceChildren();
        resultBox.className = "onboard-result";
        setKind(brandKinds()[0]);
        syncDriveView();
    });
    dialog.querySelectorAll("[data-setup-dialog-close]").forEach(button => button.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
    otherButton.addEventListener("click", () => openDialog("其他登录方式", otherOptions()));
    advancedButton.addEventListener("click", () => openDialog("高级设置", [{ kind: kindSelect.value, label: "" }]));

    track.addEventListener("click", event => {
        const item = event.target.closest("[data-brand]");
        if (item && item !== brand) selectBrand(item);
    });
    // 单选组的方向键：左右移动并选中
    track.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        const items = [...track.querySelectorAll("[data-brand]")];
        const index = items.indexOf(document.activeElement);
        if (index < 0) return;
        event.preventDefault();
        selectBrand(items[(index + (event.key === "ArrowRight" ? 1 : -1) + items.length) % items.length], true);
    });
    // 哪一侧还有内容：显示那一侧的翻页按钮，并让内容在按钮下渐隐
    function syncNav() {
        const [prev, next] = picker.querySelectorAll("[data-picker-step]");
        prev.disabled = track.scrollLeft <= 1;
        next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 1;
        picker.dataset.overflow = [prev.disabled ? "" : "start", next.disabled ? "" : "end"].filter(Boolean).join(" ");
    }
    picker.querySelectorAll("[data-picker-step]").forEach(button => button.addEventListener("click", () => {
        track.scrollBy({ left: Number(button.dataset.pickerStep) * track.clientWidth * 0.8, behavior: reduceMotion.matches ? "auto" : "smooth" });
    }));
    track.addEventListener("scroll", syncNav, { passive: true });
    addEventListener("resize", syncNav);
    selectBrand(track.querySelector("[data-brand]"));  // 默认选中横排第一项（夸克）
    syncNav();

    // ------------------------------------------------------------ 海报墙

    // 没有真实海报时页面居中、不放占位墙；第一张海报加载出来后换成左文右墙的版式
    // wallShown：换版式的动画播完时完成，TMDB 弹窗连通后等它播完再进入下一步，两段动画不叠在一起
    let wallDone = () => {};
    const wallShown = new Promise(resolve => { wallDone = resolve; });
    function revealWall() {
        if (root.hasAttribute("data-posters")) return;
        const apply = () => root.setAttribute("data-posters", "");
        if (document.startViewTransition && !reduceMotion.matches) {
            document.startViewTransition(apply).finished.finally(wallDone);
        } else { apply(); wallDone(); }
    }

    function showPosters(names) {
        if (!names.length || postersShown) return;
        postersShown = true;
        const order = tiles.map((tile, index) => index).sort(() => Math.random() - 0.5);
        tiles.forEach((tile, index) => {
            const img = tile.querySelector("img");
            img.addEventListener("load", () => {
                revealWall();
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
    }

    // 已经有启用的网盘时，网盘步骤给出“直接使用”的入口（旧数据迁移后常遇到）
    function syncExistingDrive() {
        if (!useExistingButton) return;
        useExistingButton.hidden = !(state && (state.drives || []).some(drive => drive.enabled));
    }

    // ------------------------------------------------------------ 保存

    async function finish(button) {
        if (busy) return;
        const busyText = button && button.querySelector(".onboard-btn-busy span:last-child");
        if (busyText) busyText.textContent = "正在保存…";
        setBusy(true, button);
        showResult("save", null);
        // 跳过的可选服务不带开关字段，服务端保留原有配置
        const data = payload({ ...(enabled.ai ? { ai_enabled: true } : {}), ...(enabled.mdblist ? { mdblist_enabled: true } : {}) });
        if (!enabled.ai) for (const name of SERVICE_FIELDS.llm) delete data[name];
        if (!enabled.mdblist) delete data.MDBLIST_APIKEY;
        const result = await post("/setup/save", data);
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
        const aiStatus = root.querySelector("[data-ai-model-status]");
        aiPresets = window.AiPresets.attach({
            presets: state.presets, model: field("LLM_MODEL"), base: field("LLM_BASE_URL"), key: field("LLM_API_KEY"),
            select: root.querySelector("[data-ai-preset]"), keyLink: root.querySelector("[data-ai-key-link]"),
            url: "/setup/test/llm-models", hasSavedKey: () => Boolean(state.saved.LLM_API_KEY),
            onPreset: preset => { root.querySelector("[data-ai-base]").hidden = Boolean(preset); },
            onStatus: text => { aiStatus.textContent = text || "填好 API Key 后自动获取平台的最新模型。"; },
        });
        syncExistingDrive();
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
            setConnectLabel(service, CONNECT_LABELS[service]);
        }
    });
    form.addEventListener("keydown", event => {
        if (event.key !== "Enter" || !event.target.matches("input:not([type=checkbox])")) return;
        event.preventDefault();
        if (at === "drive" || at === "tmdb") connect(at);
        else if (at === "ai") connect("llm");
        else if (at === "mdblist") connect("mdblist");
    });
    form.addEventListener("submit", event => event.preventDefault());
    root.querySelectorAll("[data-reveal]:not([data-drive-reveal])").forEach(button => button.addEventListener("click", () => {
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
    root.querySelectorAll("[data-skip]").forEach(button => button.addEventListener("click", () => skip(button.dataset.skip)));
    useExistingButton?.addEventListener("click", () => {
        tests.drive = { ok: true };
        setLevel(1);
        afterDrive();
    });
    document.addEventListener("drive-saved", () => {
        if (dialog.open) dialog.close();  // 在弹窗里检测保存成功：关掉弹窗，直接进入下一步
        tests.drive = { ok: true };
        setLevel(1);
        if (useExistingButton) useExistingButton.hidden = false;
        afterDrive();
    });

    load().catch(() => window.showAppToast?.("无法读取当前状态，请刷新页面。", "danger"));
})();
