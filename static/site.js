(() => {
    const themeToggle = document.getElementById("theme-toggle");
    if (!themeToggle) {
        return;
    }

    const icon = themeToggle.querySelector("i");

    function syncThemeControl() {
        const isDark = document.documentElement.dataset.bsTheme === "dark";
        icon.className = isDark ? "bi bi-sun" : "bi bi-moon";
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
        if (
            link.pathname === window.location.pathname
            || (isDetailPage && link.pathname === "/")
        ) {
            link.setAttribute("aria-current", "page");
        }
    });
})();
