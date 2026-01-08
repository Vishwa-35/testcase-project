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

  // Handle export submission
  const exportSubmitBtn = document.getElementById('exportSubmitBtn');
  if (exportSubmitBtn) {
    exportSubmitBtn.addEventListener('click', function() {
      const form = document.getElementById('exportForm');
      if (!form) return;
      
      const formData = new FormData(form);
      const exportType = formData.get('export_type');
      
      if (exportType === 'excel') {
        // Excel export - use direct navigation for immediate download
        // Backend handles version selection automatically (latest version only, based on created_at timestamp)
        // All request parameters are ignored - backend determines export scope internally
        window.location.href = window.EXPORT_EXCEL_URL;
      } else {
        // HTML export - no parameters needed, backend handles everything automatically
        window.open(window.EXPORT_HTML_URL, '_blank');
        
        // After opening HTML, redirect to list with export_completed flag
        setTimeout(() => {
          const timestamp = Date.now();
          window.location.href = window.TESTCASE_LIST_URL + '?export_completed=' + timestamp;
        }, 1000);
      }
      
      const modal = bootstrap.Modal.getInstance(document.getElementById('exportModal'));
      if (modal) modal.hide();
    });
  }
});

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

