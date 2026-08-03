/* Microsoft Foundry Technical Workshop - shared attendee script.
   Every feature here is progressive enhancement. The page is complete without it. */

(function () {
  "use strict";

  var STORAGE_KEY = "foundryws-workshop-theme";

  /* --- Theme ------------------------------------------------------------ */

  function systemTheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function storedTheme() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      return null;
    }
  }

  function activeTheme() {
    return document.documentElement.getAttribute("data-theme") || systemTheme();
  }

  function applyTheme(theme, persist) {
    document.documentElement.setAttribute("data-theme", theme);
    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, theme);
      } catch (error) {
        /* Storage is unavailable; the theme still applies for this page view. */
      }
    }
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      var next = theme === "dark" ? "light" : "dark";
      button.textContent = next === "dark" ? "Dark theme" : "Light theme";
      button.setAttribute("aria-label", "Switch to " + next + " theme");
    });
  }

  function initTheme() {
    applyTheme(activeTheme(), false);

    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.hidden = false;
      button.addEventListener("click", function () {
        applyTheme(activeTheme() === "dark" ? "light" : "dark", true);
      });
    });

    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function (event) {
        if (!storedTheme()) {
          applyTheme(event.matches ? "dark" : "light", false);
        }
      };
      if (query.addEventListener) {
        query.addEventListener("change", onChange);
      } else if (query.addListener) {
        query.addListener(onChange);
      }
    }
  }

  /* --- Copyable commands ------------------------------------------------ */

  function initCopyButtons() {
    if (!navigator.clipboard) {
      return;
    }

    document.querySelectorAll(".code").forEach(function (block) {
      var source = block.querySelector("pre");
      var bar = block.querySelector(".code__bar");
      if (!source || !bar || bar.querySelector("[data-copy]")) {
        return;
      }

      var button = document.createElement("button");
      button.type = "button";
      button.className = "button";
      button.setAttribute("data-copy", "");
      button.textContent = "Copy";

      var status = document.createElement("span");
      status.className = "visually-hidden";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");

      button.addEventListener("click", function () {
        navigator.clipboard.writeText(source.innerText.replace(/\s+$/, "")).then(
          function () {
            button.textContent = "Copied";
            status.textContent = "Command copied to the clipboard.";
            window.setTimeout(function () {
              button.textContent = "Copy";
              status.textContent = "";
            }, 2000);
          },
          function () {
            button.textContent = "Copy failed";
            status.textContent = "Copying failed. Select the command manually.";
            window.setTimeout(function () {
              button.textContent = "Copy";
              status.textContent = "";
            }, 2000);
          }
        );
      });

      bar.appendChild(button);
      bar.appendChild(status);
    });
  }

  /* --- Table of contents ------------------------------------------------ */

  function initTableOfContents() {
    var links = Array.prototype.slice.call(
      document.querySelectorAll(".toc a[href^='#']")
    );
    if (!links.length || !window.IntersectionObserver) {
      return;
    }

    var byId = {};
    var targets = [];
    links.forEach(function (link) {
      var target = document.getElementById(link.getAttribute("href").slice(1));
      if (target) {
        byId[target.id] = link;
        targets.push(target);
      }
    });

    var visible = {};
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          visible[entry.target.id] = entry.isIntersecting;
        });
        var current = null;
        targets.forEach(function (target) {
          if (visible[target.id] && !current) {
            current = target.id;
          }
        });
        links.forEach(function (link) {
          link.removeAttribute("aria-current");
        });
        if (current && byId[current]) {
          byId[current].setAttribute("aria-current", "true");
        }
      },
      { rootMargin: "-72px 0px -65% 0px", threshold: 0 }
    );

    targets.forEach(function (target) {
      observer.observe(target);
    });
  }

  /* --- Bulk disclosure controls ----------------------------------------- */

  function initDisclosureControls() {
    document.querySelectorAll("[data-disclosure]").forEach(function (button) {
      var open = button.getAttribute("data-disclosure") === "expand";
      button.hidden = false;
      button.addEventListener("click", function () {
        document.querySelectorAll(".content details").forEach(function (item) {
          item.open = open;
        });
      });
    });
  }

  function ready(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
    } else {
      callback();
    }
  }

  ready(function () {
    initTheme();
    initCopyButtons();
    initTableOfContents();
    initDisclosureControls();
    document.documentElement.setAttribute("data-enhanced", "true");
  });
})();
