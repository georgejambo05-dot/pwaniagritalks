document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".guide-print-button").forEach((button) => {
        button.addEventListener("click", () => window.print());
    });
});
