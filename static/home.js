// 首页主视觉：Embla 淡入淡出轮播、自动播放（悬停暂停）、圆点导航，以及随影片变化的环境色。
(() => {
    const hero = document.querySelector("[data-home-hero]");
    const viewport = hero?.querySelector("[data-hero-viewport]");
    if (!hero || !viewport || typeof window.EmblaCarousel !== "function") {
        return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const embla = window.EmblaCarousel(viewport, { loop: true, duration: 28 }, [window.EmblaCarouselFade()]);
    const slides = embla.slideNodes();
    const dots = [...hero.querySelectorAll("[data-hero-dot]")];

    // 上一张在新图淡入期间保持不透明并继续推镜（is-leaving），避免缩回与透底闪烁。
    let current = embla.selectedScrollSnap();
    function select() {
        const index = embla.selectedScrollSnap();
        if (index !== current) {
            slides.forEach((slide) => slide.classList.remove("is-leaving"));
            slides[current]?.classList.add("is-leaving");
            current = index;
        }
        slides.forEach((slide, position) => {
            const selected = position === index;
            slide.classList.toggle("is-selected", selected);
            slide.toggleAttribute("inert", !selected);
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
    // 自动播放：当前圆点的进度动画就是计时器，播完翻到下一张。
    // 悬停时 CSS 暂停动画（见 style.css），移开后从原位置继续，进度条与翻页始终同步。
    function restartProgress() {
        if (reduceMotion) return;  // 减少动态效果时不自动播放
        hero.classList.remove("is-playing");
        void hero.offsetWidth;  // 让进度动画从头开始
        hero.classList.add("is-playing");
    }
    hero.addEventListener("animationend", (event) => {
        if (event.animationName === "hero-progress" && event.target.closest(".home-hero-dot.is-selected")) embla.scrollNext();
    });
    embla.on("select", select);
    embla.on("select", restartProgress);
    embla.on("settle", () => slides.forEach((slide) => slide.classList.remove("is-leaving")));

    // 导航快照保留当前推镜位置与进度；从浏览器缓存返回时继续播放。
    let pausedAnimations = [];
    hero.addEventListener("media:navigate", () => {
        if (hero.classList.contains("is-navigating")) return;
        hero.classList.add("is-navigating");
        pausedAnimations = hero.getAnimations({ subtree: true }).filter((animation) => animation.playState === "running");
        pausedAnimations.forEach((animation) => animation.pause());
    });
    window.addEventListener("pageshow", () => {
        if (!hero.classList.contains("is-navigating")) return;
        hero.classList.remove("is-navigating");
        pausedAnimations.forEach((animation) => animation.play());
        pausedAnimations = [];
    });

    // 首屏加载完后预载其余背景图，前后切换都不再等待下载。
    const preload = () => hero.querySelectorAll("img[loading=lazy]").forEach((img) => img.removeAttribute("loading"));
    if (document.readyState === "complete") {
        preload();
    } else {
        window.addEventListener("load", preload, { once: true });
    }
    hero.classList.add("is-ready");
    select();
    restartProgress();
})();
