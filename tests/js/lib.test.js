const test = require('node:test');
const assert = require('node:assert/strict');
const { escapeHtml, safeUrl, formatAddress, isPast, defaultVisibility, lastSeasonYear, localEditsStatus, seasonYear, registrationList, parseCsv, parseCsvRows, groupTasks, boardTasks } = require('../../lib.js');

test('escapeHtml escapes markup characters', () => {
  assert.equal(escapeHtml(`<img src=x onerror="a('b')">&`),
    '&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;');
});
test('escapeHtml handles null, undefined and numbers', () => {
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
  assert.equal(escapeHtml(42), '42');
});
test('safeUrl allows only http and https', () => {
  assert.equal(safeUrl(' https://example.org/a '), 'https://example.org/a');
  assert.equal(safeUrl('HTTP://example.org'), 'HTTP://example.org');
  assert.equal(safeUrl('javascript:alert(1)'), '');
  assert.equal(safeUrl('data:text/html,x'), '');
  assert.equal(safeUrl(''), '');
  assert.equal(safeUrl(null), '');
});
test('formatAddress joins the parts that exist', () => {
  assert.equal(formatAddress({ address: '1 Main St', city: 'Burlington', state: 'VT', zip: '05401' }),
    '1 Main St, Burlington, VT 05401');
  assert.equal(formatAddress({ address: '', city: 'Hinesburg', state: 'VT', zip: '' }), 'Hinesburg, VT');
  assert.equal(formatAddress({ address: '  ', city: '', state: '', zip: '' }), '');
  assert.equal(formatAddress({}), '');
});

const T = '2026-11-20';
const P = (uid, orgId, programYear, startDate = '', endDate = '') => ({ uid, orgId, programYear, startDate, endDate });

test('isPast uses end date, then start date', () => {
  assert.equal(isPast(P('x', 'o', '2026', '2026-07-06', '2026-07-10'), T), true);
  assert.equal(isPast(P('x', 'o', '2027', '2027-07-05', '2027-07-09'), T), false);
  assert.equal(isPast(P('x', 'o', '2026'), T), false);
  assert.equal(isPast(P('x', 'o', '2026', '2026-11-20'), T), false);
  assert.equal(isPast(P('x', 'o', '2026', '2026-11-19'), T), true);
  assert.equal(isPast(P('x', 'o', '2026', '2026-11-01', '2026-11-20'), T), false);
});

test('defaultVisibility decides per organization', () => {
  const programs = [
    P('a1', 'A', '2026', '2026-07-06', '2026-07-10'),   // only last season exists
    P('b1', 'B', '2026', '2026-07-06', '2026-07-10'),   // superseded by b2
    P('b2', 'B', '2027', '2027-07-05', '2027-07-09'),
    P('c1', 'C', '2026'),                               // undated, superseded by c2
    P('c2', 'C', '2027'),
    P('d1', 'D', '2026', '2026-11-01', '2026-11-05'),   // mid-season: sibling still upcoming
    P('d2', 'D', '2026', '2026-12-01', '2026-12-05'),
    P('e1', 'E', '2026'),                               // undated, nothing newer
    P('n1', '', '2026', '2026-07-06', '2026-07-10'),    // no org: judged alone
    P('n2', '', '2027', '2027-07-05', '2027-07-09'),
  ];
  const v = defaultVisibility(programs, T);
  assert.deepEqual(Object.fromEntries(v), {
    a1: 'lastSeason', b1: 'hidden', b2: 'current', c1: 'hidden', c2: 'current',
    d1: 'hidden', d2: 'current', e1: 'current', n1: 'lastSeason', n2: 'current',
  });
  assert.equal(lastSeasonYear(programs, v), '2026');
});

test('defaultVisibility: start date only, on the boundary', () => {
  const programs = [P('s1', 'S', '2026', '2026-11-19'), P('s2', 'S', '2026', '2026-11-20')];
  assert.deepEqual(Object.fromEntries(defaultVisibility(programs, T)), { s1: 'hidden', s2: 'current' });
});

