/* search.js — global command-palette search */

const GlobalSearch = (() => {
  let _debounce = null;
  let _focused = -1;
  let _items = [];

  function init() {
    document.addEventListener("keydown", e => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        open();
      }
    });
    const overlay = document.getElementById("gsearch-overlay");
    if (overlay) {
      overlay.addEventListener("click", e => { if (e.target === overlay) close(); });
    }
    const input = document.getElementById("gsearch-input");
    if (input) {
      input.addEventListener("input", () => {
        clearTimeout(_debounce);
        _debounce = setTimeout(() => _fetch(input.value.trim()), 180);
      });
      input.addEventListener("keydown", _onKey);
    }
  }

  function open() {
    const overlay = document.getElementById("gsearch-overlay");
    if (!overlay) return;
    overlay.classList.remove("hidden");
    const input = document.getElementById("gsearch-input");
    if (input) { input.value = ""; input.focus(); }
    _items = [];
    _focused = -1;
    const results = document.getElementById("gsearch-results");
    if (results) results.innerHTML = "";
  }

  function close() {
    document.getElementById("gsearch-overlay")?.classList.add("hidden");
    clearTimeout(_debounce);
  }

  async function _fetch(q) {
    const results = document.getElementById("gsearch-results");
    if (!results) return;
    if (q.length < 2) {
      results.innerHTML = "";
      _items = [];
      _focused = -1;
      return;
    }
    results.innerHTML = '<div class="gsearch-status">Searching…</div>';
    try {
      const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      if (!r.ok) throw new Error("request failed");
      _render(await r.json());
    } catch {
      results.innerHTML = '<div class="gsearch-status">Error — try again</div>';
    }
  }

  function _render(data) {
    const results = document.getElementById("gsearch-results");
    if (!results) return;
    results.innerHTML = "";
    _items = [];
    _focused = -1;

    const sections = [
      {
        key: "tasks",
        label: "Tasks",
        urlFn: t => `/tasks?task=${t.id}`,
        titleFn: t => t.title,
        iconFn: t => t.emoji || null,
        subFn: t => t.status !== "open" ? t.status : null,
      },
      {
        key: "topics",
        label: "Topics",
        urlFn: t => `/topics/${t.id}`,
        titleFn: t => t.name,
        iconFn: t => null,
        dotFn: t => t.color,
        subFn: t => t.description ? t.description.slice(0, 60) : null,
      },
      {
        key: "entities",
        label: "Entities",
        urlFn: t => `/entities/${t.id}`,
        titleFn: t => t.name,
        iconFn: t => null,
        dotFn: t => t.color,
        subFn: t => t.description ? t.description.slice(0, 60) : null,
      },
    ];

    let anyResults = false;
    sections.forEach(({ key, label, urlFn, titleFn, iconFn, dotFn, subFn }) => {
      const items = data[key] || [];
      if (!items.length) return;
      anyResults = true;

      const section = document.createElement("div");
      section.className = "gsearch-section";

      const secLabel = document.createElement("div");
      secLabel.className = "gsearch-section-label";
      secLabel.textContent = label;
      section.appendChild(secLabel);

      items.forEach(item => {
        const a = document.createElement("a");
        a.className = "gsearch-item";
        a.href = urlFn(item);
        const idx = _items.length;
        _items.push(a);

        const iconEl = document.createElement("span");
        iconEl.className = "gsearch-item-icon";
        const emoji = iconFn ? iconFn(item) : null;
        const dot = dotFn ? dotFn(item) : null;
        if (emoji) {
          iconEl.textContent = emoji;
        } else if (dot) {
          const d = document.createElement("span");
          d.className = "gsearch-item-dot";
          d.style.background = dot;
          iconEl.appendChild(d);
        }

        const titleEl = document.createElement("span");
        titleEl.className = "gsearch-item-title";
        titleEl.textContent = titleFn(item);

        a.appendChild(iconEl);
        a.appendChild(titleEl);

        const sub = subFn ? subFn(item) : null;
        if (sub) {
          const subEl = document.createElement("span");
          subEl.className = "gsearch-item-sub";
          subEl.textContent = sub;
          a.appendChild(subEl);
        }

        a.addEventListener("mouseenter", () => _setFocus(idx));
        a.addEventListener("click", close);
        section.appendChild(a);
      });

      results.appendChild(section);
    });

    if (!anyResults) {
      results.innerHTML = '<div class="gsearch-status">No results</div>';
    }
  }

  function _setFocus(idx) {
    _items.forEach((el, i) => el.classList.toggle("gsearch-item--active", i === idx));
    _focused = idx;
  }

  function _onKey(e) {
    if (e.key === "Escape") { close(); return; }
    if (!_items.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      _setFocus((_focused + 1) % _items.length);
      _items[_focused]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      _setFocus((_focused - 1 + _items.length) % _items.length);
      _items[_focused]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && _focused >= 0) {
      e.preventDefault();
      _items[_focused]?.click();
    }
  }

  return { init, open, close };
})();

document.addEventListener("DOMContentLoaded", GlobalSearch.init);
