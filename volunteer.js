// Volunteer task board: reads the Tasks tab of the volunteer Sheet and lists open tasks by time needed.
// Everything read from the Sheet is untrusted (anyone with the link can edit it): escape every value,
// and build links only from the config and the row number.
(function () {
  const L = window.CampLib;
  const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const GROUPS = [['quick', '2 minutes'], ['medium', '10 minutes'], ['long', '30–60 minutes']];

  const el = id => document.getElementById(id);

  function shortDate(iso) {
    const [, m, d] = iso.split('-').map(Number);
    return MONTH_ABBR[m - 1] + ' ' + d;
  }

  async function fetchText(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.text();
  }

  // The Tasks tab as parsed rows, or null when the text is not that tab (a sign-in page, an error page...).
  function parseTasks(text) {
    const firstLine = String(text).replace(/^﻿/, '').split(/\r?\n/, 1)[0];
    const header = firstLine.split(',').map(h => h.replace(/^\s*"?|"?\s*$/g, ''));
    if (!header.includes('task_id')) return null;
    // Row numbers follow the tab as downloaded: the first data row is row 2.
    return L.parseCsv(text).map((t, i) => Object.assign(t, { row: i + 2 }));
  }

  async function loadLive(config) {
    try {
      const url = 'https://docs.google.com/spreadsheets/d/' + encodeURIComponent(config.sheet_id) + '/gviz/tq?tqx=out:csv&sheet=Tasks';
      return parseTasks(await fetchText(url));
    } catch (e) {
      return null;
    }
  }

  async function loadSnapshot(config) {
    const dir = 'data/staging/' + encodeURIComponent(config.season) + '/sheet/';
    let tasks;
    try {
      tasks = parseTasks(await fetchText(dir + 'tasks.csv'));
    } catch (e) {
      return null;
    }
    if (!tasks) return null;
    let savedOn = '';
    try {
      const snap = JSON.parse(await fetchText(dir + 'snapshot.json'));
      if (snap && L.validIso(snap.pulled_on)) savedOn = snap.pulled_on;
    } catch (e) { /* the date is optional */ }
    return { tasks, savedOn };
  }

  function sheetLink(config, row) {
    return 'https://docs.google.com/spreadsheets/d/' + encodeURIComponent(config.sheet_id) +
      '/edit#gid=' + encodeURIComponent(config.tasks_gid) + '&range=A' + Number(row);
  }

  function taskHtml(task, config) {
    const name = L.escapeHtml(task.org_name || task.org_id || 'Unnamed organization');
    const due = L.validIso(task.due) ? '<span class="task-due">Registration opens ' + L.escapeHtml(shortDate(task.due)) + '</span>' : '';
    return '<li class="task-item"><div class="task-info"><strong class="task-name">' + name + '</strong>' + due + '</div>' +
      '<a class="link-btn" href="' + L.escapeHtml(sheetLink(config, task.row)) + '" target="_blank" rel="noopener">Open in the Sheet</a></li>';
  }

  function programsSentence(season) {
    const n = typeof PROGRAMS === 'undefined' ? 0 :
      PROGRAMS.filter(p => String(p.programYear) === String(season)).length;
    const year = String(season);
    if (n === 0) return 'No ' + year + ' programs are live on the site yet.';
    if (n === 1) return '1 ' + year + ' program is live on the site.';
    return n + ' ' + year + ' programs are live on the site.';
  }

  function render(config, tasks, savedCopy) {
    if (savedCopy) {
      const when = savedCopy.savedOn ? ' Saved ' + shortDate(savedCopy.savedOn) + '.' : '';
      el('boardSource').textContent = 'Showing the last saved copy.' + when;
    }
    el('boardPrograms').textContent = programsSentence(config.season);
    if (!tasks.length) {
      el('boardMessage').textContent = 'No tasks yet. New ones appear here as camps post their ' + config.season + ' listings.';
      return;
    }
    el('boardMessage').textContent = '';
    const g = L.groupTasks(tasks);
    el('boardProgress').textContent = g.counts.done + ' of ' + g.counts.total + ' tasks done · ' + g.counts.claimed + ' in progress';
    el('boardGroups').innerHTML = GROUPS.map(([key, title]) =>
      '<section class="task-group"><h2>' + title + '</h2>' +
      (g[key].length
        ? '<ul class="task-list">' + g[key].map(t => taskHtml(t, config)).join('') + '</ul>'
        : '<p class="task-empty">Nothing open here right now.</p>') +
      '</section>').join('');
  }

  async function boot() {
    let config;
    try {
      config = JSON.parse(await fetchText('data/ingest_config.json'));
      if (!config || !config.season || !config.sheet_id) throw new Error('incomplete config');
    } catch (e) {
      el('boardMessage').textContent = "The task board's settings could not be loaded.";
      return;
    }
    const wantSnapshot = new URLSearchParams(location.search).get('source') === 'snapshot';
    const live = wantSnapshot ? null : await loadLive(config);
    if (live) return render(config, live, null);
    const saved = await loadSnapshot(config);
    if (saved) return render(config, saved.tasks, saved);
    render(config, [], null);
  }

  boot();
})();
