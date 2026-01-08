// Inline Edit functionality for Project Overview fields
// CRITICAL: Save ONLY on ENTER key press. NO auto-save on blur, focusout, or any other event.
document.addEventListener('DOMContentLoaded', function() {
    const editableFields = document.querySelectorAll('.editable-field');
    
    editableFields.forEach(field => {
        let originalValue = field.textContent.trim();
        let isEditing = false;
        let isSaving = false; // Flag to prevent blur from canceling during save
        let blurTimeoutId = null; // Track blur timeout to cancel it if needed
        
        field.addEventListener('click', function(e) {
            // Don't start editing if clicking on a link inside the field
            if (e.target.tagName === 'A') {
                return; // Allow link to work normally
            }
            
            if (isEditing) return;
            
            e.preventDefault();
            e.stopPropagation();
            
            isEditing = true;
            isSaving = false;
            
            // Get the actual value - if it's a link, get the href, otherwise get textContent
            const linkElement = field.querySelector('a');
            originalValue = linkElement ? linkElement.href : field.textContent.trim();
            
            // Create input element
            const input = document.createElement('input');
            input.type = 'text';
            input.value = originalValue === '-' ? '' : originalValue;
            input.className = 'form-control form-control-sm';
            input.style.width = '100%';
            
            // Clear field and add input
            field.innerHTML = '';
            field.appendChild(input);
            field.classList.add('editing');
            
            // Focus and select
            input.focus();
            input.select();
            
            // Function to restore original value (used for ESC and cancel)
            function restoreOriginalValue() {
                const isUrl = originalValue && (originalValue.startsWith('http://') || originalValue.startsWith('https://'));
                if (isUrl) {
                    const link = document.createElement('a');
                    link.href = originalValue;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.textContent = originalValue;
                    field.innerHTML = '';
                    field.appendChild(link);
                } else {
                    field.textContent = originalValue || '-';
                }
            }
            
            // Handle save ONLY on Enter key press - NO OTHER EVENT TRIGGERS THIS
            function saveField() {
                // Prevent multiple simultaneous saves
                if (!isEditing || isSaving) return;
                
                isSaving = true; // Set flag to prevent blur cancellation
                
                // Cancel any pending blur timeout
                if (blurTimeoutId !== null) {
                    clearTimeout(blurTimeoutId);
                    blurTimeoutId = null;
                }
                
                const newValue = input.value.trim();
                const key = field.getAttribute('data-key');
                const url = field.getAttribute('data-url');
                const csrfToken = field.getAttribute('data-csrf');
                
                // Update field immediately with loading state
                field.innerHTML = '<i class="bi bi-hourglass-split"></i> Saving...';
                field.classList.add('editing');
                
                // Send update request - ONLY triggered by Enter key
                fetch(url, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        key: key,
                        value: newValue
                    })
                })
                .then(response => response.json())
                .then(data => {
                    isEditing = false;
                    isSaving = false;
                    field.classList.remove('editing');
                    
                    if (data.ok) {
                        // Check if the value is a URL (for fields like DBC test_it)
                        const isUrl = newValue && (newValue.startsWith('http://') || newValue.startsWith('https://'));
                        
                        if (isUrl) {
                            // Create a link element
                            const link = document.createElement('a');
                            link.href = newValue;
                            link.target = '_blank';
                            link.rel = 'noopener noreferrer';
                            link.textContent = newValue;
                            field.innerHTML = '';
                            field.appendChild(link);
                        } else {
                            field.textContent = newValue || '-';
                        }
                        originalValue = newValue || '-';
                        
                        // Show success feedback briefly
                        field.style.backgroundColor = 'rgba(40, 167, 69, 0.2)';
                        setTimeout(() => {
                            field.style.backgroundColor = '';
                        }, 1000);
                    } else {
                        // On validation failure: keep input active and show error
                        // Restore input field so user can correct the value
                        input.value = newValue;
                        field.innerHTML = '';
                        field.appendChild(input);
                        field.classList.add('editing');
                        isEditing = true;
                        isSaving = false;
                        input.focus();
                        input.select();
                        alert(data.error || 'Failed to update field. Please correct the value and press Enter to save.');
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    // On error: keep input active so user can retry
                    input.value = input.value.trim();
                    field.innerHTML = '';
                    field.appendChild(input);
                    field.classList.add('editing');
                    isEditing = true;
                    isSaving = false;
                    input.focus();
                    alert('Error updating field. Please try again and press Enter to save.');
                });
            }
            
            // Handle keyboard events: ENTER to save, ESC to cancel
            // THIS IS THE ONLY PLACE WHERE saveField() IS CALLED
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation(); // Prevent any other handlers
                    saveField(); // ONLY save trigger
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation();
                    
                    // Cancel any pending blur timeout
                    if (blurTimeoutId !== null) {
                        clearTimeout(blurTimeoutId);
                        blurTimeoutId = null;
                    }
                    
                    isEditing = false;
                    isSaving = false;
                    field.classList.remove('editing');
                    restoreOriginalValue();
                }
            });
            
            // Handle blur: Cancel editing and restore original value (DO NOT save)
            // This ONLY cancels - it NEVER calls saveField()
            input.addEventListener('blur', function(e) {
                // Only cancel if still editing and NOT currently saving
                if (isEditing && !isSaving) {
                    // Small delay to allow Enter key to process first (if Enter was pressed)
                    blurTimeoutId = setTimeout(() => {
                        // Double-check: if we're still editing and not saving, cancel
                        if (isEditing && !isSaving) {
                            isEditing = false;
                            field.classList.remove('editing');
                            restoreOriginalValue();
                        }
                        blurTimeoutId = null;
                    }, 150);
                }
            });
        });
    });
});

