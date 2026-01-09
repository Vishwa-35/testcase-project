/**
 * Django Admin Bulk Actions Enhancement
 * 
 * Provides:
 * - Enhanced bulk action UI
 * - Confirmation dialogs with context
 * - Visual feedback for selections
 * - Role-based action restrictions
 */

(function() {
    'use strict';
    
    document.addEventListener('DOMContentLoaded', function() {
        // Enhance bulk action selection
        enhanceBulkActions();
        
        // Add confirmation for bulk delete
        addBulkDeleteConfirmation();
        
        // Visual feedback for selected items
        addSelectionFeedback();
    });
    
    /**
     * Enhance bulk actions UI
     */
    function enhanceBulkActions() {
        var actionSelect = document.querySelector('select[name="action"]');
        var actionButton = document.querySelector('button[name="index"]');
        
        if (actionSelect && actionButton) {
            // Add visual indicator when action is selected
            actionSelect.addEventListener('change', function() {
                if (this.value) {
                    actionButton.style.background = '#2563EB';
                    actionButton.style.color = '#fff';
                } else {
                    actionButton.style.background = '#9CA3AF';
                    actionButton.style.color = '#fff';
                }
            });
            
            // Disable button if no action selected
            if (!actionSelect.value) {
                actionButton.disabled = true;
                actionButton.style.background = '#9CA3AF';
                actionButton.style.cursor = 'not-allowed';
            }
        }
        
        // Check if any items are selected
        var checkboxes = document.querySelectorAll('#result_list input[type="checkbox"]:not(#action-toggle)');
        var actionToggle = document.getElementById('action-toggle');
        
        if (checkboxes.length > 0 && actionToggle) {
            actionToggle.addEventListener('change', function() {
                updateActionButtonState();
            });
            
            checkboxes.forEach(function(checkbox) {
                checkbox.addEventListener('change', function() {
                    updateActionButtonState();
                });
            });
        }
    }
    
    /**
     * Update action button state based on selections
     */
    function updateActionButtonState() {
        var checkboxes = document.querySelectorAll('#result_list input[type="checkbox"]:checked:not(#action-toggle)');
        var actionSelect = document.querySelector('select[name="action"]');
        var actionButton = document.querySelector('button[name="index"]');
        
        if (actionButton) {
            if (checkboxes.length > 0 && actionSelect && actionSelect.value) {
                actionButton.disabled = false;
                actionButton.style.background = '#2563EB';
                actionButton.style.cursor = 'pointer';
            } else {
                actionButton.disabled = true;
                actionButton.style.background = '#9CA3AF';
                actionButton.style.cursor = 'not-allowed';
            }
        }
    }
    
    /**
     * Add confirmation dialog for bulk delete
     */
    function addBulkDeleteConfirmation() {
        var actionSelect = document.querySelector('select[name="action"]');
        var actionButton = document.querySelector('button[name="index"]');
        
        if (actionSelect && actionButton) {
            actionButton.addEventListener('click', function(e) {
                if (actionSelect.value === 'delete_selected') {
                    var checkboxes = document.querySelectorAll('#result_list input[type="checkbox"]:checked:not(#action-toggle)');
                    
                    if (checkboxes.length === 0) {
                        e.preventDefault();
                        alert('Please select at least one item to delete.');
                        return false;
                    }
                    
                    // Get selected item identifiers
                    var selectedItems = [];
                    checkboxes.forEach(function(checkbox) {
                        var row = checkbox.closest('tr');
                        if (row) {
                            var firstCell = row.querySelector('td:first-child');
                            if (firstCell) {
                                var text = firstCell.textContent.trim();
                                if (text) {
                                    selectedItems.push(text.substring(0, 50)); // Limit length
                                }
                            }
                        }
                    });
                    
                    var message = 'Are you sure you want to delete ' + checkboxes.length + ' item(s)?\n\n';
                    message += 'This action cannot be undone.\n\n';
                    
                    if (selectedItems.length > 0 && selectedItems.length <= 5) {
                        message += 'Selected items:\n';
                        selectedItems.forEach(function(item) {
                            message += '  • ' + item + '\n';
                        });
                    } else if (selectedItems.length > 5) {
                        message += 'First 5 selected items:\n';
                        for (var i = 0; i < 5; i++) {
                            message += '  • ' + selectedItems[i] + '\n';
                        }
                        message += '  ... and ' + (selectedItems.length - 5) + ' more\n';
                    }
                    
                    if (!confirm(message)) {
                        e.preventDefault();
                        return false;
                    }
                }
            });
        }
    }
    
    /**
     * Add visual feedback for selected items
     */
    function addSelectionFeedback() {
        var checkboxes = document.querySelectorAll('#result_list input[type="checkbox"]:not(#action-toggle)');
        
        checkboxes.forEach(function(checkbox) {
            checkbox.addEventListener('change', function() {
                var row = this.closest('tr');
                if (row) {
                    if (this.checked) {
                        row.classList.add('selected');
                        row.style.backgroundColor = '#DBEAFE';
                    } else {
                        row.classList.remove('selected');
                        row.style.backgroundColor = '';
                    }
                }
            });
        });
        
        // Update all rows when toggle all is clicked
        var actionToggle = document.getElementById('action-toggle');
        if (actionToggle) {
            actionToggle.addEventListener('change', function() {
                var checkboxes = document.querySelectorAll('#result_list input[type="checkbox"]:not(#action-toggle)');
                checkboxes.forEach(function(checkbox) {
                    var row = checkbox.closest('tr');
                    if (row) {
                        if (checkbox.checked) {
                            row.classList.add('selected');
                            row.style.backgroundColor = '#DBEAFE';
                        } else {
                            row.classList.remove('selected');
                            row.style.backgroundColor = '';
                        }
                    }
                });
            });
        }
    }
})();

