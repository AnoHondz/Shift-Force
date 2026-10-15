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
      const dates = form.querySelector('[name="shift_date"]')
        ? [form.querySelector('[name="shift_date"]').value].filter(Boolean)
        : [...form.querySelectorAll('[name="shift_days"]:checked')].map(input => input.dataset.date);
      const editingDraft = form.querySelector('[name="shift_id"]') && form.querySelector('button[name="publish"][value="0"]');
      document.getElementById('save-schedule').textContent = editingDraft ? 'Publish shift' : (dates.length > 1 ? `Save ${dates.length} shifts` : 'Save shift');
      if (!dates.length) {
        preview.textContent = 'Choose at least one day.';
        end.setCustomValidity(preview.textContent); return;
      }
      if (!start.value || !end.value) { preview.textContent = 'Choose start and end times.'; return; }
      const keepOvernight = form.dataset.originalOvernight === 'true' &&
        start.value === form.dataset.originalStart && end.value === form.dataset.originalEnd;
      if (start.value === end.value && !keepOvernight) {
        preview.textContent = 'Choose different start and end times.';
        end.setCustomValidity(preview.textContent); return;
      }
      const overnight = end.value < start.value || keepOvernight;
      const intervals = dates.map(day => {
        const endDate = new Date(day + 'T12:00:00Z');
        if (overnight) endDate.setUTCDate(endDate.getUTCDate() + 1);
        return [instant(day + 'T' + start.value), instant(endDate.toISOString().slice(0, 10) + 'T' + end.value)];
      });
      if (intervals.some(([first, last]) => first === null || last === null)) {
        preview.textContent = 'Choose valid times outside the clock change.';
        end.setCustomValidity(preview.textContent); return;
      }
      const durations = intervals.map(([first, last]) => (last - first) / 3600000);
      if (durations.some(hours => hours <= 0 || hours > 24)) {
        preview.textContent = 'End must follow start, with a shift of at most 24 hours.';
        end.setCustomValidity(preview.textContent); return;
      }
      const endDate = new Intl.DateTimeFormat('en-US', {
        timeZone: form.dataset.timezone, weekday: 'short', month: 'short', day: 'numeric',
      }).format(intervals[0][1]);
      const low = Number(Math.min(...durations).toFixed(2)), high = Number(Math.max(...durations).toFixed(2));
      preview.textContent = `${low === high ? low : `${low}–${high}`} scheduled ${high === 1 ? 'hour' : 'hours'}` +
        (low !== high ? ' per shift' : '') +
        (overnight ? (dates.length === 1 ? ` · Ends ${endDate}` : ' · Ends next morning') : '');
    }
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    form.addEventListener('submit', event => {
      const selectedMode = document.createElement('input');
      selectedMode.type = 'hidden'; selectedMode.name = 'publish';
      selectedMode.value = event.submitter?.value === '0' ? '0' : '1';
      form.append(selectedMode);
      form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
      if (event.submitter) event.submitter.textContent = 'Saving…';
    });
    document.querySelectorAll('[data-schedule-day]').forEach(button => {
      button.addEventListener('click', event => {
        if (form.querySelector('[name="shift_id"]')) return;
        event.preventDefault();
        form.querySelectorAll('[name="shift_days"]').forEach(input => {
          input.checked = input.dataset.date === button.dataset.scheduleDay;
        });
        update(); builder.scrollIntoView({behavior: 'smooth', block: 'start'});
        document.getElementById('scheduled-employee').focus({preventScroll: true});
      });
    });
    update();
    const error = document.getElementById('schedule-error');
    if (error) error.focus();
  }
  document.querySelectorAll('.schedule-cancel').forEach(form => {
    form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); });
  });
})();
