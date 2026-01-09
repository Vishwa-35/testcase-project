// Wait for DOM to be fully loaded
document.addEventListener('DOMContentLoaded', function() {
  // Auto-scroll to highlighted row
  if (window.HIGHLIGHT_ID) {
    const row = document.getElementById('row-' + window.HIGHLIGHT_ID);
    if (row) {
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      // Keep highlight for a few seconds
      setTimeout(() => {
        row.classList.remove('table-warning');
      }, 5000);
    }
  }

  // Export modal logic is now in list.html (inline script)
});

// Helper function to get CSRF token
function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === (name + '=')) {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}

// Export validation functions removed - new 3-step flow handles validation inline

// Toggle sidebar function (needs to be global for onclick handlers)
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const mainContent = document.getElementById('mainContent');
  const toggleBtn = document.getElementById('sidebarToggle');
  const toggleIcon = document.getElementById('toggleIcon');
  
  if (sidebar) sidebar.classList.toggle('collapsed');
  if (mainContent) mainContent.classList.toggle('expanded');
  if (toggleBtn) toggleBtn.classList.toggle('collapsed');
  
  if (toggleIcon) {
    if (sidebar && sidebar.classList.contains('collapsed')) {
      toggleIcon.className = 'bi bi-list';
    } else {
      toggleIcon.className = 'bi bi-x-lg';
    }
  }
}

// Clear all filters function (needs to be global for onclick handlers)
function clearAllFilters(event) {
  if (event) event.preventDefault();
  if (window.TESTCASE_LIST_URL) {
    // Redirect to URL with empty query parameters
    window.location.href = window.TESTCASE_LIST_URL + '?sheet=&sw=&version=&feature=&q=';
  }
}

