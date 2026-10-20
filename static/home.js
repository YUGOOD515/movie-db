// 首页主视觉：Embla 淡入淡出轮播、自动播放、圆点导航，以及随影片变化的环境色。
(() => {
    const hero = document.querySelector("[data-home-hero]");
    const viewport = hero?.querySelector("[data-hero-viewport]");
    if (!hero || !viewport || typeof window.EmblaCarousel !== "function") {
        return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const plugins = [window.EmblaCarouselFade()];
    if (!reduceMotion) {
        plugins.push(window.EmblaCarouselAutoplay({ delay: 7000, stopOnInteraction: false, stopOnMouseEnter: true }));
    }
    const embla = window.EmblaCarousel(viewport, { loop: true, duration: 32 }, plugins);
    const slides = embla.slideNodes();
    const dots = [...hero.querySelectorAll("[data-hero-dot]")];

    function select() {
        const index = embla.selectedScrollSnap();
        slides.forEach((slide, position) => {
            const selected = position === index;
            slide.classList.toggle("is-selected", selected);
            slide.toggleAttribute("inert", !selected);
            if (selected) {
                // 懒加载的背景图在切换到前提前开始下载。
                slides[(position + 1) % slides.length]?.querySelector("img[loading=lazy]")?.removeAttribute("loading");
            }
        });
        dots.forEach((dot, position) => {
            dot.classList.toggle("is-selected", position === index);
            dot.setAttribute("aria-selected", position === index ? "true" : "false");
        });
        const tone = slides[index]?.dataset.tone;
        if (tone) {
            document.documentElement.style.setProperty("--ambient-tone", tone);
        }
        window.MediaEffects?.revealHeroCopy(slides[index]?.querySelector("[data-hero-copy]"));
    }

    dots.forEach((dot) => dot.addEventListener("click", () => embla.scrollTo(Number(dot.dataset.heroDot))));
    hero.querySelector("[data-hero-prev]")?.addEventListener("click", () => embla.scrollPrev());
    hero.querySelector("[data-hero-next]")?.addEventListener("click", () => embla.scrollNext());
    hero.addEventListener("keydown", (event) => {
        if (event.target.closest("input, textarea")) return;
        if (event.key === "ArrowLeft") embla.scrollPrev();
        if (event.key === "ArrowRight") embla.scrollNext();
    });
    // 自动播放时显示当前圆点的进度条。
    const autoplay = embla.plugins().autoplay;
    if (autoplay) {
        embla.on("autoplay:timerset", () => {
            hero.classList.remove("is-playing");
            void hero.offsetWidth; // 重新开始圆点进度动画
            hero.classList.add("is-playing");
        });
        embla.on("autoplay:timerstopped", () => hero.classList.remove("is-playing"));
        hero.classList.toggle("is-playing", autoplay.isPlaying());
    }
    embla.on("select", select);
    hero.classList.add("is-ready");
    select();
})();
