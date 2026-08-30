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

    // Detail overview inline 2-line clamp toggle with smooth transition.
    function initOverviewClamp(overviewWrap) {
        const overviewEl = overviewWrap.querySelector("[data-detail-overview]");
        const textEl = overviewWrap.querySelector("[data-detail-overview-text]");
        const overviewToggle = overviewWrap.querySelector("[data-detail-overview-toggle]");
        if (!overviewEl || !textEl || !overviewToggle) {
            return;
        }

        const fullText = overviewWrap.dataset.fullText || textEl.textContent.trim();
        let isExpanded = false;
        let isAnimating = false;

        function getMaxHeight() {
            const computed = window.getComputedStyle(overviewEl);
            let lineHeight = parseFloat(computed.lineHeight);
            if (isNaN(lineHeight) || lineHeight <= 0) {
                lineHeight = parseFloat(computed.fontSize) * 1.6;
            }
            return lineHeight * 2 + 2;
        }

        function getClampedText(maxHeight) {
            overviewToggle.textContent = "...更多";
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
            return fullText.slice(0, best);
        }

        function updateClamp() {
            if (isExpanded || isAnimating || overviewWrap.offsetParent === null) {
                return;
            }

            overviewToggle.classList.add("d-none");
            overviewToggle.setAttribute("aria-expanded", "false");
            textEl.textContent = fullText;

            const maxHeight = getMaxHeight();
            if (overviewEl.scrollHeight <= maxHeight) {
                return;
            }

            const clamped = getClampedText(maxHeight);
            textEl.textContent = clamped;
        }

        function toggleExpand() {
            if (isAnimating) {
                return;
            }

            const maxHeight = getMaxHeight();

            if (!isExpanded) {
                isAnimating = true;
                const startHeight = overviewEl.offsetHeight;

                textEl.textContent = fullText + " ";
                overviewToggle.textContent = "收起";
                overviewToggle.setAttribute("aria-expanded", "true");

                const targetHeight = overviewEl.scrollHeight;

                overviewEl.style.overflow = "hidden";
                overviewEl.style.height = `${startHeight}px`;
                void overviewEl.offsetHeight;

                overviewEl.style.transition = "height 0.28s cubic-bezier(0.4, 0, 0.2, 1)";
                overviewEl.style.height = `${targetHeight}px`;

                const onEnd = (e) => {
                    if (e.target !== overviewEl) return;
                    overviewEl.removeEventListener("transitionend", onEnd);
                    overviewEl.style.height = "";
                    overviewEl.style.overflow = "";
                    overviewEl.style.transition = "";
                    isExpanded = true;
                    isAnimating = false;
                };
                overviewEl.addEventListener("transitionend", onEnd);
            } else {
                isAnimating = true;
                const startHeight = overviewEl.offsetHeight;

                overviewEl.style.overflow = "hidden";
                overviewEl.style.height = `${startHeight}px`;
                void overviewEl.offsetHeight;

                overviewEl.style.transition = "height 0.28s cubic-bezier(0.4, 0, 0.2, 1)";
                overviewEl.style.height = `${maxHeight}px`;

                const onEnd = (e) => {
                    if (e.target !== overviewEl) return;
                    overviewEl.removeEventListener("transitionend", onEnd);
                    overviewToggle.textContent = "...更多";
                    overviewToggle.setAttribute("aria-expanded", "false");

                    const clamped = getClampedText(maxHeight);
                    textEl.textContent = clamped;

                    overviewEl.style.height = "";
                    overviewEl.style.overflow = "";
                    overviewEl.style.transition = "";
                    isExpanded = false;
                    isAnimating = false;
                };
                overviewEl.addEventListener("transitionend", onEnd);
            }
        }

        updateClamp();
        window.addEventListener("resize", updateClamp);
        overviewToggle.addEventListener("click", toggleExpand);
        overviewWrap.closest("[data-episode-hero]")?.addEventListener(
            "detail:shown",
            () => window.requestAnimationFrame(updateClamp),
        );
    }

    document.querySelectorAll("[data-detail-overview-wrap]").forEach(initOverviewClamp);

    // Lazy episode panels: only the featured episode ships rendered HTML; the
    // rest live in a JSON data island and are built on first selection.
    function episodeHeroControlHTML(episodeId, info) {
        const key = escapeHtml(episodeId);
        if (!info.play_url) {
            return `<div class="episode-hero-control" data-episode-hero-control="${key}" hidden>
                <span class="btn btn-secondary disabled detail-play w-100 d-inline-flex align-items-center justify-content-center" aria-disabled="true">暂无可播放资源</span>
            </div>`;
        }
        const buttonClass = "btn media-primary-action detail-play w-100 d-inline-flex align-items-center justify-content-center gap-2";
        const icon = '<i class="bi bi-play-fill" aria-hidden="true"></i>';
        if (info.sources.length > 1) {
            const items = info.sources.map((source) => `
                <li>
                    <form action="${escapeHtml(source.url)}" method="post">
                        <button class="dropdown-item d-flex align-items-center gap-2${source.active ? " active" : ""}" type="submit" title="${escapeHtml(source.path)}">
                            <i class="bi bi-play-fill flex-shrink-0" aria-hidden="true"></i>
                            <span class="text-truncate">${escapeHtml(source.label)}</span>
                        </button>
                    </form>
                </li>`).join("");
            return `<div class="episode-hero-control" data-episode-hero-control="${key}" hidden>
                <div class="w-100 dropdown">
                    <button class="${buttonClass} dropdown-toggle" type="button" data-bs-toggle="dropdown" aria-expanded="false" aria-label="${escapeHtml(info.play_label)}，选择版本">
                        ${icon}<span class="text-truncate">${escapeHtml(info.play_label)}</span>
                    </button>
                    <ul class="dropdown-menu detail-version-menu">${items}</ul>
                </div>
            </div>`;
        }
        return `<div class="episode-hero-control" data-episode-hero-control="${key}" hidden>
            <form class="d-contents" action="${escapeHtml(info.play_url)}" method="post">
                <button class="${buttonClass}" type="submit" aria-label="${escapeHtml(info.play_label)}">
                    ${icon}<span class="text-truncate">${escapeHtml(info.play_label)}</span>
                </button>
            </form>
        </div>`;
    }

    function episodeHeroPanelHTML(episodeId, info, context) {
        const key = escapeHtml(episodeId);
        const badges = [];
        if (info.runtime) {
            badges.push(`<span>${escapeHtml(info.runtime)}</span>`);
        }
        if (info.air_date) {
            badges.push(`<span>${escapeHtml(info.air_date)}</span>`);
        }
        (info.badges || []).forEach((badge) => {
            if (badge.kind === "dolby_vision") {
                badges.push(`<span class="dolby-vision-badge" title="杜比视界 (Dolby Vision)" aria-label="杜比视界">
                    <img class="dolby-vision-logo dolby-vision-logo--dark" src="${escapeHtml(context.assets.dolby_dark)}" alt="Dolby Vision" height="15">
                    <img class="dolby-vision-logo dolby-vision-logo--light" src="${escapeHtml(context.assets.dolby_light)}" alt="Dolby Vision" height="15">
                </span>`);
            } else if (badge.label) {
                badges.push(`<span>${escapeHtml(badge.label)}</span>`);
            }
        });
        if (context.genres) {
            badges.push(`<span>${escapeHtml(context.genres)}</span>`);
        }

        const overviewText = info.overview || "暂无本集简介。";
        const rating = info.rating !== null && info.rating !== "" && info.rating !== undefined
            ? `<span class="detail-rating-item" title="本集 IMDb 评分">
                  <img class="detail-rating-logo detail-rating-logo--imdb" src="${escapeHtml(context.assets.imdb)}" alt="IMDb">
                  <span class="detail-rating-score detail-rating-score--imdb">${Number(info.rating).toFixed(1)}</span>
              </span>`
            : "";

        return `<section class="episode-hero-panel d-none" data-episode-hero="${key}" data-season-number="${info.season}" data-episode-number="${info.number}" data-hero-backdrop-url="${escapeHtml(info.backdrop || "")}" hidden>
            <span class="detail-episode-index d-block small fw-bold mb-2">S${info.season} · E${info.number}</span>
            <h2 class="h3 fw-bold mb-3">${escapeHtml(info.name)}</h2>
            <div class="detail-meta d-flex flex-wrap align-items-center column-gap-3 row-gap-2 small text-body-secondary fw-semibold mb-3">${badges.join("")}</div>
            <div class="detail-overview-wrapper mb-0" data-detail-overview-wrap data-full-text="${escapeHtml(overviewText)}">
                <p class="detail-overview lh-lg mb-0" data-detail-overview><span data-detail-overview-text>${escapeHtml(overviewText)}</span><button type="button" class="btn btn-link p-0 text-decoration-none small text-body-secondary fw-semibold detail-overview-more d-none" data-detail-overview-toggle aria-expanded="false">...更多</button></p>
            </div>
            <div class="detail-ratings-row mt-3 pt-2">${rating}</div>
        </section>`;
    }

    function episodeFactsHTML(episodeId, info) {
        const key = escapeHtml(episodeId);
        if (!info.facts || !info.facts.length) {
            return `<div class="episode-facts-panel d-none" data-episode-facts="${key}" hidden>
                <p class="small text-body-secondary mb-0">暂无本地媒体参数。</p>
            </div>`;
        }
        const rows = info.facts.map((fact) => `
            <dt class="col-sm-4 text-body-secondary fw-normal">${escapeHtml(fact.label)}</dt>
            <dd class="col-sm-8 mb-0 text-break">${escapeHtml(fact.value)}</dd>`).join("");
        return `<div class="episode-facts-panel d-none" data-episode-facts="${key}" hidden>
            <dl class="row gy-3 mb-0 detail-facts">${rows}</dl>
        </div>`;
    }

    // Episode cards drive the Hero; playback remains an explicit action.
    document.querySelectorAll("[data-episode-browser]").forEach((browser) => {
        const selectors = browser.querySelectorAll("[data-episode-target]");
        const seasonControls = browser.querySelectorAll("[data-season-target]");
        const seasonPanes = browser.querySelectorAll("[data-season-pane]");
        const seasonSummaries = browser.querySelectorAll("[data-season-summary]");
        const seasonLabel = browser.querySelector("[data-season-label]");
        const backdropStage = browser.querySelector("[data-backdrop-stage]");
        const backdropFallback = browser.querySelector(".detail-backdrop-fallback");

        let episodeData = null;
        const episodeDataScript = browser.querySelector("script[data-episode-data]");
        if (episodeDataScript) {
            try {
                episodeData = JSON.parse(episodeDataScript.textContent);
            } catch (error) {
                episodeData = null;
            }
        }
        const heroPanelsContainer = browser.querySelector("[data-episode-hero-panels]");
        const heroControlsContainer = browser.querySelector(".detail-episode-controls");
        const factsPanelsContainer = browser.querySelector("[data-episode-facts-panels]");

        // Series/episode info toggle in the hero area.
        const seriesToggle = browser.querySelector("[data-series-toggle]");
        const seriesPanel = browser.querySelector("[data-series-hero-panel]");
        let seriesMode = false;

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

        function ensureEpisodePanels(episodeId) {
            if (!episodeData || !episodeData.episodes) {
                return;
            }
            const safeKey = CSS.escape(episodeId);
            if (browser.querySelector(`[data-episode-hero="${safeKey}"]`)) {
                return;
            }
            const info = episodeData.episodes[episodeId];
            if (!info) {
                return;
            }
            if (heroPanelsContainer) {
                heroPanelsContainer.insertAdjacentHTML(
                    "beforeend",
                    episodeHeroPanelHTML(episodeId, info, episodeData),
                );
                const panel = heroPanelsContainer.lastElementChild;
                const overviewWrap = panel?.querySelector("[data-detail-overview-wrap]");
                if (overviewWrap) {
                    initOverviewClamp(overviewWrap);
                }
            }
            if (heroControlsContainer) {
                heroControlsContainer.insertAdjacentHTML(
                    "beforeend",
                    episodeHeroControlHTML(episodeId, info),
                );
            }
            if (factsPanelsContainer) {
                factsPanelsContainer.insertAdjacentHTML(
                    "beforeend",
                    episodeFactsHTML(episodeId, info),
                );
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
                const legacyBackdrop = browser.querySelector("[data-detail-backdrop]");
                if (legacyBackdrop && backdropUrl) {
                    legacyBackdrop.src = backdropUrl;
                    legacyBackdrop.classList.remove("d-none");
                    backdropFallback?.classList.add("d-none");
                    backdropFallback?.classList.remove("d-flex");
                } else if (legacyBackdrop) {
                    legacyBackdrop.classList.add("d-none");
                    backdropFallback?.classList.remove("d-none");
                    backdropFallback?.classList.add("d-flex");
                }
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

        function selectEpisode(selector) {
            const targetKey = selector.dataset.episodeTarget;
            ensureEpisodePanels(targetKey);

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
            if (!seriesMode) {
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

    // Floating toast for save feedback (no page reload, no alert block)
    function showSaveToast(message, isError = false) {
        let toast = document.querySelector(".save-toast");
        if (!toast) {
            toast = document.createElement("div");
            toast.className = "save-toast";
            toast.setAttribute("role", "status");
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.toggle("save-toast--error", isError);
        toast.classList.remove("save-toast--show");
        // Restart the animation in case a previous toast is still visible
        void toast.offsetWidth;
        toast.classList.add("save-toast--show");
        clearTimeout(toast._hideTimer);
        toast._hideTimer = setTimeout(
            () => toast.classList.remove("save-toast--show"),
            2000,
        );
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
