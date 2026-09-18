(() => {
  "use strict";

  function init() {
    const figures = [...document.querySelectorAll("[data-diagram]")];
    if (!figures.length || document.documentElement.hasAttribute("data-architecture-ready")) return;

    const nodes = figures.flatMap(figure => [...figure.querySelectorAll(".arch-node[data-detail]")]);
    const details = [...document.querySelectorAll(".component-detail")];
    const popup = document.createElement("aside");
    popup.className = "diagram-popup";
    popup.id = uniqueId("architecture-popup");
    popup.hidden = true;
    const head = document.createElement("div");
    head.className = "diagram-popup-head";
    const heading = document.createElement("h2");
    heading.className = "diagram-popup-title";
    heading.id = uniqueId("architecture-popup-title");
    const close = document.createElement("button");
    close.className = "diagram-popup-close";
    close.type = "button";
    close.textContent = "Close";
    close.setAttribute("aria-label", "Close component details");
    const content = document.createElement("div");
    content.className = "diagram-popup-content";
    head.append(heading, close);
    popup.append(head, content);
    popup.setAttribute("aria-labelledby", heading.id);
    document.body.append(popup);

    let activeNode = null;
    let pointerNode = null;
    let pointerInPopup = false;
    let pinned = false;
    let dismissTimer = 0;
    let positionFrame = 0;
    let focusVersion = 0;
    let ignoreFocus = null;
    let focusState = null;
    let printState = null;
    let printTheme = null;

    function uniqueId(base) {
      let id = base;
      let suffix = 1;
      while (document.getElementById(id)) id = `${base}-${suffix++}`;
      return id;
    }

    function detailFor(node) {
      const detail = document.getElementById(node.dataset.detail);
      return detail?.matches(".component-detail") ? detail : null;
    }

    function highlight(node, on) {
      node.classList.toggle("is-active", on);
      node.classList.toggle("is-pinned", on && pinned);
      node.setAttribute("aria-expanded", String(on));
      node.closest("[data-diagram]").querySelectorAll(".edge[data-from], .edge[data-to]").forEach(edge => {
        edge.classList.toggle("is-active", on &&
          (edge.dataset.from === node.dataset.detail || edge.dataset.to === node.dataset.detail));
      });
    }

    function positionPopup() {
      positionFrame = 0;
      if (!activeNode || popup.hidden) return;
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0;
      const top = viewport?.offsetTop || 0;
      const width = viewport?.width || window.innerWidth;
      const height = viewport?.height || window.innerHeight;
      const margin = 12;
      const gap = 12;
      popup.style.maxWidth = `${Math.max(1, width - margin * 2)}px`;
      popup.style.maxHeight = `${Math.max(1, height - margin * 2)}px`;
      const box = activeNode.getBoundingClientRect();
      const size = popup.getBoundingClientRect();
      let x = box.right + gap;
      let y = box.top;
      if (x + size.width > left + width - margin) x = box.left - size.width - gap;
      if (x < left + margin) {
        x = box.left;
        y = box.bottom + gap;
        if (y + size.height > top + height - margin) y = box.top - size.height - gap;
      }
      popup.style.left = `${Math.max(left + margin, Math.min(x, left + width - size.width - margin))}px`;
      popup.style.top = `${Math.max(top + margin, Math.min(y, top + height - size.height - margin))}px`;
    }

    function queuePosition() {
      if (!positionFrame) positionFrame = requestAnimationFrame(positionPopup);
    }

    function hidePopup(restoreFocus = false) {
      clearTimeout(dismissTimer);
      focusVersion++;
      const previous = activeNode;
      if (previous) highlight(previous, false);
      activeNode = null;
      pinned = false;
      pointerInPopup = false;
      popup.hidden = true;
      popup.removeAttribute("aria-modal");
      if (restoreFocus && previous?.isConnected) {
        ignoreFocus = previous;
        previous.focus({ preventScroll: true });
        ignoreFocus = null;
      }
    }

    function dismissSoon() {
      clearTimeout(dismissTimer);
      dismissTimer = setTimeout(() => {
        if (!pinned && activeNode && pointerNode !== activeNode && !pointerInPopup &&
          !activeNode.contains(document.activeElement) && !popup.contains(document.activeElement)) hidePopup();
      }, 240);
    }

    function showPopup(node, pin = false, keyboard = false) {
      if (pinned && !pin) return;
      const detail = detailFor(node);
      const summary = detail?.querySelector(":scope > summary");
      const copy = detail?.querySelector(":scope > .component-copy");
      if (!summary || !copy) return;
      clearTimeout(dismissTimer);
      if (activeNode !== node) {
        if (activeNode) highlight(activeNode, false);
        const clone = copy.cloneNode(true);
        [clone, ...clone.querySelectorAll("[id]")].forEach(element => element.removeAttribute("id"));
        clone.querySelectorAll("[aria-labelledby], [aria-describedby], [aria-controls], [for]").forEach(element => {
          ["aria-labelledby", "aria-describedby", "aria-controls", "for"].forEach(name => element.removeAttribute(name));
        });
        heading.textContent = summary.textContent.trim();
        content.replaceChildren(clone);
        content.scrollTop = 0;
      }
      activeNode = node;
      pinned = pin;
      popup.setAttribute("role", pin ? "dialog" : "region");
      if (pin) popup.setAttribute("aria-modal", "false");
      else popup.removeAttribute("aria-modal");
      close.hidden = !pin;
      popup.hidden = false;
      highlight(node, true);
      positionPopup();
      if (pin && keyboard) close.focus({ preventScroll: true });
    }

    function openAncestors(target) {
      for (let current = target; current && current !== document.body; current = current.parentElement) {
        if (current.matches("details")) current.open = true;
        if (current.matches(".card, .reveal")) {
          current.setAttribute("data-open", "");
          current.querySelector(":scope > .card-head > .card-toggle, :scope > .reveal-toggle")
            ?.setAttribute("aria-expanded", "true");
        }
      }
    }

    function hashTarget(hash = location.hash) {
      try { return document.getElementById(decodeURIComponent(hash.slice(1))); }
      catch { return null; }
    }

    function revealHash() {
      const target = hashTarget();
      const detail = target?.closest(".component-detail");
      const node = nodes.find(candidate => candidate.dataset.detail === detail?.id);
      if (!node) return;
      if (focusState) exitFocus(false);
      hidePopup();
      openAncestors(node);
      node.scrollIntoView({ block: "center" });
      requestAnimationFrame(() => showPopup(node, true));
    }

    function exitFocus(restoreFocus = true) {
      if (!focusState) return;
      hidePopup();
      const { figure, button, placeholder, label, role, ariaLabel, scrollX, scrollY } = focusState;
      focusState = null;
      figure.classList.remove("is-focused");
      if (role === null) figure.removeAttribute("role");
      else figure.setAttribute("role", role);
      if (ariaLabel === null) figure.removeAttribute("aria-label");
      else figure.setAttribute("aria-label", ariaLabel);
      placeholder.replaceWith(figure);
      document.body.classList.remove("architecture-focus-open");
      button.textContent = label;
      button.setAttribute("aria-pressed", "false");
      window.scrollTo({ left: scrollX, top: scrollY, behavior: "instant" });
      if (restoreFocus) button.focus({ preventScroll: true });
    }

    function enterFocus(figure, button) {
      if (focusState?.figure === figure) {
        exitFocus();
        return;
      }
      if (focusState) exitFocus(false);
      hidePopup();
      const placeholder = document.createComment("diagram focus location");
      focusState = {
        figure, button, placeholder, label: button.textContent,
        role: figure.getAttribute("role"), ariaLabel: figure.getAttribute("aria-label"),
        scrollX: window.scrollX, scrollY: window.scrollY
      };
      figure.before(placeholder);
      document.body.insertBefore(figure, popup);
      figure.classList.add("is-focused");
      figure.setAttribute("role", "region");
      figure.setAttribute("aria-label", `${figure.querySelector(".diagram-label")?.textContent.trim() || "Architecture"} diagram focus view`);
      document.body.classList.add("architecture-focus-open");
      button.textContent = "Exit focus";
      button.setAttribute("aria-pressed", "true");
      button.focus({ preventScroll: true });
    }

    details.forEach(detail => { detail.open = false; });

    nodes.forEach(node => {
      if (!detailFor(node)) return;
      node.setAttribute("aria-controls", popup.id);
      node.setAttribute("aria-expanded", "false");
      node.setAttribute("aria-haspopup", "dialog");
      node.addEventListener("pointerenter", event => {
        if (event.pointerType === "touch") return;
        pointerNode = node;
        showPopup(node);
      });
      node.addEventListener("pointerleave", event => {
        if (event.pointerType === "touch") return;
        if (pointerNode === node) pointerNode = null;
        dismissSoon();
      });
      node.addEventListener("focus", () => {
        if (ignoreFocus === node || pinned) return;
        hidePopup();
        const version = focusVersion;
        // Native keyboard focus can scroll a tall diagram before its next paint.
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (version === focusVersion && document.activeElement === node) showPopup(node);
        }));
      });
      node.addEventListener("blur", dismissSoon);
      node.addEventListener("click", event => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        showPopup(node, true, event.detail === 0);
      });
      node.addEventListener("keydown", event => {
        if (event.key !== " " || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
        event.preventDefault();
        if (!event.repeat) showPopup(node, true, true);
      });
    });

    figures.forEach(figure => {
      const button = figure.querySelector(".diagram-focus");
      const scroll = figure.querySelector(".diagram-scroll");
      if (button) {
        button.setAttribute("aria-pressed", "false");
        button.addEventListener("click", () => enterFocus(figure, button));
      }
      if (scroll) {
        scroll.tabIndex = 0;
        scroll.setAttribute("role", "region");
        scroll.setAttribute("aria-label", `${figure.querySelector(".diagram-label")?.textContent.trim() || "Architecture"} diagram; scroll to explore`);
      }
    });

    popup.addEventListener("pointerenter", () => {
      pointerInPopup = true;
      clearTimeout(dismissTimer);
    });
    popup.addEventListener("pointerleave", () => {
      pointerInPopup = false;
      dismissSoon();
    });
    popup.addEventListener("focusout", dismissSoon);
    close.addEventListener("click", () => hidePopup(true));

    document.addEventListener("pointerdown", event => {
      if (activeNode && !popup.contains(event.target) && !activeNode.contains(event.target)) hidePopup();
    });

    document.addEventListener("focusin", event => {
      if (focusState && !focusState.figure.contains(event.target) && !popup.contains(event.target)) exitFocus(false);
      if (pinned && !popup.contains(event.target) && !activeNode?.contains(event.target) &&
        !event.target.closest(".arch-node")) hidePopup();
    });

    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      focusVersion++;
      if (activeNode) {
        event.preventDefault();
        hidePopup(pinned || popup.contains(document.activeElement));
      } else if (focusState) {
        event.preventDefault();
        exitFocus();
      }
    });

    document.addEventListener("click", event => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const anchor = event.target.closest("a[href]");
      const href = anchor?.getAttribute("href");
      if (!href?.startsWith("#")) return;
      const target = hashTarget(href);
      if (!target?.closest(".component-detail")) return;
      event.preventDefault();
      if (location.hash === href) revealHash();
      else location.hash = href;
    });

    document.addEventListener("scroll", event => {
      if (!activeNode || popup.contains(event.target)) return;
      if (pinned || activeNode.contains(document.activeElement)) queuePosition();
      else hidePopup();
    }, true);
    window.addEventListener("resize", queuePosition, { passive: true });
    window.visualViewport?.addEventListener("resize", queuePosition, { passive: true });
    window.visualViewport?.addEventListener("scroll", queuePosition, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(queuePosition).observe(popup);
    window.addEventListener("hashchange", revealHash);
    window.addEventListener("beforeprint", () => {
      exitFocus();
      hidePopup();
      if (!printState) {
        printState = details.map(detail => [detail, detail.open]);
        printTheme = document.documentElement.getAttribute("data-theme");
      }
      document.documentElement.setAttribute("data-theme", "light");
      details.forEach(detail => { detail.open = true; });
    });
    window.addEventListener("afterprint", () => {
      printState?.forEach(([detail, open]) => { detail.open = open; });
      if (printTheme) document.documentElement.setAttribute("data-theme", printTheme);
      printState = null;
      printTheme = null;
    });
    document.documentElement.setAttribute("data-architecture-ready", "");
    revealHash();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
