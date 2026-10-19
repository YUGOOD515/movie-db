// Media manager: batch import rows, rating refresh progress and artwork picking.
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
        });
        updateRows();
    });

    // Manager rating refresh: one local background job with a persistent
    // bottom-center progress notification.
    document.querySelectorAll("[data-rating-refresh]").forEach((button) => {
        let pollTimer = null;

        function progressToast() {
            let toast = document.querySelector("[data-rating-progress-toast]");
            if (toast) {
                return toast;
            }
            toast = document.createElement("div");
            toast.className = "rating-progress-toast";
            toast.dataset.ratingProgressToast = "";
            toast.setAttribute("role", "status");
            toast.setAttribute("aria-live", "polite");
            toast.innerHTML = `
                <div class="d-flex align-items-center justify-content-between gap-3 mb-2">
                    <span class="small fw-semibold" data-rating-progress-label>正在刷新评分</span>
                    <span class="small text-body-secondary" data-rating-progress-count>0 / 0</span>
                </div>
                <div class="progress" role="progressbar" aria-label="评分刷新进度" aria-valuemin="0" aria-valuemax="100">
                    <div class="progress-bar" data-rating-progress-bar style="width: 0%"></div>
                </div>`;
            document.body.appendChild(toast);
            return toast;
        }

        function renderProgress(status) {
            const toast = progressToast();
            const total = Number(status.total) || 0;
            const completed = Number(status.completed) || 0;
            const percent = total ? Math.round((completed / total) * 100) : 100;
            toast.querySelector("[data-rating-progress-label]").textContent = status.running
                ? `正在刷新评分${status.current_title_id ? ` · ${status.current_title_id}` : ""}`
                : "评分刷新完成";
            toast.querySelector("[data-rating-progress-count]").textContent = `${completed} / ${total}`;
            const bar = toast.querySelector("[data-rating-progress-bar]");
            bar.style.width = `${percent}%`;
            bar.closest("[role='progressbar']").setAttribute("aria-valuenow", String(percent));
            toast.classList.add("rating-progress-toast--show");
            document.body.classList.add("rating-progress-active");
        }

        function finishProgress(status) {
            renderProgress(status);
            button.disabled = false;
            window.setTimeout(() => {
                document.querySelector("[data-rating-progress-toast]")?.classList.remove(
                    "rating-progress-toast--show",
                );
                document.body.classList.remove("rating-progress-active");
                const message = `评分刷新完成：成功 ${status.succeeded} 条，失败 ${status.failed} 条。`;
                window.showAppToast?.(message, status.failed ? "warning" : "success");
            }, 900);
        }

        async function pollStatus() {
            try {
                const response = await fetch(button.dataset.statusUrl);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const status = await response.json();
                if (status.running) {
                    button.disabled = true;
                    renderProgress(status);
                    pollTimer = window.setTimeout(pollStatus, 600);
                } else {
                    finishProgress(status);
                }
            } catch {
                button.disabled = false;
                window.showAppToast?.("无法读取评分刷新进度。", "danger");
            }
        }

        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                const response = await fetch(button.dataset.startUrl, { method: "POST" });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const status = await response.json();
                renderProgress(status);
                window.clearTimeout(pollTimer);
                pollTimer = window.setTimeout(pollStatus, 300);
            } catch {
                button.disabled = false;
                window.showAppToast?.("评分刷新启动失败。", "danger");
            }
        });

        fetch(button.dataset.statusUrl)
            .then((response) => response.ok ? response.json() : null)
            .then((status) => {
                if (status?.running) {
                    button.disabled = true;
                    renderProgress(status);
                    pollTimer = window.setTimeout(pollStatus, 600);
                }
            })
            .catch(() => {});
    });

    // Overview cards artwork preview trial and confirmation
    document.querySelectorAll(".overview-card").forEach((card) => {
        const form = card.querySelector(".overview-card-form");
        const confirmBtn = card.querySelector("[data-artwork-confirm]");
        if (!form || !confirmBtn) {
            return;
        }

        const options = card.querySelectorAll("[data-artwork-option]");
        options.forEach((btn) => {
            btn.addEventListener("click", () => {
                const imageType = btn.dataset.type;
                const position = btn.dataset.position;
                const src = btn.dataset.src;

                // 1. Update preview image in card
                const previewImg = card.querySelector(
                    `[data-preview-type="${imageType}"]`,
                );
                if (previewImg && src) {
                    previewImg.src = src;
                    previewImg.classList.remove("d-none");
                    const fallback = previewImg.parentElement?.querySelector(".image-fallback");
                    if (fallback) {
                        fallback.classList.add("d-none");
                    }
                }

                // 2. Update hidden form input
                const hiddenInput = form.querySelector(
                    `[data-artwork-input="${imageType}"]`,
                );
                if (hiddenInput) {
                    hiddenInput.value = position;
                }

                // 3. Update dropdown items status & active states
                const siblings = card.querySelectorAll(
                    `[data-artwork-option][data-type="${imageType}"]`,
                );
                siblings.forEach((sib) => {
                    const statusSpan = sib.querySelector(".artwork-option-status");
                    const checkBadge = sib.querySelector(".artwork-poster-check");
                    if (sib === btn) {
                        sib.classList.add("active");
                        if (statusSpan) {
                            statusSpan.textContent = position === "1" ? "当前使用" : "试用中";
                        }
                        if (checkBadge) {
                            checkBadge.classList.remove("d-none");
                        }
                    } else {
                        sib.classList.remove("active");
                        if (statusSpan) {
                            statusSpan.textContent = sib.dataset.position === "1" ? "原首选" : "点击试用";
                        }
                        if (checkBadge) {
                            checkBadge.classList.add("d-none");
                        }
                    }
                });

                // 4. Check if any image is changed from default (position !== "1")
                const inputs = form.querySelectorAll("[data-artwork-input]");
                let hasChanges = false;
                inputs.forEach((input) => {
                    if (input.value !== "1") {
                        hasChanges = true;
                    }
                });

                if (hasChanges) {
                    confirmBtn.classList.remove("d-none");
                } else {
                    confirmBtn.classList.add("d-none");
                }
            });
        });

        // Save via fetch: no reload, scroll position preserved, floating toast feedback
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            confirmBtn.disabled = true;
            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    body: new FormData(form),
                });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                window.showAppToast?.("保存成功", "success");
                syncCardAfterSave();
            } catch {
                window.showAppToast?.("保存失败", "danger");
            } finally {
                confirmBtn.disabled = false;
            }
        });

        // The server swaps the selected image into position 1; mirror that in the DOM
        function syncCardAfterSave() {
            form.querySelectorAll("[data-artwork-input]").forEach((input) => {
                const position = input.value;
                if (position === "1") {
                    return;
                }
                const imageType = input.dataset.artworkInput;
                const chosen = card.querySelector(
                    `[data-artwork-option][data-type="${imageType}"][data-position="${position}"]`,
                );
                const previous = card.querySelector(
                    `[data-artwork-option][data-type="${imageType}"][data-position="1"]`,
                );
                if (chosen) {
                    chosen.dataset.position = "1";
                    chosen.classList.add("active");
                    setOptionState(chosen, "当前使用");
                }
                if (previous) {
                    previous.dataset.position = position;
                    previous.classList.remove("active");
                    setOptionState(previous, "点击试用");
                }
                input.value = "1";
            });
            confirmBtn.classList.add("d-none");
        }

        function setOptionState(option, status) {
            const statusSpan = option.querySelector(".artwork-option-status");
            if (statusSpan) {
                statusSpan.textContent = status;
            }
            const checkBadge = option.querySelector(".artwork-poster-check");
            if (checkBadge) {
                checkBadge.classList.toggle("d-none", status !== "当前使用");
            }
        }
    });
})();
