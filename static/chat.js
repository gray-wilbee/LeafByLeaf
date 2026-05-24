// Shared chat UI renderer and the main /chat page controller.
// CHAT_ID is defined inline by templates when a full-page chat is active.

const TOOL_LABELS = {
  search_tasks: 'Searching tasks',
  get_task: 'Reading task',
  create_task: 'Creating task',
  update_task: 'Updating task',
  delete_task: 'Deleting task',
  batch_update_tasks: 'Updating tasks',
  batch_delete_tasks: 'Deleting tasks',
  search_tags: 'Searching tags',
  list_tags: 'Listing tags',
  get_tag: 'Reading tag',
  create_tag: 'Creating tag',
  update_tag: 'Updating tag',
  delete_tag: 'Deleting tag',
  merge_tags: 'Merging tags',
  get_tag_notes: 'Reading notes',
  add_tag_note: 'Adding note',
  tag_object: 'Tagging',
  untag_object: 'Removing tag',
  add_tag_link: 'Linking tags',
  remove_tag_link: 'Unlinking tags',
  get_tag_links: 'Reading links',
  search_entries: 'Searching entries',
  get_entry: 'Reading entry',
  list_entries: 'Listing entries',
  get_setting: 'Reading setting',
  set_setting: 'Updating setting',
  list_settings: 'Reading settings',
};

function toolLabel(name) {
  return TOOL_LABELS[name] || String(name || 'tool').replace(/_/g, ' ');
}

function scrollChatWindow(windowId) {
  const win = document.getElementById(windowId);
  if (win) win.scrollTop = win.scrollHeight;
}

function renderMarkdown(el, text) {
  el.innerHTML = text ? window.VJSanitize(marked.parse(text)) : '';
}

function createToolCard(tool) {
  const card = document.createElement('div');
  card.className = `tool-card tool-card--${tool.status || 'done'}`;
  card.dataset.toolName = tool.name || 'tool';

  const label = document.createElement('span');
  label.className = 'tool-card-label';
  label.textContent = toolLabel(tool.name);
  card.appendChild(label);

  if ((tool.status || 'done') === 'running') {
    const dots = document.createElement('span');
    dots.className = 'tool-card-dots';
    dots.textContent = '...';
    card.appendChild(dots);
  } else {
    const summary = document.createElement('span');
    summary.className = 'tool-card-summary';
    summary.textContent = ` - ${tool.summary || 'Done'}`;
    card.appendChild(summary);
  }

  return card;
}

function ensureToolTray(bubble) {
  let tray = bubble.querySelector(':scope > .tool-tray');
  if (tray) return tray;

  tray = document.createElement('div');
  tray.className = 'tool-tray';
  tray.dataset.expanded = 'false';
  tray.dataset.manual = 'false';

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'tool-summary-toggle';
  toggle.addEventListener('click', () => {
    const expanded = tray.dataset.expanded !== 'true';
    tray.dataset.expanded = expanded ? 'true' : 'false';
    tray.dataset.manual = 'true';
    toggle.setAttribute('aria-expanded', String(expanded));
  });

  const details = document.createElement('div');
  details.className = 'tool-tray-details';

  tray.append(toggle, details);
  bubble.appendChild(tray);
  return tray;
}

function updateToolTrayLabel(tray) {
  const cards = Array.from(tray.querySelectorAll('.tool-card'));
  const running = cards.filter(card => card.classList.contains('tool-card--running')).length;
  const toggle = tray.querySelector('.tool-summary-toggle');
  if (!toggle) return;

  if (running > 0 && tray.dataset.manual !== 'true') {
    tray.dataset.expanded = 'true';
  } else if (running === 0 && tray.dataset.manual !== 'true') {
    tray.dataset.expanded = 'false';
  }

  const count = cards.length;
  const noun = count === 1 ? 'tool' : 'tools';
  toggle.textContent = running > 0
    ? `Using ${running} ${running === 1 ? 'tool' : 'tools'}`
    : `Used ${count} ${noun}`;
  toggle.setAttribute('aria-expanded', String(tray.dataset.expanded === 'true'));
}

function addToolToBubble(bubble, tool) {
  const tray = ensureToolTray(bubble);
  const details = tray.querySelector('.tool-tray-details');
  details.appendChild(createToolCard(tool));
  updateToolTrayLabel(tray);
}

function finishToolInBubble(bubble, toolName, summary) {
  const tray = ensureToolTray(bubble);
  const cards = Array.from(tray.querySelectorAll(`.tool-card[data-tool-name="${toolName}"]`));
  const card = [...cards].reverse().find(c => c.classList.contains('tool-card--running')) || cards[cards.length - 1];
  if (!card) return;

  card.className = 'tool-card tool-card--done';
  const dots = card.querySelector('.tool-card-dots');
  if (dots) dots.remove();

  let sum = card.querySelector('.tool-card-summary');
  if (!sum) {
    sum = document.createElement('span');
    sum.className = 'tool-card-summary';
    card.appendChild(sum);
  }
  sum.textContent = ` - ${summary || 'Done'}`;
  updateToolTrayLabel(tray);
}

