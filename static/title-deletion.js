(() => {
    const opener = document.querySelector("[data-title-delete-open]");
    const dialog = document.getElementById("title-deletion");
    if (!opener || !dialog) return;
    const $ = selector => dialog.querySelector(selector);
    const modal = bootstrap.Modal.getOrCreateInstance(dialog);
    const confirm = $("[data-delete-confirm]");
    const status = $("[data-delete-status]");
    const body = $("[data-delete-body]");
    let token = null, controller = null, executing = false;

    async function check(message = "正在检查数据库和本地图片…") {
        controller?.abort();
        const request = new AbortController();
        controller = request;
        token = null;
        confirm.disabled = true;
        confirm.textContent = "确认删除";
        body.setAttribute("aria-busy", "true");
        $("[data-delete-summary]").classList.add("d-none");
        $("[data-delete-retry]").classList.add("d-none");
        status.classList.remove("d-none", "text-danger");
        status.textContent = message;
        try {
            const response = await fetch(opener.dataset.previewUrl, { signal: request.signal, cache: "no-store" });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "检查失败，请重试。");
            if (request.signal.aborted) return;
            $("[data-delete-summary]").innerHTML = data.html;
            dialog.querySelectorAll("[data-delete-watched]").forEach(node => {
                const value = node.dataset.deleteWatched;
                const date = new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : value.replace(" ", "T") + "Z");
                if (!Number.isNaN(date.getTime())) {
                    node.dateTime = date.toISOString();
                    node.textContent = date.toLocaleDateString("zh-CN");
                }
            });
            token = data.token;
            confirm.textContent = data.confirm_label;
            confirm.disabled = false;
            status.classList.add("d-none");
            $("[data-delete-summary]").classList.remove("d-none");
        } catch (error) {
            if (request.signal.aborted) return;
            status.textContent = error.message;
            status.classList.add("text-danger");
            $("[data-delete-retry]").classList.remove("d-none");
        } finally {
            if (controller === request) body.setAttribute("aria-busy", "false");
        }
    }

    opener.addEventListener("click", () => { modal.show(); check(); });
    $("[data-delete-retry]").addEventListener("click", () => check());
    dialog.addEventListener("hide.bs.modal", event => {
        if (executing) event.preventDefault();
        else { controller?.abort(); token = null; }
    });
    confirm.addEventListener("click", async () => {
        if (!token || executing) return;
        executing = true;
        confirm.disabled = true;
        confirm.textContent = "正在删除…";
        dialog.querySelectorAll("[data-delete-dismiss]").forEach(node => { node.disabled = true; });
        body.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(opener.dataset.deleteUrl, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
            });
            const data = await response.json();
            if (!response.ok) {
                window.showAppToast(data.error || "删除失败，请重试。", response.status === 409 ? "warning" : "danger");
                await check();
                return;
            }
            window.location.assign(data.redirect_url);
        } catch (error) {
            window.showAppToast("无法确认删除结果，请重新检查作品状态。", "danger");
            await check();
        } finally {
            executing = false;
            dialog.querySelectorAll("[data-delete-dismiss]").forEach(node => { node.disabled = false; });
            body.setAttribute("aria-busy", "false");
        }
    });
})();
