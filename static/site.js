(() => {
    const themeToggle = document.getElementById("theme-toggle");
    if (!themeToggle) {
        return;
    }

    const icon = themeToggle.querySelector("i");
    const label = themeToggle.querySelector(".theme-label");

    function syncThemeControl() {
        const isDark = document.documentElement.dataset.bsTheme === "dark";
        if (icon) {
            icon.className = isDark ? "bi bi-sun" : "bi bi-moon";
        }
        if (label) {
            label.textContent = isDark ? "深色" : "浅色";
        }
        themeToggle.setAttribute(
            "aria-label",
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

    const isDetailPage = /^\/(movies|series)\/\d+\/?$/.test(
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
})();
