(() => {
    function bindMediaFallback(root = document) {
        root.querySelectorAll("[data-media-image]:not([data-fallback-bound])").forEach((image) => {
            image.setAttribute("data-fallback-bound", "");
            image.addEventListener("error", () => {
                image.classList.add("d-none");
                const fallback = image.parentElement?.querySelector(".image-fallback")
                    || image.closest(".detail-hero")?.querySelector(".detail-backdrop-fallback");
                fallback?.classList.remove("d-none");
                fallback?.classList.add("d-flex");
            });
        });
        root.querySelectorAll("[data-media-logo]:not([data-fallback-bound])").forEach((logo) => {
            logo.setAttribute("data-fallback-bound", "");
            logo.addEventListener("error", () => logo.classList.add("d-none"));
        });
    }

    bindMediaFallback();

    // Theme toggle
    const themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
        const icon = themeToggle.querySelector("i");
        const label = themeToggle.querySelector(".theme-label");

        function syncThemeControl() {
            const isDark = document.documentElement.dataset.bsTheme === "dark";
            if (icon) {
                icon.className = isDark ? "bi bi-sun-fill" : "bi bi-moon-stars-fill";
            }
            if (label) {
                label.textContent = isDark ? "深色" : "浅色";
            }
            themeToggle.setAttribute(
                "aria-label",
                isDark ? "切换为浅色主题" : "切换为深色主题",
            );
            themeToggle.setAttribute(
                "title",
                isDark ? "切换为浅色主题" : "切换为深色主题",
            );
        }

        themeToggle.addEventListener("click", () => {
            const nextTheme = document.documentElement.dataset.bsTheme === "dark"
                ? "light"
                : "dark";
            document.documentElement.dataset.bsTheme = nextTheme;
            localStorage.setItem("media-theme", nextTheme);
            syncThemeControl();
        });

        syncThemeControl();
    }

    // Sidebar active states
    const isDetailPage = /^\/title\/[^/]+\/?$/.test(
        window.location.pathname,
    );
    document.querySelectorAll(".site-sidebar .nav-link").forEach((link) => {
        const isActive = link.pathname === window.location.pathname
            || (isDetailPage && link.pathname === "/");
        if (isActive) {
            link.setAttribute("aria-current", "page");
            link.classList.add("active");
        } else {
            link.removeAttribute("aria-current");
            link.classList.remove("active");
        }
    });

    const escapeHtml = (value) =>
        String(value ?? "").replace(/[&<>"']/g, (char) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
        })[char]);

    // Media manager: editable batch rows and JSON-to-form loading.
    document.querySelectorAll("[data-batch-import-form]").forEach((form) => {
        const rows = form.querySelector("[data-batch-import-rows]");
        const rowTemplate = document.querySelector("[data-batch-import-template]");
        const addButton = form.querySelector("[data-batch-add]");
        const fileInput = form.querySelector("[data-batch-json-file]");
        const fileStatus = form.querySelector("[data-batch-file-status]");
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
                fileStatus.textContent = `已载入 ${file.name}，共 ${items.length} 条；提交前仍可修改。`;
                fileStatus.classList.remove("text-danger");
                fileStatus.classList.add("text-success");
                fileInput.value = "";
            } catch (error) {
                fileStatus.textContent = error instanceof SyntaxError
                    ? "JSON 文件格式错误，请检查后重新上传。"
                    : error.message;
                fileStatus.classList.remove("text-success");
                fileStatus.classList.add("text-danger");
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

    // Detail overview: CSS line-clamp holds 2 lines on first paint; JS then
    // trims the text so the inline "...更多" toggle lands exactly at the end
    // of the second line, right after the last visible character.
    function initOverviewClamp(overviewWrap) {
        const overviewEl = overviewWrap.querySelector("[data-detail-overview]");
        const textEl = overviewWrap.querySelector("[data-detail-overview-text]");
        const overviewToggle = overviewWrap.querySelector("[data-detail-overview-toggle]");
        if (!overviewEl || !textEl || !overviewToggle) {
            return;
        }

        const expandedPanel = overviewWrap.querySelector(
            "[data-detail-overview-expanded]",
        );
        const collapseToggle = overviewWrap.querySelector(
            "[data-detail-overview-collapse]",
        );
        const fullText = (
            textEl.dataset.fullText || textEl.textContent
        ).trim();

        // Shield lives directly under <body>: fixed positioning then maps to
        // the viewport and the z-index compares in the root stacking context,
        // immune to any ancestor containment/stacking-context traps.
        let shieldEl = document.querySelector(".detail-overview-shield");

        function setExpanded(expanded) {
            if (expandedPanel) {
                expandedPanel.hidden = !expanded;
            }
            document.body.classList.toggle(
                "detail-overview-open",
                expanded,
            );
            if (expanded) {
                if (!shieldEl) {
                    shieldEl = document.createElement("div");
                    shieldEl.className = "detail-overview-shield";
                    shieldEl.addEventListener("click", () => setExpanded(false));
                    document.body.appendChild(shieldEl);
                }
                shieldEl.hidden = false;
            } else if (shieldEl) {
                shieldEl.hidden = true;
            }
            overviewToggle.setAttribute("aria-expanded", String(expanded));
        }

        function updateClamp() {
            if (overviewWrap.offsetParent === null) {
                return;
            }

            const computed = window.getComputedStyle(overviewEl);
            let lineHeight = parseFloat(computed.lineHeight);
            if (isNaN(lineHeight) || lineHeight <= 0) {
                lineHeight = parseFloat(computed.fontSize) * 1.5;
            }
            const maxHeight = lineHeight * 2 + 2;

            // Release the clamp to measure real content height (clamped
            // lines are not laid out, so scrollHeight would lie otherwise).
            overviewEl.style.webkitLineClamp = "unset";
            overviewEl.style.lineClamp = "unset";

            try {
                textEl.textContent = fullText;
                if (overviewEl.scrollHeight <= maxHeight) {
                    overviewToggle.classList.add("d-none");
                    return;
                }

                overviewToggle.classList.remove("d-none");
                let low = 0;
                let high = fullText.length;
                let best = 0;
                while (low <= high) {
                    const mid = Math.floor((low + high) / 2);
                    textEl.textContent = fullText.slice(0, mid);
                    if (overviewEl.scrollHeight <= maxHeight) {
                        best = mid;
                        low = mid + 1;
                    } else {
                        high = mid - 1;
                    }
                }
                textEl.textContent = fullText.slice(0, best);
            } finally {
                overviewEl.style.webkitLineClamp = "";
                overviewEl.style.lineClamp = "";
            }
        }

        overviewToggle.addEventListener("click", () => {
            setExpanded(true);
        });
        collapseToggle?.addEventListener("click", (event) => {
            event.stopPropagation();
            setExpanded(false);
        });

        updateClamp();
        window.addEventListener("resize", updateClamp);
        overviewWrap.closest("[data-episode-hero]")?.addEventListener(
            "detail:shown",
            () => window.requestAnimationFrame(updateClamp),
        );
    }

    document.querySelectorAll("[data-detail-overview-wrap]").forEach(initOverviewClamp);

    // Episode cards drive the Hero; playback remains an explicit action.
    document.querySelectorAll("[data-episode-browser]").forEach((browser) => {
        const selectors = browser.querySelectorAll("[data-episode-target]");
        const seasonControls = browser.querySelectorAll("[data-season-target]");
        const seasonPanes = browser.querySelectorAll("[data-season-pane]");
        const seasonSummaries = browser.querySelectorAll("[data-season-summary]");
        const seasonLabel = browser.querySelector("[data-season-label]");
        const backdropStage = browser.querySelector("[data-backdrop-stage]");
        const backdropFallback = browser.querySelector(".detail-backdrop-fallback");

        const heroPanelsContainer = browser.querySelector("[data-episode-hero-panels]");
        const heroControlsContainer = browser.querySelector(".detail-episode-controls");
        const factsPanelsContainer = browser.querySelector("[data-episode-facts-panels]");

        // Series/episode info toggle in the hero area.
        const seriesToggle = browser.querySelector("[data-series-toggle]");
        const seriesPanel = browser.querySelector("[data-series-hero-panel]");
        let seriesMode = true;

        function applySeriesMode() {
            seriesPanel?.classList.toggle("hero-pane-hidden", !seriesMode);
            seriesPanel?.setAttribute("aria-hidden", String(!seriesMode));
            heroPanelsContainer?.classList.toggle("hero-pane-hidden", seriesMode);
            heroPanelsContainer?.setAttribute("aria-hidden", String(seriesMode));
            if (!seriesToggle) {
                return;
            }
            seriesToggle.setAttribute("aria-pressed", String(seriesMode));
            seriesToggle.title = seriesMode ? "查看本集信息" : "查看全剧信息";
            seriesToggle.querySelector("[data-series-toggle-icon-tv]")
                ?.classList.toggle("d-none", seriesMode);
            seriesToggle.querySelector("[data-series-toggle-icon-episode]")
                ?.classList.toggle("d-none", !seriesMode);
            if (seriesMode) {
                switchBackdrop(seriesPanel?.dataset.heroBackdropUrl || "", "from-right");
            } else {
                const activePanel = browser.querySelector(
                    "[data-episode-hero]:not(.d-none)",
                );
                switchBackdrop(
                    activePanel?.dataset.heroBackdropUrl || "",
                    "from-left",
                );
            }
        }

        seriesToggle?.addEventListener("click", () => {
            seriesMode = !seriesMode;
            applySeriesMode();
        });

        async function ensureEpisodePanels(episodeId, url) {
            if (browser.querySelector(`[data-episode-hero="${CSS.escape(episodeId)}"]`)) {
                return;
            }
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const fragment = document.createElement("template");
            fragment.innerHTML = await response.text();
            const parts = [
                ["[data-episode-hero]", heroPanelsContainer],
                ["[data-episode-hero-control]", heroControlsContainer],
                ["[data-episode-facts]", factsPanelsContainer],
            ].map(([selector, container]) => [fragment.content.querySelector(selector), container]);
            if (parts.some(([panel, container]) => !panel || !container)
                || parts[0][0].dataset.episodeHero !== episodeId) {
                throw new Error("Invalid episode fragment");
            }
            // A second click can finish fetching the same episode first.
            if (browser.querySelector(`[data-episode-hero="${CSS.escape(episodeId)}"]`)) {
                return;
            }
            for (const [panel, container] of parts) {
                panel.hidden = true;
                panel.classList.add("d-none");
                container.appendChild(panel);
            }
            const overview = parts[0][0].querySelector("[data-detail-overview-wrap]");
            if (overview) {
                initOverviewClamp(overview);
            }
        }

        let currentSeasonNum = null;
        let currentEpisodeNum = null;

        const initialActivePanel = Array.from(
            browser.querySelectorAll("[data-episode-hero]"),
        ).find(
            (panel) => !panel.classList.contains("d-none") && !panel.hidden,
        ) || browser.querySelector("[data-episode-hero]");
        if (initialActivePanel) {
            currentSeasonNum = parseInt(initialActivePanel.dataset.seasonNumber, 10) || 0;
            currentEpisodeNum = parseInt(initialActivePanel.dataset.episodeNumber, 10) || 0;
        }

        function switchBackdrop(backdropUrl, direction) {
            if (!backdropStage) {
                return;
            }

            if (!backdropUrl) {
                backdropStage.innerHTML = "";
                backdropFallback?.classList.remove("d-none");
                backdropFallback?.classList.add("d-flex");
                return;
            }

            backdropFallback?.classList.add("d-none");
            backdropFallback?.classList.remove("d-flex");

            const currentActiveImg = backdropStage.querySelector(".detail-backdrop--active")
                || backdropStage.querySelector(".detail-backdrop:not(.detail-backdrop--to-left):not(.detail-backdrop--to-right)");

            if (currentActiveImg && (currentActiveImg.getAttribute("src") === backdropUrl || currentActiveImg.src.endsWith(backdropUrl))) {
                return;
            }

            // Immediately remove any elements already animating out to keep animation crisp under fast clicks
            backdropStage.querySelectorAll(".detail-backdrop--to-left, .detail-backdrop--to-right").forEach((el) => el.remove());

            const nextImg = document.createElement("img");
            nextImg.className = `detail-backdrop detail-backdrop--${direction}`;
            nextImg.src = backdropUrl;
            nextImg.alt = "";
            nextImg.width = 1920;
            nextImg.height = 1080;
            nextImg.setAttribute("data-media-image", "");
            nextImg.setAttribute("data-detail-backdrop", "");

            backdropStage.appendChild(nextImg);
            bindMediaFallback(backdropStage);

            // Force reflow
            void nextImg.offsetWidth;

            nextImg.classList.add("detail-backdrop--sliding");
            if (currentActiveImg) {
                currentActiveImg.classList.add("detail-backdrop--sliding");
                currentActiveImg.classList.remove("detail-backdrop--active");
                currentActiveImg.classList.add(direction === "from-left" ? "detail-backdrop--to-right" : "detail-backdrop--to-left");
            }

            requestAnimationFrame(() => {
                nextImg.classList.remove(`detail-backdrop--${direction}`);
                nextImg.classList.add("detail-backdrop--active");
            });

            setTimeout(() => {
                if (currentActiveImg && currentActiveImg.parentNode === backdropStage) {
                    currentActiveImg.remove();
                }
                nextImg.classList.remove("detail-backdrop--sliding");
            }, 550);
        }

        let selectionVersion = 0;
        async function selectEpisode(selector) {
            const version = ++selectionVersion;
            const targetKey = selector.dataset.episodeTarget;
            selector.setAttribute("aria-busy", "true");
            try {
                await ensureEpisodePanels(targetKey, selector.dataset.panelUrl);
            } catch {
                if (version === selectionVersion) {
                    showAppToast("单集信息加载失败，请重试。", "danger");
                }
                return;
            } finally {
                selector.removeAttribute("aria-busy");
            }
            if (version !== selectionVersion) {
                return;
            }

            const target = browser.querySelector(
                `[data-episode-hero="${CSS.escape(targetKey)}"]`,
            );
            if (!target) {
                return;
            }

            browser.querySelectorAll("[data-episode-hero]").forEach((panel) => {
                const isSelected = panel === target;
                panel.hidden = !isSelected;
                panel.classList.toggle("d-none", !isSelected);
            });
            browser.querySelectorAll("[data-episode-hero-control]").forEach((control) => {
                const isSelected = control.dataset.episodeHeroControl === targetKey;
                control.hidden = !isSelected;
                control.classList.toggle("d-none", !isSelected);
            });
            browser.querySelectorAll("[data-episode-facts]").forEach((panel) => {
                const isSelected = panel.dataset.episodeFacts === targetKey;
                panel.hidden = !isSelected;
                panel.classList.toggle("d-none", !isSelected);
            });

            selectors.forEach((item) => {
                const isSelected = item === selector;
                item.setAttribute("aria-pressed", String(isSelected));
                item.closest("[data-episode-card]")?.classList.toggle(
                    "active",
                    isSelected,
                );
            });

            const seasonPane = selector.closest("[data-season-pane]");
            if (seasonPane) {
                seasonPane.dataset.selectedEpisode = targetKey;
            }

            const newSeasonNum = parseInt(target.dataset.seasonNumber, 10) || 0;
            const newEpisodeNum = parseInt(target.dataset.episodeNumber, 10) || 0;

            let direction = "from-right";
            if (currentSeasonNum !== null && currentEpisodeNum !== null) {
                const currentScore = currentSeasonNum * 10000 + currentEpisodeNum;
                const newScore = newSeasonNum * 10000 + newEpisodeNum;
                if (newScore < currentScore) {
                    direction = "from-left";
                } else if (newScore > currentScore) {
                    direction = "from-right";
                }
            }

            currentSeasonNum = newSeasonNum;
            currentEpisodeNum = newEpisodeNum;

            const backdropUrl = target.dataset.heroBackdropUrl;
            if (seriesMode) {
                seriesMode = false;
                applySeriesMode();
            } else {
                switchBackdrop(backdropUrl, direction);
            }

            target.dispatchEvent(new Event("detail:shown"));
        }

        selectors.forEach((selector) => {
            selector.addEventListener("click", () => selectEpisode(selector));
        });

        seasonControls.forEach((control) => {
            control.addEventListener("click", () => {
                const targetId = control.dataset.seasonTarget;
                const targetPane = targetId
                    ? document.getElementById(targetId)
                    : null;
                if (!targetPane || !browser.contains(targetPane)) {
                    return;
                }

                seasonPanes.forEach((pane) => {
                    const isSelected = pane === targetPane;
                    pane.hidden = !isSelected;
                    pane.classList.toggle("d-none", !isSelected);
                });
                seasonControls.forEach((item) => {
                    const isSelected = item === control;
                    item.classList.toggle("active", isSelected);
                    item.classList.toggle("genre-dropdown-toggle--active", isSelected);
                    item.setAttribute("aria-pressed", String(isSelected));
                    item.setAttribute("aria-selected", String(isSelected));
                });
                seasonSummaries.forEach((summary) => {
                    const isSelected = summary.dataset.seasonSummary
                        === control.dataset.seasonKey;
                    summary.hidden = !isSelected;
                    summary.classList.toggle("d-none", !isSelected);
                });
                if (seasonLabel && control.dataset.seasonName) {
                    seasonLabel.textContent = control.dataset.seasonName;
                }

                const preferredKey = targetPane.dataset.selectedEpisode;
                const targetSelector = Array.from(
                    targetPane.querySelectorAll("[data-episode-target]"),
                ).find((item) => item.dataset.episodeTarget === preferredKey)
                    || targetPane.querySelector("[data-episode-target]");
                if (targetSelector) {
                    selectEpisode(targetSelector);
                }
            });
        });
    });

    // Global bottom-center notifications. Flask flash messages and client-side
    // actions share the same queue so notifications never overlap.
    const appToastQueue = [];
    let appToastActive = false;
    let appToastStatus = null;

    function renderAppToast(notification, persistent = false) {
        let toast = document.querySelector(".save-toast");
        if (!toast) {
            toast = document.createElement("div");
            toast.className = "save-toast";
            document.body.appendChild(toast);
        }
        if (!notification) {
            toast.classList.remove("save-toast--show");
            return;
        }
        const isError = ["danger", "error"].includes(notification.category);
        toast.replaceChildren(document.createTextNode(notification.message));
        if (notification.action) {
            const action = document.createElement("button");
            action.type = "button";
            action.className = "btn btn-link p-0 ms-3 save-toast-action";
            action.textContent = notification.action.label;
            action.addEventListener("click", notification.action.run);
            toast.append(action);
        }
        toast.setAttribute("role", isError ? "alert" : "status");
        toast.setAttribute("aria-live", isError ? "assertive" : "polite");
        toast.classList.toggle("save-toast--error", isError);
        toast.classList.toggle("save-toast--warning", notification.category === "warning");
        toast.classList.toggle("save-toast--success", notification.category === "success");
        toast.classList.toggle("save-toast--persistent", persistent);
        toast.classList.add("save-toast--show");
    }

    // A current task status returns when the transient notification queue clears.
    window.setAppToastStatus = (message = "", category = "warning", action = null) => {
        if (appToastStatus?.message === message && appToastStatus?.category === category) return;
        appToastStatus = message ? { message, category, action } : null;
        if (!appToastActive) renderAppToast(appToastStatus, true);
    };

    function showAppToast(message, category = "success") {
        if (!message) {
            return;
        }
        appToastQueue.push({ message, category });
        if (appToastActive) {
            return;
        }
        showNextAppToast();
    }

    function showNextAppToast() {
        const notification = appToastQueue.shift();
        if (!notification) {
            appToastActive = false;
            renderAppToast(appToastStatus, true);
            return;
        }
        appToastActive = true;
        renderAppToast(notification);
        const toast = document.querySelector(".save-toast");
        const isError = notification.category === "danger"
            || notification.category === "error";
        const isWarning = notification.category === "warning";
        toast.classList.remove("save-toast--show");
        void toast.offsetWidth;
        toast.classList.add("save-toast--show");
        const duration = isError || isWarning ? 4000 : 2200;
        window.setTimeout(() => {
            toast.classList.remove("save-toast--show");
            window.setTimeout(() => {
                appToastActive = false;
                showNextAppToast();
            }, 220);
        }, duration);
    }

    document.querySelectorAll("[data-flash-toast]").forEach((message) => {
        showAppToast(message.textContent.trim(), message.dataset.category);
    });
    window.showAppToast = showAppToast;

    function showSaveToast(message, isError = false) {
        showAppToast(message, isError ? "danger" : "success");
    }

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
                showSaveToast("保存成功");
                syncCardAfterSave();
            } catch {
                showSaveToast("保存失败", true);
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
