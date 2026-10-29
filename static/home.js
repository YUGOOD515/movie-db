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
    const embla = window.EmblaCarousel(viewport, { loop: true, duration: 28 }, plugins);
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
    embla.on("settle", () => slides.forEach((slide) => slide.classList.remove("is-leaving")));

    // 导航快照保留当前推镜位置；从浏览器缓存返回时继续播放。
    let pausedAnimations = [];
    let resumeAutoplay = false;
    hero.addEventListener("media:navigate", () => {
        if (hero.classList.contains("is-navigating")) return;
        hero.classList.add("is-navigating");
        resumeAutoplay = autoplay?.isPlaying() || false;
        autoplay?.stop();
        pausedAnimations = hero.getAnimations({ subtree: true }).filter((animation) => animation.playState === "running");
        pausedAnimations.forEach((animation) => animation.pause());
    });
    window.addEventListener("pageshow", () => {
        if (!hero.classList.contains("is-navigating")) return;
        hero.classList.remove("is-navigating");
        pausedAnimations.forEach((animation) => animation.play());
        pausedAnimations = [];
        if (resumeAutoplay) autoplay?.play();
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
})();
