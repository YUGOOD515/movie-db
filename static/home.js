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
})();
