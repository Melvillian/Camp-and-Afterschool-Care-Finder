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

  // The month a calendar should open on, as { year, month } with month 0-11:
  // the first month that still has dated programs to come (never earlier than today's month);
  // when every dated program is over, the first month of the latest year that has any;
  // with no dated programs at all, today's month.
  function calendarStart(programs, todayIso) {
    const ym = iso => ({ year: Number(iso.slice(0, 4)), month: Number(iso.slice(5, 7)) - 1 });
    const dated = programs.filter(p => validIso(p.startDate));
    if (!dated.length) return ym(todayIso);
    const starts = list => list.map(p => p.startDate).sort();
    const upcoming = starts(dated.filter(p => !isPast(p, todayIso)));
    if (upcoming.length) return ym(upcoming[0] > todayIso ? upcoming[0] : todayIso);
    const all = starts(dated);
    const latestYear = all[all.length - 1].slice(0, 4);
    return ym(all.find(d => d.startsWith(latestYear)));
  }

  // RFC 4180 CSV text -> array of records, each an array of fields. Blank lines come back as [''].
  function csvRecords(text) {
    const records = [];
    let record = [], field = '', inQuotes = false;
    const src = String(text || '').replace(/^﻿/, '');
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
    return records;
  }

  // RFC 4180 CSV -> [{ row, values }] for each non-blank data row, `values` keyed by the header row.
  // `row` is the record's position in the text, counting blank rows (the header is row 1), which is its Sheet row.
  function parseCsvRows(text) {
    const records = csvRecords(text);
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

  // ----- The Admin page's CSV editing -----
  // The Admin page never rebuilds a CSV from data.js (that drops inactive rows and every column the page
  // does not show). It reads the file, changes only the cells that were edited, and writes the rest back as read.

  // A data CSV as { header, rows, eol }: every line that is not empty, values exactly as in the file.
  function parseCsvTable(text) {
    const src = String(text || '');
    const records = csvRecords(src).filter(r => !(r.length === 1 && r[0] === ''));
    const header = records.length ? records[0] : [];
    const rows = records.slice(1).map(r => {
      const row = {};
      header.forEach((h, j) => { row[h] = r[j] === undefined ? '' : r[j]; });
      return row;
    });
    return { header, rows, eol: src.includes('\r\n') ? '\r\n' : '\n' };
  }

  // The reverse, quoting the way Python's csv module does, so an unedited file comes back byte for byte.
  function toCsv(header, rows, eol) {
    const cell = v => {
      const s = v === null || v === undefined ? '' : String(v);
      return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const line = values => values.map(cell).join(',');
    return [line(header)].concat(rows.map(r => line(header.map(h => r[h])))).join(eol || '\n') + (eol || '\n');
  }

  // Apply the Admin page's changes to a table's rows:
  //   changes.edits   { id: { column: value } }  cells to change on existing rows
  //   changes.deleted [id]                       rows to remove
  //   changes.added   [row]                      rows to append
  // Rows that are not named come back untouched and in order. Columns the file does not have are ignored.
  // `missing` lists edited or deleted ids that are not in the file; `clashes` lists added ids that already are.
  function applyTableChanges(table, idCol, changes) {
    const edits = (changes && changes.edits) || {};
    const deleted = new Set((changes && changes.deleted) || []);
    const added = (changes && changes.added) || [];
    const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
    const seen = new Set();
    const rows = [];
    table.rows.forEach(row => {
      const id = row[idCol];
      seen.add(id);
      if (deleted.has(id)) return;
      if (!own(edits, id)) { rows.push(row); return; }
      const next = Object.assign({}, row);
      table.header.forEach(h => { if (own(edits[id], h)) next[h] = String(edits[id][h]); });
      rows.push(next);
    });
    const clashes = [];
    added.forEach(row => {
      if (seen.has(row[idCol])) { clashes.push(row[idCol]); return; }
      seen.add(row[idCol]);
      const next = {};
      table.header.forEach(h => { next[h] = row[h] === null || row[h] === undefined ? '' : String(row[h]); });
      rows.push(next);
    });
    const missing = Object.keys(edits).concat([...deleted]).filter(id => !table.rows.some(r => r[idCol] === id));
    return { rows, missing: [...new Set(missing)], clashes };
  }

  function idSlug(text) {
    return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50).replace(/-+$/, '');
  }

  // `<org_id>-<name-slug>-<year>`, with -2, -3... when taken: the rule in scripts/staging.py.
  function newProgramId(orgId, name, year, taken) {
    const base = orgId + '-' + (idSlug(name) || 'program') + '-' + year;
    let candidate = base;
    for (let n = 2; taken.has(candidate); n++) candidate = base + '-' + n;
    return candidate;
  }

  // The season a program made in the Admin page belongs to: the year of its end (or start) date, else the
  // season before the one being ingested. null when that would be the ingestion season or later: those rows
  // are written only by scripts/merge_staging.py.
  function adminProgramYear(startDate, endDate, ingestSeason) {
    const season = Number(ingestSeason);
    if (!(season > 0)) return null;
    const d = validIso(endDate) ? endDate : validIso(startDate) ? startDate : '';
    const year = d ? Number(d.slice(0, 4)) : season - 1;
    return year < season ? year : null;
  }

  // True when the Admin page may change this program: it is from a season before the one being ingested.
  function adminMayEdit(programYear, ingestSeason) {
    const year = Number(programYear), season = Number(ingestSeason);
    return season > 0 && year > 0 && year < season;
  }

  // The ids of season rows (program_year at or after the ingestion season, or unreadable) that differ between
  // two versions of programs.csv: changed, removed or added. The Admin page refuses to save when there are any.
  function seasonRowChanges(beforeRows, afterRows, ingestSeason) {
    const inSeason = r => !adminMayEdit(r.program_year, ingestSeason);
    const key = r => JSON.stringify(Object.keys(r).sort().map(k => [k, r[k]]));
    const before = new Map(beforeRows.filter(inSeason).map(r => [r.program_id, key(r)]));
    const out = [];
    afterRows.filter(inSeason).forEach(r => {
      if (before.get(r.program_id) !== key(r)) out.push(r.program_id);
      before.delete(r.program_id);
    });
    return out.concat([...before.keys()]);
  }

  const same = (a, b) => JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
  const checkbox = (before, after) => (after ? 'TRUE' : before ? 'FALSE' : '');

  const PROGRAM_FORM_COLUMNS = {
    name: 'program_name', orgId: 'org_id', category: 'program_type', sessionType: 'session_type',
    scheduleType: 'schedule_type', confidence: 'confidence', address: 'site_address', city: 'site_city',
    county: 'site_county', registrationUrl: 'registration_url', gradesMin: 'grades_min', gradesMax: 'grades_max',
    ageMin: 'age_min', ageMax: 'age_max', daysOffered: 'days_of_week', description: 'description',
    startDate: 'start_date', endDate: 'end_date', registrationOpens: 'registration_opens',
    registrationOpensEarly: 'registration_opens_early', registrationNotes: 'registration_notes', notes: 'notes',
  };

  // The programs.csv cells to change, given the edit form as it opened (`before`) and as it was saved (`after`).
  // Only fields the person changed produce cells, so values the form shows but the CSV does not hold (a
  // description written by the build, a city taken from the organization) are never written back.
  // `activities` is the row's current activities cell: tags the form has no checkbox for are kept.
  // For a new row pass `before` = {}.
  function programFormPatch(before, after, activities) {
    const patch = {};
    Object.keys(PROGRAM_FORM_COLUMNS).forEach(k => {
      if (!same(before[k], after[k])) patch[PROGRAM_FORM_COLUMNS[k]] = after[k] === null || after[k] === undefined ? '' : String(after[k]);
    });
    if (!same(before.hours, after.hours)) {
      const parts = String(after.hours || '').split(/\s*[–—-]\s*/);
      patch.start_time = (parts[0] || '').trim();
      patch.end_time = (parts[1] || '').trim();
    }
    if (!same(before.cost, after.cost) || !same(before.costPeriod, after.costPeriod)) {
      const cost = Number(after.cost) || 0;
      const was = Number(before.cost) || 0;
      if (cost > 0) {
        const unit = { week: '/week', day: '/day', month: '/month' }[after.costPeriod] || '';
        patch.cost_raw = '$' + cost + unit;
        patch.cost_per_week = after.costPeriod === 'week' ? String(cost) : '';
      } else if (was > 0) {
        patch.cost_raw = '';
        patch.cost_per_week = '';
      }
    }
    if (!same(before.transportation, after.transportation)) patch.transportation_provided = checkbox(before.transportation, after.transportation);
    if (!same(before.mealsProvided, after.mealsProvided)) patch.meals_provided = checkbox(before.mealsProvided, after.mealsProvided);
    if (!same(before.subjects || [], after.subjects || [])) {
      const was = before.subjects || [], now = after.subjects || [];
      const current = String(activities || '').split(',').map(t => t.trim()).filter(Boolean);
      const kept = current.filter(t => !was.includes(t) || now.includes(t));
      patch.activities = kept.concat(now.filter(t => !kept.includes(t))).join(/,\s/.test(String(activities || '')) ? ', ' : ',');
    }
    return patch;
  }

  const ORG_FORM_COLUMNS = {
    orgId: 'org_id', name: 'org_name', type: 'org_type', confidence: 'confidence', phone: 'phone', email: 'email',
    website: 'website', address: 'street_address', city: 'city', county: 'county', state: 'state', zip: 'zip',
    financialAidNotes: 'financial_aid_notes', registrationOpens: 'registration_opens',
    registrationOpensEarly: 'registration_opens_early', registrationNotes: 'registration_notes', notes: 'notes',
  };

  // The organizations.csv cells to change; the same rule as programFormPatch.
  function orgFormPatch(before, after) {
    const patch = {};
    Object.keys(ORG_FORM_COLUMNS).forEach(k => {
      if (!same(before[k], after[k])) patch[ORG_FORM_COLUMNS[k]] = after[k] === null || after[k] === undefined ? '' : String(after[k]);
    });
    if (!same(before.financialAidAvailable, after.financialAidAvailable)) {
      patch.financial_aid_available = checkbox(before.financialAidAvailable, after.financialAidAvailable);
    }
    return patch;
  }

  // Counts for the About and Data pages, from the data the site is showing.
  function dataCounts(programs, organizations) {
    const withPrograms = new Set(programs.map(p => p.orgId).filter(Boolean));
    return {
      programs: programs.length,
      camps: programs.filter(p => p.category === 'camp').length,
      afterschool: programs.filter(p => p.category === 'afterschool').length,
      orgs: organizations.length,
      orgsWithPrograms: organizations.filter(o => withPrograms.has(o.orgId)).length,
    };
  }

  return { escapeHtml, safeUrl, formatAddress, isPast, defaultVisibility, lastSeasonYear, localEditsStatus, seasonYear, registrationList, parseCsv, parseCsvRows, groupTasks, boardTasks, validIso,
    calendarStart, dataCounts, parseCsvTable, toCsv, applyTableChanges, newProgramId, adminProgramYear, adminMayEdit, seasonRowChanges,
    programFormPatch, orgFormPatch };
});
