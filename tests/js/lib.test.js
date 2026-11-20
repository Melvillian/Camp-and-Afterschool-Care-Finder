const test = require('node:test');
const assert = require('node:assert/strict');
const { escapeHtml, safeUrl, formatAddress } = require('../../lib.js');

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
