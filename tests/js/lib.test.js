const test = require('node:test');
const assert = require('node:assert/strict');
const { escapeHtml, safeUrl, formatAddress, isPast, defaultVisibility, lastSeasonYear } = require('../../lib.js');

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
