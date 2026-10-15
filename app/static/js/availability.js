(() => {
  document.querySelectorAll('.availability-row').forEach(row => {
    const mode = row.querySelector('select');
    const times = row.querySelectorAll('input[type="time"]');
    function update() {
      times.forEach(input => {
        input.parentElement.hidden = mode.value !== 'hours';
        input.disabled = mode.value !== 'hours';
        input.required = mode.value === 'hours';
      });
    }
    mode.addEventListener('change', update);
    update();
  });
})();
