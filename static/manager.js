// Media manager: batch import rows, rating refresh progress (status bar) and artwork picking.
(() => {
    // Media manager: editable batch rows and JSON-to-form loading.
    document.querySelectorAll("[data-batch-import-form]").forEach((form) => {
        const rows = form.querySelector("[data-batch-import-rows]");
        const rowTemplate = document.querySelector("[data-batch-import-template]");
        const addButton = form.querySelector("[data-batch-add]");
        const fileInput = form.querySelector("[data-batch-json-file]");
        const count = form.querySelector("[data-batch-count]");
        const submit = form.querySelector("[data-batch-submit]");
        if (!rows || !rowTemplate || !addButton || !fileInput) {
            return;
        }

        function updateRows() {
            const currentRows = Array.from(
                rows.querySelectorAll("[data-batch-import-row]"),
            );
            currentRows.forEach((row, index) => {
                const label = row.querySelector("[data-batch-row-label]");
                const remove = row.querySelector("[data-batch-remove]");
                if (label) {
                    label.textContent = String(index + 1);
                }
                if (remove) {
                    remove.disabled = currentRows.length === 1;
                }
            });
            if (count) {
                count.textContent = `共 ${currentRows.length} 条`;
            }
            addButton.disabled = currentRows.length >= 100;
        }

        function addRow(item = {}) {
            if (rows.querySelectorAll("[data-batch-import-row]").length >= 100) {
                return;
            }
            const fragment = rowTemplate.content.cloneNode(true);
            const row = fragment.querySelector("[data-batch-import-row]");
            ["title_id", "season_number", "episode_number", "alist_url"].forEach(
                (field) => {
                    const input = row?.querySelector(`[name="${field}"]`);
                    if (input) {
                        input.value = item[field] ?? "";
                    }
                },
            );
            rows.appendChild(fragment);
            updateRows();
        }

        addButton.addEventListener("click", () => addRow());
        form.addEventListener("batch:add-records", (event) => {
            const currentRows = Array.from(rows.querySelectorAll("[data-batch-import-row]"));
            // 初始全空行只是占位；保留所有已填写及用户额外添加的行。
            const placeholder = currentRows.length === 1 &&
                Array.from(currentRows[0].querySelectorAll("input")).every((input) => !input.value.trim());
            if (currentRows.length - Number(placeholder) + event.detail.length > 100) return;
            if (placeholder) currentRows[0].remove();
            event.detail.forEach(addRow);
            event.preventDefault(); // 确认记录已被接收。
        });
        rows.addEventListener("click", (event) => {
            const remove = event.target.closest("[data-batch-remove]");
            if (!remove || remove.disabled) {
                return;
            }
            remove.closest("[data-batch-import-row]")?.remove();
            updateRows();
        });

        fileInput.addEventListener("change", async () => {
            const file = fileInput.files?.[0];
            if (!file) {
                return;
            }
            try {
                const payload = JSON.parse(await file.text());
                const items = Array.isArray(payload) ? payload : payload?.items;
                if (!Array.isArray(items) || items.length === 0) {
                    throw new Error("JSON 必须包含非空的 items 数组。");
                }
                if (items.length > 100) {
                    throw new Error("单次最多录入 100 条数据。");
                }
                if (items.some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
                    throw new Error("items 中的每一条都必须是对象。");
                }
                rows.replaceChildren();
                items.forEach(addRow);
                window.showAppToast?.(`已载入 ${file.name}，共 ${items.length} 条；提交前仍可修改。`, "success");
            } catch (error) {
                window.showAppToast?.(
                    error instanceof SyntaxError
                        ? "JSON 文件格式错误，请检查后重新上传。"
                        : error.message,
                    "danger",
                );
            } finally {
                // 已载入为表单行，不再随表单重复上传；同名文件也可再次选择。
                fileInput.value = "";
            }
        });

        form.addEventListener("submit", () => {
            if (submit) {
                submit.disabled = true;
                submit.textContent = "正在批量录入…";
            }
            const total = rows.querySelectorAll("[data-batch-import-row]").length;
            window.StatusBar?.progress("batch-import", { text: `正在批量录入 ${total} 条`, detail: "完成后自动刷新" });
        });
        updateRows();
    });

    // 评分刷新：服务端单个后台任务，进度显示在统一状态栏（两个标签页都会接续显示）。
    const ratingStatusUrl = document.querySelector("[data-rating-status-url]")?.dataset.ratingStatusUrl;
    const ratingButton = document.querySelector("[data-rating-refresh]");
    if (ratingStatusUrl && window.StatusBar) {
        let pollTimer = null;
        let watching = false;

        function renderRating(status) {
            const total = Number(status.total) || 0;
            const completed = Number(status.completed) || 0;
            window.StatusBar.progress("rating", {
                layer: "task",
                text: status.current_title_id ? `正在刷新评分 · ${status.current_title_id}` : "正在刷新评分",
                done: completed,
                total,
            });
        }

        function finishRating(status) {
            if (ratingButton) ratingButton.disabled = false;
            if (!watching) return;
            watching = false;
            const failed = Number(status.failed) || 0;
            window.StatusBar.result("rating", {
                tone: failed ? "warning" : "success",
                text: `评分刷新完成：成功 ${status.succeeded || 0} 条${failed ? `，失败 ${failed} 条` : ""}`,
                duration: failed ? 0 : 5000,
            });
        }

        async function pollRating() {
            try {
                const response = await fetch(ratingStatusUrl);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const status = await response.json();
                if (status.running) {
                    watching = true;
                    if (ratingButton) ratingButton.disabled = true;
                    renderRating(status);
                    pollTimer = window.setTimeout(pollRating, 600);
                } else {
                    finishRating(status);
                }
            } catch {
                if (ratingButton) ratingButton.disabled = false;
                if (watching) {
                    watching = false;
                    window.StatusBar.result("rating", { tone: "danger", text: "无法读取评分刷新进度。", duration: 0 });
                }
            }
        }

        ratingButton?.addEventListener("click", async () => {
            ratingButton.disabled = true;
            try {
                const response = await fetch(ratingButton.dataset.startUrl, { method: "POST" });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                watching = true;
                renderRating(await response.json());
                window.clearTimeout(pollTimer);
                pollTimer = window.setTimeout(pollRating, 300);
            } catch {
                ratingButton.disabled = false;
                window.StatusBar.result("rating", {
                    tone: "danger",
                    text: "评分刷新启动失败。",
                    duration: 0,
                    action: { label: "重试", run: () => ratingButton.click() },
                });
            }
        });

        pollRating();
    }

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
