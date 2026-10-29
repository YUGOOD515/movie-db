(() => {
    const form = document.querySelector("[data-drive-form]");
    if (!form) return;
    const setup = form.hasAttribute("data-drive-setup");
    const kindInput = form.querySelector("[data-drive-kind]");
    const mountInput = form.querySelector("[data-drive-mount-input]");
    const resultBox = form.querySelector("[data-drive-result]");
    const saveButton = form.querySelector("[data-drive-save]");
    const cancelEditButton = form.querySelector("[data-drive-cancel-edit]");
    const page = document.querySelector("[data-drive-page]");
    const defaultMounts = { local: "本机", quark: "夸克", uc: "UC", aliyun: "阿里", baidu: "百度", pan123: "123", alist: "*" };
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

    function syncKind(prefill = false) {
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
        kindInput.querySelectorAll("[data-drive-edit-option]").forEach(option => option.remove());
        kindInput.selectedIndex = 0;
        form.querySelectorAll("[data-drive-secret]").forEach(input => {
            input.placeholder = input.dataset.drivePlaceholder || "";
        });
        if (page) page.querySelector("[data-drive-editor-title]").textContent = "添加网盘";
        if (cancelEditButton) cancelEditButton.hidden = true;
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
        const response = await fetch("/media?tab=drives", { headers: { Accept: "text/html" } });
        if (!response.ok) throw new Error("refresh");
        const document = new DOMParser().parseFromString(await response.text(), "text/html");
        const next = document.querySelector("[data-drive-list]");
        if (!next) throw new Error("refresh");
        page.querySelector("[data-drive-list]").replaceWith(next);
        bindDriveActions();
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
        const result = await post("/manager/drives/save", { mount: mountInput.value.trim(), kind, config });
        saveButton.disabled = false;
        saveButton.removeAttribute("aria-busy");
        if (result.ok && page) {
            try { await refreshDrives(); } catch {}
            resetEditor();
        }
        showResult(result);
        if (result.ok) document.dispatchEvent(new CustomEvent("drive-saved", { detail: result }));
        return result;
    }

    function editDrive(button) {
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
        syncKind();
        const fields = form.querySelector(`[data-drive-fields="${drive.kind}"]`);
        fields.querySelectorAll("[data-drive-config]").forEach(input => {
            const name = input.dataset.driveConfig;
            if (input.hasAttribute("data-drive-secret")) {
                input.value = "";
                input.placeholder = drive.saved[name] ? "已保存，留空保持不变" : "";
            } else if (input.type === "checkbox") input.checked = Boolean(drive.config[name]);
            else input.value = drive.config[name] || "";
        });
        page.querySelector("[data-drive-editor-title]").textContent = `编辑 ${mount}`;
        cancelEditButton.hidden = false;
        form.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function cardResult(card, result, pending = false) {
        renderResult(card.querySelector("[data-drive-card-result]"), result, pending);
        if (!pending) {
            const status = card.querySelector("[data-drive-status]");
            status.dataset.state = result.ok ? "ok" : "fail";
            card.querySelector("[data-drive-status-text]").textContent = result.ok ? "已连接" : "检测失败";
            const detail = result.ok ? (result.status || "已连接") : result.message;
            const detailBox = card.querySelector("[data-drive-status-detail]");
            detailBox.textContent = detail || "操作失败";
            detailBox.hidden = false;
        }
    }

    async function cardAction(button) {
        const card = button.closest("[data-drive-card]");
        const action = button.dataset.driveAction;
        const mount = button.dataset.mount;
        if (action === "edit") { editDrive(button); return; }
        if (action === "delete") {
            card.querySelector("[data-drive-delete-confirm]").hidden = false;
            return;
        }
        button.disabled = true;
        cardResult(card, null, true);
        const result = await post(`/manager/drives/${encodeURIComponent(mount)}/${action}`);
        button.disabled = false;
        if (!result.ok) { cardResult(card, result); return; }
        if (action === "test") { cardResult(card, result); return; }
        try { await refreshDrives(); } catch { cardResult(card, { ok: false, message: "操作已完成，请刷新页面查看。" }); }
    }

    kindInput.addEventListener("change", () => syncKind(true));
    saveButton.addEventListener("click", save);
    cancelEditButton?.addEventListener("click", resetEditor);
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
                if (editingMount === card.dataset.driveCard) resetEditor();
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
})();
