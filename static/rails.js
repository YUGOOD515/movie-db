// 横向轨道的左右翻页按钮（templates/_ui.html 的 rail_nav）。
// 只在鼠标设备上、轨道放不下时显示；点一下翻过当前完整可见的卡片，到头时对应按钮禁用。
// 触屏设备不显示按钮，靠轨道右侧露出的半张卡片提示可以滑动。
(() => {
    const fine = matchMedia("(hover: hover) and (pointer: fine)");
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");

    for (const nav of document.querySelectorAll("[data-rail-nav]")) {
        const scope = nav.closest("section") || document;
        const prev = nav.querySelector("[data-rail-prev]"), next = nav.querySelector("[data-rail-next]");
        const rails = [...scope.querySelectorAll(nav.dataset.railNav || ".media-rail")];
        if (!rails.length) continue;
        // 剧集按季分成多条轨道，只控制当前显示的那条。
        const rail = () => rails.find(item => item.offsetParent) || rails[0];
        let frame = 0;

        function update() {
            frame = 0;
            const target = rail();
            const overflow = target.scrollWidth > target.clientWidth + 1;
            nav.hidden = !fine.matches || !overflow;
            prev.disabled = target.scrollLeft <= 1;
            next.disabled = target.scrollLeft + target.clientWidth >= target.scrollWidth - 1;
        }
        const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };

        // 一页 = 当前完整可见的卡片数 × (卡宽 + 间距)，翻页后下一张露出的卡片正好到最左边。
        function page(direction) {
            const target = rail();
            const items = target.querySelectorAll(".media-rail-item");
            if (items.length < 2) return;
            const step = items[1].getBoundingClientRect().left - items[0].getBoundingClientRect().left;
            const edge = parseFloat(getComputedStyle(target).scrollPaddingLeft) || 0;
            const count = Math.max(1, Math.floor((target.clientWidth - edge + 1) / step));
            target.scrollBy({ left: direction * count * step, behavior: reduced.matches ? "auto" : "smooth" });
        }

        prev.addEventListener("click", () => page(-1));
        next.addEventListener("click", () => page(1));
        rails.forEach(item => {
            item.addEventListener("scroll", schedule, { passive: true });
            new ResizeObserver(schedule).observe(item);
        });
        // 切换季度后轨道换成另一条
        scope.addEventListener("click", event => { if (event.target.closest("[data-season-target]")) setTimeout(schedule); });
        fine.addEventListener("change", schedule);
        update();
    }
})();
