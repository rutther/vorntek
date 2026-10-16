/* Preserve bookmarked hash tabs after the old marketing root redirects. */
(() => {
  const targets = {
    integrations: '/admin/marketing/integrations/',
    matching: '/admin/marketing/attribution/?tab=matching',
    ctas: '/admin/marketing/?tab=ctas#ctas',
    rules: '/admin/marketing/?tab=rules#rules',
    mappings: '/admin/marketing/?tab=mappings#mappings',
  };
  const target = targets[window.location.hash.slice(1)];
  if (typeof target === 'string') window.location.replace(target);
})();