test('defaultVisibility: year falls back to the dates, then to 0', () => {
  const programs = [P('f1', 'F', '', '2026-07-06', '2026-07-10'), P('f2', 'F', '', '2027-07-05', '2027-07-09'), P('f3', 'F', '')];
  assert.deepEqual(Object.fromEntries(defaultVisibility(programs, T)), { f1: 'hidden', f2: 'current', f3: 'hidden' });
});

test('lastSeasonYear is null when nothing is last season', () => {
  const programs = [P('b2', 'B', '2027', '2027-07-05', '2027-07-09')];
  assert.equal(lastSeasonYear(programs, defaultVisibility(programs, T)), null);
  assert.equal(lastSeasonYear([], new Map()), null);
});

test('localEditsStatus: nothing saved', () => {
  const s = localEditsStatus({ programs: null, orgs: null, stamp: null, savedAt: null }, 'A');
  assert.deepEqual([s.useLocal, s.stale], [false, false]);
});
test('localEditsStatus: saved against the current build', () => {
  const s = localEditsStatus({ programs: '[{"uid":"x"}]', orgs: '[]', stamp: 'A', savedAt: '2026-11-20T09:00:00' }, 'A');
  assert.deepEqual([s.useLocal, s.stale], [true, false]);
  assert.deepEqual(s.programs, [{ uid: 'x' }]);
});
test('localEditsStatus: saved against an older build is stale', () => {
  const s = localEditsStatus({ programs: '[]', orgs: '[]', stamp: 'A', savedAt: 'x' }, 'B');
  assert.deepEqual([s.useLocal, s.stale], [true, true]);
});
test('localEditsStatus: copies from before stamping are stale', () => {
  assert.equal(localEditsStatus({ programs: '[]', orgs: null, stamp: null, savedAt: null }, 'B').stale, true);
});
test('localEditsStatus: corrupt JSON is ignored', () => {
  const s = localEditsStatus({ programs: '{not json', orgs: '"a string"', stamp: 'A', savedAt: 'x' }, 'A');
  assert.deepEqual([s.useLocal, s.programs, s.orgs], [false, null, null]);
});

test('seasonYear prefers the programYear field, then the dates', () => {
  assert.equal(seasonYear({ programYear: '2026', startDate: '2025-07-07', endDate: '2025-07-11' }), '2026');
  assert.equal(seasonYear({ programYear: '', startDate: '2025-07-07', endDate: '' }), '2025');
  assert.equal(seasonYear({ programYear: 2027 }), '2027');
  assert.equal(seasonYear({ programYear: '', startDate: '', endDate: '' }), null);
  assert.equal(seasonYear({}), null);
});

const T2 = '2027-01-20';
const O = (orgId, early, opens, notes = '') => ({ orgId, name: orgId.toUpperCase(), website: 'https://example.org/' + orgId,
  registrationOpensEarly: early, registrationOpens: opens, registrationNotes: notes });

test('registrationList picks, labels and sorts', () => {
  const list = registrationList([
    O('later', '', '2027-02-11'),
    O('lottery', '2027-01-08', '2027-02-03', 'Lottery Jan 8\u201312'),   // early passed, general upcoming
    O('soon', '2027-01-26', '2027-02-09'),
    O('longago', '', '2026-12-01'),                                 // more than 30 days ago
    O('recent', '', '2027-01-05'),                                  // opened 15 days ago
    O('blank', '', ''),
    O('bad', '', '2/4/2027'),
  ], T2);
  assert.deepEqual(list.map(x => [x.orgId, x.sortDate, x.openNow]), [
    ['recent', '2027-01-05', true],
    ['soon', '2027-01-26', false],
    ['lottery', '2027-02-03', true],
    ['later', '2027-02-11', false],
  ]);
  assert.equal(list[2].notes, 'Lottery Jan 8\u201312');
});
test('registrationList: only an early date', () => {
  assert.deepEqual(registrationList([O('e', '2027-01-25', '')], T2).map(x => [x.sortDate, x.openNow]), [['2027-01-25', false]]);
});
test('registrationList: boundary is 30 days inclusive', () => {
  assert.equal(registrationList([O('a', '', '2026-12-21')], T2).length, 1);
  assert.equal(registrationList([O('a', '', '2026-12-20')], T2).length, 0);
});

