/* tasks.js — full task management: intake, detail, depends-on, tags, sort/filter */

let _currentTaskId = null;
let _selection = new Set();
let _selectMode = false;

const Tasks = {

  // ── Quick-add / Intake ────────────────────────────────────

  toggleMoreOptions() {
    const panel = document.getElementById("task-add-more");
    const btn = document.getElementById("task-add-more-btn");
    const hidden = panel.classList.toggle("hidden");
    btn.textContent = hidden ? "More options ▾" : "Fewer options ▴";
  },

  async submitIntake() {
    const input = document.getElementById("task-add-input");
    const text = input.value.trim();
    if (!text) { input.focus(); return; }

    const due = document.getElementById("task-add-due")?.value || "";
    const priority = document.getElementById("task-add-priority")?.value || "";
    const btn = document.getElementById("task-add-submit");
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Adding…";
    input.disabled = true;

    try {
      const body = { text };
      if (due) body.due_at = due;
      if (priority) body.priority = priority;

      const r = await fetch("/api/tasks/intake", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (r.ok && d.ok) {
        input.value = "";
        if (document.getElementById("task-add-due")) document.getElementById("task-add-due").value = "";
        if (document.getElementById("task-add-priority")) document.getElementById("task-add-priority").value = "";
        Tasks._prependTaskRow(d.task);
      } else {
        alert(d.error || "Failed to create task");
      }
    } catch (e) {
      alert("Error: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = origText;
      input.disabled = false;
      input.focus();
    }
  },

  _prependTaskRow(task) {
    const section = document.getElementById("section-active");
    // Remove empty state if present
    const empty = section?.querySelector(".tasks-empty");
    if (empty) empty.remove();

    const row = Tasks._buildTaskRow(task);
    section?.insertBefore(row, section.firstChild);
  },

  _buildTaskRow(task) {
    const row = document.createElement("div");
    row.className = "task-row";
    row.dataset.taskId = task.id;
    row.dataset.status = task.status;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "task-checkbox";
    if (task.status === "done") checkbox.checked = true;
    checkbox.onchange = (e) => { e.stopPropagation(); Tasks.toggleDone(task.id, checkbox.checked); };
    checkbox.onclick = (e) => e.stopPropagation();

    const main = document.createElement("span");
    main.className = "task-row-main";
    main.onclick = () => Tasks.openDetail(task.id);
    if (task.emoji) {
      const em = document.createElement("span");
      em.className = "task-emoji";
      em.textContent = task.emoji;
      main.appendChild(em);
    }
    const titleSpan = document.createElement("span");
    titleSpan.className = "task-row-title";
    if (task.status === "done") titleSpan.classList.add("task-row-title--done");
    titleSpan.textContent = task.title;
    main.appendChild(titleSpan);

    const meta = document.createElement("span");
    meta.className = "task-row-meta";
    if (task.due_at) {
      const due = document.createElement("span");
      due.className = "task-due";
      if (window.TODAY && task.due_at < window.TODAY) due.classList.add("task-due--overdue");
      due.textContent = task.due_at.slice(0, 10);
      meta.appendChild(due);
    }
    const pill = document.createElement("span");
    pill.className = `task-priority task-priority--${task.priority || "medium"}`;
    pill.textContent = task.priority || "medium";
    meta.appendChild(pill);

    const sel = document.createElement("select");
    sel.className = `task-status-select task-status--${task.status}`;
    ["open", "waiting", "done", "cancelled"].forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
      if (s === task.status) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = (e) => { e.stopPropagation(); Tasks.setStatus(task.id, sel.value); };
    sel.onclick = (e) => e.stopPropagation();
    meta.appendChild(sel);

    row.appendChild(checkbox);
    row.appendChild(main);
    row.appendChild(meta);
    return row;
  },

  // ── Checkbox / done toggle ────────────────────────────────

  async toggleDone(taskId, checked) {
    const newStatus = checked ? "done" : "open";
    try {
      const r = await fetch(`/api/tasks/${taskId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (r.ok) {
        const row = document.querySelector(`.task-row[data-task-id="${taskId}"]`);
        if (row) Tasks._moveRowToSection(row, newStatus);
      }
    } catch (e) {
      console.error(e);
    }
  },

  // ── Status selector ───────────────────────────────────────

  async setStatus(taskId, newStatus) {
    try {
      const r = await fetch(`/api/tasks/${taskId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (r.ok) {
        const row = document.querySelector(`.task-row[data-task-id="${taskId}"]`);
        if (row) Tasks._moveRowToSection(row, newStatus);
      }
    } catch (e) {
      console.error(e);
    }
  },

  // ── Collapsible sections ──────────────────────────────────

  toggleSection(id) {
    const body = document.getElementById(`section-${id}`);
    const toggle = document.getElementById(`toggle-${id}`);
    if (!body) return;
    const hidden = body.classList.toggle("hidden");
    if (toggle) toggle.textContent = hidden ? "▶" : "▼";
  },

  // ── Sort & Filters modal ──────────────────────────────────

  openFilter() {
    document.getElementById("filter-modal").classList.remove("hidden");
    if (window.SavedFilters) SavedFilters.load("tasks", "task-saved-filters");
    if (window.TagFilterTree) TagFilterTree.init("filter-tag-list");
  },
  closeFilter() {
    document.getElementById("filter-modal").classList.add("hidden");
  },
  applyFilter() {
    const sort = document.getElementById("filter-sort").value;
    const showDone = document.getElementById("filter-show-done").checked;
    const showCancelled = document.getElementById("filter-show-cancelled").checked;
    const params = Tasks._currentFilterParams();
    params.set("sort", sort);
    if (showDone) params.set("show_done", "1");
    if (showCancelled) params.set("show_cancelled", "1");
    window.location.href = "/tasks?" + params.toString();
  },

  _currentFilterParams() {
    const params = new URLSearchParams();
    const sort = document.getElementById("filter-sort")?.value || "default";
    const showDone = document.getElementById("filter-show-done")?.checked;
    const showCancelled = document.getElementById("filter-show-cancelled")?.checked;
    params.set("sort", sort);
    if (showDone) params.set("show_done", "1");
    if (showCancelled) params.set("show_cancelled", "1");
    if (window.TagFilterTree) TagFilterTree.checkedParams("filter-tag-list").forEach(id => params.append("tag", id));
    return params;
  },

  saveCurrentFilter() {
    const params = Tasks._currentFilterParams();
    SavedFilters.save("tasks", {
      sort: params.get("sort") || "default",
      show_done: params.get("show_done") || "",
      show_cancelled: params.get("show_cancelled") || "",
      tag: params.getAll("tag"),
    }, "task-saved-filters");
  },

  switchFilterTab(tab) {
    document.querySelectorAll(".filter-tab").forEach(btn => btn.classList.toggle("filter-tab--active", btn.dataset.tab === tab));
    document.getElementById("filter-tab-topics").classList.toggle("hidden", tab !== "topics");
    document.getElementById("filter-tab-entities").classList.toggle("hidden", tab !== "entities");
  },

  filterTagSearch(query) {
    if (window.TagFilterTree) TagFilterTree.search("filter-tag-list", query);
  },

  // ── Inline filter chips ───────────────────────────────────

  _initFilterChips() {
    const strip = document.getElementById("task-filter-chips");
    if (!strip) return;
    const tags = window.TASK_ALL_TAGS || [];
    if (!tags.length) return;

    const activeIds = new Set((window.ACTIVE_TAG_IDS || []).map(String));

    // Active first, then by task count desc, then alphabetical
    const sorted = [...tags].sort((a, b) => {
      const aA = activeIds.has(String(a.id));
      const bA = activeIds.has(String(b.id));
      if (aA !== bA) return aA ? -1 : 1;
      if ((b.task_count || 0) !== (a.task_count || 0)) return (b.task_count || 0) - (a.task_count || 0);
      return a.name.localeCompare(b.name);
    });

    sorted.forEach(tag => {
      const isActive = activeIds.has(String(tag.id));
      const hasCount = (tag.task_count || 0) > 0;
      const chip = document.createElement("button");
      chip.className = "task-filter-chip" +
        (isActive ? " task-filter-chip--active" : "") +
        (!hasCount ? " task-filter-chip--empty" : "");
      chip.style.setProperty("--chip-color", tag.color || "#888");
      chip.type = "button";
      chip.dataset.tagName = tag.name.toLowerCase();
      chip.dataset.taskCount = tag.task_count || 0;
      // Hide zero-task chips by default; search will reveal them
      if (!hasCount && !isActive) chip.hidden = true;

      const dot = document.createElement("span");
      dot.className = "task-filter-chip-dot";
      chip.appendChild(dot);
      chip.appendChild(document.createTextNode(tag.name));

      chip.addEventListener("click", () => {
        const params = new URLSearchParams(window.location.search);
        const currentTags = params.getAll("tag");
        const tagStr = String(tag.id);
        params.delete("tag");
        if (isActive) {
          currentTags.filter(t => t !== tagStr).forEach(t => params.append("tag", t));
        } else {
          currentTags.forEach(t => params.append("tag", t));
          params.append("tag", tagStr);
        }
        window.location.href = "/tasks?" + params.toString();
      });

      strip.appendChild(chip);
    });
  },

  _filterChipSearch(query) {
    const strip = document.getElementById("task-filter-chips");
    if (!strip) return;
    const q = query.trim().toLowerCase();
    strip.querySelectorAll(".task-filter-chip").forEach(chip => {
      const name = chip.dataset.tagName || "";
      const hasCount = parseInt(chip.dataset.taskCount || "0") > 0;
      const isActive = chip.classList.contains("task-filter-chip--active");
      if (!q) {
        // Restore default: show only task-bearing and active chips
        chip.hidden = !hasCount && !isActive;
      } else {
        chip.hidden = !name.includes(q);
      }
    });
  },

  // ── Group by topic ────────────────────────────────────────

  toggleGroupBy() {
    const active = localStorage.getItem("tasks-group-by") === "topic";
    Tasks.setGroupBy(active ? "none" : "topic");
  },

  setGroupBy(mode) {
    localStorage.setItem("tasks-group-by", mode);
    const btn = document.getElementById("group-by-btn");
    if (btn) btn.classList.toggle("btn-secondary--active", mode === "topic");
    const url = new URL(window.location);
    const currentTab = url.searchParams.get("tab") || "all";
    if (mode === "topic") {
      if (currentTab === "today") {
        Tasks._clearGroupBy();
        Tasks._applyTodayGroupBy();
      } else {
        Tasks._clearTodayGroupBy();
        Tasks._applyGroupBy();
      }
    } else {
      Tasks._clearTodayGroupBy();
      Tasks._clearGroupBy();
    }
  },

  _onTabShow(tab) {
    const mode = localStorage.getItem("tasks-group-by") || "none";
    if (mode !== "topic") return;
    if (tab === "today") {
      Tasks._clearGroupBy();
      if (!document.getElementById("section-today-grouped")) Tasks._applyTodayGroupBy();
    } else if (tab === "all") {
      Tasks._clearTodayGroupBy();
      Tasks._applyGroupBy();
    } else {
      Tasks._clearTodayGroupBy();
      Tasks._clearGroupBy();
    }
  },

  _primaryTopic(taskId) {
    const tagsByCount = {};
    (window.TASK_ALL_TAGS || []).forEach(t => { tagsByCount[t.id] = t; });
    const taskTags = (window.TASK_TAGS || {})[taskId] || [];
    const topics = taskTags.filter(t => t.kind === "topic");
    if (!topics.length) return null;
    return topics.reduce((best, t) => {
      const bestCount = (tagsByCount[best.id] || {}).task_count || 0;
      const tCount = (tagsByCount[t.id] || {}).task_count || 0;
      if (tCount !== bestCount) return tCount > bestCount ? t : best;
      return t.name < best.name ? t : best;
    });
  },

  _applyGroupBy() {
    const section = document.getElementById("section-active");
    if (!section) return;
    Tasks._clearGroupBy();
    Tasks._applyGroupByToSection(section);
  },

  _applyTodayGroupBy() {
    if (document.getElementById("section-today-grouped")) return;

    const container = document.createElement("div");
    container.id = "section-today-grouped";

    let i = 0;
    ["section-today-must", "section-today-should", "section-today-tiny"].forEach(sId => {
      const sec = document.getElementById(sId);
      if (!sec) return;
      const label = sec.querySelector(".today-section-label");
      if (label) label.hidden = true;
      Array.from(sec.querySelectorAll(".task-row")).forEach(row => {
        row.dataset.todaySection = sId;
        row.dataset.originalIndex = i++;
        container.appendChild(row);
      });
    });

    const tab = document.getElementById("tab-today");
    if (tab) {
      const firstSection = tab.querySelector(".tasks-section");
      tab.insertBefore(container, firstSection || null);
    }

    Tasks._applyGroupByToSection(container);
  },

  _clearTodayGroupBy() {
    const grouped = document.getElementById("section-today-grouped");
    if (!grouped) return;
    grouped.querySelectorAll(".task-group-header").forEach(h => h.remove());
    Array.from(grouped.querySelectorAll(".task-row")).forEach(row => {
      const targetId = row.dataset.todaySection;
      if (targetId) {
        const sec = document.getElementById(targetId);
        if (sec) sec.appendChild(row);
      }
      delete row.dataset.todaySection;
      delete row.dataset.originalIndex;
    });
    grouped.remove();
    ["section-today-must", "section-today-should", "section-today-tiny"].forEach(sId => {
      const sec = document.getElementById(sId);
      if (!sec) return;
      const label = sec.querySelector(".today-section-label");
      if (label) label.hidden = false;
    });
  },

  _applyGroupByToSection(section) {
    const rows = Array.from(section.querySelectorAll(".task-row"));
    if (!rows.length) return;

    const groups = new Map();
    rows.forEach((row, i) => {
      row.dataset.originalIndex = i;
      const topic = Tasks._primaryTopic(row.dataset.taskId);
      const key = topic ? String(topic.id) : "__none__";
      if (!groups.has(key)) groups.set(key, { topic, rows: [] });
      groups.get(key).rows.push(row);
    });

    const tagsByCount = {};
    (window.TASK_ALL_TAGS || []).forEach(t => { tagsByCount[t.id] = t; });
    const sorted = [...groups.entries()].sort(([ka, ga], [kb, gb]) => {
      if (ka === "__none__") return 1;
      if (kb === "__none__") return -1;
      const ca = (tagsByCount[ka] || {}).task_count || 0;
      const cb = (tagsByCount[kb] || {}).task_count || 0;
      if (ca !== cb) return cb - ca;
      return (ga.topic.name || "").localeCompare(gb.topic.name || "");
    });

    sorted.forEach(([key, { topic, rows: groupRows }]) => {
      const header = document.createElement("div");
      header.className = "task-group-header";
      header.dataset.groupKey = key;
      if (topic) {
        header.style.setProperty("--group-color", topic.color || "#888");
        header.innerHTML =
          `<span class="task-group-dot"></span>` +
          `<span class="task-group-name">${topic.name}</span>` +
          `<span class="task-group-count">${groupRows.length}</span>`;
      } else {
        header.style.setProperty("--group-color", "var(--ink-3)");
        header.innerHTML =
          `<span class="task-group-name task-group-name--muted">No topic</span>` +
          `<span class="task-group-count">${groupRows.length}</span>`;
      }
      section.appendChild(header);
      groupRows.forEach(row => section.appendChild(row));
    });
  },

  _clearGroupBy() {
    const section = document.getElementById("section-active");
    if (!section) return;
    section.querySelectorAll(".task-group-header").forEach(h => h.remove());
    const rows = Array.from(section.querySelectorAll(".task-row[data-original-index]"));
    if (!rows.length) return;
    rows.sort((a, b) => parseInt(a.dataset.originalIndex) - parseInt(b.dataset.originalIndex));
    rows.forEach(row => section.appendChild(row));
  },

  editDueInline(wrap, taskId, currentDue) {
    if (wrap.querySelector("input")) return;
    wrap.innerHTML = "";
    const input = document.createElement("input");
    input.type = "date";
    input.className = "task-due-inline-input";
    input.value = currentDue || "";
    wrap.appendChild(input);
    input.focus();
    const save = async () => {
      const val = input.value || null;
      await fetch(`/api/tasks/${taskId}/due`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ due_at: val }),
      });
      wrap.innerHTML = val
        ? `<span class="task-due ${val < window.TODAY ? "task-due--overdue" : ""}">${val}</span>`
        : '<span class="task-due task-due--none">no date</span>';
    };
    input.addEventListener("change", save);
    input.addEventListener("blur", save);
  },

  // ── Section management helpers ────────────────────────────

  _moveRowToSection(row, newStatus) {
    const inToday = !!row.closest("#tab-today");

    // Update row attributes
    row.dataset.status = newStatus;

    // Update checkbox
    const checkbox = row.querySelector(".task-checkbox");
    if (checkbox) checkbox.checked = (newStatus === "done");

    // Update title strikethrough
    const title = row.querySelector(".task-row-title");
    if (title) title.classList.toggle("task-row-title--done", newStatus === "done" || newStatus === "cancelled");

    // Update status select
    const sel = row.querySelector(".task-status-select");
    if (sel) {
      sel.value = newStatus;
      sel.className = `task-status-select task-status--${newStatus}`;
    }

    if (inToday) {
      // Today tab: remove row if terminal status, otherwise leave in place
      if (newStatus === "done" || newStatus === "cancelled") row.remove();
      return;
    }

    // All Tasks tab: move to correct section
    const sectionMap = { open: "section-active", waiting: "section-active", done: "section-done", cancelled: "section-cancelled" };
    const targetId = sectionMap[newStatus];
    if (!targetId) return;

    let target = document.getElementById(targetId);
    if (!target) {
      // Create the collapsible section on demand
      const label = newStatus === "done" ? "Done" : "Cancelled";
      const outer = document.createElement("section");
      outer.className = "tasks-section tasks-section--collapsed";
      const hdr = document.createElement("div");
      hdr.className = "tasks-section-header tasks-section-header--clickable";
      hdr.setAttribute("onclick", `Tasks.toggleSection('${newStatus}')`);
      hdr.innerHTML = `<span>${label} <span class="tasks-count">0</span></span><span class="tasks-toggle" id="toggle-${newStatus}">▼</span>`;
      const body = document.createElement("div");
      body.id = targetId;
      outer.appendChild(hdr);
      outer.appendChild(body);
      const tabAll = document.getElementById("tab-all");
      if (tabAll) tabAll.appendChild(outer);
      target = body;
    }

    if (targetId === "section-active") {
      target.insertBefore(row, target.firstChild);
    } else {
      target.classList.remove("hidden");
      target.appendChild(row);
    }

    Tasks._updateSectionCount("section-done");
    Tasks._updateSectionCount("section-cancelled");
  },

  _updateRowFromTask(row, task) {
    row.dataset.status = task.status;

    const main = row.querySelector(".task-row-main");
    if (main) {
      let emojiSpan = main.querySelector(".task-emoji");
      if (task.emoji) {
        if (!emojiSpan) {
          emojiSpan = document.createElement("span");
          emojiSpan.className = "task-emoji";
          main.insertBefore(emojiSpan, main.firstChild);
        }
        emojiSpan.textContent = task.emoji;
      } else if (emojiSpan) {
        emojiSpan.remove();
      }
      const titleSpan = main.querySelector(".task-row-title");
      if (titleSpan) {
        titleSpan.textContent = task.title;
        titleSpan.classList.toggle("task-row-title--done", task.status === "done" || task.status === "cancelled");
      }
    }

    const dueWrap = row.querySelector(".task-due-wrap");
    if (dueWrap) {
      const d = task.due_at ? task.due_at.slice(0, 10) : "";
      const overdue = d && d < (window.TODAY || "");
      dueWrap.innerHTML = d
        ? `<span class="task-due${overdue ? " task-due--overdue" : ""}">${d}</span>`
        : '<span class="task-due task-due--none">no date</span>';
      dueWrap.onclick = (e) => { e.stopPropagation(); Tasks.editDueInline(dueWrap, task.id, d); };
    }

    const pill = row.querySelector(".task-priority");
    if (pill) {
      pill.className = `task-priority task-priority--${task.priority || "medium"}`;
      pill.textContent = task.priority || "medium";
    }

    const sel = row.querySelector(".task-status-select");
    if (sel) {
      sel.value = task.status;
      sel.className = `task-status-select task-status--${task.status}`;
    }
  },

  _updateSectionCount(sectionId) {
    const section = document.getElementById(sectionId);
    if (!section) return;
    const count = section.querySelectorAll(".task-row").length;
    const countEl = section.parentElement?.querySelector(".tasks-count");
    if (countEl) countEl.textContent = count;
  },

  // ── Task detail modal ─────────────────────────────────────

  async openDetail(taskId) {
    _currentTaskId = taskId;
    const modal = document.getElementById("task-detail-modal");
    modal.classList.remove("hidden");

    // Reset UI while loading
    document.getElementById("detail-title").value = "";
    document.getElementById("detail-emoji").value = "";
    document.getElementById("detail-description").value = "";
    document.getElementById("detail-due").value = "";
    document.getElementById("detail-recurrence").value = "";
    document.getElementById("detail-tags").innerHTML = '<span class="task-detail-loading">Loading…</span>';
    document.getElementById("detail-depends").innerHTML = "";

    try {
      const r = await fetch(`/api/tasks/${taskId}`);
      const task = await r.json();

      document.getElementById("detail-title").value = task.title || "";
      document.getElementById("detail-emoji").value = task.emoji || "";
      document.getElementById("detail-description").value = task.description || "";
      document.getElementById("detail-due").value = task.due_at ? task.due_at.slice(0, 10) : "";
      document.getElementById("detail-recurrence").value = task.recurrence_rule || "";
      document.getElementById("detail-priority").value = task.priority || "medium";
      document.getElementById("detail-status").value = task.status || "open";
      document.getElementById("detail-waiting-reason").value = task.waiting_reason || "";

      // Show/hide waiting reason
      const waitRow = document.getElementById("detail-waiting-row");
      waitRow.classList.toggle("hidden", task.status !== "waiting");
      document.getElementById("detail-status").onchange = () => {
        waitRow.classList.toggle("hidden",
          document.getElementById("detail-status").value !== "waiting");
      };

      // Tags
      Tasks._renderDetailTags(task.tags || []);

      // Depends on
      Tasks._renderDetailDepends(task.depends_on || []);

    } catch (e) {
      console.error(e);
    }
  },

  closeDetail() {
    document.getElementById("task-detail-modal").classList.add("hidden");
    document.getElementById("detail-tag-results").classList.add("hidden");
    document.getElementById("detail-dep-results").classList.add("hidden");
    document.getElementById("detail-tag-search").value = "";
    document.getElementById("detail-dep-search").value = "";
    _currentTaskId = null;
  },

  async saveDetail() {
    if (!_currentTaskId) return;
    const fields = {
      title: document.getElementById("detail-title").value.trim(),
      emoji: document.getElementById("detail-emoji").value.trim() || null,
      description: document.getElementById("detail-description").value.trim() || null,
      due_at: document.getElementById("detail-due").value || null,
      priority: document.getElementById("detail-priority").value,
      status: document.getElementById("detail-status").value,
      waiting_reason: document.getElementById("detail-waiting-reason").value.trim() || null,
      recurrence_rule: document.getElementById("detail-recurrence").value.trim() || null,
    };
    if (!fields.title) { document.getElementById("detail-title").focus(); return; }
    const btn = document.querySelector(".task-detail-actions .btn-save");
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const r = await fetch(`/api/tasks/${_currentTaskId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (r.ok) {
        const task = await r.json();
        const row = document.querySelector(`.task-row[data-task-id="${_currentTaskId}"]`);
        if (row) {
          Tasks._updateRowFromTask(row, task);
          Tasks._moveRowToSection(row, task.status);
        }
      }
      Tasks.closeDetail();
    } catch (e) {
      alert("Save failed: " + e.message);
    } finally {
      btn.disabled = false; btn.textContent = "Save";
    }
  },

  async deleteCurrentTask() {
    if (!_currentTaskId) return;
    if (!confirm("Delete this task?")) return;
    await fetch(`/api/tasks/${_currentTaskId}/delete`, { method: "POST" });
    Tasks.closeDetail();
    const row = document.querySelector(`.task-row[data-task-id="${_currentTaskId}"]`);
    if (row) row.remove();
  },

  // ── Detail: Tags ──────────────────────────────────────────

  _renderDetailTags(tags) {
    const container = document.getElementById("detail-tags");
    container.innerHTML = "";
    if (!tags.length) {
      container.innerHTML = '<span class="task-detail-empty-hint">No tags yet</span>';
      return;
    }
    tags.forEach(tag => {
      const chip = document.createElement("span");
      chip.className = "task-detail-chip";
      chip.style.setProperty("--chip-color", tag.color || "#888");
      const href = tag.kind === "entity" ? `/entities/${tag.id}` : `/topics/${tag.id}`;
      chip.innerHTML = `<a class="task-detail-chip-link" href="${href}">${tag.name}</a> <button class="task-detail-chip-remove" data-tag-id="${tag.id}" title="Remove">×</button>`;
      chip.querySelector("button").onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        Tasks.removeTag(tag.id);
      };
      container.appendChild(chip);
    });
  },

  async searchTags(q) {
    const results = document.getElementById("detail-tag-results");
    if (!q.trim()) { results.classList.add("hidden"); return; }

    const all = (window.TASK_ALL_TAGS || []).filter(t =>
      t.name.toLowerCase().includes(q.toLowerCase())
    ).slice(0, 10);

    results.innerHTML = all.length
      ? all.map(t => `<div class="task-detail-result-item" data-tag-id="${t.id}" data-tag-name="${t.name}" data-tag-color="${t.color}">${t.name} <span class="task-detail-result-kind">${t.kind}</span></div>`).join("")
      : '<div class="task-detail-result-item task-detail-result-empty">No tags found</div>';
    results.classList.remove("hidden");

    results.querySelectorAll(".task-detail-result-item[data-tag-id]").forEach(el => {
      el.onclick = () => Tasks.addTag(parseInt(el.dataset.tagId), el.dataset.tagName, el.dataset.tagColor);
    });
  },

  async addTag(tagId, tagName, tagColor) {
    if (!_currentTaskId) return;
    document.getElementById("detail-tag-search").value = "";
    document.getElementById("detail-tag-results").classList.add("hidden");

    await fetch(`/api/tasks/${_currentTaskId}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "add", tag_id: tagId }),
    });
    // Re-fetch full tag list
    const tr = await fetch(`/api/tasks/${_currentTaskId}`);
    const task = await tr.json();
    Tasks._renderDetailTags(task.tags || []);
  },

  async removeTag(tagId) {
    if (!_currentTaskId) return;
    await fetch(`/api/tasks/${_currentTaskId}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "remove", tag_id: tagId }),
    });
    const r = await fetch(`/api/tasks/${_currentTaskId}`);
    const task = await r.json();
    Tasks._renderDetailTags(task.tags || []);
  },

  // ── Detail: Depends-on ────────────────────────────────────

  _renderDetailDepends(deps) {
    const container = document.getElementById("detail-depends");
    container.innerHTML = "";
    if (!deps.length) {
      container.innerHTML = '<span class="task-detail-empty-hint">No dependencies</span>';
      return;
    }
    deps.forEach(dep => {
      const chip = document.createElement("span");
      chip.className = "task-detail-chip task-detail-chip--dep";
      chip.innerHTML = `${dep.emoji ? dep.emoji + " " : ""}${dep.title} <button class="task-detail-chip-remove" data-dep-id="${dep.id}" title="Remove">×</button>`;
      chip.querySelector("button").onclick = () => Tasks.removeDep(dep.id);
      container.appendChild(chip);
    });
  },

  async searchDeps(q) {
    const results = document.getElementById("detail-dep-results");
    if (!q.trim()) { results.classList.add("hidden"); return; }

    const r = await fetch(`/api/tasks/search?q=${encodeURIComponent(q)}&exclude=${_currentTaskId || ""}`);
    const tasks = await r.json();

    results.innerHTML = tasks.length
      ? tasks.map(t => `<div class="task-detail-result-item" data-dep-id="${t.id}">${t.emoji ? t.emoji + " " : ""}${t.title}</div>`).join("")
      : '<div class="task-detail-result-item task-detail-result-empty">No tasks found</div>';
    results.classList.remove("hidden");

    results.querySelectorAll(".task-detail-result-item[data-dep-id]").forEach(el => {
      el.onclick = () => Tasks.addDep(el.dataset.depId);
    });
  },

  async addDep(depTaskId) {
    if (!_currentTaskId) return;
    document.getElementById("detail-dep-search").value = "";
    document.getElementById("detail-dep-results").classList.add("hidden");

    await fetch(`/api/tasks/${_currentTaskId}/depends`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "add", depends_on_task_id: depTaskId }),
    });
    const r = await fetch(`/api/tasks/${_currentTaskId}`);
    const task = await r.json();
    Tasks._renderDetailDepends(task.depends_on || []);
  },

  async removeDep(depTaskId) {
    if (!_currentTaskId) return;
    await fetch(`/api/tasks/${_currentTaskId}/depends`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "remove", depends_on_task_id: depTaskId }),
    });
    const r = await fetch(`/api/tasks/${_currentTaskId}`);
    const task = await r.json();
    Tasks._renderDetailDepends(task.depends_on || []);
  },

  // ── Bulk select mode ──────────────────────────────────────

  toggleSelectMode() {
    if (_selectMode) Tasks.exitSelectMode(); else Tasks.enterSelectMode();
  },

  enterSelectMode() {
    _selectMode = true;
    _selection.clear();
    document.getElementById("select-mode-btn")?.classList.add("btn-secondary--active");
    document.getElementById("bulk-bar")?.classList.remove("hidden");
    document.querySelectorAll(".tasks-section").forEach(s => s.classList.add("tasks-section--selectable"));
    Tasks._updateBulkBar();
  },

  exitSelectMode() {
    _selectMode = false;
    _selection.clear();
    document.getElementById("select-mode-btn")?.classList.remove("btn-secondary--active");
    document.getElementById("bulk-bar")?.classList.add("hidden");
    document.querySelectorAll(".tasks-section").forEach(s => s.classList.remove("tasks-section--selectable"));
    document.querySelectorAll(".task-row--selected").forEach(r => r.classList.remove("task-row--selected"));
  },

  toggleSelectTask(row, taskId) {
    if (_selection.has(taskId)) {
      _selection.delete(taskId);
      row.classList.remove("task-row--selected");
    } else {
      _selection.add(taskId);
      row.classList.add("task-row--selected");
    }
    Tasks._updateBulkBar();
  },

  bulkSelectAll() {
    document.querySelectorAll(".task-row").forEach(row => {
      const id = row.dataset.taskId;
      if (id) { _selection.add(id); row.classList.add("task-row--selected"); }
    });
    Tasks._updateBulkBar();
  },

  _updateBulkBar() {
    const n = _selection.size;
    const countEl = document.getElementById("bulk-count");
    if (countEl) countEl.textContent = n + " selected";
    document.querySelectorAll(".bulk-bar-action").forEach(btn => { btn.disabled = n === 0; });
  },

  async bulkAction(action) {
    const ids = [..._selection];
    if (!ids.length) return;
    if (action === "delete" && !confirm(`Delete ${ids.length} task${ids.length !== 1 ? "s" : ""}?`)) return;
    document.querySelectorAll(".bulk-bar-action").forEach(b => { b.disabled = true; });
    try {
      const r = await fetch("/api/tasks/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_ids: ids, action }),
      });
      if (r.ok) {
        if (action === "delete") {
          ids.forEach(id => {
            const row = document.querySelector(`.task-row[data-task-id="${id}"]`);
            if (row) row.remove();
          });
        } else {
          ids.forEach(id => {
            const row = document.querySelector(`.task-row[data-task-id="${id}"]`);
            if (row) Tasks._moveRowToSection(row, action);
          });
        }
        Tasks.exitSelectMode();
        return;
      }
    } catch { /* fall through */ }
    alert("Bulk action failed — please try again");
    Tasks._updateBulkBar();
  },
};

// ── Event listeners ───────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".filter-tag-item--collapsed").forEach(item => {
    item.style.display = "none";
  });
  if (window.TagFilterTree) TagFilterTree.init("filter-tag-list");
  Tasks._initFilterChips();
  const savedGroupBy = localStorage.getItem("tasks-group-by") || "none";
  Tasks.setGroupBy(savedGroupBy);
  if (window.INITIAL_TASK_ID) {
    Tasks.openDetail(window.INITIAL_TASK_ID);
  }
  // Filter modal backdrop close
  const filterModal = document.getElementById("filter-modal");
  if (filterModal) {
    filterModal.addEventListener("click", e => {
      if (e.target === filterModal) Tasks.closeFilter();
    });
  }

  // Detail modal backdrop close
  const detailModal = document.getElementById("task-detail-modal");
  if (detailModal) {
    detailModal.addEventListener("click", e => {
      if (e.target === detailModal) Tasks.closeDetail();
    });
  }

  // Capture-phase intercept: row clicks in select mode
  document.addEventListener("click", e => {
    if (!_selectMode) return;
    const row = e.target.closest(".task-row");
    if (!row) return;
    e.stopPropagation();
    Tasks.toggleSelectTask(row, row.dataset.taskId);
  }, true);

  // Escape key
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      if (_selectMode) {
        Tasks.exitSelectMode();
      } else if (!document.getElementById("task-detail-modal").classList.contains("hidden")) {
        Tasks.closeDetail();
      } else if (!document.getElementById("filter-modal").classList.contains("hidden")) {
        Tasks.closeFilter();
      }
    }
  });

  // Close dropdowns when clicking outside
  document.addEventListener("click", e => {
    const tagResults = document.getElementById("detail-tag-results");
    const tagSearch = document.getElementById("detail-tag-search");
    if (tagResults && !tagResults.contains(e.target) && e.target !== tagSearch) {
      tagResults.classList.add("hidden");
    }
    const depResults = document.getElementById("detail-dep-results");
    const depSearch = document.getElementById("detail-dep-search");
    if (depResults && !depResults.contains(e.target) && e.target !== depSearch) {
      depResults.classList.add("hidden");
    }
  });
});
