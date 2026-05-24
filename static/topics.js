/* global TOPIC_ID */
let modalChatId = null;

const Topics = {

  // ---------------------------------------------------------------------------
  // Inline editing
  // ---------------------------------------------------------------------------

  editName() {
    const display = document.getElementById('topic-name-display');
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
        await Topics.save({ name: val });
        document.title = val + ' — Topics';
      }
      const h1 = document.createElement('h1');
      h1.className = 'topic-name';
      h1.id = 'topic-name-display';
      h1.textContent = val || current;
      input.replaceWith(h1);
    };
    input.addEventListener('blur', save);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); input.blur(); } });
  },

  editDesc() {
    const display = document.getElementById('topic-desc-display');
    const current = display.textContent.trim();
    const ta = document.createElement('textarea');
    ta.value = current === 'No description.' ? '' : current;
    ta.className = 'topic-edit-input topic-edit-input--desc';
    ta.rows = 3;
    display.replaceWith(ta);
    ta.focus();
    const save = async () => {
      const val = ta.value.trim();
      await Topics.save({ description: val });
      const p = document.createElement('p');
      p.className = 'topic-desc';
      p.id = 'topic-desc-display';
      p.textContent = val || 'No description.';
      ta.replaceWith(p);
    };
    ta.addEventListener('blur', save);
  },

  async save(fields) {
    await fetch(`/api/topics/${TOPIC_ID}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
  },

  // ---------------------------------------------------------------------------
  // Color picker
  // ---------------------------------------------------------------------------

  async setColor(hex) {
    document.getElementById('topic-header').style.setProperty('--topic-color', hex);
    document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('active'));
    const swatch = document.querySelector(`.color-swatch[data-color="${hex}"]`);
    if (swatch) swatch.classList.add('active');
    await Topics.save({ color: hex });
  },

  // ---------------------------------------------------------------------------
  // AI actions
  // ---------------------------------------------------------------------------

  async refreshDescription() {
    const btn = document.querySelector('[onclick="Topics.refreshDescription()"]');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Refreshing…';
    try {
      const r = await fetch(`/api/topics/${TOPIC_ID}/refresh-description`, { method: 'POST' });
      const data = await r.json();
      const el = document.getElementById('topic-desc-display');
      if (el) el.textContent = data.description;
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  },

  editSummary() {
    document.getElementById('topic-summary-wrap').classList.add('hidden');
    document.getElementById('summary-edit-btn').classList.add('hidden');
    document.getElementById('summary-edit-area').classList.remove('hidden');
    document.getElementById('summary-edit-textarea').focus();
  },

  cancelSummaryEdit() {
    document.getElementById('summary-edit-area').classList.add('hidden');
    document.getElementById('topic-summary-wrap').classList.remove('hidden');
    document.getElementById('summary-edit-btn').classList.remove('hidden');
  },

  async saveSummaryEdit() {
    const val = document.getElementById('summary-edit-textarea').value;
    await Topics.save({ summary: val });
    let wrap = document.getElementById('topic-summary');
    const emptyEl = document.getElementById('topic-summary-empty');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'topic-summary';
      wrap.id = 'topic-summary';
      if (emptyEl) emptyEl.replaceWith(wrap);
      else document.getElementById('topic-summary-wrap').appendChild(wrap);
    }
    if (val.trim()) {
      wrap.innerHTML = window.VJSanitize(marked.parse(val));
    } else {
      wrap.innerHTML = '';
    }
    Topics.cancelSummaryEdit();
  },

  async refreshSummary() {
    const btn = document.getElementById('summary-refresh-btn');
    btn.disabled = true;
    btn.textContent = 'Generating…';
    try {
      const r = await fetch(`/api/topics/${TOPIC_ID}/refresh-summary`, { method: 'POST' });
      const data = await r.json();
      let wrap = document.getElementById('topic-summary');
      const emptyEl = document.getElementById('topic-summary-empty');
      if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'topic-summary';
        wrap.id = 'topic-summary';
        if (emptyEl) emptyEl.replaceWith(wrap);
        else document.getElementById('topic-summary-wrap').appendChild(wrap);
      }
      wrap.innerHTML = window.VJSanitize(marked.parse(data.summary));
      btn.textContent = 'Refresh summary';
    } catch (e) {
      btn.textContent = 'Error — try again';
    } finally {
      btn.disabled = false;
    }
  },

  async compact() {
    if (!confirm('Compact all entries into a single AI-generated summary?\n\nAll current entries will be archived (still viewable but no longer loaded into AI context).')) return;
    const r = await fetch(`/api/topics/${TOPIC_ID}/compact`, { method: 'POST' });
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
      const r = await fetch(`/api/topics/${TOPIC_ID}/merge`, {
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

  async deleteTopic() {
    if (!confirm('Delete this topic and all its entries and chats? This cannot be undone.')) return;
    const r = await fetch(`/api/topics/${TOPIC_ID}/delete`, { method: 'POST' });
    if (r.ok) {
      window.location.href = '/topics';
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
      const r = await fetch(`/api/topics/${TOPIC_ID}/entries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, date }),
      });
      if (r.ok) {
        Topics.closeAddNote();
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
    await fetch(`/api/tags/${TOPIC_ID}/links`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_tag_id: toTagId, note: note || undefined }),
    });
    location.reload();
  },

  async removeLink(toTagId) {
    if (!confirm('Remove this connection?')) return;
    await fetch(`/api/tags/${TOPIC_ID}/unlink`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_tag_id: toTagId }),
    });
    location.reload();
  },

  // ---------------------------------------------------------------------------
  // Chat modal (unified chat, pre-scoped to this topic)
  // ---------------------------------------------------------------------------

  async openChatModal(chatId = null) {
    const modal = document.getElementById('topic-chat-modal');
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    if (chatId) {
      modalChatId = chatId;
      await Topics._loadModalMessages(chatId);
    } else {
      // Create a new chat scoped to this topic
      const r = await fetch('/api/chats', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope_tag_ids: [TOPIC_ID] }),
      });
      const data = await r.json();
      modalChatId = data.chat_id;
    }
    document.getElementById('modal-chat-input').focus();
  },

  closeChatModal() {
    document.getElementById('topic-chat-modal').classList.add('hidden');
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
        const d = await r.json();
        alert(d.error || 'Save failed');
        btn.disabled = false;
        btn.textContent = orig;
      }
    } catch (e) {
      alert('Error: ' + e.message);
      btn.disabled = false;
      btn.textContent = orig;
    }
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
        const d = await r.json();
        alert(d.error || 'Save failed');
        btn.disabled = false;
        btn.textContent = orig;
      }
    } catch (e) {
      alert('Error: ' + e.message);
      btn.disabled = false;
      btn.textContent = orig;
    }
  },

  // ---------------------------------------------------------------------------
  // Hierarchy: set/clear parent
  // ---------------------------------------------------------------------------

  openSetParent() {
    document.getElementById('set-parent-modal').classList.remove('hidden');
  },

  closeSetParent() {
    document.getElementById('set-parent-modal').classList.add('hidden');
  },

  async confirmSetParent() {
    const sel = document.getElementById('parent-select');
    const parentId = sel.value ? parseInt(sel.value) : null;
    try {
      const r = await fetch(`/api/topics/${TOPIC_ID}/set-parent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_tag_id: parentId }),
      });
      const data = await r.json();
      if (data.ok) location.reload();
      else alert(data.error || 'Failed to set parent');
    } catch (e) {
      alert('Error: ' + e.message);
    }
  },

  async clearParent() {
    if (!confirm('Remove parent topic?')) return;
    try {
      const r = await fetch(`/api/topics/${TOPIC_ID}/set-parent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_tag_id: null }),
      });
      if ((await r.json()).ok) location.reload();
    } catch (e) {
      alert('Error: ' + e.message);
    }
  },

  // ---------------------------------------------------------------------------
  // Hierarchy: create subtopic
  // ---------------------------------------------------------------------------

  openCreateSubtopic() {
    document.getElementById('create-subtopic-modal').classList.remove('hidden');
    document.getElementById('subtopic-name').focus();
  },

  closeCreateSubtopic() {
    document.getElementById('create-subtopic-modal').classList.add('hidden');
  },

  async submitCreateSubtopic() {
    const name = document.getElementById('subtopic-name').value.trim();
    if (!name) return;
    const desc = document.getElementById('subtopic-desc').value.trim();
    const btn = document.getElementById('create-subtopic-submit');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Creating…';
    try {
      const r = await fetch('/api/topics/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description: desc || null, parent_tag_id: TOPIC_ID }),
      });
      const data = await r.json();
      if (data.ok) window.location.href = `/topics/${data.topic_id}`;
      else alert(data.error || 'Create failed');
    } catch (e) {
      alert('Error: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  },
};

// ---------------------------------------------------------------------------
// Modal event listeners
// ---------------------------------------------------------------------------

document.getElementById('add-note-modal').addEventListener('click', function(e) {
  if (e.target === this) Topics.closeAddNote();
});
document.getElementById('merge-modal').addEventListener('click', function(e) {
  if (e.target === this) Topics.closeMerge();
});
document.getElementById('topic-chat-modal').addEventListener('click', function(e) {
  if (e.target === this) Topics.closeChatModal();
});
document.getElementById('set-parent-modal').addEventListener('click', function(e) {
  if (e.target === this) Topics.closeSetParent();
});
document.getElementById('create-subtopic-modal').addEventListener('click', function(e) {
  if (e.target === this) Topics.closeCreateSubtopic();
});
document.getElementById('link-modal').addEventListener('click', function(e) {
  if (e.target === this) Topics.closeLinkModal();
});
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    Topics.closeAddNote();
    Topics.closeMerge();
    Topics.cancelSummaryEdit();
    Topics.closeChatModal();
    Topics.closeSetParent();
    Topics.closeCreateSubtopic();
    Topics.closeLinkModal();
  }
});

// ---------------------------------------------------------------------------
// Modal chat: SSE streaming form submit
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
