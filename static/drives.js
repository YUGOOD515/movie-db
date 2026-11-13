(() => {
    const form = document.querySelector("[data-drive-form]");
    if (!form) return;
    const setup = form.hasAttribute("data-drive-setup");
    const kindInput = form.querySelector("[data-drive-kind]");
    const mountInput = form.querySelector("[data-drive-mount-input]");
    const resultBox = form.querySelector("[data-drive-result]");
    const saveButton = form.querySelector("[data-drive-save]");
    const drivePage = document.querySelector("[data-drive-page]");
    const page = drivePage?.closest("[data-association-workspace]") || drivePage;
    let selectedDrive = null;
    let inlineMode = false;
    // 弹窗挪到 body 下：页面容器的层叠上下文会把它压在内容后面
    const modalNode = page?.querySelector("[data-drive-modal]");
    if (modalNode) document.body.append(modalNode);
    const modal = modalNode ? bootstrap.Modal.getOrCreateInstance(modalNode) : null;
    const cancelButton = form.querySelector("[data-drive-cancel-edit]");

    function moveFormToModal() {
        if (!modalNode) return;
        modalNode.querySelector(".modal-body").append(form);
        inlineMode = false;
        const kindRow = kindInput.closest(".ui-row");
        if (kindRow) kindRow.hidden = false;
        cancelButton?.setAttribute("data-bs-dismiss", "modal");
        const actions = form.querySelector(".drive-form-actions");
        if (actions) actions.hidden = false;
    }

    function setInlineEditing(enabled) {
        const card = page.querySelector("[data-drive-detail-card]");
        if (!card) return;
        card.querySelector("[data-drive-inline-form]").disabled = !enabled;
        card.querySelector('[data-drive-action="edit"]').hidden = enabled;
        form.querySelector(".drive-form-actions").hidden = !enabled;
        if (enabled) form.querySelector(`[data-drive-fields="${kindInput.value}"] [data-drive-config]`)?.focus();
    }
    const defaultMounts = {
        local: "本机", quark: "夸克", quark_tv: "夸克", uc: "UC", uc_tv: "UC", aliyun: "阿里", baidu: "百度",
        pan115: "115", pan123: "123", onedrive: "OneDrive", gdrive: "Google", webdav: "WebDAV", alist: "*",
    };
    let qrTimer = null;
    let editingMount = "";

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    };
    const icon = name => window.appIcon(name);

    function renderResult(box, result, pending = false) {
        box.replaceChildren();
        box.className = `${setup ? "onboard" : "settings"}-result`;
        if (!result && !pending) return;
        if (pending) {
            const spinner = el("span", "spinner-border spinner-border-sm");
            spinner.setAttribute("aria-hidden", "true");
            box.classList.add("is-pending");
            box.append(spinner, el("span", null, "正在检测…"));
            return;
        }
        box.classList.add(result.ok ? "is-ok" : "is-fail");
        const head = el("p", `${setup ? "onboard" : "settings"}-result-head`);
        head.append(icon(result.ok ? "circle-check" : "circle-x"),
            el("strong", null, result.ok ? (result.status || "已保存") : (result.message || "操作失败")));
        box.append(head);
    }

    function showResult(result) {
        renderResult(resultBox, result);
    }

    function stopQr() {
        clearTimeout(qrTimer);
        qrTimer = null;
        form.querySelectorAll("[data-drive-qr], [data-drive-oauth-paste]").forEach(box => { box.hidden = true; });
        form.querySelectorAll("[data-drive-qr-start] span").forEach(label => { label.textContent = "获取二维码"; });
    }

    function syncKind(prefill = false) {
        stopQr();
        const kind = kindInput.value;
        form.querySelectorAll("[data-drive-fields]").forEach(node => { node.hidden = node.dataset.driveFields !== kind; });
        form.querySelector("[data-drive-mount]").hidden = setup || kind === "alist";
        if (prefill && !editingMount) mountInput.value = defaultMounts[kind] || kindInput.selectedOptions[0]?.textContent || "";
        if (kind === "alist" && editingMount) mountInput.value = "*";
    }

    function resetEditor() {
        editingMount = "";
        form.querySelectorAll("[data-drive-config]").forEach(input => {
            if (input.type === "checkbox") input.checked = input.defaultChecked;
            else if (input.tagName === "SELECT") {
                [...input.options].forEach(option => { option.selected = option.defaultSelected; });
            } else input.value = input.defaultValue;
        });
        mountInput.value = mountInput.defaultValue;
        kindInput.disabled = false;
        mountInput.readOnly = false;
        mountInput.disabled = false;
        kindInput.querySelectorAll("[data-drive-edit-option]").forEach(option => option.remove());
        kindInput.selectedIndex = 0;
        form.querySelectorAll("[data-drive-secret]").forEach(input => {
            input.placeholder = input.dataset.drivePlaceholder || "";
        });
        if (page) {
            modalNode.querySelector("[data-drive-editor-title]").textContent = "添加网盘";
            modalNode.querySelector("[data-drive-editor-icon]").replaceChildren(icon("plus"));
        }
        renderResult(resultBox, null);
        syncKind(true);
    }

    async function post(url, body = {}) {
        try {
            const response = await fetch(url, {
                method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify(body),
            });
            try { return await response.json(); } catch { return { ok: false, message: `请求失败（HTTP ${response.status}）` }; }
        } catch {
            return { ok: false, message: "MovieDB 没有响应，请稍后重试。" };
        }
    }

    async function refreshDrives() {
        const response = await fetch("/media?tab=associate", { headers: { Accept: "text/html" } });
        if (!response.ok) throw new Error("refresh");
        const nextDocument = new DOMParser().parseFromString(await response.text(), "text/html");
        const next = nextDocument.querySelector("[data-drive-list]");
        if (!next) throw new Error("refresh");
        page.querySelector("[data-drive-list]").replaceWith(next);
        bindDriveActions();
        if (selectedDrive) openDriveDetails(selectedDrive);
        document.dispatchEvent(new CustomEvent("drives:updated"));
    }

    async function save() {
        if (saveButton.disabled) return;
        saveButton.disabled = true;
        saveButton.setAttribute("aria-busy", "true");
        renderResult(resultBox, null, true);
        const kind = kindInput.value;
        const config = {};
        const fields = form.querySelector(`[data-drive-fields="${kind}"]`);
        fields.querySelectorAll("[data-drive-config]").forEach(input => {
            config[input.dataset.driveConfig] = input.type === "checkbox" ? (input.checked ? "1" : "") : input.value;
        });
        // 首次引导不做重名拦截：同名按更新处理，重名校验只用于网盘页的“添加”
        const result = await post("/manager/drives/save", { mount: mountInput.value.trim(), kind, config, create: !editingMount && !setup });
        saveButton.disabled = false;
        saveButton.removeAttribute("aria-busy");
        return finishSave(result);
    }

    async function finishSave(result) {
        if (result.ok && page) {
            const wasInline = inlineMode, wasEditing = Boolean(editingMount);
            try { await refreshDrives(); } catch {}
            if (!wasInline) modal?.hide();
            window.showAppToast?.(wasEditing ? "连接配置已更新" : "网盘已添加");
            if (result.message) window.showAppToast?.(result.message, "warning");
            return result;
        }
        showResult(result);
        if (result.ok) document.dispatchEvent(new CustomEvent("drive-saved", { detail: result }));
        return result;
    }

    function editDrive(button, inline = false) {
        const drive = JSON.parse(button.dataset.drive);
        const mount = drive.mount;
        resetEditor();
        editingMount = mount;
        if (!kindInput.querySelector(`option[value="${drive.kind}"]`)) {
            const option = new Option(drive.label, drive.kind);
            option.dataset.driveEditOption = "true";
            kindInput.add(option);
        }
        kindInput.value = drive.kind;
        kindInput.disabled = true;
        mountInput.value = mount;
        mountInput.readOnly = true;
        mountInput.disabled = true;
        syncKind();
        const fields = form.querySelector(`[data-drive-fields="${drive.kind}"]`);
        fields.querySelectorAll("[data-drive-config]").forEach(input => {
            const name = input.dataset.driveConfig;
            if (input.hasAttribute("data-drive-secret")) {
                input.value = "";
                input.placeholder = drive.saved[name] ? "已保存，留空保持不变" : input.dataset.drivePlaceholder || "";
            } else if (input.type === "checkbox") input.checked = Boolean(drive.config[name]);
            else input.value = drive.config[name] || "";
        });
        modalNode.querySelector("[data-drive-editor-title]").textContent = `编辑 ${mount}`;
        modalNode.querySelector("[data-drive-editor-icon]").replaceChildren(icon("pencil"));
        if (inline) {
            inlineMode = true;
            page.querySelector("[data-drive-inline-form]").append(form);
            kindInput.closest(".ui-row").hidden = true;
            form.querySelector("[data-drive-mount]").hidden = true;
            cancelButton?.removeAttribute("data-bs-dismiss");
            setInlineEditing(true);
        } else {
            moveFormToModal();
            openFromSidebar(() => modal?.show());
        }
    }

    function cardResult(card, result, pending = false) {
        renderResult(card.querySelector("[data-drive-card-result]"), result, pending);
        if (!pending) {
            const status = card.querySelector("[data-drive-status]");
            status.dataset.state = result.ok ? "ok" : "fail";
            card.querySelector("[data-drive-status-text]").textContent = result.ok ? "已连接" : "检测失败";
            // 详情只在比状态标签多出信息时显示
            const detail = result.ok ? "" : (result.message || "操作失败");
            const detailBox = card.querySelector("[data-drive-status-detail]");
            detailBox.textContent = detail;
            detailBox.hidden = !detail || detail === "已连接" || card.classList.contains("is-disabled");
        }
    }

    async function cardAction(button) {
        const card = button.closest("[data-drive-card]");
        const action = button.dataset.driveAction;
        const mount = button.dataset.mount;
        if (action === "edit") { editDrive(button, card.hasAttribute("data-drive-detail-card")); return; }
        if (action === "delete") {
            card.querySelector("[data-drive-delete-confirm]").hidden = false;
            return;
        }
        button.disabled = true;
        cardResult(card, null, true);
        const result = await post(`/manager/drives/${encodeURIComponent(mount)}/${action}`);
        button.disabled = false;
        if (action === "test") {
            cardResult(card, result);
            try {
                await refreshDrives();
                const fresh = page.querySelector("[data-drive-detail-card]");
                if (fresh) cardResult(fresh, result);
            } catch {}
            return;
        }
        if (!result.ok) { cardResult(card, result); return; }
        try { await refreshDrives(); } catch { cardResult(card, { ok: false, message: "操作已完成，请刷新页面查看。" }); }
        // 路由变化（停用、移交透传等）后服务端已开始重新扫描
        if (result.message) window.showAppToast?.(result.message, "warning");
    }

    // 扫码登录：获取二维码后每 2 秒查询一次，确认后服务端已保存挂载，这里按保存成功处理
    form.querySelectorAll("[data-drive-qr-start]").forEach(button => button.addEventListener("click", async () => {
        const fields = button.closest("[data-drive-fields]");
        const box = fields.querySelector("[data-drive-qr]");
        const image = fields.querySelector("[data-drive-qr-image]");
        const status = fields.querySelector("[data-drive-qr-status]");
        const hint = fields.querySelector("[data-drive-qr-hint]");
        stopQr();
        renderResult(resultBox, null);
        button.disabled = true;
        const started = await post("/manager/drives/qr-login", {
            action: "start", kind: kindInput.value, mount: mountInput.value.trim(), create: !editingMount && !setup,
        });
        button.disabled = false;
        if (!started.ok) { showResult(started); return; }
        image.src = started.qr;
        image.classList.remove("is-expired");
        hint.textContent = started.hint || "";
        hint.hidden = !started.hint;
        status.textContent = "请扫码并在手机上确认登录…";
        box.hidden = false;
        button.querySelector("span").textContent = "重新获取";
        const deadline = Date.now() + (started.expires_in || 300) * 1000;
        const poll = async () => {
            if (Date.now() > deadline) {
                image.classList.add("is-expired");
                status.textContent = "二维码已过期，请重新获取。";
                return;
            }
            const result = await post("/manager/drives/qr-login", { action: "poll", login_id: started.login_id });
            if (result.ok && result.pending) { qrTimer = setTimeout(poll, 2000); return; }
            if (!result.ok) {
                image.classList.add("is-expired");
                status.textContent = result.message || "登录失败，请重新获取二维码。";
                return;
            }
            status.textContent = "登录成功，已保存。";
            await finishSave(result);
        };
        qrTimer = setTimeout(poll, 2000);
    }));

    // 网页授权：桌面版（OneDrive）在 MovieDB 窗口内完成并自动读取；其余打开浏览器，授权后粘贴网址
    async function finishOauth(text) {
        renderResult(resultBox, null, true);
        const result = await post("/manager/drives/oauth", {
            kind: kindInput.value, mount: mountInput.value.trim(), url: text, create: !editingMount && !setup,
        });
        if (!result.ok) { showResult(result); return; }
        await finishSave(result);
    }

    form.querySelectorAll("[data-drive-oauth-start]").forEach(button => button.addEventListener("click", async () => {
        const fields = button.closest("[data-drive-fields]");
        const paste = fields.querySelector("[data-drive-oauth-paste]");
        const page = button.dataset.oauthPage;
        renderResult(resultBox, null);
        if (button.dataset.oauthEmbedded === "true" && window.pywebview?.api?.authorize) {
            button.disabled = true;
            const result = await window.pywebview.api.authorize(button.dataset.oauthUrl);
            button.disabled = false;
            if (result) { await finishOauth(result); return; }
            showResult({ ok: false, message: "没有完成授权。可以重试，或在浏览器里授权后粘贴 Refresh Token。" });
            if (window.pywebview?.api?.open_external) window.pywebview.api.open_external(page);
        } else if (window.pywebview?.api?.open_external) {
            window.pywebview.api.open_external(page);
        } else {
            window.open(page, "_blank", "noopener");
        }
        paste.hidden = false;
        paste.querySelector("[data-drive-oauth-input]").focus();
    }));
    form.querySelectorAll("[data-drive-oauth-finish]").forEach(button => button.addEventListener("click", async () => {
        const input = button.closest("[data-drive-oauth-paste]").querySelector("[data-drive-oauth-input]");
        if (!input.value.trim()) { input.focus(); return; }
        button.disabled = true;
        await finishOauth(input.value.trim());
        button.disabled = false;
    }));

    kindInput.addEventListener("change", () => syncKind(true));
    saveButton.addEventListener("click", save);
    const openFromSidebar = open => {
        const sidebar = document.getElementById("association-sources");
        if (sidebar?.classList.contains("show") && matchMedia("(max-width: 991.98px)").matches) {
            sidebar.addEventListener("hidden.bs.offcanvas", open, { once: true });
            bootstrap.Offcanvas.getInstance(sidebar)?.hide();
        } else open();
    };
    function openDriveDetails(mount) {
        const template = page.querySelector(`template[data-drive-details="${CSS.escape(mount)}"]`);
        if (!template) {
            selectedDrive = null;
            document.dispatchEvent(new CustomEvent("drives:details-closed"));
            return;
        }
        selectedDrive = mount;
        moveFormToModal();
        page.querySelector("[data-drive-details-body]").replaceChildren(template.content.cloneNode(true));
        bindDriveActions();
        editDrive(page.querySelector('[data-drive-detail-card] [data-drive-action="edit"]'), true);
        setInlineEditing(false);
        page.querySelectorAll("[data-drive-details-open]").forEach(node => node.setAttribute("aria-expanded", String(node.dataset.driveDetailsOpen === mount)));
        openFromSidebar(() => document.dispatchEvent(new CustomEvent("drives:details", {detail:{mount}})));
    }
    page?.addEventListener("click", event => {
        const button = event.target.closest("[data-drive-details-open]");
        if (button) openDriveDetails(button.dataset.driveDetailsOpen);
    });
    page?.addEventListener("contextmenu", event => {
        const button = event.target.closest("[data-drive-details-open]");
        if (!button) return;
        event.preventDefault(); openDriveDetails(button.dataset.driveDetailsOpen);
    });
    document.addEventListener("association:view", event => {
        if (!page || event.detail.view === "drive") return;
        selectedDrive = null;
        page.querySelectorAll("[data-drive-details-open]").forEach(node => node.setAttribute("aria-expanded", "false"));
    });
    page?.querySelector("[data-drive-add]")?.addEventListener("click", () => openFromSidebar(() => { moveFormToModal(); resetEditor(); modal?.show(); }));
    cancelButton?.addEventListener("click", event => {
        if (!inlineMode) return;
        event.preventDefault();
        editDrive(page.querySelector('[data-drive-detail-card] [data-drive-action="edit"]'), true);
        setInlineEditing(false);
    });
    // 关闭弹窗即放弃编辑；下次打开从干净的表单开始
    modalNode?.addEventListener("hidden.bs.modal", () => { if (selectedDrive) openDriveDetails(selectedDrive); else resetEditor(); });
    modalNode?.addEventListener("shown.bs.modal", () => (editingMount ? form.querySelector(`[data-drive-fields="${kindInput.value}"] [data-drive-config]`) : kindInput)?.focus());
    syncKind(true);

    function bindDriveActions() {
        if (!page) return;
        page.querySelectorAll("[data-drive-action]").forEach(button => {
            if (button.dataset.driveBound) return;
            button.dataset.driveBound = "true";
            button.addEventListener("click", () => cardAction(button));
        });
        page.querySelectorAll("[data-drive-cancel-delete]").forEach(button => {
            if (button.dataset.driveBound) return;
            button.dataset.driveBound = "true";
            button.addEventListener("click", () => {
                button.closest("[data-drive-card]").querySelector("[data-drive-delete-confirm]").hidden = true;
            });
        });
        page.querySelectorAll("[data-drive-confirm-delete]").forEach(button => {
            if (button.dataset.driveBound) return;
            button.dataset.driveBound = "true";
            button.addEventListener("click", async () => {
                const card = button.closest("[data-drive-card]");
                button.disabled = true;
                const result = await post(`/manager/drives/${encodeURIComponent(card.dataset.driveCard)}/delete`);
                button.disabled = false;
                if (!result.ok) { cardResult(card, result); return; }
                try { await refreshDrives(); } catch {}
                window.showAppToast?.(`已删除 ${card.querySelector(".ui-row-title")?.textContent || card.dataset.driveCard}`);
                if (result.message) window.showAppToast?.(result.message, "warning");
            });
        });
    }
    bindDriveActions();

    form.querySelectorAll("[data-secret-textarea]").forEach(input => { input.style.webkitTextSecurity = "disc"; });
    form.querySelectorAll("[data-drive-reveal]").forEach(button => button.addEventListener("click", () => {
        const input = button.parentElement.querySelector("[data-drive-secret]");
        const textarea = input.hasAttribute("data-secret-textarea");
        const shown = textarea ? input.style.webkitTextSecurity !== "disc" : input.type === "text";
        if (textarea) input.style.webkitTextSecurity = shown ? "disc" : "none";
        else input.type = shown ? "password" : "text";
        button.replaceChildren(icon(shown ? "eye" : "eye-off"));
        button.setAttribute("aria-pressed", String(!shown));
        button.setAttribute("aria-label", button.getAttribute("aria-label").replace(/^(显示|隐藏)/, shown ? "显示" : "隐藏"));
        button.title = shown ? "显示" : "隐藏";
    }));

    const pickers = [...form.querySelectorAll("[data-pick-path]")];
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
            const picked = await window.pywebview.api.pick_path("folder", input.value.trim());
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

    // 从 AList / OpenList 导入：读取 → 预览每个存储的处理方式 → 勾选后导入。
    // 网盘页在弹窗里，首次引导页是可折叠的内联区域
    const importNode = page?.querySelector("[data-drive-import-modal]") || document.querySelector("[data-drive-import-inline]");
    if (importNode) {
        const inModal = importNode.hasAttribute("data-drive-import-modal");
        if (inModal) document.body.append(importNode);
        const importModal = inModal ? bootstrap.Modal.getOrCreateInstance(importNode) : null;
        const pathInput = importNode.querySelector("[data-drive-import-path]");
        const list = importNode.querySelector("[data-drive-import-list]");
        const importResult = importNode.querySelector("[data-drive-import-result]");
        const readButton = importNode.querySelector("[data-drive-import-read]");
        const applyButton = importNode.querySelector("[data-drive-import-apply]");
        const pickButton = importNode.querySelector("[data-drive-import-pick]");
        const PILL = { create: "ok", update: "ok", skip: "off" };

        const selected = () => [...list.querySelectorAll("[data-import-mount]:checked")].map(input => input.dataset.importMount);
        const syncApply = () => {
            const count = selected().length;
            applyButton.disabled = !count;
            applyButton.querySelector("span").textContent = count ? `导入 ${count} 个` : "导入";
        };

        function renderPreview(items) {
            list.replaceChildren();
            for (const item of items) {
                const row = el("label", "drive-item drive-import-item");
                const main = el("div", "drive-item-main");
                const check = el("input", "form-check-input m-0");
                check.type = "checkbox";
                check.dataset.importMount = item.mount;
                check.checked = item.action !== "skip";
                check.disabled = item.action === "skip";
                check.addEventListener("change", syncApply);
                const text = el("div", "ui-group-text");
                const hint = [item.label, item.reason, item.note].filter(Boolean).join(" · ");
                text.append(el("h3", "ui-row-title", item.mount_path || item.mount), el("p", "ui-row-hint", hint));
                const pill = el("span", "ui-pill", item.action_label);
                pill.dataset.state = PILL[item.action] || "off";
                main.append(check, text, pill);
                row.append(main);
                list.append(row);
            }
            if (!items.length) list.append(el("p", "drive-note", "这个配置里没有存储。"));
            list.hidden = false;
            applyButton.hidden = !items.some(item => item.action !== "skip");
            syncApply();
        }

        async function readConfig() {
            readButton.disabled = true;
            renderResult(importResult, null, true);
            const result = await post("/manager/drives/import-alist", { action: "preview", path: pathInput.value.trim() });
            readButton.disabled = false;
            if (!result.ok) {
                list.hidden = true;
                applyButton.hidden = true;
                renderResult(importResult, result);
                return;
            }
            renderResult(importResult, null);
            renderPreview(result.items || []);
        }

        async function applyImport() {
            const mounts = selected();
            if (!mounts.length) return;
            applyButton.disabled = readButton.disabled = true;
            renderResult(importResult, null, true);
            const result = await post("/manager/drives/import-alist", { action: "apply", path: pathInput.value.trim(), mounts });
            readButton.disabled = false;
            syncApply();
            const failed = (result.results || []).filter(item => !item.ok);
            if (!result.ok || failed.length) {
                const detail = failed.map(item => `/${item.mount}：${item.message}`).join("；");
                renderResult(importResult, { ok: false, message: [result.message, detail].filter(Boolean).join(" ") });
            } else if (inModal) {
                renderResult(importResult, null);
                importModal.hide();
            } else {
                renderResult(importResult, { ok: true, status: result.message });
            }
            if (!result.results) return;
            if (!page) {
                // 首次引导：导入成功即视为片源已就绪，交给 setup.js 进入下一步
                if (result.ok) document.dispatchEvent(new CustomEvent("drive-saved", { detail: result }));
                return;
            }
            try { await refreshDrives(); } catch {}
            if (result.ok) window.showAppToast?.(result.message);
            if (result.route_message) window.showAppToast?.(result.route_message, "warning");
            if (result.alist_unused) window.showAppToast?.("AList 中启用的存储已全部由 MovieDB 接管，可以停止 AList 服务并停用默认（透传）。", "warning");
        }

        page?.querySelector("[data-drive-import]")?.addEventListener("click", () => openFromSidebar(() => importModal?.show()));
        readButton.addEventListener("click", readConfig);
        applyButton.addEventListener("click", applyImport);
        pathInput.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); readConfig(); } });
        importNode.addEventListener("shown.bs.modal", () => pathInput.focus());
        importNode.addEventListener("hidden.bs.modal", () => {
            list.hidden = true;
            applyButton.hidden = true;
            renderResult(importResult, null);
        });
        const enablePick = () => {
            if (!pickButton || !window.pywebview?.api?.pick_path) return;
            pickButton.hidden = false;
            pickButton.parentElement.classList.add("has-picker");
        };
        pickButton?.addEventListener("click", async () => {
            const picked = await window.pywebview.api.pick_path("file", pathInput.value.trim());
            if (picked) { pathInput.value = picked; readConfig(); }
        });
        enablePick();
        addEventListener("pywebviewready", enablePick);
    }
})();
