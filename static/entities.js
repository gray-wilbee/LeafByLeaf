/* global ENTITY_ID */
let modalChatId = null;

const Entity = {

  // ---------------------------------------------------------------------------
  // Inline editing
  // ---------------------------------------------------------------------------

  editName() {
    const display = document.getElementById('entity-name-display');
    const current = display.textContent.trim();
    const input = document.createElement('input');
    input.type = 'text';
    input.value = current;
    input.className = 'topic-edit-input topic-edit-input--name';
    display.replaceWith(input);
    input.focus();
    input.select();
    const save = async () => {
      const val = input.value.trim();
      if (val && val !== current) {
        await Entity.save({ name: val });
        document.title = val + ' — Entities';
      }
      const h1 = document.createElement('h1');
      h1.className = 'topic-name';
      h1.id = 'entity-name-display';
      h1.textContent = val || current;
      input.replaceWith(h1);
    };
    input.addEventListener('blur', save);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); input.blur(); } });
  },

  editDesc() {
    const display = document.getElementById('entity-desc-display');
    const current = display.textContent.trim();
    const ta = document.createElement('textarea');
    ta.value = current === 'No description.' ? '' : current;
    ta.className = 'topic-edit-input topic-edit-input--desc';
    ta.rows = 3;
    display.replaceWith(ta);
    ta.focus();
    const save = async () => {
      const val = ta.value.trim();
      await Entity.save({ description: val });
      const p = document.createElement('p');
      p.className = 'topic-desc';
      p.id = 'entity-desc-display';
      p.textContent = val || 'No description.';
      ta.replaceWith(p);
    };
    ta.addEventListener('blur', save);
  },

  async save(fields) {
    await fetch(`/api/entities/${ENTITY_ID}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
  },

  // ---------------------------------------------------------------------------
  // Color picker
  // ---------------------------------------------------------------------------

  async setColor(hex) {
    document.getElementById('entity-header').style.setProperty('--topic-color', hex);
    document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('active'));
    const swatch = document.querySelector(`.color-swatch[data-color="${hex}"]`);
    if (swatch) swatch.classList.add('active');
    await Entity.save({ color: hex });
  },

  // ---------------------------------------------------------------------------
  // AI actions
  // ---------------------------------------------------------------------------

  async refreshDescription() {
    const btn = document.querySelector('[onclick="Entity.refreshDescription()"]');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Refreshing…';
    try {
      const r = await fetch(`/api/entities/${ENTITY_ID}/refresh-description`, { method: 'POST' });
      const data = await r.json();
      const el = document.getElementById('entity-desc-display');
      if (el) el.textContent = data.description;
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  },

  editSummary() {
    document.getElementById('entity-summary-wrap').classList.add('hidden');
    document.getElementById('summary-edit-btn').classList.add('hidden');
    document.getElementById('summary-edit-area').classList.remove('hidden');
    document.getElementById('summary-edit-textarea').focus();
  },

  cancelSummaryEdit() {
    document.getElementById('summary-edit-area').classList.add('hidden');
    document.getElementById('entity-summary-wrap').classList.remove('hidden');
    document.getElementById('summary-edit-btn').classList.remove('hidden');
  },

  async saveSummaryEdit() {
    const val = document.getElementById('summary-edit-textarea').value;
    await Entity.save({ summary: val });
    let wrap = document.getElementById('entity-summary');
    const emptyEl = document.getElementById('entity-summary-empty');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'topic-summary';
      wrap.id = 'entity-summary';
      if (emptyEl) emptyEl.replaceWith(wrap);
      else document.getElementById('entity-summary-wrap').appendChild(wrap);
    }
    if (val.trim()) {
      wrap.innerHTML = window.VJSanitize(marked.parse(val));
    } else {
      wrap.innerHTML = '';
    }
    Entity.cancelSummaryEdit();
  },

  async refreshSummary() {
    const btn = document.getElementById('summary-refresh-btn');
    btn.disabled = true;
    btn.textContent = 'Generating…';
    try {
      const r = await fetch(`/api/entities/${ENTITY_ID}/refresh-summary`, { method: 'POST' });
      const data = await r.json();
      let wrap = document.getElementById('entity-summary');
      const emptyEl = document.getElementById('entity-summary-empty');
      if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'topic-summary';
        wrap.id = 'entity-summary';
        if (emptyEl) emptyEl.replaceWith(wrap);
        else document.getElementById('entity-summary-wrap').appendChild(wrap);
      }
      wrap.innerHTML = window.VJSanitize(marked.parse(data.summary));
      btn.textContent = 'Refresh summary';
    } catch (e) {
      btn.textContent = 'Error — try again';
    } finally {
      btn.disabled = false;
    }
  },

  // ---------------------------------------------------------------------------
  // Compact
  // ---------------------------------------------------------------------------

  async compact() {
    if (!confirm('Compact all entries into a single AI-generated summary?\n\nAll current entries will be archived (still viewable but no longer loaded into AI context).')) return;
    const r = await fetch(`/api/entities/${ENTITY_ID}/compact`, { method: 'POST' });
    if (r.ok) {
      location.reload();
    } else {
      const d = await r.json();
      alert(d.error || 'Compact failed');
    }
  },

  // ---------------------------------------------------------------------------
  // Merge
  // ---------------------------------------------------------------------------

  openMerge() {
    document.getElementById('merge-modal').classList.remove('hidden');
  },

  closeMerge() {
    document.getElementById('merge-modal').classList.add('hidden');
  },

  async confirmMerge() {
    const sel = document.getElementById('merge-target-select');
    if (!sel) return;
    const targetId = parseInt(sel.value);
    if (!targetId) return;
    try {
      const r = await fetch(`/api/entities/${ENTITY_ID}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_id: targetId }),
      });
      const data = await r.json();
      if (data.ok) {
        window.location.href = data.redirect;
      } else {
        alert(data.error || 'Merge failed');
      }
    } catch (e) {
      alert('Merge error: ' + e.message);
    }
  },

  // ---------------------------------------------------------------------------
  // Delete
  // ---------------------------------------------------------------------------

  async deleteEntity() {
    if (!confirm('Delete this entity and all its entries and chats? This cannot be undone.')) return;
    const r = await fetch(`/api/entities/${ENTITY_ID}/delete`, { method: 'POST' });
    if (r.ok) {
      window.location.href = '/entities';
    } else {
      alert('Delete failed');
    }
  },

  // ---------------------------------------------------------------------------
  // Entries
  // ---------------------------------------------------------------------------

  openAddNote() {
    document.getElementById('add-note-modal').classList.remove('hidden');
    document.getElementById('note-content').focus();
  },

  closeAddNote() {
    document.getElementById('add-note-modal').classList.add('hidden');
  },

  async submitNote() {
    const content = document.getElementById('note-content').value.trim();
    if (!content) return;
    const date = document.getElementById('note-date').value;
    const btn = document.getElementById('add-note-submit');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Adding…';
    try {
      const r = await fetch(`/api/entities/${ENTITY_ID}/entries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, date }),
      });
      if (r.ok) {
        Entity.closeAddNote();
        location.reload();
      } else {
        const d = await r.json();
        alert(d.error || 'Failed to add note');
      }
    } catch (e) {
      alert('Error: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  },

  toggleEntry(id) {
    const content = document.getElementById(`entry-${id}`);
    const toggle = document.getElementById(`toggle-${id}`);
    if (!content) return;
    const isHidden = content.classList.toggle('hidden');
    if (toggle) toggle.textContent = isHidden ? '▶' : '▼';
  },

  toggleArchived() {
    const section = document.getElementById('archived-entries');
    const btn = document.getElementById('archived-toggle-btn');
    if (!section) return;
    const isHidden = section.classList.toggle('hidden');
    if (btn) btn.textContent = isHidden
      ? btn.textContent.replace('Hide', 'View')
      : btn.textContent.replace('View', 'Hide');
  },

  // ---------------------------------------------------------------------------
  // Connections (tag_links)
  // ---------------------------------------------------------------------------

  openLinkModal() {
    document.getElementById('link-modal').classList.remove('hidden');
  },

  closeLinkModal() {
    document.getElementById('link-modal').classList.add('hidden');
  },

  async submitLink() {
    const toTagId = parseInt(document.getElementById('link-target-select').value);
    if (!toTagId) return;
    const note = document.getElementById('link-note').value.trim();
    await fetch(`/api/tags/${ENTITY_ID}/links`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_tag_id: toTagId, note: note || undefined }),
    });
    location.reload();
  },

  async removeLink(toTagId) {
    if (!confirm('Remove this connection?')) return;
    await fetch(`/api/tags/${ENTITY_ID}/unlink`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_tag_id: toTagId }),
    });
    location.reload();
  },

  // ---------------------------------------------------------------------------
  // Chat modal
  // ---------------------------------------------------------------------------

  async openChatModal(chatId = null) {
    const modal = document.getElementById('entity-chat-modal');
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    if (chatId) {
      modalChatId = chatId;
      await Entity._loadModalMessages(chatId);
    } else {
      const r = await fetch('/api/chats', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope_tag_ids: [ENTITY_ID] }),
      });
      const data = await r.json();
      modalChatId = data.chat_id;
    }
    document.getElementById('modal-chat-input').focus();
  },

  closeChatModal() {
    document.getElementById('entity-chat-modal').classList.add('hidden');
    document.body.style.overflow = '';
    modalChatId = null;
  },

  async _loadModalMessages(chatId) {
    const r = await fetch(`/api/chat/${chatId}/messages`);
    const data = await r.json();
    const el = document.getElementById('modal-chat-messages');
    el.innerHTML = '';
    (data.messages || []).forEach(m => ChatUI.appendMessage(m.role, m.content, {
      messagesId: 'modal-chat-messages',
      windowId: 'modal-chat-window',
      tools: m.tools || [],
    }));
    const win = document.getElementById('modal-chat-window');
    win.scrollTop = win.scrollHeight;
  },

  _appendModalMessage(role, text) {
    return ChatUI.appendMessage(role, text, {
      messagesId: 'modal-chat-messages',
      windowId: 'modal-chat-window',
    });
  },

  async saveChatSummary() {
    if (!modalChatId) return;
    const btn = document.getElementById('modal-save-summary-btn');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      const r = await fetch(`/api/chat/${modalChatId}/save-summary`, { method: 'POST' });
      if (r.ok) {
        btn.textContent = '✓ Saved';
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
      } else {
        btn.disabled = false; btn.textContent = orig;
      }
    } catch { btn.disabled = false; btn.textContent = orig; }
  },

  async saveChatTranscript() {
    if (!modalChatId) return;
    const btn = document.getElementById('modal-save-transcript-btn');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      const r = await fetch(`/api/chat/${modalChatId}/save-transcript`, { method: 'POST' });
      if (r.ok) {
        btn.textContent = '✓ Saved';
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
      } else {
        btn.disabled = false; btn.textContent = orig;
      }
    } catch { btn.disabled = false; btn.textContent = orig; }
  },
};

// ---------------------------------------------------------------------------
// Modal event listeners
// ---------------------------------------------------------------------------

document.getElementById('add-note-modal').addEventListener('click', function(e) {
  if (e.target === this) Entity.closeAddNote();
});
document.getElementById('merge-modal').addEventListener('click', function(e) {
  if (e.target === this) Entity.closeMerge();
});
document.getElementById('link-modal').addEventListener('click', function(e) {
  if (e.target === this) Entity.closeLinkModal();
});
document.getElementById('entity-chat-modal').addEventListener('click', function(e) {
  if (e.target === this) Entity.closeChatModal();
});
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    Entity.closeAddNote();
    Entity.closeMerge();
    Entity.closeLinkModal();
    Entity.cancelSummaryEdit();
    Entity.closeChatModal();
  }
});

// ---------------------------------------------------------------------------
// Chat form: SSE streaming
// ---------------------------------------------------------------------------

document.getElementById('modal-chat-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!modalChatId) return;
  await ChatUI.submitChatMessage({
    chatId: modalChatId,
    formId: 'modal-chat-form',
    inputId: 'modal-chat-input',
    buttonId: 'modal-chat-send',
    messagesId: 'modal-chat-messages',
    windowId: 'modal-chat-window',
  });
});

// Auto-resize modal textarea
const modalInput = document.getElementById('modal-chat-input');
if (modalInput) ChatUI.bindTextareaSubmit('modal-chat-input', 'modal-chat-form');
