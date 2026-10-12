(() => {
  const panel = document.querySelector('[data-chat]');
  if (!panel) return;
  const list = panel.querySelector('.chat-messages');
  const form = panel.querySelector('form');
  const input = form.elements.body;
  const picture = form.elements.picture;
  const preview = panel.querySelector('[data-picture-preview]');
  const status = panel.querySelector('[data-chat-status]');
  const button = form.querySelector('button[type="submit"]');
  const history = panel.querySelector('[data-load-older]');
  const endpoint = `${panel.dataset.endpoint}?channel_id=${panel.dataset.channelId}`;
  let cursor = Math.max(0, ...Array.from(list.querySelectorAll('[data-message-id]'), el => Number(el.dataset.messageId)));
  let fetching = false;
  let stopped = false;
  let sending = false;
  function append(message, before = null) {
    if (list.querySelector(`[data-message-id="${message.id}"]`)) return;
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    const row = document.createElement('article');
    row.className = 'chat-message';
    row.dataset.messageId = message.id;
    const avatar = document.createElement('span');
    avatar.className = 'message-avatar';
    avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = message.author.slice(0, 1).toUpperCase();
    const content = document.createElement('div');
    content.className = 'chat-message-content';
    const heading = document.createElement('div');
    heading.className = 'chat-message-heading';
    const author = document.createElement('strong');
    author.textContent = message.author;
    const time = document.createElement('time');
    time.dateTime = message.created_at;
    time.textContent = new Date(message.created_at).toLocaleString([], {timeZone: panel.dataset.timezone, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'});
    heading.append(author, time);
    content.append(heading);
    if (message.body) {
      const body = document.createElement('p');
      body.textContent = message.body;
      content.append(body);
    }
    if (message.attachment) {
      const link = document.createElement('a');
      link.className = 'chat-picture-link';
      link.href = message.attachment.url;
      link.target = '_blank';
      link.rel = 'noopener';
      const image = document.createElement('img');
      image.className = 'chat-picture';
      image.src = message.attachment.url;
      image.alt = `${message.attachment.filename} shared by ${message.author}`;
      image.loading = 'lazy';
      image.addEventListener('load', () => { if (nearBottom && !before) list.scrollTop = list.scrollHeight; });
      link.append(image);
      content.append(link);
    }
    row.append(avatar, content);
    list.insertBefore(row, before);
    panel.querySelector('.chat-empty').hidden = true;
    if (nearBottom && !before) list.scrollTop = list.scrollHeight;
  }
  async function responseData(response) {
    if (response.status === 401 || response.status === 403) {
      stopped = true;
      button.disabled = true;
      throw new Error('Please sign in again to use team chat.');
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Chat is unavailable. Try again.');
    return data;
  }
  async function refresh() {
    if (fetching || stopped) return;
    fetching = true;
    try {
      let hasMore;
      do {
        const data = await responseData(await fetch(`${endpoint}&after=${cursor}`));
        data.messages.forEach(message => { append(message); cursor = Math.max(cursor, message.id); });
        hasMore = data.messages.length === 100;
      } while (hasMore);
      if (!sending) status.textContent = 'Updates automatically';
    } catch (error) { status.textContent = error.message; }
    finally { fetching = false; }
  }
  history.addEventListener('click', async () => {
    const first = list.querySelector('[data-message-id]');
    if (!first || stopped) return;
    history.disabled = true;
    const previousHeight = list.scrollHeight;
    try {
      const data = await responseData(await fetch(`${endpoint}&before=${first.dataset.messageId}`));
      data.messages.forEach(message => append(message, first));
      history.hidden = !data.has_older;
      list.scrollTop += list.scrollHeight - previousHeight;
    } catch (error) { status.textContent = error.message; }
    finally { history.disabled = false; }
  });
  function clearPicture() {
    picture.value = '';
    preview.hidden = true;
    preview.querySelector('img').removeAttribute('src');
  }
  picture.addEventListener('change', () => {
    const file = picture.files[0];
    if (!file) { clearPicture(); return; }
    if (file.size > 5 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
      clearPicture();
      status.textContent = 'Choose a JPG, PNG, WebP, or GIF smaller than 5 MB.';
      return;
    }
    const reader = new FileReader();
    reader.addEventListener('load', () => {
      if (picture.files[0] !== file) return;
      preview.querySelector('img').src = reader.result;
      preview.querySelector('[data-picture-name]').textContent = file.name;
      preview.hidden = false;
      status.textContent = 'Picture ready to share';
    });
    reader.readAsDataURL(file);
  });
  panel.querySelector('[data-remove-picture]').addEventListener('click', clearPicture);
  panel.querySelector('[data-emoji]').addEventListener('click', () => {
    if (sending) return;
    input.setRangeText('\u{1F642}', input.selectionStart, input.selectionEnd, 'end');
    input.focus();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!sending) form.requestSubmit();
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if ((!input.value.trim() && !picture.files.length) || stopped || sending) return;
    const data = new FormData(form);
    data.set('body', input.value.trim());
    data.set('channel_id', panel.dataset.channelId);
    if (!picture.files.length) data.delete('picture');
    sending = true;
    button.disabled = true;
    input.readOnly = true;
    picture.disabled = true;
    status.textContent = 'Sending...';
    try {
      const result = await responseData(await fetch(panel.dataset.endpoint, {
        method: 'POST', headers: {'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
        body: data
      }));
      append(result.message);
      input.value = '';
      clearPicture();
      list.scrollTop = list.scrollHeight;
      status.textContent = 'Sent';
      await refresh();
    } catch (error) { status.textContent = error.message; }
    finally { sending = false; button.disabled = stopped; input.readOnly = false; picture.disabled = false; }
  });
  list.scrollTop = list.scrollHeight;
  setInterval(() => { if (!document.hidden) refresh(); }, 8000);
  window.addEventListener('focus', refresh);
})();
