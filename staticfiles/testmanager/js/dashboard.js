document.addEventListener("DOMContentLoaded", function () {
    function safeParse(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch { return null; }
  }

    const statusLabels = safeParse("statusLabels");
    const statusValues = safeParse("statusValues");
    const sheetLabels = safeParse("sheetLabels");
    const sheetValues = safeParse("sheetValues");

    const pieEl = document.getElementById("statusPie");
    const barEl = document.getElementById("sheetBar");

    if (pieEl && statusLabels.length) {
        const ctx = pieEl.getContext("2d");
        statusPieChart = new Chart(ctx, {
            type: "pie",
            data: {
                labels: statusLabels,
                datasets: [{
                    data: statusValues,
                    /* sensible default palette - overrideable if you prefer custom colors */
                    backgroundColor: [
                      "#28b70b",
                      "#ff0202",
                      "#fdf90e",
                      "#9ca3af"
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom" } }
                
            }
        });
    }

    if (!barEl) return;

  const labelsFromView = safeParse("sheetLabels") || [];
  const valuesFromView = safeParse("sheetValues") || [];
  const barMode = window.BAR_MODE;

  barEl.classList.add("chart-animate-enter");

  let labels = [];
  let datasets = [];

  /* =====================================
     CASE 1: SW SELECTED → STATUS OVERVIEW
     ===================================== */
  if (barMode === "status_overview") {

    labels = labelsFromView; // already: Total, Pass, Fail, Not Executed

    datasets = [{
      label: "Test Cases",
      data: valuesFromView,
      backgroundColor: [
        "#0d6efd",  // Total - blue
        "#28b70b",  // Pass - green
        "#ff0202",  // Fail - red
        "#fdf90e"   // Not Executed - yellow
      ]
    }];

  }

  /* =====================================
     CASE 2: ALL / SHEET SELECTED → PER SW
     ===================================== */
  else {

    labels = labelsFromView;     // SW part numbers
    datasets = [{
      label: "Total Test Cases",
      data: valuesFromView,
      backgroundColor: "#0d6efd" // blue
    }];

  }

  new Chart(barEl.getContext("2d"), {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });

  requestAnimationFrame(() => {
    barEl.classList.add("chart-animate-enter-active");
  });

});

document.addEventListener("DOMContentLoaded", function () {
    const root = document.getElementById("dashboard-root");
    if (!root) return;

    const sheetSelect = document.querySelector('select[name="sheet"]');
    const swSelect = document.querySelector('select[name="sw"]');
    const filterForm = sheetSelect ? sheetSelect.closest("form") : null;

    // Prefer-reduced-motion detection
    function reduceMotion() {
        return window.matchMedia &&
               window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    }

    // Initial enter animation
    requestAnimationFrame(() => {
        setTimeout(() => {
            root.classList.remove("anim-preload");
            root.classList.add("anim-ready");
        }, 40);
    });

    // Animate exit → submit
    function animateExitAndSubmit(fast = false) {
        if (reduceMotion()) {
            filterForm.submit();
            return;
        }
        root.classList.remove("anim-ready");
        root.classList.add(fast ? "anim-exiting-fast" : "anim-exiting");

        const delay = fast ? 200 : 320;
        setTimeout(() => {
            root.classList.remove("anim-exiting", "anim-exiting-fast");
            filterForm.submit();
        }, delay);
    }

    // Trigger animation on sheet change
    if (sheetSelect) {
        sheetSelect.addEventListener("change", () => {
            if (!filterForm) return;
            animateExitAndSubmit(false);
        });
    }

    // SW select changes also animate
    if (swSelect) {
        swSelect.addEventListener("change", () => {
            if (!filterForm) return;
            animateExitAndSubmit(false);
        });
    }

    // Explicit "Apply" submit button
    if (filterForm) {
        filterForm.addEventListener("submit", function (e) {
            if (reduceMotion()) return;
            e.preventDefault();
            animateExitAndSubmit(true);
        });
    }
});