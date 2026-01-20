// Add v2 banner inside content-container with negative margins
(function() {
  if (typeof window === 'undefined') return;
  
  function addBanner() {
    const isV2 = window.location.pathname.includes('/v2/');
    const container = document.getElementById('content-container');
    let banner = document.getElementById('v2-banner');
    
    if (isV2 && container) {
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'v2-banner';
        banner.innerHTML = 'These are the docs for FastMCP 2.0. The beta of <a href="/getting-started/welcome" style="color: white; text-decoration: underline; font-weight: 700;">FastMCP 3.0</a> is now available.';
        container.insertBefore(banner, container.firstChild);
      }
    } else if (!isV2 && banner) {
      banner.remove();
    }
  }
  
  function run() {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', addBanner);
    } else {
      addBanner();
    }
  }
  
  run();
  
  let lastUrl = location.href;
  new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      setTimeout(addBanner, 100);
    }
  }).observe(document.body, {subtree: true, childList: true});
})();
