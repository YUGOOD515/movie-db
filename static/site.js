// Site-wide behavior: image fallbacks, theme, filter forms and notifications.
window.appIcon = (name, classes = '') => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', `app-icon ${classes}`.trim()); svg.setAttribute('width','16'); svg.setAttribute('height','16'); svg.setAttribute('aria-hidden','true'); svg.setAttribute('focusable','false'); svg.dataset.icon = name;
    const use = document.createElementNS(svg.namespaceURI, 'use');
    use.setAttribute('href', `${document.body.dataset.iconUrl}#${name}`); svg.append(use); return svg;
};
// Page scripts (detail.js, manager.js, …) reuse the helpers exposed on window.
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
    window.bindMediaFallback = bindMediaFallback;

    // Theme toggle
    const themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
        function syncThemeControl() {
            const isDark = document.documentElement.dataset.bsTheme === "dark";
            const label = isDark ? "切换为浅色主题" : "切换为深色主题";
            themeToggle.replaceChildren(window.appIcon(isDark ? 'sun' : 'moon'));
            themeToggle.setAttribute("aria-label", label);
            themeToggle.setAttribute("title", label);
        }

        themeToggle.addEventListener("click", () => {
            const nextTheme = document.documentElement.dataset.bsTheme === "dark"
                ? "light"
                : "dark";
            document.documentElement.dataset.bsTheme = nextTheme;
            try {
                localStorage.setItem("media-theme", nextTheme);
            } catch {
                // Private windows may block storage; the theme still applies to this page.
            }
            syncThemeControl();
        });

        syncThemeControl();
    }

    // Filter forms are server-owned: choices submit immediately. Dropdown
    // options write `[data-filter-set]` into the same-named hidden field.
    document.querySelectorAll("form[data-auto-submit]").forEach((form) => {
        form.addEventListener("change", (event) => {
            if (event.target.matches("input[type='radio'], input[type='checkbox'], select")) {
                form.requestSubmit();
            }
        });
        form.addEventListener("click", (event) => {
            const option = event.target.closest("[data-filter-set]");
            const field = option && form.elements.namedItem(option.dataset.filterSet);
            if (!field) {
                return;
            }
            field.value = option.dataset.filterValue;
            form.requestSubmit();
        });
    });

    // Global top-right notifications. Flask flash messages and client-side
    // actions share the same queue so notifications never overlap.
    const appToastQueue = [];
    let appToastActive = false;

    function renderAppToast(notification) {
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
        const icon = window.appIcon(isError ? 'circle-alert' : notification.category === 'warning' ? 'triangle-alert' : notification.category === 'success' ? 'circle-check' : 'info');
        const message = document.createElement("span");
        message.textContent = notification.message;
        toast.replaceChildren(icon, message);
        toast.setAttribute("role", isError ? "alert" : "status");
        toast.setAttribute("aria-live", isError ? "assertive" : "polite");
        toast.classList.toggle("save-toast--error", isError);
        toast.classList.toggle("save-toast--warning", notification.category === "warning");
        toast.classList.toggle("save-toast--success", notification.category === "success");
        toast.classList.add("save-toast--show");
    }

    function showAppToast(message, category = "success") {
        if (!message) {
            return;
        }
        if (!appToastQueue.some(item => item.message === message && item.category === category)) appToastQueue.push({ message, category });
        if (!appToastActive) {
            showNextAppToast();
        }
    }

    function showNextAppToast() {
        const notification = appToastQueue.shift();
        if (!notification) {
            appToastActive = false;
            renderAppToast(null);
            return;
        }
        appToastActive = true;
        renderAppToast(notification);
        const toast = document.querySelector(".save-toast");
        toast.classList.remove("save-toast--show");
        void toast.offsetWidth;
        toast.classList.add("save-toast--show");
        const isUrgent = ["danger", "error", "warning"].includes(notification.category);
        window.setTimeout(() => {
            toast.classList.remove("save-toast--show");
            window.setTimeout(() => {
                appToastActive = false;
                showNextAppToast();
            }, 220);
        }, isUrgent ? 4000 : 2200);
    }

    window.showAppToast = showAppToast;
    document.querySelectorAll("[data-flash-toast]").forEach((message) => {
        showAppToast(message.textContent.trim(), message.dataset.category);
    });
})();

// Read-only snapshot (cloud copy while the local app is offline): write
// controls stay visible but are disabled, and any submission explains why.
(() => {
    if (!document.body.hasAttribute("data-read-only")) {
        return;
    }
    const message = "本地 MovieDB 未连接，当前为只读快照。";
    const writeControls = [
        "form[method='post' i] button",
        "form[method='post' i] input",
        "[data-start-url]",
        "[data-title-delete-open]",
    ].join(",");

    document.querySelectorAll(writeControls).forEach((control) => {
        if (control.type === "hidden") {
            return;
        }
        control.disabled = true;
        control.classList.add("read-only-disabled");
        control.setAttribute("title", message);
    });
    document.addEventListener("submit", (event) => {
        if ((event.target.method || "").toLowerCase() !== "post") {
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        window.showAppToast?.(message, "warning");
    }, true);
})();

// MovieDB desktop window (pywebview): keep the native title bar in step with
// the page theme and open other sites in the system browser.
(() => {
    // WKWebView (macOS window) never asks about "beforeunload", so pages with
    // unsaved changes would be left silently. Ask the same listeners ourselves:
    // the native menu (back / reload / settings…) and in-app links call this first.
    const canLeave = () => {
        const probe = new Event("beforeunload", { cancelable: true });
        window.dispatchEvent(probe);
        return !probe.defaultPrevented;
    };
    window.moviedbConfirmLeave = () => canLeave() || window.confirm("有未保存的修改，离开后会丢失。确定离开吗？");
    const isMac = /Macintosh|Mac OS X/.test(navigator.userAgent);
    let ready = false;
    const connect = () => {
        const api = window.pywebview?.api;
        if (ready || !api?.set_theme) {
            return;
        }
        ready = true;
        const root = document.documentElement;
        root.classList.add("is-desktop");
        const syncTheme = () => api.set_theme(root.dataset.bsTheme === "light" ? "light" : "dark");
        new MutationObserver(syncTheme).observe(root, { attributes: true, attributeFilter: ["data-bs-theme"] });
        syncTheme();
        document.addEventListener("click", (event) => {
            const link = event.target.closest?.("a[href]");
            if (!link || event.defaultPrevented || event.button !== 0) {
                return;
            }
            const target = new URL(link.href, location.href);
            if (!/^https?:$/.test(target.protocol)) {
                return;
            }
            if (target.origin === location.origin) {
                const samePage = target.pathname === location.pathname && target.search === location.search;
                const special = link.target || link.hasAttribute("download")
                    || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
                if (isMac && !samePage && !special && !window.moviedbConfirmLeave()) {
                    event.preventDefault();
                }
                return;
            }
            event.preventDefault();
            api.open_external(target.href);
        });
    };
    connect();
    window.addEventListener("pywebviewready", connect);
})();
