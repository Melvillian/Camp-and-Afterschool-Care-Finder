// Small pure helpers shared by the pages and the Node tests.
// Browser: exposes window.CampLib. Node: module.exports.
(function (root, factory) {
  const lib = factory();
  if (typeof module === 'object' && module.exports) module.exports = lib;
  else root.CampLib = lib;
})(typeof self !== 'undefined' ? self : this, function () {
  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function safeUrl(value) {
    if (value === null || value === undefined) return '';
    const v = String(value).trim();
    return /^https?:\/\//i.test(v) ? v : '';
  }

  // "street, city, ST zip", skipping blank parts.
  function formatAddress(p) {
    const clean = v => (v === null || v === undefined ? '' : String(v).trim());
    const stateZip = [clean(p.state), clean(p.zip)].filter(Boolean).join(' ');
    return [clean(p.address), clean(p.city), stateZip].filter(Boolean).join(', ');
  }

  // A program is past once its end date (or, lacking one, its start date) is before today.
  function isPast(p, todayIso) {
    if (p.endDate) return p.endDate < todayIso;
    if (p.startDate) return p.startDate < todayIso;
    return false;
  }

  // The season a program belongs to, as a string: the programYear field if set,
  // else the year of its end (or start) date, else null.
  function seasonYear(p) {
    const fromField = Number(p.programYear);
    if (fromField > 0) return String(fromField);
    const d = p.endDate || p.startDate;
    return d && /^\d{4}/.test(d) ? d.slice(0, 4) : null;
  }

  function yearOf(p) {
    return Number(seasonYear(p)) || 0;
  }

  // Per-organization default visibility, keyed by uid:
  //   'current'    - show normally
  //   'lastSeason' - past, but the organization has posted nothing newer: keep it visible
  //   'hidden'     - superseded by a later year, or the organization still has upcoming dates
  function defaultVisibility(programs, todayIso) {
    const groups = new Map();
    programs.forEach((p, i) => {
      const key = p.orgId ? 'org:' + p.orgId : 'solo:' + i;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(p);
    });

    const result = new Map();
    groups.forEach(group => {
      const latestYear = Math.max(...group.map(yearOf));
      const hasUpcomingDated = group.some(
        q => (q.startDate || q.endDate) && !isPast(q, todayIso)
      );
      group.forEach(p => {
        let state;
        if (yearOf(p) < latestYear) state = 'hidden';
        else if (!isPast(p, todayIso)) state = 'current';
        else if (hasUpcomingDated) state = 'hidden';
        else state = 'lastSeason';
        result.set(p.uid, state);
      });
    });
    return result;
  }

  // Greatest year among 'lastSeason' programs, as a string, or null when there are none.
  function lastSeasonYear(programs, visibility) {
    let best = 0;
    programs.forEach(p => {
      if (visibility.get(p.uid) === 'lastSeason') best = Math.max(best, yearOf(p));
    });
    return best ? String(best) : null;
  }

  function parseArray(text) {
    if (typeof text !== 'string') return null;
    try {
      const v = JSON.parse(text);
      return Array.isArray(v) ? v : null;
    } catch (e) {
      return null;
    }
  }

  // Decide whether the admin page should use browser-saved edits instead of data.js.
  function localEditsStatus(saved, currentStamp) {
    const programs = parseArray(saved.programs);
    const orgs = parseArray(saved.orgs);
    const useLocal = programs !== null || orgs !== null;
    return {
      useLocal,
      stale: useLocal && saved.stamp !== currentStamp,
      programs,
      orgs,
      savedAt: saved.savedAt || null,
    };
  }

  function validIso(v) {
    if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
    const d = new Date(v + 'T00:00:00Z');
    return !isNaN(d) && d.toISOString().slice(0, 10) === v;
  }

  function shiftIso(iso, days) {
    const d = new Date(iso + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }

  // Organizations whose registration opens soon, or opened within the last 30 days.
  // sortDate: the earliest date not yet passed, else the latest date.
  // openNow: the earliest date is today or earlier.
  function registrationList(orgs, todayIso) {
    const cutoff = shiftIso(todayIso, -30);
    const out = [];
    orgs.forEach(o => {
      const dates = [o.registrationOpensEarly, o.registrationOpens].filter(validIso).sort();
      if (!dates.some(d => d >= cutoff)) return;
      const upcoming = dates.find(d => d >= todayIso);
      out.push({
        orgId: o.orgId,
        name: o.name,
        website: o.website,
        sortDate: upcoming || dates[dates.length - 1],
        early: validIso(o.registrationOpensEarly) ? o.registrationOpensEarly : '',
        opens: validIso(o.registrationOpens) ? o.registrationOpens : '',
        notes: o.registrationNotes || '',
        openNow: dates[0] <= todayIso,
      });
    });
    return out.sort((a, b) => a.sortDate < b.sortDate ? -1 : a.sortDate > b.sortDate ? 1 : String(a.name).localeCompare(String(b.name)));
  }

  // RFC 4180 CSV -> [{ row, values }] for each non-blank data row, `values` keyed by the header row.
  // `row` is the record's position in the text, counting blank rows (the header is row 1), which is its Sheet row.
  function parseCsvRows(text) {
    const records = [];
    let record = [], field = '', inQuotes = false;
    const src = String(text || '').replace(/^\ufeff/, '');
    for (let i = 0; i < src.length; i++) {
      const c = src[i];
      if (inQuotes) {
        if (c === '"') {
          if (src[i + 1] === '"') { field += '"'; i++; } else inQuotes = false;
        } else field += c;
      } else if (c === '"') inQuotes = true;
      else if (c === ',') { record.push(field); field = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && src[i + 1] === '\n') i++;
        record.push(field); records.push(record); record = []; field = '';
      } else field += c;
    }
    if (field !== '' || record.length) { record.push(field); records.push(record); }
    const headerAt = records.findIndex(r => r.some(v => v.trim() !== ''));
    if (headerAt < 0) return [];
    const header = records[headerAt].map(h => h.trim());
    const out = [];
    records.forEach((r, i) => {
      if (i <= headerAt || !r.some(v => v.trim() !== '')) return;
      const values = {};
      header.forEach((h, j) => { values[h] = r[j] === undefined ? '' : r[j]; });
      out.push({ row: i + 1, values });
    });
    return out;
  }

  // Same, as plain objects. Fully blank rows are dropped.
  function parseCsv(text) {
    return parseCsvRows(text).map(r => r.values);
  }

  // Open tasks by time (<=5, 6-15, longer or unreadable), each by due date (blank or invalid last), then org name.
  // Tasks with a status other than the four known ones count in total only.
  function groupTasks(tasks) {
    const counts = { open: 0, claimed: 0, done: 0, needs_help: 0, total: tasks.length };
    const quick = [], medium = [], long = [];
    tasks.forEach(t => {
      const status = String(t.status === undefined ? '' : t.status).trim().toLowerCase();
      if (!Object.prototype.hasOwnProperty.call(counts, status) || status === 'total') return;
      counts[status]++;
      if (status !== 'open') return;
      const raw = String(t.minutes === undefined ? '' : t.minutes).trim();
      const m = /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : NaN;
      (m <= 5 ? quick : m <= 15 ? medium : long).push(t);
    });
    const due = t => (validIso(t.due) ? t.due : '');
    const cmp = (a, b) => {
      const da = due(a), db = due(b);
      if (da !== db) return da === '' ? 1 : db === '' ? -1 : da < db ? -1 : 1;
      return String(a.org_name || '').localeCompare(String(b.org_name || ''));
    };
    return { quick: quick.sort(cmp), medium: medium.sort(cmp), long: long.sort(cmp), counts };
  }

  // The tasks the board may show: only those whose org_id is an organization in the site's data, each named
  // from that data. The Sheet's org_name is never used: anyone with the link can type in it.
  function boardTasks(tasks, organizations) {
    const names = new Map((organizations || []).map(o => [String(o.orgId), String(o.name || o.orgId)]));
    const id = t => String(t.org_id === undefined ? '' : t.org_id).trim();
    return tasks.filter(t => names.has(id(t))).map(t => Object.assign({}, t, { org_name: names.get(id(t)) }));
  }

  return { escapeHtml, safeUrl, formatAddress, isPast, defaultVisibility, lastSeasonYear, localEditsStatus, seasonYear, registrationList, parseCsv, parseCsvRows, groupTasks, boardTasks, validIso };
});
