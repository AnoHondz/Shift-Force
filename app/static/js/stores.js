(() => {
  const button = document.getElementById('generate-store-code');
  const code = document.getElementById('store-slug');
  const status = document.getElementById('store-code-status');
  if (!button || !window.crypto?.getRandomValues) return;

  button.hidden = false;
  button.addEventListener('click', () => {
    let value;
    do {
      value = window.crypto.getRandomValues(new Uint32Array(1))[0];
    } while (value >= 4294000000 || String(value % 1000000).padStart(6, '0') === code.value);
    code.value = String(value % 1000000).padStart(6, '0');
    status.textContent = 'Code generated. You can edit it before creating your store.';
  });
  code.addEventListener('input', () => { status.textContent = ''; });
})();
