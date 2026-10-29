// 影院化视觉效果（全站）：BlurHash 占位、海报倾斜、入场动画与跨页面转场。
// 均为渐进增强：脚本或第三方库加载失败时，页面保持原有显示。
import { decode } from "./vendor/blurhash.mjs";

const root = document.documentElement;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
const { animate, inView, stagger } = window.Motion || {};
const EASE = [0.16, 1, 0.3, 1];

// ---------------------------------------------------------------------------
// BlurHash：图片加载完成前显示模糊色块，加载后淡入
// ---------------------------------------------------------------------------
const placeholderCache = new Map();

function placeholderUrl(hash, ratio) {
    const width = 32;
    const height = Math.max(8, Math.min(64, Math.round(width * ratio)));
    const key = `${hash}:${height}`;
    if (!placeholderCache.has(key)) {
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").putImageData(new ImageData(decode(hash, width, height), width, height), 0, 0);
        placeholderCache.set(key, canvas.toDataURL());
    }
    return placeholderCache.get(key);
}

function bindPlaceholders(scope = document) {
    scope.querySelectorAll("[data-blurhash]:not([data-blurhash-bound])").forEach((element) => {
        element.setAttribute("data-blurhash-bound", "");
        const image = element.querySelector("img.media-cover");
        if (!image || (image.complete && image.naturalWidth)) {
            return;
        }
        try {
            const ratio = element.clientWidth ? element.clientHeight / element.clientWidth : 0.5625;
            element.style.backgroundImage = `url(${placeholderUrl(element.dataset.blurhash, ratio || 0.5625)})`;
        } catch {
            return;
        }
        element.classList.add("has-placeholder");
        const done = () => element.classList.add("is-loaded");
        image.addEventListener("load", done, { once: true });
        image.addEventListener("error", done, { once: true });
        if (image.complete) {
            done();
        }
    });
}

// ---------------------------------------------------------------------------
// 海报悬停倾斜（vanilla-tilt），仅桌面精确指针
// ---------------------------------------------------------------------------
function bindTilt(scope = document) {
    if (!finePointer || reduceMotion || !window.VanillaTilt) {
        return;
    }
    const cards = [...scope.querySelectorAll("[data-tilt-card]:not([data-tilt-bound])")];
    cards.forEach((card) => card.setAttribute("data-tilt-bound", ""));
    if (cards.length) {
        window.VanillaTilt.init(cards, {
            max: 7,
            speed: 500,
            scale: 1.035,
            perspective: 900,
            glare: true,
            "max-glare": 0.22,
            gyroscope: false,
        });
    }
}

// ---------------------------------------------------------------------------
// 入场动画（Motion）：列表进入视口时依次上浮
// ---------------------------------------------------------------------------
function revealSection(section) {
    if (section.classList.contains("is-revealed")) {
        return;
    }
    section.classList.add("is-revealed");
    const header = section.querySelector(":scope > header");
    if (header) {
        animate(header, { opacity: [0, 1], transform: ["translateY(12px)", "none"] }, { duration: 0.5, ease: EASE });
    }
    const items = [...section.querySelectorAll(".media-rail-item")].slice(0, 10);
    if (items.length) {
        animate(
            items,
            { opacity: [0, 1], transform: ["translateY(28px)", "none"] },
            { duration: 0.7, ease: EASE, delay: stagger(0.045, { startDelay: 0.06 }) },
        );
    }
}

function bindReveal() {
    if (!root.classList.contains("motion-reveal")) {
        return;
    }
    if (!animate || !inView || reduceMotion) {
        root.classList.remove("motion-reveal");
        return;
    }
    inView(".library-section", (section) => revealSection(section), { amount: 0.1 });
}

function revealHeroCopy(copy) {
    if (!copy || !animate || reduceMotion) {
        return;
    }
    animate(
        [...copy.children],
        { opacity: [0, 1], transform: ["translateY(18px)", "none"] },
        { duration: 0.8, ease: EASE, delay: stagger(0.07, { startDelay: 0.12 }) },
    );
}

// ---------------------------------------------------------------------------
// 跨页面转场（View Transitions API）：点击的卡片图片过渡为详情页背景图
// ---------------------------------------------------------------------------
function clearTransitionNames() {
    document.querySelectorAll("[data-vt-named]").forEach((element) => {
        element.style.viewTransitionName = "";
        element.removeAttribute("data-vt-named");
    });
}

function nameForTransition(element, name) {
    if (element) {
        element.style.viewTransitionName = name;
        element.setAttribute("data-vt-named", "");
    }
}

function bindTransitions() {
    document.addEventListener(
        "click",
        (event) => {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                return;
            }
            const link = event.target.closest('a[href^="/title/"]');
            if (!link) {
                return;
            }
            clearTransitionNames();
            const card = link.closest(".poster-card, .continue-card, .ranked-feature, .home-hero-slide");
            if (!card || reduceMotion || !("onpageswap" in window)) {
                return;
            }
            const fromHero = card.classList.contains("home-hero-slide");
            const fromRanked = card.classList.contains("ranked-feature");
            if (fromHero) {
                card.closest("[data-home-hero]")?.dispatchEvent(new Event("media:navigate"));
            }
            // 倾斜中的海报先复位，避免快照带着旋转与放大
            const tilted = card.querySelector("[data-tilt-bound]");
            if (tilted) {
                tilted.vanillaTilt?.reset();
                tilted.style.transition = "none";
                tilted.style.transform = "none";
            }
            const visual = fromHero ? ".home-hero-media" : fromRanked ? ".ranked-feature-card" : "img.media-cover";
            nameForTransition(card.querySelector(visual), "media-hero");
            nameForTransition(card.querySelector("[data-transition-logo]"), "media-logo");
            if (fromRanked) {
                nameForTransition(card.querySelector(".ranked-feature-meta"), "media-meta");
            }
            // 告诉详情页本次来自哪类卡片（layout.html 读取后设置 data-vt-from）
            try {
                sessionStorage.setItem(
                    "media-vt",
                    JSON.stringify({ from: fromHero ? "hero" : fromRanked ? "ranked" : "card", path: new URL(link.href).pathname, at: Date.now() }),
                );
            } catch {}
        },
        true,
    );
    window.addEventListener("pageshow", clearTransitionNames);
    window.addEventListener("pagereveal", (event) => {
        if (event.viewTransition) {
            event.viewTransition.finished.then(clearTransitionNames, clearTransitionNames);
        } else {
            clearTransitionNames();
        }
    });
}

function bindAll(scope = document) {
    bindPlaceholders(scope);
    bindTilt(scope);
}

// 页面脚本插入新卡片后会调用 bindMediaFallback，这里顺带绑定效果。
const bindMediaFallback = window.bindMediaFallback;
if (typeof bindMediaFallback === "function") {
    window.bindMediaFallback = (scope) => {
        bindMediaFallback(scope);
        bindAll(scope || document);
    };
}

bindAll();
bindReveal();
bindTransitions();
window.MediaEffects = { bind: bindAll, revealHeroCopy };
