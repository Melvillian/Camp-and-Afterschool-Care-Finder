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

  return { escapeHtml, safeUrl };
});
