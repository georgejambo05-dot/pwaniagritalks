document.addEventListener("DOMContentLoaded", function () {
    const forms = document.querySelectorAll("form[data-cost-calculator]");

    forms.forEach(function (form) {
        const result = form.nextElementSibling;
        const animal = form.dataset.animal;

        if (!result) return;

        form.addEventListener("submit", function (event) {
            event.preventDefault();

            const count = Number(form.elements["animal-count"].value);
            const unitPrice = Number(form.elements["animal-price"].value);
            const housing = Number(form.elements["housing-cost"].value || 0);
            const monthlyFeed = Number(form.elements["monthly-feed"].value);
            const months = Number(form.elements["months"].value);
            const otherCosts = Number(form.elements["other-costs"].value || 0);

            if (count < 1 || unitPrice < 0 || housing < 0 || monthlyFeed < 0 || months < 1 || otherCosts < 0) {
                result.textContent = "Please enter zero or a positive number in each field, and at least one animal and one month.";
                return;
            }

            const animalTotal = count * unitPrice;
            const feedTotal = monthlyFeed * months;
            const total = animalTotal + housing + feedTotal + otherCosts;
            const money = new Intl.NumberFormat("en-KE", {
                style: "currency",
                currency: "KES",
                maximumFractionDigits: 2
            });

            result.textContent = "Estimated total: " + money.format(total) +
                " (animals: " + money.format(animalTotal) +
                ", housing: " + money.format(housing) +
                ", feed for " + months + " months: " + money.format(feedTotal) +
                ", other costs: " + money.format(otherCosts) + ").";
        });
    });
});
