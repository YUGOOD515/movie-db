(() => {
    document.querySelectorAll("[data-media-image]").forEach((image) => {
        image.addEventListener("error", () => {
            image.classList.add("d-none");
            const fallback = image.parentElement.querySelector(".image-fallback");
            if (fallback) {
                fallback.classList.remove("d-none");
            }
        });
    });

    document.querySelectorAll("[data-media-logo]").forEach((logo) => {
        logo.addEventListener("error", () => logo.classList.add("d-none"));
    });
})();
