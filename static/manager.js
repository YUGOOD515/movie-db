// Media manager: rating refresh progress (status bar) and artwork picking.
(() => {
    // Progress belongs to the global sidebar; this page only submits the action.
    const ratingButton = document.querySelector("[data-rating-refresh]");
    window.addEventListener('tasks:update', event => {
        if (ratingButton) ratingButton.disabled = event.detail.tasks.some(task => task.id === 'rating' && task.state === 'running');
    });
    ratingButton?.addEventListener('click', async () => {
        ratingButton.disabled = true;
        try {
            const response = await fetch(ratingButton.dataset.startUrl, {method:'POST'});
            if (!response.ok) throw new Error('评分刷新启动失败');
            window.TaskCenter?.refresh();
        } catch (error) { ratingButton.disabled = false; window.showAppToast?.(error.message,'danger'); }
    });

    // 概览卡片：点选图片即时试用，未保存的修改统一显示在底部状态栏（放弃 / 保存）。
    const dirtyCards = new Set();

    function syncArtworkBar() {
        if (!window.StatusBar) return;
        if (!dirtyCards.size) {
            window.StatusBar.clear("artwork");
            return;
        }
        const names = [...dirtyCards].map((card) => card.dataset.name).filter(Boolean);
        window.StatusBar.pending("artwork", {
            text: dirtyCards.size === 1 && names[0] ? `《${names[0]}》的图片有未保存的修改` : `${dirtyCards.size} 部作品的图片有未保存的修改`,
            discard: { run: () => [...dirtyCards].forEach((card) => card.artwork.discard()) },
            save: { label: dirtyCards.size > 1 ? `保存 ${dirtyCards.size} 部` : "保存", run: saveAllArtwork },
        });
    }

    async function saveAllArtwork() {
        const cards = [...dirtyCards];
        window.StatusBar?.progress("artwork", { text: "正在保存图片", done: 0, total: cards.length });
        let saved = 0;
        const failed = [];
        for (const card of cards) {
            if (await card.artwork.save()) saved += 1;
            else failed.push(card);
            window.StatusBar?.progress("artwork", { text: "正在保存图片", done: saved + failed.length, total: cards.length });
        }
        if (failed.length) {
            window.StatusBar?.clear("artwork");
            window.StatusBar?.result("artwork-result", { tone: "danger", text: `${failed.length} 部作品的图片保存失败`, duration: 0, action: { label: "重试", run: saveAllArtwork } });
            syncArtworkBar();
        } else {
            window.StatusBar?.clear("artwork");
            window.StatusBar?.result("artwork-result", { tone: "success", text: saved > 1 ? `已保存 ${saved} 部作品的图片` : "图片已保存", duration: 3000 });
        }
    }

    window.addEventListener("beforeunload", (event) => {
        if (dirtyCards.size) {
            event.preventDefault();
            event.returnValue = "";
        }
    });

    document.querySelectorAll(".overview-card").forEach((card) => {
        const form = card.querySelector(".overview-card-form");
        if (!form) {
            return;
        }
        // 记录初始预览，放弃修改时原样恢复。
        const originals = new Map();
        card.querySelectorAll("[data-preview-type]").forEach((image) => {
            originals.set(image, { src: image.getAttribute("src"), hidden: image.classList.contains("d-none") });
        });
        // 保存原图按钮跟随当前预览的那张图。
        const downloads = new Map();
        card.querySelectorAll("[data-artwork-download]").forEach((link) => downloads.set(link, link.getAttribute("href")));

        function setOptionState(option, status) {
            const statusSpan = option.querySelector(".artwork-option-status");
            if (statusSpan) {
                statusSpan.textContent = status;
            }
            const checkBadge = option.querySelector(".artwork-poster-check");
            if (checkBadge) {
                checkBadge.classList.toggle("d-none", status !== "当前使用" && status !== "试用中");
            }
        }

        function markOptions(imageType, chosen) {
            card.querySelectorAll(`[data-artwork-option][data-type="${imageType}"]`).forEach((option) => {
                const active = option.dataset.position === chosen;
                option.classList.toggle("active", active);
                if (active) setOptionState(option, chosen === "1" ? "当前使用" : "试用中");
                else setOptionState(option, option.dataset.position === "1" ? "原首选" : "点击试用");
            });
        }

        function refreshDirty() {
            const changed = [...form.querySelectorAll("[data-artwork-input]")].some((input) => input.value !== "1");
            if (changed) dirtyCards.add(card);
            else dirtyCards.delete(card);
            card.classList.toggle("has-pending", changed);
            syncArtworkBar();
        }

        card.querySelectorAll("[data-artwork-option]").forEach((option) => {
            option.addEventListener("click", () => {
                const imageType = option.dataset.type;
                const preview = card.querySelector(`[data-preview-type="${imageType}"]`);
                if (preview && option.dataset.src) {
                    preview.src = option.dataset.src;
                    preview.classList.remove("d-none");
                    preview.parentElement?.querySelector(".image-fallback")?.classList.add("d-none");
                }
                const download = card.querySelector(`[data-artwork-download="${imageType}"]`);
                if (download && option.dataset.original) download.href = option.dataset.original;
                const input = form.querySelector(`[data-artwork-input="${imageType}"]`);
                if (input) input.value = option.dataset.position;
                markOptions(imageType, option.dataset.position);
                refreshDirty();
            });
        });

        function discard() {
            originals.forEach((state, image) => {
                image.setAttribute("src", state.src || "");
                image.classList.toggle("d-none", state.hidden);
            });
            downloads.forEach((href, link) => link.setAttribute("href", href));
            form.querySelectorAll("[data-artwork-input]").forEach((input) => {
                input.value = "1";
                markOptions(input.dataset.artworkInput, "1");
            });
            refreshDirty();
        }

        // 服务端把选中的图片换到第 1 位，这里同步页面状态。
        function syncAfterSave() {
            form.querySelectorAll("[data-artwork-input]").forEach((input) => {
                const position = input.value;
                if (position === "1") {
                    return;
                }
                const imageType = input.dataset.artworkInput;
                const chosen = card.querySelector(`[data-artwork-option][data-type="${imageType}"][data-position="${position}"]`);
                const previous = card.querySelector(`[data-artwork-option][data-type="${imageType}"][data-position="1"]`);
                if (chosen) chosen.dataset.position = "1";
                if (previous) previous.dataset.position = position;
                input.value = "1";
                markOptions(imageType, "1");
            });
            originals.forEach((state, image) => {
                state.src = image.getAttribute("src");
                state.hidden = image.classList.contains("d-none");
            });
            downloads.forEach((_, link) => downloads.set(link, link.getAttribute("href")));
            refreshDirty();
        }

        async function save() {
            try {
                const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                syncAfterSave();
                return true;
            } catch {
                return false;
            }
        }

        card.artwork = { discard, save };
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            if (dirtyCards.has(card)) saveAllArtwork();
        });
    });
})();
