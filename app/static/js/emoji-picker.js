(() => {
  const popup = document.querySelector('[data-emoji-catalog]');
  if (!popup) return;
  const trigger = document.querySelector('[data-emoji]');
  const input = document.querySelector('#team-message');
  const search = popup.querySelector('#emoji-search');
  const tone = popup.querySelector('#emoji-tone');
  const grid = popup.querySelector('.emoji-grid');
  const categories = popup.querySelector('.emoji-categories');
  const heading = popup.querySelector('[data-emoji-heading]');
  const status = popup.querySelector('[data-emoji-status]');
  const groups = [
    ['Smileys & Emotion', 'Smileys', '😀'], ['People & Body', 'People', '👋'],
    ['Animals & Nature', 'Nature', '🐻'], ['Food & Drink', 'Food', '🍎'],
    ['Travel & Places', 'Travel', '🚗'], ['Activities', 'Activities', '⚽'],
    ['Objects', 'Objects', '💡'], ['Symbols', 'Symbols', '❤️'], ['Flags', 'Flags', '🏁'],
    ['new', 'New on iPhone', '✨']
  ];
  let catalog;
  let loading;
  let active = groups[0][0];
  let selection = [0, 0];
  function rememberSelection() { selection = [input.selectionStart, input.selectionEnd]; }
  input.addEventListener('select', rememberSelection);
  input.addEventListener('keyup', rememberSelection);
  input.addEventListener('click', rememberSelection);
  input.addEventListener('input', rememberSelection);
  trigger.addEventListener('pointerdown', rememberSelection);
  function close(returnFocus = false) {
    popup.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (returnFocus) trigger.focus();
  }
  function render() {
    if (!catalog) return;
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = catalog.emoji.filter(item => {
      const matchesGroup = words.length || (active === 'new' ? item.version === '17.0' : item.group === active);
      const matchesTone = tone.value === 'all' || item.tone === 0 || item.tone === Number(tone.value);
      return matchesGroup && matchesTone && words.every(word => `${item.name} ${item.emoji}`.toLowerCase().includes(word));
    });
    heading.textContent = words.length ? 'Search results' : groups.find(group => group[0] === active)[1];
    categories.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(!words.length && button.dataset.group === active)));
    const fragment = document.createDocumentFragment();
    matches.forEach((item, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'emoji-choice';
      button.textContent = item.emoji;
      button.title = item.name;
      button.setAttribute('aria-label', item.name);
      button.tabIndex = index === 0 ? 0 : -1;
      button.addEventListener('click', () => {
        if (input.readOnly || input.disabled) return;
        const [start, end] = selection.map(position => Math.min(position, input.value.length));
        if (input.value.length - (end - start) + item.emoji.length > input.maxLength) {
          status.textContent = 'Your message is full. Remove some text to add an emoji.';
          return;
        }
        input.setRangeText(item.emoji, start, end, 'end');
        rememberSelection();
        input.dispatchEvent(new Event('input', {bubbles: true}));
        close();
        input.focus();
      });
      fragment.append(button);
    });
    grid.replaceChildren(fragment);
    grid.scrollTop = 0;
    status.textContent = matches.length ? `${matches.length} emoji · Choose one to add it` : 'No emoji found. Try another search or skin tone.';
  }
  groups.forEach(([group, label, icon]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = icon;
    button.dataset.group = group;
    button.title = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-pressed', String(group === active));
    button.addEventListener('click', () => { active = group; search.value = ''; render(); });
    categories.append(button);
  });
  trigger.addEventListener('click', async () => {
    if (input.readOnly || input.disabled) return;
    if (!popup.hidden) { close(); return; }
    popup.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    search.focus({preventScroll: true});
    if (catalog) { render(); return; }
    status.textContent = 'Loading emoji…';
    try {
      if (!loading) loading = fetch(popup.dataset.emojiCatalog).then(response => {
        if (!response.ok) throw new Error('Emoji unavailable');
        return response.json();
      });
      catalog = await loading;
      render();
    } catch {
      loading = null;
      status.textContent = 'Could not load emoji. Close and reopen to try again.';
    }
  });
  search.addEventListener('input', render);
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === 'ArrowDown') {
      event.preventDefault();
      grid.querySelector('button')?.focus();
    }
  });
  tone.addEventListener('change', render);
  popup.querySelector('[data-emoji-close]').addEventListener('click', () => close(true));
  document.addEventListener('pointerdown', event => {
    if (!popup.hidden && !popup.contains(event.target) && !trigger.contains(event.target)) close();
  });
  document.addEventListener('focusin', event => {
    if (!popup.hidden && !popup.contains(event.target) && event.target !== trigger) close();
  });
  document.addEventListener('keydown', event => {
    if (!popup.hidden && event.key === 'Escape') { event.preventDefault(); close(true); }
  });
  grid.addEventListener('keydown', event => {
    const buttons = Array.from(grid.querySelectorAll('button'));
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    const columns = getComputedStyle(grid).gridTemplateColumns.split(' ').length;
    const offsets = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns};
    let next;
    if (event.key in offsets) next = Math.max(0, Math.min(buttons.length - 1, index + offsets[event.key]));
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = buttons.length - 1;
    else return;
    event.preventDefault();
    buttons[index].tabIndex = -1;
    buttons[next].tabIndex = 0;
    buttons[next].focus();
  });
})();
