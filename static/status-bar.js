// 资源管理统一状态栏：固定在视口底部居中，按需调用。
//
// 两层：
//   task —— 后台任务（扫描、刷新评分、批量关联的结果），可同时存在多条，叠在上方；
//   page —— 当前视图的操作（选择、校验、提交进度），同一时间通常只有一条。
// 预设：
//   StatusBar.selection(key, { text, checked, indeterminate, onToggle, toggleLabel, action })
//       已选 N 项 + 主操作（列表批量确认）
//   StatusBar.check(key, { state: "ok" | "warning" | "idle" | "loading", text, link, action })
//       校验结果 + 主操作（编辑器“N 个文件需处理 · 查看 / 关联 N 个”）
//   StatusBar.progress(key, { text, detail, done, total, layer })
//       进度；有 total 时显示 done/total 与进度条，否则为不确定进度
//   StatusBar.result(key, { tone: "success" | "warning" | "danger" | "info", text, action, duration })
//       任务结果；默认 5 秒后自动收起，悬停或聚焦时暂停计时
//   StatusBar.notice(key, { icon, text, action })
//       建议（“N 项可能不是影视内容 · 全部忽略”）
//   StatusBar.pending(key, { text, discard, save })
//       未保存的修改：放弃 / 保存
// 通用：StatusBar.set(key, spec) 更新或创建，StatusBar.clear(key) 收起，StatusBar.has(key)。
// action / link 形如 { label, run, disabled }；同一个 key 重复调用只更新内容，不会闪烁。
(() => {
    const entries = new Map();
    const timers = new Map();
    let dock = null;

    const el = (tag, className = "", text = "") => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    };

    function ensureDock() {
        if (dock) return dock;
        dock = el("div", "status-dock");
        dock.setAttribute("role", "region");
        dock.setAttribute("aria-label", "状态栏");
        dock.append(el("div", "status-dock-layer is-task"), el("div", "status-dock-layer is-page"));
        document.body.append(dock);
        // 内容区与全站提示按状态栏高度让位，避免遮挡页面底部。
        new ResizeObserver(() => {
            const height = dock.getBoundingClientRect().height;
            document.documentElement.style.setProperty("--status-dock-space", `${Math.ceil(height)}px`);
            document.body.classList.toggle("has-status-dock", height > 0);
        }).observe(dock);
        return dock;
    }

    function icon(name) {
        const node = el("i", `bi bi-${name}`);
        node.setAttribute("aria-hidden", "true");
        return node;
    }

    function lead(spec) {
        if (spec.toggle) {
            const label = el("label", "status-bar-lead status-bar-toggle");
            const box = el("input", "form-check-input mt-0");
            box.type = "checkbox";
            box.checked = !!spec.toggle.checked;
            box.indeterminate = !!spec.toggle.indeterminate;
            box.disabled = !!spec.toggle.disabled;
            box.dataset.statusRole = "toggle";
            box.setAttribute("aria-label", spec.toggle.label || "全选");
            box.addEventListener("change", () => spec.toggle.onChange?.(box.checked));
            label.append(box);
            return label;
        }
        if (spec.icon === "spinner") {
            const wrap = el("span", "status-bar-lead");
            const spin = el("span", "spinner-border spinner-border-sm");
            spin.setAttribute("aria-hidden", "true");
            wrap.append(spin);
            return wrap;
        }
        if (spec.icon) {
            const wrap = el("span", "status-bar-lead");
            wrap.append(icon(spec.icon));
            return wrap;
        }
        return null;
    }

    function actionButton(action, index) {
        const node = el("button", `btn btn-sm ${action.variant === "secondary" ? "chip-btn" : "media-primary-action"} px-3`, action.label);
        node.type = "button";
        node.disabled = !!action.disabled;
        node.dataset.statusRole = `action-${index}`;
        node.addEventListener("click", () => action.run?.());
        return node;
    }

    function render(key) {
        const spec = entries.get(key);
        const layer = ensureDock().querySelector(spec.layer === "task" ? ".is-task" : ".is-page");
        let bar = dock.querySelector(`[data-status-key="${CSS.escape(key)}"]`);
        const fresh = !bar;
        if (fresh) {
            bar = el("div", "status-bar is-entering");
            bar.dataset.statusKey = key;
            bar.addEventListener("pointerenter", () => pause(key, true));
            bar.addEventListener("pointerleave", () => pause(key, false));
            bar.addEventListener("focusin", () => pause(key, true));
            bar.addEventListener("focusout", () => pause(key, false));
        }
        if (bar.parentElement !== layer) layer.append(bar);
        bar.classList.remove("is-leaving");
        const focused = bar.contains(document.activeElement) ? document.activeElement.dataset.statusRole : null;
        bar.className = `status-bar${fresh ? " is-entering" : ""}${spec.tone ? ` is-${spec.tone}` : ""}`;
        // 进度频繁刷新时沿用旧的转圈与进度条节点：转圈不重新开始，进度条宽度平滑过渡。
        const oldSpinner = spec.icon === "spinner" ? bar.querySelector(":scope > .status-bar-lead:has(.spinner-border)") : null;
        const oldTrack = typeof spec.progress === "number" ? bar.querySelector(":scope > .status-bar-progress:not(.is-indeterminate)") : null;
        bar.replaceChildren();

        const front = oldSpinner || lead(spec);
        if (front) bar.append(front);
        const body = el("div", "status-bar-body");
        const text = el("span", "status-bar-text", spec.text || "");
        body.append(text);
        if (spec.detail) body.append(el("span", "status-bar-detail", spec.detail));
        if (spec.link) {
            const link = el("button", "btn btn-link status-bar-link", spec.link.label);
            link.type = "button";
            link.dataset.statusRole = "link";
            link.addEventListener("click", () => spec.link.run?.());
            body.append(link);
        }
        // 进度类内容频繁刷新，只让文字区域在结果/校验时播报。
        body.setAttribute("aria-live", spec.progress != null ? "off" : spec.tone === "danger" ? "assertive" : "polite");
        bar.append(body);

        const actions = (spec.actions || []).filter(Boolean);
        if (actions.length || spec.dismissible) {
            const group = el("div", "status-bar-actions");
            actions.forEach((action, index) => group.append(actionButton(action, index)));
            if (spec.dismissible) {
                const close = el("button", "btn icon-btn status-bar-close");
                close.type = "button";
                close.setAttribute("aria-label", "关闭");
                close.dataset.statusRole = "close";
                close.append(icon("x-lg"));
                close.addEventListener("click", () => clear(key));
                group.append(close);
            }
            bar.append(group);
        }

        if (spec.progress != null) {
            const track = oldTrack || el("span", `status-bar-progress${spec.progress === "indeterminate" ? " is-indeterminate" : ""}`);
            track.setAttribute("role", "progressbar");
            track.setAttribute("aria-label", spec.text || "进度");
            track.setAttribute("aria-valuemin", "0");
            track.setAttribute("aria-valuemax", "100");
            if (spec.progress !== "indeterminate") {
                const percent = Math.round(Math.min(Math.max(spec.progress, 0), 1) * 100);
                track.style.setProperty("--progress", `${percent}%`);
                track.setAttribute("aria-valuenow", String(percent));
            }
            bar.append(track);
        }

        if (focused) bar.querySelector(`[data-status-role="${focused}"]:not(:disabled)`)?.focus({ preventScroll: true });
        if (fresh) requestAnimationFrame(() => requestAnimationFrame(() => bar.classList.remove("is-entering")));
    }

    function schedule(key) {
        clearTimeout(timers.get(key)?.id);
        const spec = entries.get(key);
        if (!spec?.duration) { timers.delete(key); return; }
        timers.set(key, { id: setTimeout(() => clear(key), spec.duration), paused: false });
    }

    function pause(key, value) {
        const timer = timers.get(key), spec = entries.get(key);
        if (!timer || !spec?.duration) return;
        clearTimeout(timer.id);
        timer.paused = value;
        if (!value) timer.id = setTimeout(() => clear(key), 2000);
    }

    function set(key, spec) {
        entries.set(key, { layer: "page", ...spec });
        render(key);
        schedule(key);
        return key;
    }

    function clear(key) {
        if (!entries.has(key)) return;
        entries.delete(key);
        clearTimeout(timers.get(key)?.id);
        timers.delete(key);
        const bar = dock?.querySelector(`[data-status-key="${CSS.escape(key)}"]`);
        if (!bar) return;
        bar.classList.add("is-leaving");
        const done = () => { if (!entries.has(key)) bar.remove(); };
        if (matchMedia("(prefers-reduced-motion: reduce)").matches) done();
        else setTimeout(done, 200);
    }

    const toneIcon = { success: "check2-circle", warning: "exclamation-triangle-fill", danger: "x-octagon-fill", info: "info-circle" };

    window.StatusBar = {
        set,
        clear,
        has: key => entries.has(key),

        selection(key, { text, checked, indeterminate, onToggle, toggleLabel, action, secondary } = {}) {
            return set(key, {
                toggle: { checked, indeterminate, onChange: onToggle, label: toggleLabel },
                text,
                actions: [secondary && { ...secondary, variant: "secondary" }, action],
            });
        },

        check(key, { state = "idle", text, link, action } = {}) {
            const icons = { ok: "check2-circle", warning: "exclamation-triangle-fill", loading: "spinner", idle: null };
            return set(key, { icon: icons[state], tone: state === "ok" ? "success" : state === "warning" ? "warning" : "", text, link, actions: [action] });
        },

        progress(key, { text, detail, done, total, layer = "page", action } = {}) {
            const known = Number(total) > 0;
            return set(key, {
                layer,
                icon: "spinner",
                text,
                detail: detail ?? (known ? `${done || 0}/${total}` : ""),
                progress: known ? (Number(done) || 0) / Number(total) : "indeterminate",
                actions: [action && { ...action, variant: "secondary" }],
            });
        },

        result(key, { tone = "success", text, detail, action, duration = 5000, layer = "task" } = {}) {
            return set(key, { layer, tone, icon: toneIcon[tone] || toneIcon.info, text, detail, actions: [action && { ...action, variant: "secondary" }], duration, dismissible: true });
        },

        notice(key, { icon = "lightbulb", text, detail, action } = {}) {
            return set(key, { icon, text, detail, actions: [action && { ...action, variant: "secondary" }] });
        },

        pending(key, { text = "有未保存的修改", discard, save } = {}) {
            return set(key, { icon: "pencil", tone: "info", text, actions: [discard && { label: "放弃", ...discard, variant: "secondary" }, save && { label: "保存", ...save }] });
        },
    };
})();
