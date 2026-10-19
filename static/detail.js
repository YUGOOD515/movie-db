// Detail page: clamped overviews and the series episode browser.
(() => {
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
            window.bindMediaFallback?.(backdropStage);

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
                    window.showAppToast?.("单集信息加载失败，请重试。", "danger");
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
                    item.setAttribute("aria-pressed", String(isSelected));
                    item.setAttribute("aria-selected", String(isSelected));
                });
                seasonSummaries.forEach((summary) => {
                    const isSelected = summary.dataset.seasonSummary
                        === control.dataset.seasonKey;
                    summary.hidden = !isSelected;
                    summary.classList.toggle("d-none", !isSelected);
                });

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
})();