function appendMessage(role, text, options = {}) {
  const messagesId = options.messagesId || 'chat-messages';
  const windowId = options.windowId || 'chat-window';
  const el = document.getElementById(messagesId);
  if (!el) return null;

  const wrap = document.createElement('div');
  wrap.className = `chat-msg chat-msg--${role}`;

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  if (options.streaming) bubble.classList.add('streaming');
  if (options.error) bubble.classList.add('chat-bubble--error');

  if (role === 'assistant') {
    const content = document.createElement('div');
    content.className = 'chat-bubble-content';
    bubble.appendChild(content);
    renderMarkdown(content, text || '');
    (options.tools || []).forEach(tool => addToolToBubble(bubble, tool));
  } else {
    bubble.textContent = text || '';
  }

  wrap.appendChild(bubble);
  el.appendChild(wrap);
  scrollChatWindow(windowId);
  return bubble;
}

function setAssistantText(bubble, text) {
  let content = bubble.querySelector(':scope > .chat-bubble-content');
  if (!content) {
    content = document.createElement('div');
    content.className = 'chat-bubble-content';
    bubble.appendChild(content);
  }
  renderMarkdown(content, text || '');
}

async function streamAssistantResponse(chatId, text, options = {}) {
  const messagesId = options.messagesId || 'chat-messages';
  const windowId = options.windowId || 'chat-window';
  let bubble = appendMessage('assistant', '', { messagesId, windowId, streaming: true });
  let accumulated = '';
  let needNewBubble = false;

  try {
    const res = await fetch(`/api/chat/${chatId}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || res.statusText);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let currentEvent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, idx);
        buf = buf.slice(idx + 1);

        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
          continue;
        }

        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw || raw === '[DONE]') {
          currentEvent = '';
          continue;
        }

        try {
          const ev = JSON.parse(raw);
          if (currentEvent === 'tool_call') {
            if (!bubble) bubble = appendMessage('assistant', '', { messagesId, windowId, streaming: true });
            addToolToBubble(bubble, {
              name: ev.name,
              input: ev.input || {},
              status: 'running',
            });
            scrollChatWindow(windowId);
            currentEvent = '';
            continue;
          }

          if (currentEvent === 'tool_result') {
            finishToolInBubble(bubble, ev.name, ev.summary || 'Done');
            scrollChatWindow(windowId);
            needNewBubble = true;
            currentEvent = '';
            continue;
          }

          if (currentEvent === 'chat_renamed') {
            if (ev.title) Chat.setVisibleTitle(ev.title);
            currentEvent = '';
            continue;
          }

          if (ev.type === 'content_block_delta' && ev.delta?.type === 'text_delta') {
            if (needNewBubble) {
              if (bubble) bubble.classList.remove('streaming');
              bubble = appendMessage('assistant', '', { messagesId, windowId, streaming: true });
              accumulated = '';
              needNewBubble = false;
            }
            accumulated += ev.delta.text;
            setAssistantText(bubble, accumulated);
            scrollChatWindow(windowId);
          }
        } catch (_) {
          // Ignore malformed stream fragments.
        }
        currentEvent = '';
      }
    }
  } catch (err) {
    bubble.classList.remove('streaming');
    bubble.classList.add('chat-bubble--error');
    setAssistantText(bubble, `Error: ${err.message}`);
    return bubble;
  }

  bubble.classList.remove('streaming');
  return bubble;
}

async function submitChatMessage(config) {
  const input = document.getElementById(config.inputId);
  const btn = document.getElementById(config.buttonId);
  if (!input || !btn || !config.chatId) return;

  const text = input.value.trim();
  if (!text || btn.disabled) return;

  input.value = '';
  input.style.height = 'auto';
  input.disabled = true;
  btn.disabled = true;

  appendMessage('user', text, {
    messagesId: config.messagesId,
    windowId: config.windowId,
  });

  try {
    await streamAssistantResponse(config.chatId, text, config);
  } catch (err) {
    appendMessage('assistant', `Error: ${err.message}`, {
      messagesId: config.messagesId,
      windowId: config.windowId,
      error: true,
    });
  } finally {
    input.disabled = false;
    btn.disabled = false;
    input.focus();
  }
}

function bindTextareaSubmit(inputId, formId) {
  const input = document.getElementById(inputId);
  const form = document.getElementById(formId);
  if (!input || !form) return;

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event('submit'));
    }
  });
}

window.ChatUI = {
  appendMessage,
  bindTextareaSubmit,
  submitChatMessage,
};

const Chat = {
  async newChat() {
    const r = await fetch('/api/chats', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New Chat' }),
    });
    const data = await r.json();
    window.location.href = `/chat/${data.chat_id}`;
  },

  async loadMessages(chatId) {
    const r = await fetch(`/api/chat/${chatId}/messages`);
    const data = await r.json();
    const el = document.getElementById('chat-messages');
    if (!el) return;
    el.innerHTML = '';
    (data.messages || []).forEach(m => ChatUI.appendMessage(m.role, m.content, { tools: m.tools || [] }));
    scrollChatWindow('chat-window');
  },

  async saveSummary() {
    const btn = document.getElementById('save-summary-btn');
    await saveAction(`/api/chat/${CHAT_ID}/save-summary`, btn, 'Save Summary');
  },

  async saveTranscript() {
    const btn = document.getElementById('save-transcript-btn');
    await saveAction(`/api/chat/${CHAT_ID}/save-transcript`, btn, 'Save Transcript');
  },

  async renameChat() {
    const titleEl = document.getElementById('chat-panel-title');
    if (!titleEl) return;
    const current = titleEl.textContent.trim();
    const val = prompt('Rename chat:', current);
    if (!val || val.trim() === current) return;
    const r = await fetch(`/api/chat/${CHAT_ID}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: val.trim() }),
    });
    if (r.ok) {
      titleEl.textContent = val.trim();
      const sidebarTitle = document.querySelector('.chat-sidebar-item--active .chat-sidebar-title');
      if (sidebarTitle) sidebarTitle.textContent = val.trim();
    }
  },

  setVisibleTitle(title) {
    const titleEl = document.getElementById('chat-panel-title');
    if (titleEl) titleEl.textContent = title;
    const sidebarTitle = document.querySelector('.chat-sidebar-item--active .chat-sidebar-title');
    if (sidebarTitle) sidebarTitle.textContent = title;
  },

  async openInstructions() {
    const modal = document.getElementById('agent-instructions-modal');
    const editor = document.getElementById('agent-instructions-editor');
    if (!modal || !editor) return;
    modal.classList.remove('hidden');
    editor.focus();
    try {
      const r = await fetch('/api/chat/instructions');
      const data = await r.json();
      editor.value = data.instructions || '';
    } catch (_) {}
  },

  closeInstructions() {
    document.getElementById('agent-instructions-modal')?.classList.add('hidden');
  },

  wrapInstruction(before, after) {
    const editor = document.getElementById('agent-instructions-editor');
    if (!editor) return;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const text = editor.value.slice(start, end);
    editor.setRangeText(before + text + after, start, end, 'select');
    editor.focus();
  },

  prefixInstruction(prefix) {
    const editor = document.getElementById('agent-instructions-editor');
    if (!editor) return;
    const start = editor.selectionStart;
    const lineStart = editor.value.lastIndexOf('\n', start - 1) + 1;
    editor.setRangeText(prefix, lineStart, lineStart, 'end');
    editor.focus();
  },

  async saveInstructions() {
    const editor = document.getElementById('agent-instructions-editor');
    const btn = document.getElementById('agent-instructions-save');
    if (!editor || !btn) return;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Saving...';
    try {
      const r = await fetch('/api/chat/instructions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instructions: editor.value }),
      });
      if (!r.ok) throw new Error('Save failed');
      Chat.closeInstructions();
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  },

  resumeChat() {
    const notice = document.getElementById('chat-saved-notice');
    if (notice) notice.remove();
    const form = document.getElementById('chat-form');
    if (form) form.classList.remove('chat-form--locked');
    const input = document.getElementById('chat-input');
    if (input) {
      input.disabled = false;
      input.focus();
    }
    const send = document.getElementById('chat-send');
    if (send) send.disabled = false;
  },
};

