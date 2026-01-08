"""
Logging Utilities

RISK REMOVAL: Custom _debug_log() functions were scattered across:
- views.py, excel_export.py, apps.py

These functions had hardcoded file paths and were not production-ready.
This module replaces them with proper Django logging.
"""

import logging

# RISK REMOVAL: Replaces all _debug_log() calls with proper Django logging
# No hardcoded file paths, uses Django's logging configuration
logger = logging.getLogger(__name__)


def log_debug(location, message, data=None, exc_info=None):
    """
    Log debug message with context.
    
    RISK REMOVAL: Replaces _debug_log() functions that had hardcoded paths.
    Uses Django's logging system which is configurable and production-ready.
    
    Args:
        location: Code location (e.g., "views.py:function_name")
        message: Log message
        data: Optional dictionary of additional data
        exc_info: Optional exception info for error logging
    """
    extra_data = {"location": location}
    if data:
        extra_data["data"] = data
    
    logger.debug(f"{location}: {message}", extra=extra_data, exc_info=exc_info)


def log_info(location, message, data=None):
    """
    Log info message with context.
    
    RISK REMOVAL: Provides structured logging for important operations.
    
    Args:
        location: Code location
        message: Log message
        data: Optional dictionary of additional data
    """
    extra_data = {"location": location}
    if data:
        extra_data["data"] = data
    
    logger.info(f"{location}: {message}", extra=extra_data)


def log_error(location, message, data=None, exc_info=None):
    """
    Log error message with context.
    
    RISK REMOVAL: Provides structured error logging.
    
    Args:
        location: Code location
        message: Log message
        data: Optional dictionary of additional data
        exc_info: Optional exception info
    """
    extra_data = {"location": location}
    if data:
        extra_data["data"] = data
    
    logger.error(f"{location}: {message}", extra=extra_data, exc_info=exc_info)


def log_warning(location, message, data=None):
    """
    Log warning message with context.
    
    RISK REMOVAL: Provides structured warning logging.
    
    Args:
        location: Code location
        message: Log message
        data: Optional dictionary of additional data
    """
    extra_data = {"location": location}
    if data:
        extra_data["data"] = data
    
    logger.warning(f"{location}: {message}", extra=extra_data)

