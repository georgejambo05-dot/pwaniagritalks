document.addEventListener("DOMContentLoaded", () => {
    const checklistTasks = Array.from(document.querySelectorAll(".maize-task"));
    const checklistProgress = document.getElementById("maize-checklist-progress");
    if (checklistTasks.length && checklistProgress) {
        const updateProgress = () => {
            const completed = checklistTasks.filter((task) => task.checked).length;
            checklistProgress.textContent = `${completed} of ${checklistTasks.length} steps complete.`;
        };
        checklistTasks.forEach((task) => task.addEventListener("change", updateProgress));
    }

    const form = document.getElementById("maize-budget-form");
    const result = document.getElementById("maize-budget-result");
    if (!form || !result) return;

    const money = new Intl.NumberFormat("en-KE", {
        style: "currency",
        currency: "KES",
        maximumFractionDigits: 2
    });
    const value = (name) => Number(form.elements[name].value);

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;

        const area = value("area");
        const perAcreCosts = value("seed_cost") + value("fertilizer_cost") + value("prep_cost");
        const otherCosts = value("other_cost");
        if (area <= 0 || perAcreCosts < 0 || otherCosts < 0) {
            result.textContent = "Enter a farm area above zero and costs of zero or more.";
            return;
        }

        const totalCosts = area * perAcreCosts + otherCosts;
        const yieldText = form.elements["yield_per_acre"].value.trim();
        const priceText = form.elements["sale_price"].value.trim();
        if (Boolean(yieldText) !== Boolean(priceText)) {
            result.textContent = "For the harvest estimate, fill in both optional fields or leave both blank.";
            return;
        }

        let message = `Estimated listed costs: ${money.format(totalCosts)}. This excludes any costs you did not enter.`;
        if (yieldText && priceText) {
            const totalBags = area * Number(yieldText);
            const revenue = totalBags * Number(priceText);
            const grossMargin = revenue - totalCosts;
            message += ` Estimated harvest: ${totalBags.toFixed(1)} bags. Estimated sales: ${money.format(revenue)}. Estimated gross margin before unlisted costs: ${money.format(grossMargin)}.`;
        }
        result.textContent = message;
    });
});


