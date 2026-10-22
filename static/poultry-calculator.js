document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("poultry-cost-form");
    const result = document.getElementById("poultry-cost-result");

    if (!form || !result) return;

    form.addEventListener("submit", function (event) {
        event.preventDefault();

        const chicks = Number(form.elements["chick-count"].value);
        const chickPrice = Number(form.elements["chick-price"].value);
        const feedBags = Number(form.elements["feed-bags"].value);
        const feedPrice = Number(form.elements["feed-price"].value);
        const otherCosts = Number(form.elements["other-costs"].value || 0);

        if (chicks < 1 || chickPrice < 0 || feedBags < 0 || feedPrice < 0 || otherCosts < 0) {
            result.textContent = "Please enter zero or a positive number in each field, and at least one chick.";
            return;
        }

        const chickTotal = chicks * chickPrice;
        const feedTotal = feedBags * feedPrice;
        const total = chickTotal + feedTotal + otherCosts;
        const money = new Intl.NumberFormat("en-KE", {
            style: "currency",
            currency: "KES",
            maximumFractionDigits: 2
        });

        result.textContent = "Estimated total: " + money.format(total) +
            " (chicks: " + money.format(chickTotal) +
            ", feed: " + money.format(feedTotal) +
            ", other costs: " + money.format(otherCosts) + ").";
    });
});
