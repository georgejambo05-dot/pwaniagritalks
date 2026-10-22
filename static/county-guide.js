// Show county-specific notes from the sources used in each animal guide.
document.addEventListener("DOMContentLoaded", function () {
    const guide = document.body.dataset.guide;
    const countySelect = document.getElementById("county-select");
    const advice = document.getElementById("county-advice");

    if (!guide || !countySelect || !advice) return;

    const notes = {
        rabbit: {
            "taita-taveta": "A University of Nairobi study included Taita Taveta, but its summary does not say which breeds were recorded in that county. The rabbit types below are reported across the study sample, not confirmed Taita Taveta stock. Check locally.",
            "default": "The sources used here do not identify rabbit breeds county by county. Ask your county livestock office or a local breeder to confirm current availability."
        },
        dairy: {
            kilifi: "A Kilifi dairy report discusses Ayrshire, Guernsey, Jersey, Brown Swiss, Sahiwal, and crosses. This is research and historical reporting, not a current seller list. Ask the county livestock office what is suitable and available now.",
            kwale: "Kwale has reported distribution of hybrid dairy cows, but the source does not name the exact breed or cross. Ask the county livestock office what is currently available.",
            "default": "The sources used here do not identify specific dairy breeds or current sellers for this county. Ask your county livestock office for local guidance."
        },
        chicken: {
            mombasa: "The Kenya Livestock Breeds Catalogue lists Kuroiler, Rainbow Rooster, Sasso, and Kenbro as improved types distributed in the Coastal region. It does not confirm current stock in Mombasa. Ask a hatchery or the county livestock office before travelling or paying.",
            kwale: "The Kenya Livestock Breeds Catalogue lists Kuroiler, Rainbow Rooster, Sasso, and Kenbro as improved types distributed in the Coastal region. It does not confirm current stock in Kwale. Ask a hatchery or the county livestock office before travelling or paying.",
            kilifi: "The Kenya Livestock Breeds Catalogue lists Kuroiler, Rainbow Rooster, Sasso, and Kenbro as improved types distributed in the Coastal region. It does not confirm current stock in Kilifi. Ask a hatchery or the county livestock office before travelling or paying.",
            "tana-river": "The Kenya Livestock Breeds Catalogue lists Kuroiler, Rainbow Rooster, Sasso, and Kenbro as improved types distributed in the Coastal region. It does not confirm current stock in Tana River. Ask a hatchery or the county livestock office before travelling or paying.",
            lamu: "The Kenya Livestock Breeds Catalogue lists Kuroiler, Rainbow Rooster, Sasso, and Kenbro as improved types distributed in the Coastal region. It does not confirm current stock in Lamu. Ask a hatchery or the county livestock office before travelling or paying.",
            "taita-taveta": "The Kenya Livestock Breeds Catalogue lists Kuroiler, Rainbow Rooster, Sasso, and Kenbro as improved types distributed in the Coastal region. It does not confirm current stock in Taita Taveta. Ask a hatchery or the county livestock office before travelling or paying.",
            "default": "Choose a county to see the regional availability note. This is not a live list of chicks for sale."
        },        goats: {
            kwale: "Galla goats and Galla-based hybrid goats are documented in Kwale. Confirm current stock and, for a hybrid, ask which breeds were crossed.",
            "default": "The sources used here document Galla and Galla-based hybrid goats in Kwale, but do not confirm county-specific stock here. Ask your county livestock office or a local breeder."
        }
    };

    countySelect.addEventListener("change", function () {
        const guideNotes = notes[guide];
        if (!guideNotes) return;
        advice.textContent = countySelect.value
            ? (guideNotes[countySelect.value] || guideNotes.default)
            : "Choose a county to see what the sources document. This is not a live list of animals for sale.";
    });
});

