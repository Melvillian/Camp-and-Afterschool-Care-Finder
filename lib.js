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

  return { escapeHtml, safeUrl, formatAddress };
});
