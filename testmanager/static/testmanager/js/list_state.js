document.addEventListener("DOMContentLoaded", function () {
  const rowId = window.HIGHLIGHT_ID;
  if (rowId) {
    const row = document.getElementById("row-" + rowId);
    if (row) {
      row.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
});

(function(){
    const storageKey = "testcase_list_state";

    function saveState() {
        try { sessionStorage.setItem(storageKey, window.location.href); } catch (e) {}
    }

    function restoreState() {
        try {
            const saved = sessionStorage.getItem(storageKey);
            if (!saved) return;
            const current = window.location.href;
            // Only restore when current URL has NO querystring (no filters)
            if (saved && saved !== current && window.location.pathname.endsWith("/testcases/") && !window.location.search) {
                sessionStorage.removeItem(storageKey);
                window.location.replace(saved);
            }
        } catch (e) {}
    }

    document.addEventListener("click", function(e){
        const el = e.target.closest("a, button, input[type='submit']");
        if (!el) return;
        if (el.tagName === "A" && el.getAttribute("href") && !el.getAttribute("href").startsWith("#")) {
            saveState();
        }
        if (el.tagName === "BUTTON" || (el.tagName==="INPUT" && el.type==="submit")) {
            saveState();
        }
    }, true);

    document.addEventListener("DOMContentLoaded", restoreState);
})();