(() => {
    const picker = document.getElementById("title-picker");
    const batch = document.querySelector("[data-batch-import-form]");
    if (!picker || !batch) return;

    const modal = bootstrap.Modal.getOrCreateInstance(picker);
    const form = document.querySelector("[data-title-search-form]");
    const query = form.querySelector("[data-title-search-query]");
    const searchStep = picker.querySelector("[data-title-search-step]");
    const recordForm = picker.querySelector("[data-title-record-form]");
    const episodeRows = picker.querySelector("[data-title-episode-rows]");
    const episodeTemplate = picker.querySelector("[data-title-episode-template]");
    const addEpisode = picker.querySelector("[data-title-episode-add]");
    const movieURL = picker.querySelector("[data-title-movie-url]");
    const add = picker.querySelector("[data-title-record-add]");
    const results = picker.querySelector("[data-title-search-results]");
    const status = picker.querySelector("[data-title-search-status]");
    const more = picker.querySelector("[data-title-search-more]");
    let selectedTitle = null;
    let search = null;
    let controller = null;
    let revision = 0;
    let selecting = false;

    function cancelPending() {
        revision += 1;
        controller?.abort();
        controller = null;
        selecting = false;
        form.querySelector("button").disabled = false;
        more.disabled = false;
        results.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    }

    function message(text, error = false) {
        status.textContent = error ? "" : text;
        if (error) window.showAppToast?.(text, "danger");
    }

    async function getJSON(url, params, signal) {
        const response = await fetch(`${url}?${new URLSearchParams(params)}`, { signal });
        let data;
        try {
            data = await response.json();
        } catch {
            throw new Error("服务暂时不可用，请稍后重试或手动填写 IMDb ID。");
        }
        if (!response.ok) throw new Error(data.error || "请求失败，请稍后重试。");
        return data;
    }

    function renderCard(item, mediaType) {
        const card = document.createElement("div");
        card.className = "squircle-card border d-flex align-items-start gap-3 p-3";
        const artwork = document.createElement("div");
        artwork.className = "title-search-artwork squircle-card bg-body-tertiary flex-shrink-0 d-flex align-items-center justify-content-center";
        artwork.textContent = "暂无海报";
        if (item.localized_poster_url || item.poster_url) {
            const poster = document.createElement("img");
            poster.src = item.localized_poster_url || item.poster_url;
            poster.alt = "";
            poster.width = 72;
            poster.height = 108;
            poster.loading = "lazy";
            poster.className = "squircle-card object-fit-cover flex-shrink-0";
            artwork.replaceChildren(poster);
            poster.addEventListener("error", () => {
                if (item.poster_url && poster.getAttribute("src") !== item.poster_url) poster.src = item.poster_url;
                else artwork.textContent = "暂无海报";
            });
        }
        card.append(artwork);
        const body = document.createElement("div");
        body.className = "flex-grow-1 min-w-0";
        const name = document.createElement("h3");
        name.className = "h6 mb-1";
        name.textContent = item.name;
        const subtitle = document.createElement("p");
        subtitle.className = "small text-body-secondary mb-1";
        subtitle.textContent = [item.original_name, item.year || "年份未知", mediaType === "tv" ? "剧集" : "电影"].filter(Boolean).join(" · ");
        const overview = document.createElement("p");
        overview.className = "small mb-2 title-search-overview";
        overview.textContent = item.overview.length > 160 ? `${item.overview.slice(0, 160)}…` : item.overview;
        const choose = document.createElement("button");
        choose.type = "button";
        choose.className = "btn btn-sm chip-btn";
        choose.textContent = "选择此作品";
        choose.setAttribute("aria-label", `选择 ${item.name} ${item.year}`);
        choose.addEventListener("click", () => select(item, mediaType));
        body.append(name, subtitle, overview, choose);
        card.append(body);
        return card;
    }

    async function runSearch(append = false) {
        if (!query.value.trim()) return;
        cancelPending();
        const current = revision;
        controller = new AbortController();
        if (!append) {
            search = { q: query.value.trim(), type: form.querySelector('[name="search_type"]:checked').value, page: 0 };
            results.replaceChildren();
            more.classList.add("d-none");
        }
        const params = { ...search, page: search.page + 1 };
        form.querySelector("button").disabled = true;
        more.disabled = true;
        results.querySelectorAll("button").forEach((button) => { button.disabled = true; });
        message("正在搜索…");
        try {
            const data = await getJSON(picker.dataset.searchUrl, params, controller.signal);
            if (current !== revision) return;
            data.items.forEach((item) => results.append(renderCard(item, params.type)));
            search.page = data.page;
            more.classList.toggle("d-none", !data.has_more);
            message("");
            window.showAppToast?.(results.children.length ? `已显示 ${results.children.length} 部作品，请核对名称、年份和简介。` : "没有找到结果，请尝试原名、其他译名或手动填写 IMDb ID。", results.children.length ? "info" : "warning");
        } catch (error) {
            if (current === revision && error.name !== "AbortError") message(error.message, true);
        } finally {
            if (current === revision) {
                form.querySelector("button").disabled = false;
                more.disabled = false;
                results.querySelectorAll("button").forEach((button) => { button.disabled = false; });
            }
        }
    }

    async function select(item, mediaType) {
        if (selecting) return;
        cancelPending();
        selecting = true;
        const current = revision;
        controller = new AbortController();
        results.querySelectorAll("button").forEach((button) => { button.disabled = true; });
        message(`正在核对 ${item.name} 的 IMDb 信息…`);
        try {
            const selected = await getJSON(picker.dataset.selectUrl, { type: mediaType, tmdb_id: item.tmdb_id }, controller.signal);
            if (current !== revision) return;
            selectedTitle = selected;
            if (!picker.dispatchEvent(new CustomEvent("title:selected", { detail: selected, cancelable: true }))) {
                modal.hide();
                return;
            }
            recordForm.reset();
            picker.querySelector("[data-title-record-name]").textContent = [selected.name, selected.year].filter(Boolean).join(" · ");
            picker.querySelector("[data-title-record-id]").textContent = selected.title_id;
            const isSeries = selected.media_type === "tv";
            picker.querySelector("[data-title-episode-fields]").classList.toggle("d-none", !isSeries);
            picker.querySelector("[data-title-movie-fields]").classList.toggle("d-none", isSeries);
            movieURL.disabled = isSeries;
            episodeRows.replaceChildren();
            if (isSeries) addEpisodeRow();
            add.textContent = isSeries ? "全部添加到待导入列表" : "添加到待导入列表";
            add.disabled = false;
            searchStep.classList.add("d-none");
            recordForm.classList.remove("d-none");
            (isSeries ? episodeRows.querySelector("[data-title-season]") : movieURL).focus();
        } catch (error) {
            if (current === revision && error.name !== "AbortError") message(error.message, true);
        } finally {
            if (current === revision) {
                selecting = false;
                results.querySelectorAll("button").forEach((button) => { button.disabled = false; });
            }
        }
    }

    function updateEpisodeRows() {
        const rows = Array.from(episodeRows.children);
        rows.forEach((row, index) => {
            row.querySelector("[data-title-episode-index]").textContent = index + 1;
            row.querySelector("[data-title-episode-remove]").disabled = rows.length === 1;
        });
        addEpisode.disabled = rows.length >= 100;
        picker.querySelector("[data-title-episode-count]").textContent = `共 ${rows.length} 条`;
    }

    function addEpisodeRow() {
        if (episodeRows.children.length >= 100) return;
        const previousSeason = episodeRows.lastElementChild?.querySelector("[data-title-season]").value;
        const fragment = episodeTemplate.content.cloneNode(true);
        const row = fragment.querySelector("[data-title-episode-row]");
        const season = row.querySelector("[data-title-season]");
        if (previousSeason) season.value = previousSeason;
        episodeRows.append(fragment);
        updateEpisodeRows();
    }

    addEpisode.addEventListener("click", addEpisodeRow);
    episodeRows.addEventListener("click", (event) => {
        const remove = event.target.closest("[data-title-episode-remove]");
        if (!remove || remove.disabled) return;
        remove.closest("[data-title-episode-row]").remove();
        updateEpisodeRows();
    });
    picker.querySelector("[data-title-back]").addEventListener("click", () => {
        selectedTitle = null;
        recordForm.classList.add("d-none");
        searchStep.classList.remove("d-none");
        query.focus();
    });
    recordForm.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!selectedTitle || !recordForm.reportValidity()) return;
        const isSeries = selectedTitle.media_type === "tv";
        const common = {
            title_id: selectedTitle.title_id,
        };
        const items = isSeries ? Array.from(episodeRows.children).map((row) => ({
            ...common,
            season_number: row.querySelector("[data-title-season]").value,
            episode_number: row.querySelector("[data-title-episode]").value,
            alist_url: row.querySelector("[data-title-alist-url]").value.trim(),
        })) : [{ ...common, season_number: "", episode_number: "", alist_url: movieURL.value.trim() }];
        const accepted = !batch.dispatchEvent(new CustomEvent("batch:add-records", { detail: items, cancelable: true }));
        if (!accepted) {
            window.showAppToast?.("待导入列表合计最多 100 条，本次记录均未添加，请减少单集数量或先完成当前批次。", "warning");
            return;
        }
        add.disabled = true;
        batch.closest("details")?.setAttribute("open", "");
        modal.hide();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        runSearch();
    });
    for (const input of [query, ...form.querySelectorAll('[name="search_type"]')]) {
        input.addEventListener("input", () => {
            cancelPending();
            results.replaceChildren();
            more.classList.add("d-none");
            search = null;
            message("输入名称后按回车搜索。");
        });
    }
    more.addEventListener("click", () => runSearch(true));
    picker.addEventListener("show.bs.modal", event => {
        if (event.relatedTarget) document.getElementById("title-picker-heading").textContent = "通过 TMDB 搜索添加";
        selectedTitle = null;
        recordForm.classList.add("d-none");
        searchStep.classList.remove("d-none");
        message(results.children.length ? (event.relatedTarget ? "选择作品后填写季集和视频链接。" : "选择作品后匹配待关联的视频。") : "输入名称后按回车搜索。");
    });
    picker.addEventListener("shown.bs.modal", () => {
        query.focus();
        // 从待关联打开时已预填名称：直接搜索，省去一次点击。
        if (query.value.trim() && !search && !results.children.length) runSearch();
    });
    picker.addEventListener("hide.bs.modal", cancelPending);

})();
