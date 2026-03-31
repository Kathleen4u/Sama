/* =============================================================
   translate.js  —  Google Translate initialiser + custom toggle
   Load this file ONCE from _base.html (or _scripts.html).
   ============================================================= */

/* ------------------------------------------------------------------
   1. GOOGLE TRANSLATE INITIALISER
   This function is called automatically by the Google script after
   it loads (we pass the callback name in the script URL).
   ------------------------------------------------------------------ */
function googleTranslateElementInit() {
    new google.translate.TranslateElement(
        {
            autoDisplay: false,
            // 0 = SIMPLE (just a <select>), 1 = HORIZONTAL, 2 = VERTICAL
            layout: 0
        },
        "google_translate_element"
    );
}

/* ------------------------------------------------------------------
   2. CUSTOM DROPDOWN TOGGLE
   Runs after DOM is ready. Handles open/close of our custom panel
   and closes it when the user clicks away.
   ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", function () {

    const wrapper  = document.getElementById("translateWrapper");
    const toggle   = document.getElementById("translateToggle");
    const dropdown = document.getElementById("translateDropdown");

    if (!wrapper || !toggle || !dropdown) return; // widget not on this page

    // Open / close on button click
    toggle.addEventListener("click", function (e) {
        e.stopPropagation();
        const isOpen = wrapper.classList.toggle("open");
        dropdown.setAttribute("aria-hidden", String(!isOpen));
    });

    // Close when clicking anywhere outside the wrapper
    document.addEventListener("click", function (e) {
        if (!wrapper.contains(e.target)) {
            wrapper.classList.remove("open");
            dropdown.setAttribute("aria-hidden", "true");
        }
    });

    // Close on Escape key
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            wrapper.classList.remove("open");
            dropdown.setAttribute("aria-hidden", "true");
            toggle.focus();
        }
    });

    // Auto-close the dropdown after the user picks a language
    // (Google rebuilds the select, so we watch for change events via delegation)
    dropdown.addEventListener("change", function () {
        setTimeout(function () {
            wrapper.classList.remove("open");
            dropdown.setAttribute("aria-hidden", "true");
        }, 400); // small delay so the translation starts visibly
    });
});