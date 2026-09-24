// File: static/js/event-bus-events.js
/**
 * 📋 EVENT BUS - CONSTANTE EVENIMENTE
 * Toate evenimentele disponibile în sistem
 *
 * @version 2.0.0
 * @author Adelina Trandafir - Avatar Soft SRL
 */

export const EVENTS = {
  // ========== EVENIMENTE PENTRU TABLE CONTROLLER ==========
  TABLE_INIT: 'table-init',
  TABLE_MANAGER_READY: 'table-manager-ready',
  TABLE_READY: 'table-ready',
  TABLE_REFRESH: 'table-refresh',
  TABLE_REBUILD: 'table-rebuild',
  TABLE_DESTROY: 'table-destroy',
  TABLE_RESIZE: 'table-resize',

  // ========== EVENIMENTE PENTRU DATA LOADING ==========
  DATA_LOAD_START: 'data-load-start',
  DATA_LOAD_COMPLETE: 'data-load-complete',
  DATA_REFRESH_START: 'data-refresh-start',
  DATA_REFRESH_COMPLETE: 'data-refresh-complete',
  DATA_LOAD_SKIPPED: 'data-load-skipped',
  DATA_LOAD_ERROR: 'data-load-error',

  // ========== EVENIMENTE PENTRU TABELELE SECUNDARE ==========
  EXTRA_DATA_LOAD_START: 'extra-data-load-start',
  EXTRA_DATA_LOAD_COMPLETE: 'extra-data-load-complete',
  // ---------- Folosesc refresh pentru a nu interfera cu load ----------
  EXTRA_DATA_REFRESH_START: 'extra-data-refresh-start',
  EXTRA_DATA_REFRESH_COMPLETE: 'extra-data-refresh-complete',
  // ---------- Folosesc search pentru a nu interfera cu load ----------
  EXTRA_DATA_SEARCH_START: 'extra-data-search-start',
  EXTRA_DATA_SEARCH_COMPLETE: 'extra-data-search-complete',
  // ---------- Eroarea este comună pentru load, refresh și search ----------
  EXTRA_DATA_ERROR: 'extra-data-error',

  // ========== EVENIMENTE PENTRU TABLE BUILDING ==========
  TABLE_BUILD_START: 'table-build-start',
  TABLE_BUILD_COMPLETE: 'table-build-complete',
  TABLE_BUILD_ERROR: 'table-build-error',
  TABLE_EMPTY: 'table-empty',
  SETUP_TABLE_LISTENERS: 'setup-table-listeners',

  // ========== EVENIMENTE PENTRU FILTRARE ==========
  FILTER_INIT_REQUEST: 'filter-init',
  FILTER_APPLY: 'filter-apply',
  FILTER_APPLIED: 'filter-applied',
  FILTER_CLEAR: 'filter-clear',
  FILTER_CLEARED: 'filter-cleared',
  FILTER_SHOW_WINDOW: 'filter-show-window',
  FILTER_CLOSE_WINDOW: 'filter-close-window',
  FILTER_CHANGE: 'filter-change',
  FILTER_SQL_REQUEST: 'filter-init-request',
  FILTER_SQL_GENERATED: 'filter-sql-generated',
  FILTER_WINDOW_CLOSED: 'filter-window-closed',

  // Filter Column Values Fetch
  FILTER_FETCH_COLUMN_VALUES: 'filter-fetch-column-values',
  FILTER_FETCH_COLUMN_VALUES_SUCCESS: 'filter-fetch-column-values-success',
  FILTER_FETCH_COLUMN_VALUES_ERROR: 'filter-fetch-column-values-error',
  STRING_FILTER_CREATE_REQUEST: 'string-filter-create-request',
  STRING_FILTER_CREATE_ERROR: 'string-filter-create-error',
  STRING_FILTER_CREATE_SUCCESS: 'string-filter-create-success',

  // ========== EVENIMENTE PENTRU SORTING ==========
  SORT_CHANGED: 'sort-changed',
  SORT_APPLIED: 'sort-applied',
  SORT_CLEARED: 'sort-cleared',

  // ========== EVENIMENTE PENTRU SELECȚIE RÂNDURI ==========
  ROW_SELECT_TOGGLE: 'row-select-toggle',
  ROW_SELECTED: 'row-selected',
  ROW_DESELECTED: 'row-deselected',
  ROW_CLICKED: 'row-clicked',
  ROW_DOUBLE_CLICKED: 'row-double-clicked',
  ROWS_MULTI_SELECTED: 'rows-multi-selected',
  ROW_OPTIONS_CLICKED: 'row-options-clicked',
  SELECTION_CLEARED: 'selection-cleared',

  // ========== EVENIMENTE PENTRU COLOANE ==========
  COLUMN_RESIZE: 'column-resize',
  COLUMN_RESIZED: 'column-resized',
  COLUMN_VISIBILITY_CHANGED: 'column-visibility-changed',
  COLUMN_CLICKED: 'column-clicked',

  // ========== EVENIMENTE PENTRU STATISTICI ==========
  STATS_UPDATE: 'stats-update',
  STATS_REFRESH: 'stats-refresh',
  STATS_ERROR: 'stats-error',

  // ========== EVENIMENTE PENTRU ERORI ȘI DEBUGGING ==========
  ERROR_OCCURRED: 'error-occurred',
  WARNING_OCCURRED: 'warning-occurred',
  DEBUG_INFO: 'debug-info',

  // ========== EVENIMENTE PENTRU PERFORMANCE ==========
  PERFORMANCE_METRIC: 'performance-metric',
  MEMORY_WARNING: 'memory-warning',

  // ========== EVENIMENTE PENTRU STATE MANAGEMENT ==========
  STATE_CHANGED: 'state-changed',
  STATE_SAVED: 'state-saved',
  STATE_RESTORED: 'state-restored',

  // ========== EVENIMENTE PENTRU DASHBOARD ==========
  DASHBOARD_READY: 'dashboard-ready',
  DASHBOARD_REFRESH_INIT: 'dashboard-refresh-init',
  DASHBOARD_REFRESH_DONE: 'dashboard-refresh-done',
  DASHBOARD_ERROR: 'dashboard-error',

  // ========== EVENIMENTE PENTRU TAB-uri ==========
  TAB_CLICKED_SAME: 'same-tab-clicked',
  TAB_CLICKED_OTHER: 'other-tab-clicked',

  // ========== EVENIMENTE PENTRU MONITORIZARE ȘI SESSIUNI ==========
  USER_ACTIVITY: 'user-activity',

  // ========== EVENIMENTE PENTRU PANEL-URI ==========
  PANEL_TOGGLE_REQUEST: 'panel-toggle-request',
  PANEL_SHOW_REQUEST: 'panel-show-request',
  PANEL_HIDE_REQUEST: 'panel-hide-request',
  PANEL_SHOWN: 'panel-shown',
  PANEL_HIDDEN: 'panel-hidden',
  PANEL_POPULATED: 'panel-populated',
  PANEL_CLEARED: 'panel-cleared',
  PANEL_CLOSE_ALL_REQUEST: 'panel-close-all-request',
  PANEL_STICKY_TOGGLE_REQUEST: 'panel-sticky-toggle-request',

  // ========== EVENIMENTE PENTRU OPTIONS MANAGER ==========
  OPTIONS_SHOW: 'options-show',
  OPTIONS_HIDE: 'options-hide',
  OPTIONS_CHECKBOX_REQUEST: 'options-checkbox-request',
  OPTIONS_EXPORT_DATA_REQUEST: 'options-export-data-request',
  OPTIONS_COLUMN_SETTINGS_REQUEST: 'options-column-settings-request',
  OPTIONS_FILTER_SHOW_FILTER_PANEL: 'options-filter-show-filter-panel',
  OPTIONS_FILTER_HIDE_FILTER_PANEL: 'options-filter-hide-filter-panel',

  // ========== EVENIMENTE PENTRU SESSION MANAGEMENT ==========
  SESSION_CLEANUP: 'session-cleanup',
  SESSION_EXPIRED: 'session-expired',
  SESSION_FINAL_WARNING: 'session-final-warning',
  SESSION_ERROR: 'session-error',
  SESSION_EXTENDED: 'session-extended',
  SESSION_WARNING: 'session-warning',

  // ========== EVENIMENTE PENTRU DETALII PANOU ==========
  DETAILS_PANEL_CLOSED: 'details-panel-closed',
  DETAILS_PANEL_OPENED: 'details-panel-opened',
  DETAILS_PANEL_FEEDBACK_SAVED: 'details-panel-feedback-saved',

  // ========== EVENIMENTE PENTRU CALENDAR ==========
  CALENDAR_DATE_SELECTED: 'calendar-date-selected',
  CALENDAR_DATE_CLEARED: 'calendar-date-cleared',

  ADAUGA_LEAD_INIT: 'adauga-lead-init',

  // ========== EVENIMENTE PENTRU TRANSFER LEAD ==========
  TRANSFER_LEAD_OPEN_REQUEST: 'transfer-lead-open-request',
  TRANSFER_LEAD_OPENED:       'transfer-lead-opened',
  TRANSFER_LEAD_CLOSE_REQUEST:'transfer-lead-close-request',
  TRANSFER_LEAD_CLOSED:       'transfer-lead-closed',
  TRANSFER_LEAD_SAVE_REQUEST: 'transfer-lead-save-request',
  TRANSFER_LEAD_SAVED:        'transfer-lead-saved',
  TRANSFER_LEAD_ERROR:        'transfer-lead-error',
  TRANSFER_LEAD_TAB_CHANGE:   'transfer-lead-tab-change',
  TRANSFER_LEAD_TAB_CHANGED:  'transfer-lead-tab-changed',
  TRANSFER_LEAD_DATA_READY:   'transfer-lead-data-ready',

  // ========== EVENIMENTE PENTRU CAUTARE ANAF ==========
  ANAF_SEARCH_OPEN:   'anaf-search-open',
  ANAF_SEARCH_SELECT: 'anaf-search-select',
  ANAF_SEARCH_CLOSE:  'anaf-search-close',
};
