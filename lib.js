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

  return { escapeHtml, safeUrl, formatAddress, isPast, defaultVisibility, lastSeasonYear, localEditsStatus, seasonYear };
});
