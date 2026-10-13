(() => {
  const form = document.getElementById('shift-form');
  const builder = document.getElementById('schedule-builder');
  if (form) {
    const start = document.getElementById('shift-start');
    const end = document.getElementById('shift-end');
    const preview = document.getElementById('shift-preview');
    const formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: form.dataset.timezone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    });
    function localNumber(timestamp) {
      const parts = Object.fromEntries(formatter.formatToParts(timestamp).map(p => [p.type, p.value]));
      return Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
    }
    function instant(value) {
      if (!value) return null;
      const nominal = Date.parse(value + 'Z');
      if (!Number.isFinite(nominal)) return null;
      const offsets = new Set([-36, 0, 36].map(h => {
        const stamp = nominal + h * 3600000;
        return localNumber(stamp) - stamp;
      }));
      const matches = [...offsets].map(offset => nominal - offset)
        .filter(stamp => localNumber(stamp) === nominal);
      return matches.length === 1 ? matches[0] : null;
    }
    function update() {
      end.setCustomValidity('');
      const first = instant(start.value), last = instant(end.value);
      if (!start.value || !end.value) { preview.textContent = 'Choose start and end times.'; return; }
      if (first === null || last === null) {
        preview.textContent = 'Choose valid times outside the clock change.';
        end.setCustomValidity(preview.textContent); return;
      }
      const hours = (last - first) / 3600000;
      if (hours <= 0 || hours > 24) {
        preview.textContent = 'End must follow start, with a shift of at most 24 hours.';
        end.setCustomValidity(preview.textContent); return;
      }
      const endDate = new Intl.DateTimeFormat('en-US', {
        timeZone: form.dataset.timezone, weekday: 'short', month: 'short', day: 'numeric',
      }).format(last);
      preview.textContent = `${Number(hours.toFixed(2))} scheduled ${hours === 1 ? 'hour' : 'hours'}` +
        (end.value.slice(0, 10) > start.value.slice(0, 10) ? ` · Ends ${endDate}` : '');
      const count = new Set([new Date(start.value.slice(0, 10) + 'T12:00:00Z').getUTCDay(),
        ...[...form.querySelectorAll('[name="repeat_days"]:checked')]
          .map(input => (+input.value + 1) % 7)]).size;
      document.getElementById('save-schedule').textContent = count > 1 ? `Save ${count} shifts` : 'Save shift';
    }
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    form.addEventListener('submit', () => {
      const button = document.getElementById('save-schedule'); button.disabled = true; button.textContent = 'Saving…';
    });
    document.querySelectorAll('[data-schedule-day]').forEach(button => {
      button.addEventListener('click', event => {
        if (form.querySelector('[name="shift_id"]')) return;
        event.preventDefault();
        builder.open = true;
        start.value = button.dataset.scheduleDay + 'T09:00'; end.value = button.dataset.scheduleDay + 'T17:00';
        form.querySelectorAll('[name="repeat_days"]').forEach(input => { input.checked = false; });
        update(); builder.scrollIntoView({behavior: 'smooth', block: 'start'});
        document.getElementById('scheduled-employee').focus({preventScroll: true});
      });
    });
    if (window.location.hash === '#schedule-builder') builder.open = true;
    update();
    const error = document.getElementById('schedule-error');
    if (error) error.focus();
  }
  document.querySelectorAll('.schedule-cancel').forEach(form => {
    form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); });
  });
})();
