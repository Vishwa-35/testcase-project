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

  // Handle export submission for #exportModal
  const exportSubmitBtn = document.getElementById('versionExportSubmitBtn');
  if (exportSubmitBtn) {
    exportSubmitBtn.addEventListener('click', function() {
      // Collect selected features and versions
      const selectedFeatures = Array.from(document.querySelectorAll('#exportModal .feature-checkbox:checked:not(:disabled)'))
        .map(cb => cb.value);
      
      const selectedVersionIds = Array.from(document.querySelectorAll('#exportModal .version-checkbox:checked:not(:disabled)'))
        .map(cb => parseInt(cb.value));
      
      // Export always allowed - if nothing selected, export ALL data
      // Backend will handle empty selections by exporting all available data
      
      // Get export type from modal data attribute (set when opening modal)
      const modal = document.getElementById('exportModal');
      const exportType = modal.getAttribute('data-export-type') || 'excel';
      
      // Build JSON payload - backend expects features and versions arrays
      const payload = {
        features: selectedFeatures,
        versions: selectedVersionIds
      };
      
      // Close modal
      const bootstrapModal = bootstrap.Modal.getInstance(modal);
      if (bootstrapModal) bootstrapModal.hide();
      
      // Submit export request
      if (exportType === 'excel') {
        // Excel export - POST request that downloads file
        fetch(window.EXPORT_EXCEL_URL, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCookie('csrftoken'),
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(payload)
        })
        .then(response => {
          if (response.ok) {
            return response.blob();
          }
          throw new Error('Export failed');
        })
        .then(blob => {
          const url = window.URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `TestCases_Export_${new Date().toISOString().slice(0, 10)}.xlsx`;
          document.body.appendChild(a);
          a.click();
          window.URL.revokeObjectURL(url);
          document.body.removeChild(a);
          // Redirect to list page with success message
          window.location.href = window.TESTCASE_LIST_URL + '?export_completed=1';
        })
        .catch(error => {
          console.error('Error:', error);
          alert('Error exporting to Excel. Please try again.');
        });
      } else {
        // HTML export - POST request (returns redirect)
        fetch(window.EXPORT_HTML_URL, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCookie('csrftoken'),
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(payload),
          redirect: 'follow'
        })
        .then(response => {
          if (response.redirected) {
            // Follow redirect to snapshot view
            window.location.href = response.url;
          } else if (response.ok) {
            // Try to parse as JSON (if server returns JSON)
            return response.json().then(data => {
              if (data.redirect_url) {
                window.location.href = data.redirect_url;
              } else {
                window.location.href = window.TESTCASE_LIST_URL + '?export_completed=1';
              }
            }).catch(() => {
              // Not JSON, just redirect to list
              window.location.href = window.TESTCASE_LIST_URL + '?export_completed=1';
            });
          } else {
            throw new Error('Export failed');
          }
        })
        .catch(error => {
          console.error('Error:', error);
          alert('Error exporting to HTML. Please try again.');
        });
      }
    });
  }
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

// Helper function to show validation message in export modal
function showExportValidation(message) {
  const validationDiv = document.getElementById('exportValidationMessage');
  const validationText = document.getElementById('exportValidationText');
  if (validationDiv && validationText) {
    validationText.textContent = message;
    validationDiv.classList.remove('d-none');
  }
}

// Helper function to hide validation message in export modal
function hideExportValidation() {
  const validationDiv = document.getElementById('exportValidationMessage');
  if (validationDiv) {
    validationDiv.classList.add('d-none');
  }
}

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