test('parseCsv handles quotes, commas, newlines and CRLF', () => {
  const rows = parseCsv('a,b\r\n1,"x, y"\r\n2,"line1\nline2"\r\n3,"say ""hi"""\r\n,\r\n');
  assert.deepEqual(rows, [{ a: '1', b: 'x, y' }, { a: '2', b: 'line1\nline2' }, { a: '3', b: 'say "hi"' }]);
  assert.deepEqual(parseCsv(''), []);
});
test('parseCsv tolerates a BOM, a missing final newline and short rows', () => {
  assert.deepEqual(parseCsv('\ufeffa,b\n1'), [{ a: '1', b: '' }]);
});
test('groupTasks buckets open tasks by time and sorts by due date', () => {
  const t = (task_id, minutes, status, due = '', org_name = task_id) => ({ task_id, minutes, status, due, org_name });
  const g = groupTasks([t('a', '2', 'open'), t('b', '10', 'open', '2027-02-04'), t('c', '10', 'open', '2027-01-12'),
    t('d', '10', 'open'), t('e', '45', 'open'), t('f', '10', 'claimed'), t('g', '10', 'done'), t('h', '2', 'needs_help')]);
  assert.deepEqual(g.quick.map(x => x.task_id), ['a']);
  assert.deepEqual(g.medium.map(x => x.task_id), ['c', 'b', 'd']);
  assert.deepEqual(g.long.map(x => x.task_id), ['e']);
  assert.deepEqual(g.counts, { open: 5, claimed: 1, done: 1, needs_help: 1, total: 8 });
});
test('groupTasks: boundaries, bad minutes, unknown status, ties by org name', () => {
  const t = (task_id, minutes, status, due = '', org_name = task_id) => ({ task_id, minutes, status, due, org_name });
  const g = groupTasks([t('m5', '5', 'open'), t('m6', '6', 'open'), t('m15', '15', 'open'), t('m16', '16', 'open'),
    t('bad', 'soon', 'open'), t('empty', '', 'open'), t('odd', '2', 'reopened'), t('zed', '10', 'open', '2027-01-01', 'Zed'),
    t('amy', '10', 'open', '2027-01-01', 'Amy'), t('junk', '10', 'open', 'next week', 'Junk')]);
  assert.deepEqual(g.quick.map(x => x.task_id), ['m5']);
  assert.deepEqual(g.medium.map(x => x.task_id), ['amy', 'zed', 'junk', 'm15', 'm6']);
  assert.deepEqual(g.long.map(x => x.task_id), ['bad', 'empty', 'm16']);
  assert.deepEqual(g.counts, { open: 9, claimed: 0, done: 0, needs_help: 0, total: 10 });
});
test('parseCsvRows keeps each row\'s position when blank rows are dropped', () => {
  const rows = parseCsvRows('a,b\n1,x\n,\n\n2,"multi\nline"\n3,z');
  assert.deepEqual(rows.map(r => [r.row, r.values.a]), [[2, '1'], [5, '2'], [6, '3']]);
  assert.deepEqual(parseCsvRows(''), []);
});
test('groupTasks ignores case and stray spaces in status', () => {
  const g = groupTasks([{ task_id: 'a', minutes: '2', status: ' Open ' }, { task_id: 'b', minutes: '2', status: 'Done ' }]);
  assert.deepEqual(g.quick.map(x => x.task_id), ['a']);
  assert.deepEqual(g.counts, { open: 1, claimed: 0, done: 1, needs_help: 0, total: 2 });
});

test('boardTasks names tasks from the site data and drops unknown organizations', () => {
  const orgs = [{ orgId: 'o1', name: 'Org One' }, { orgId: 'o2', name: 'Org Two' }];
  const tasks = [{ task_id: 'a', org_id: 'o1', org_name: '<i>Typed</i>', row: 2 }, { task_id: 'b', org_id: 'zz', org_name: 'Spam' },
    { task_id: 'c', org_id: ' o2 ', org_name: '' }, { task_id: 'd' }];
  const out = boardTasks(tasks, orgs);
  assert.deepEqual(out.map(t => [t.task_id, t.org_name, t.row]), [['a', 'Org One', 2], ['c', 'Org Two', undefined]]);
  assert.equal(tasks[0].org_name, '<i>Typed</i>');
  assert.deepEqual(boardTasks(tasks, undefined), []);
});
