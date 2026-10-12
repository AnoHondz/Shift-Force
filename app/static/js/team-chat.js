(() => {
  const panel = document.querySelector('[data-chat]');
  if (!panel) return;
  const list = panel.querySelector('.chat-messages');
  const form = panel.querySelector('form');
  const input = form.elements.body;
  const status = panel.querySelector('[data-chat-status]');
  const button = form.querySelector('button');
  let cursor = Math.max(0, ...Array.from(list.querySelectorAll('[data-message-id]'), el => Number(el.dataset.messageId)));
  let fetching = false;
  let stopped = false;
  function append(message) {
    if (list.querySelector(`[data-message-id="${message.id}"]`)) return;
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    const row = document.createElement('article');
    row.className = 'chat-message';
    row.dataset.messageId = message.id;
    const heading = document.createElement('div');
    heading.className = 'chat-message-heading';
    const author = document.createElement('strong');
    author.textContent = message.author;
    const time = document.createElement('time');
    time.dateTime = message.created_at;
    time.textContent = new Date(message.created_at).toLocaleString([], {timeZone: panel.dataset.timezone, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'});
    const body = document.createElement('p');
    body.textContent = message.body;
    heading.append(author, time);
    row.append(heading, body);
    list.append(row);
    panel.querySelector('.chat-empty').hidden = true;
    while (list.querySelectorAll('article').length > 100) list.querySelector('article').remove();
    if (nearBottom) list.scrollTop = list.scrollHeight;
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
        const data = await responseData(await fetch(`${panel.dataset.endpoint}?after=${cursor}`));
        data.messages.forEach(message => { append(message); cursor = Math.max(cursor, message.id); });
        hasMore = data.messages.length === 100;
      } while (hasMore);
      status.textContent = 'Updates automatically';
    } catch (error) { status.textContent = error.message; }
    finally { fetching = false; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body || stopped) return;
    button.disabled = true;
    status.textContent = 'Sending…';
    try {
      const data = await responseData(await fetch(panel.dataset.endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
        body: JSON.stringify({body})
      }));
      append(data.message);
      input.value = '';
      list.scrollTop = list.scrollHeight;
      status.textContent = 'Sent';
      await refresh();
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = stopped; }
  });
  list.scrollTop = list.scrollHeight;
  setInterval(() => { if (!document.hidden) refresh(); }, 8000);
  window.addEventListener('focus', refresh);
})();
