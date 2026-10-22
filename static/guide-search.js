(() => {
    const searchInput = document.getElementById("guide-search");
    const guideCards = Array.from(document.querySelectorAll("#guide-cards .farmer-card"));
    const status = document.getElementById("guide-search-status");

    if (!searchInput || !status || guideCards.length === 0) return;

    searchInput.addEventListener("input", () => {
        const query = searchInput.value.trim().toLocaleLowerCase();
        let visibleCount = 0;

        guideCards.forEach((card) => {
            const searchableText = `${card.textContent} ${card.dataset.searchTerms || ""}`.toLocaleLowerCase();
            const matches = searchableText.includes(query);
            card.hidden = !matches;
            if (matches) visibleCount += 1;
        });

        if (!query) {
            status.textContent = "";
        } else if (visibleCount === 0) {
            status.textContent = "No matching guides. Try another word.";
        } else {
            status.textContent = `${visibleCount} guide${visibleCount === 1 ? "" : "s"} found.`;
        }
    });
})();