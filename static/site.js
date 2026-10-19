// Site-wide behavior: image fallbacks, theme, filter forms and notifications.
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
        const icon = themeToggle.querySelector("i");

        function syncThemeControl() {
            const isDark = document.documentElement.dataset.bsTheme === "dark";
            const label = isDark ? "切换为浅色主题" : "切换为深色主题";
            if (icon) {
                icon.className = isDark ? "bi bi-sun-fill" : "bi bi-moon-stars-fill";
            }
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
        if (!appToastActive) {
            showNextAppToast();
        }
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
