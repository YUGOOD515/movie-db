(() => {
    const form = document.querySelector("[data-drive-form]");
    if (!form) return;
    const kindInput = form.querySelector("[data-drive-kind]");
    const mountInput = form.querySelector("[data-drive-mount-input]");
    const resultBox = form.querySelector("[data-drive-result]");
    const saveButton = form.querySelector("[data-drive-save]");
    const page = document.querySelector("[data-drive-page]");

    function syncKind() {
        const kind = kindInput.value;
        form.querySelectorAll("[data-drive-fields]").forEach(node => { node.hidden = node.dataset.driveFields !== kind; });
        form.querySelector("[data-drive-mount]").hidden = kind === "alist";
        if (kind === "alist") mountInput.value = "*";
        else if (mountInput.value === "*") mountInput.value = "";
    }

    function showResult(result) {
        resultBox.className = `small ${result.ok ? "text-success" : "text-danger"}`;
        resultBox.textContent = result.ok ? (result.status || "已保存") : (result.message || "操作失败");
    }

    async function post(url, body = {}) {
        try {
            const response = await fetch(url, {
                method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify(body),
            });
            return await response.json();
        } catch {
            return { ok: false, message: "MovieDB 没有响应，请稍后重试。" };
        }
    }

    async function save() {
        saveButton.disabled = true;
        resultBox.className = "small text-body-secondary";
        resultBox.textContent = "正在检测…";
        const kind = kindInput.value;
        const config = {};
        const fields = form.querySelector(`[data-drive-fields="${kind}"]`);
        fields.querySelectorAll("[data-drive-config]").forEach(input => {
            config[input.dataset.driveConfig] = input.type === "checkbox" ? (input.checked ? "1" : "") : input.value;
        });
        const result = await post("/manager/drives/save", { mount: mountInput.value.trim(), kind, config });
        saveButton.disabled = false;
        showResult(result);
        if (result.ok) {
            document.dispatchEvent(new CustomEvent("drive-saved", { detail: result }));
            if (page) window.setTimeout(() => window.location.reload(), 300);
        }
        return result;
    }

    kindInput.addEventListener("change", syncKind);
    saveButton.addEventListener("click", save);
    syncKind();

    if (page) {
        page.querySelectorAll("[data-drive-action]").forEach(button => button.addEventListener("click", async () => {
            const action = button.dataset.driveAction;
            const mount = button.dataset.mount;
            if (action === "edit") {
                const drive = JSON.parse(button.dataset.drive);
                kindInput.value = drive.kind;
                syncKind();
                mountInput.value = drive.mount;
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
                form.scrollIntoView({ behavior: "smooth", block: "start" });
                return;
            }
            if (action === "delete" && !window.confirm(`确定删除挂载“${mount}”吗？媒体库记录不会删除。`)) return;
            button.disabled = true;
            const result = await post(`/manager/drives/${encodeURIComponent(mount)}/${action}`);
            button.disabled = false;
            if (!result.ok) { showResult(result); return; }
            window.location.reload();
        }));
    }
})();