window.Chat = Chat;

async function saveAction(url, btn, label) {
  if (!btn) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const r = await fetch(url, { method: 'POST' });
    if (r.ok) {
      btn.textContent = 'Saved';
      setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 2000);
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
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.js-agent-instructions').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      Chat.openInstructions();
    });
  });
  document.querySelectorAll('.js-agent-instructions-close').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      Chat.closeInstructions();
    });
  });
  document.querySelectorAll('[data-editor-wrap]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      const [before, after] = btn.dataset.editorWrap.split('|');
      Chat.wrapInstruction(before, after);
    });
  });
  document.querySelectorAll('[data-editor-prefix]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      Chat.prefixInstruction(btn.dataset.editorPrefix);
    });
  });
  document.getElementById('agent-instructions-save')?.addEventListener('click', e => {
    e.preventDefault();
    Chat.saveInstructions();
  });

  if (typeof CHAT_ID !== 'undefined') {
    Chat.loadMessages(CHAT_ID);
  }

  const form = document.getElementById('chat-form');
  if (!form) return;

  ChatUI.bindTextareaSubmit('chat-input', 'chat-form');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (typeof CHAT_ID === 'undefined') return;
    await ChatUI.submitChatMessage({
      chatId: CHAT_ID,
      formId: 'chat-form',
      inputId: 'chat-input',
      buttonId: 'chat-send',
      messagesId: 'chat-messages',
      windowId: 'chat-window',
    });
  });
});

document.addEventListener('click', e => {
  const modal = document.getElementById('agent-instructions-modal');
  if (modal && e.target === modal) Chat.closeInstructions();
});
